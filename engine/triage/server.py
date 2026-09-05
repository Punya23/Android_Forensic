"""
Local Flask + SocketIO service the Electron dashboard talks to.

Everything runs on localhost only — this is a field tool, not a networked service.
The acquisition runs in a background thread and streams progress over SocketIO so the UI
can render a live countdown, while REST endpoints serve finished case data.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import shutil
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file, abort
from flask_cors import CORS

# Load engine/.env (sibling of the triage/ package, not cwd — so this works whether
# the process is launched from the repo root, from engine/, or spawned by Electron
# with its own cwd). Real values here override the insecure SNAGR_AUTH_* defaults
# below without needing `export` in every shell that starts the server.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

try:
    from flask_socketio import SocketIO

    _HAVE_SOCKETIO = True
except Exception:
    _HAVE_SOCKETIO = False


from . import TOOL_NAME, __version__
from .acquire import MockDeviceSource, RealDeviceSource
from .adb import Adb
from .cancellation import AcquisitionCancelled, CancellationToken
from .config import ACQUISITION_DISCLAIMER, Tier
from .custody import Case
from .pipeline import PipelineConfig, run_acquisition
from .validation_utils import (
    validate_case_id,
    validate_mock_path,
    validate_serial,
    validate_text_field,
    validate_webhook_url,
)
from . import registry


# ---------------------------------------------------------------------------
# CORS origin allowlist — loopback only by default
# ---------------------------------------------------------------------------
# Vite dev server (5173), Electron local server (5057), and Electron app://
# origin are the only consumers.  Additional origins can be added via the
# SNAGR_CORS_ORIGIN env var (comma-separated) for unusual Electron configs.
_ALLOWED_ORIGINS: list[str] = [
    "http://localhost:5173",
    "http://localhost:5057",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5057",
    "app://.",  # Electron file-protocol origin
]
_extra_cors = os.environ.get("SNAGR_CORS_ORIGIN", "").strip()
if _extra_cors:
    _ALLOWED_ORIGINS.extend(o.strip() for o in _extra_cors.split(",") if o.strip())


# Default cases root — change to _test_output if running integration tests
CASES_ROOT = Path("cases")


def create_app(cases_root: Path = CASES_ROOT):

    app = Flask(__name__)

    CORS(app, origins=_ALLOWED_ORIGINS, supports_credentials=False)

    cases_root.mkdir(parents=True, exist_ok=True)

    # Backfill the case registry from any case folders that predate it (or were
    # created by a build before this feature shipped). Cheap and idempotent — only
    # un-indexed folders are touched.
    try:
        registry.sync_registry(cases_root)
    except Exception:  # pragma: no cover - the registry must never block startup
        pass

    def _finalize_report(case: Case, *, trigger: str) -> None:
        """After (re-)generating a report: snapshot it into history and refresh the
        case's registry row. Called from every call site that writes report.html so
        the "previous reports" history and the case list stay live without a full
        directory rescan on each dashboard request."""
        try:
            registry.upsert_case(cases_root, case)
            registry.record_report(cases_root, case.root, case.meta.case_id, trigger=trigger)
        except Exception:  # pragma: no cover - report generation must not fail on this
            pass

    socketio = (
        SocketIO(
            app,
            cors_allowed_origins=_ALLOWED_ORIGINS,
            async_mode="threading",
            # Werkzeug dev server cannot handle the WebSocket upgrade handshake;
            # force HTTP long-polling so real-time progress streaming still works.
            allow_upgrades=False,
        )
        if _HAVE_SOCKETIO
        else None
    )

    state: dict[str, Any] = {
        "running": False,
        "last_case": None,
        "cancel_token": None,       # CancellationToken for the current run
        "report_generating": False, # True while background report is building
        "report_ready_cases": set(),# case_ids whose reports have been generated
    }

    # ---------------------------------------------------------
    # AUTH
    # ---------------------------------------------------------
    #
    # Threat model: loopback-only; the goal is "unattended laptop cannot be
    # opened by whoever walks past", not defending against a network attacker.
    #
    # Two operating modes:
    #   PRODUCTION (default): SNAGR_AUTH_PASS must be set in env.  If missing
    #     the login endpoint returns 503 with a clear message.  Use
    #     SNAGR_AUTH_HASH for a bcrypt-hashed password (recommended).
    #   DEMO (--demo / SNAGR_DEMO=1): falls back to examiner/snagr with a
    #     loud warning at startup and in every auth-failure log line.
    #
    # Login rate limiting: max 5 attempts per IP per 60 s; lock out for 300 s.
    # CSRF: a per-session token is returned at login; all state-changing
    #   methods (POST/PUT/PATCH/DELETE) require X-CSRF-Token header.

    _DEMO_MODE: bool = bool(
        os.environ.get("SNAGR_DEMO", "").strip()
        or os.environ.get("SNAGR_DEMO_MODE", "").strip()
    )
    AUTH_USER = os.environ.get("SNAGR_AUTH_USER", "examiner")
    AUTH_PASS = os.environ.get("SNAGR_AUTH_PASS", "")
    AUTH_HASH = os.environ.get("SNAGR_AUTH_HASH", "")  # bcrypt hash (optional)

    _credentials_ok: bool
    if AUTH_HASH:
        _credentials_ok = True  # bcrypt-hashed password always accepted
    elif AUTH_PASS:
        _credentials_ok = True
    elif _DEMO_MODE:
        AUTH_PASS = "snagr"
        _credentials_ok = True
        print(
            f"[auth] DEMO MODE — default credentials ({AUTH_USER}/snagr) are active. "
            f"Do NOT use against real evidence.",
            flush=True,
        )
    else:
        _credentials_ok = False
        print(
            "[auth] FATAL: SNAGR_AUTH_PASS is not set and demo mode is off. "
            "Set SNAGR_AUTH_PASS before handling evidence, or start with SNAGR_DEMO=1.",
            flush=True,
        )

    SESSION_TTL_SECONDS = 12 * 3600
    _sessions: dict[str, dict] = {}  # token -> {expiry, csrf_token, username}

    # Rate-limiting: IP -> list of failure timestamps
    _login_failures: dict[str, list[float]] = defaultdict(list)
    _RATE_LIMIT_WINDOW = 60.0   # seconds
    _RATE_LIMIT_MAX = 5          # failures allowed per window
    _RATE_LIMIT_LOCKOUT = 300.0  # lockout duration after exceeding limit

    def _check_rate_limit(ip: str) -> bool:
        """Return True if the IP is allowed to attempt login."""
        now = time.time()
        failures = _login_failures[ip]
        # Trim old failures
        failures[:] = [t for t in failures if now - t < _RATE_LIMIT_WINDOW]
        if len(failures) >= _RATE_LIMIT_MAX:
            # Check if oldest failure is within lockout window
            if failures and (now - failures[0]) < _RATE_LIMIT_LOCKOUT:
                return False
        return True

    def _record_failure(ip: str) -> None:
        _login_failures[ip].append(time.time())

    def _issue_token() -> str:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        _sessions[token] = {
            "expiry": time.time() + SESSION_TTL_SECONDS,
            "csrf_token": csrf,
            "username": AUTH_USER,
        }
        return token

    def _token_valid(token: str | None) -> bool:
        if not token:
            return False
        sess = _sessions.get(token)
        if sess is None:
            return False
        if sess["expiry"] < time.time():
            _sessions.pop(token, None)
            return False
        return True

    def _get_csrf(token: str) -> str:
        sess = _sessions.get(token, {})
        return sess.get("csrf_token", "")

    def _verify_password(password: str) -> bool:
        """Verify password against hash (bcrypt) or plaintext depending on config."""
        if not _credentials_ok:
            return False
        if AUTH_HASH:
            try:
                import bcrypt  # type: ignore[import]
                return bcrypt.checkpw(password.encode(), AUTH_HASH.encode())
            except Exception:
                return False
        return hmac.compare_digest(password, AUTH_PASS)

    def _is_public_route(path: str) -> bool:
        if path in ("/api/health", "/api/auth/login"):
            return True
        # Raw-URL resource routes — see the comment above the AUTH block.
        if path.startswith("/api/case/") and (
            path.endswith("/report")
            or "/reports/" in path
            or "/media/" in path
            or path.endswith("/export/download")
        ):
            return True
        return False

    @app.before_request
    def _require_auth():
        if request.method == "OPTIONS" or not request.path.startswith("/api/"):
            return None
        if _is_public_route(request.path):
            return None
        token = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if not _token_valid(token):
            return jsonify({"error": "unauthorized"}), 401
        # CSRF check for state-changing methods
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            expected_csrf = _get_csrf(token)
            provided_csrf = (request.headers.get("X-CSRF-Token") or "").strip()
            if expected_csrf and not hmac.compare_digest(provided_csrf, expected_csrf):
                return jsonify({"error": "invalid or missing CSRF token"}), 403
        return None

    @app.post("/api/auth/login")
    def auth_login():
        ip = request.remote_addr or "unknown"
        if not _check_rate_limit(ip):
            return jsonify({"error": "too many login attempts — wait 5 minutes"}), 429

        if not _credentials_ok:
            return jsonify(
                {"error": "server not configured for authentication — see startup logs"}
            ), 503

        body = request.get_json(silent=True) or {}
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        user_ok = hmac.compare_digest(username, AUTH_USER)
        pass_ok = _verify_password(password)
        if not (user_ok and pass_ok):
            _record_failure(ip)
            return jsonify({"error": "invalid credentials"}), 401
        token = _issue_token()
        csrf = _get_csrf(token)
        return jsonify(
            {
                "token": token,
                "csrf_token": csrf,
                "expires_in": SESSION_TTL_SECONDS,
                "username": AUTH_USER,
            }
        )

    @app.post("/api/auth/logout")
    def auth_logout():
        token = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        _sessions.pop(token, None)
        return jsonify({"ok": True})

    @app.get("/api/auth/me")
    def auth_me():
        # Reaching this point already proves the token is valid — before_request gated it.
        return jsonify({"username": AUTH_USER})

    # ---------------------------------------------------------
    # CASE-INTELLIGENCE STORES
    #
    # The case bank (retrieval corpus) and the knowledge graph (learned artifact
    # priors) are shared by every case in this store, so they are loaded lazily and
    # cached on the app rather than rebuilt per request.
    # ---------------------------------------------------------

    #: Department-local corpus. Sits beside the case store so it survives upgrades.
    def _local_corpus_path() -> Path:
        return cases_root / "case_studies.jsonl"

    def _case_bank(refresh: bool = False):
        from .intel import CaseBank

        if refresh or "case_bank" not in state:
            state["case_bank"] = CaseBank.load(_local_corpus_path())
        return state["case_bank"]

    def _knowledge_graph(root: Path, bank=None, refresh: bool = False):
        from .intel import KnowledgeGraph, GRAPH_FILENAME

        if refresh or "knowledge_graph" not in state:
            state["knowledge_graph"] = KnowledgeGraph.load(
                root / GRAPH_FILENAME,
                bootstrap=bank if bank is not None else _case_bank(),
            )
        return state["knowledge_graph"]

    def _embedder(refresh: bool = False):
        """Local embedding model for semantic retrieval, or None if switched off.

        Cached like the other intel stores: probing Ollama and reloading the vector
        cache on every request would add a round-trip to each plan preview. Availability
        is re-probed on ``refresh`` so starting Ollama mid-session is picked up without
        restarting the engine.
        """
        from .intel.embeddings import get_embedder

        if refresh or "embedder" not in state:
            state["embedder"] = get_embedder(cases_root)
        return state["embedder"]

    def _save_graph(graph) -> None:
        from .intel import GRAPH_FILENAME

        graph.save(cases_root / GRAPH_FILENAME)

    # ---------------------------------------------------------
    # META
    # ---------------------------------------------------------

    @app.get("/api/health")
    def health():

        return jsonify(
            {
                "tool": TOOL_NAME,
                "version": __version__,
                "disclaimer": ACQUISITION_DISCLAIMER,
                "adb": Adb().available,
                "running": state["running"],
            }
        )

    @app.get("/api/validation")
    def validation():
        """Installation-wide tool validation: known-answer self-test + CFTT coverage.

        Distinct from the per-case copy written into each case folder. This one answers
        "what is this build demonstrated to do, right now" without needing a case open.
        The self-test is offline (temp fixtures, no device, no network) and takes about a
        second, so it is computed on demand rather than cached — a stale validation record
        is worse than none.
        """
        try:
            from .validation import (
                coverage_matrix,
                coverage_summary,
                known_limitations,
                render_report_json,
                run_self_validation,
                validate_report,
            )

            report = run_self_validation()
            data = json.loads(render_report_json(report))
            data["coverage"] = coverage_matrix()
            data["coverage_summary"] = coverage_summary()
            data["completeness"] = validate_report(report)
            data["known_limitations"] = known_limitations()
            return jsonify(data)
        except Exception as exc:  # pragma: no cover - defensive
            return (
                jsonify(
                    {
                        "error": str(exc),
                        "note": (
                            "The self-validation could not run. Treat this build as "
                            "unvalidated — this is not a pass."
                        ),
                    }
                ),
                500,
            )

    @app.get("/api/devices")
    def devices():

        real = Adb.list_devices()

        mocks = []

        corpus_root = Path("_corpus")

        if corpus_root.exists():

            for d in sorted(corpus_root.iterdir()):

                if (d / "_device.json").exists():

                    meta = json.loads((d / "_device.json").read_text())

                    mocks.append(
                        {
                            "id": str(d),
                            "kind": "mock",
                            "label": meta.get("device", {}).get("model", d.name),
                        }
                    )

        return jsonify({"real": real, "mock": mocks})

    @app.get("/api/devices/check")
    def devices_check():
        """Connection-state + Developer-Options guidance for one device.

        The dashboard equivalent of ``triage.cli check-device`` — nothing here needs a
        terminal. Meant to be polled from the Acquisition view's device picker (once on
        selection, or on an explicit "Re-check" click) so "no devices found" is never
        the examiner's only signal. See ``triage.preflight`` for why the very first
        Developer-Options/USB-debugging enable can never be automated, on any brand,
        by this tool or any other.
        """
        from .preflight import ConnectionState, detect_connection_state, steps_for_brand

        serial = request.args.get("serial") or None
        brand = request.args.get("brand", "")
        adb = Adb(serial=serial)
        readiness = detect_connection_state(adb)

        result: dict[str, Any] = {
            "state": readiness.state.value,
            "serial": readiness.serial,
            "note": readiness.note,
            "ready": readiness.state == ConnectionState.READY,
        }

        if readiness.state == ConnectionState.READY:
            source = RealDeviceSource(adb)
            info = source.device_info()
            result["device"] = {
                "manufacturer": info.manufacturer,
                "model": info.model,
                "brand": info.brand,
                "os_skin": info.os_skin,
                "android_version": info.android_version,
                "oem_quirks": info.oem_quirks,
            }
            # A ready device tells us its own brand — no need to ask the caller for it.
            brand = brand or info.brand

        result["brand"] = brand
        result["checklist"] = steps_for_brand(brand)
        return jsonify(result)

    @app.post("/api/devices/reassert-dev-options")
    def devices_reassert_dev_options():
        """STATE-CHANGING: re-enable Developer Options + USB debugging.

        Runs ``settings put global development_settings_enabled 1`` / ``adb_enabled 1``
        over an ADB shell session that must already exist — see
        ``triage.preflight.reassert_developer_options``. This is the only step in the
        whole Developer-Options sequence that can be scripted, and only because an
        existing session is the precondition for running it; it cannot perform the
        first-time enable on a device that has never had USB debugging turned on.
        This call happens before any case is opened, so — unlike every other
        state-changing step in this tool — it is NOT written to a case audit trail;
        the response says so explicitly so the dashboard can surface it.
        """
        from .preflight import reassert_developer_options

        body = request.get_json(silent=True) or {}
        adb = Adb(serial=body.get("serial") or None)
        if not adb.available:
            return jsonify({"error": "adb not available"}), 400

        dev_opts, adb_enabled = reassert_developer_options(adb)
        return jsonify(
            {
                "development_settings_enabled": {
                    "ok": dev_opts.ok,
                    "stderr": dev_opts.stderr.strip(),
                },
                "adb_enabled": {"ok": adb_enabled.ok, "stderr": adb_enabled.stderr.strip()},
                "note": "pre-case action — not written to any case's audit trail "
                "(no case is open yet); mention it in the case scope note if you "
                "go on to acquire from this device",
            }
        )

    # ---------------------------------------------------------
    # CASE INTELLIGENCE
    # ---------------------------------------------------------

    @app.post("/api/plan")
    def plan_preview():

        body = request.get_json(force=True) or {}

        description = str(body.get("description", "")).strip()

        if not description:

            return jsonify({"error": "a case description is required"}), 400

        from .intel import plan_case, get_provider

        provider = get_provider(str(body.get("llm_provider", "")) or None)

        # Retrieval + learned priors are on by default; a caller can ask for the
        # pure-doctrine plan to see what the ontology alone recommends.
        use_rag = bool(body.get("use_case_bank", True))
        bank = _case_bank() if use_rag else None
        graph = _knowledge_graph(cases_root, bank) if use_rag else None

        profile, plan = plan_case(
            description,
            provider=provider,
            allow_tier2=bool(body.get("allow_tier2", True)),
            case_number=str(body.get("case_number", "")).strip(),
            bank=bank,
            graph=graph,
            use_rag=use_rag,
            embedder=_embedder(refresh=bool(body.get("refresh_models"))) if use_rag else None,
        )

        return jsonify(
            {
                "profile": profile.to_dict(),
                "plan": plan.to_dict(),
                "provider": provider.name,
                "provider_degraded_from": provider.degraded_from,
                "case_bank_size": len(bank) if bank is not None else 0,
                # How retrieval actually ran. A hybrid plan and a lexical one can rank
                # the same corpus differently, so the plan has to say which it was
                # rather than leaving the examiner to assume the better of the two.
                "retrieval_mode": getattr(bank, "retrieval_mode", "lexical") if bank else "none",
                "embedding": (_embedder().status() if _embedder() else {"available": False, "mode": "disabled"}),
            }
        )

    # ---------------------------------------------------------
    # CASE BANK  (retrieval corpus)
    # ---------------------------------------------------------

    @app.get("/api/casebank")
    def casebank_list():
        """List the retrieval corpus. ``?q=`` runs a search instead of a full listing."""
        bank = _case_bank()
        query = str(request.args.get("q", "")).strip()
        crime = str(request.args.get("crime_type", "")).strip() or None

        if query:
            hits = bank.search(
                query,
                crime_type=crime,
                top_k=int(request.args.get("top_k", 5)),
                embedder=_embedder(),
            )
            return jsonify(
                {
                    "query": query,
                    "crime_type": crime,
                    "total": len(bank),
                    "retrieval_mode": bank.retrieval_mode,
                    "results": [h.to_dict() for h in hits],
                }
            )

        studies = bank.by_crime(crime) if crime else bank.all()
        return jsonify(
            {
                "total": len(bank),
                "crime_type": crime,
                "studies": [s.to_dict() for s in studies],
                "disclaimer": (
                    "Case studies rank artifacts for collection planning. They are not "
                    "evidence in any case and carry no precedential weight. Entries marked "
                    "'synthetic' are expert-curated teaching exemplars, not real records."
                ),
            }
        )

    @app.post("/api/casebank")
    def casebank_add():
        """Add a worked case to the department's local corpus."""
        from .intel import CaseStudy

        body = request.get_json(force=True) or {}
        if not str(body.get("case_number", "")).strip():
            return jsonify({"error": "case_number is required"}), 400
        if not (body.get("artifacts") or []):
            return (
                jsonify(
                    {
                        "error": "at least one artifact outcome is required — a study with no "
                        "artifact yields teaches the planner nothing"
                    }
                ),
                400,
            )

        study = CaseStudy.from_dict(body)
        if study.source in ("", "unspecified"):
            study.source = "worked case (local installation)"

        bank = _case_bank()
        bank.append_to_file(study, _local_corpus_path())

        # Fold it into the learned graph immediately so the next plan sees it.
        graph = _knowledge_graph(cases_root, bank)
        edges = graph.observe_study(study)
        _save_graph(graph)

        return (
            jsonify(
                {
                    "added": study.case_number,
                    "corpus_size": len(bank),
                    "graph_edges_updated": edges,
                    "study": study.to_dict(),
                }
            ),
            201,
        )

    # ---------------------------------------------------------
    # KNOWLEDGE GRAPH  (learned artifact priors)
    # ---------------------------------------------------------

    @app.get("/api/knowledge-graph")
    def knowledge_graph_view():
        """The learned graph. ``?crime_type=`` returns that crime's artifact priors."""
        bank = _case_bank()
        graph = _knowledge_graph(cases_root, bank)
        crime = str(request.args.get("crime_type", "")).strip()

        if crime:
            return jsonify(
                {
                    "crime_type": crime,
                    "artifact_priors": graph.artifact_priors(crime),
                    "similar_crime_types": graph.similar_crime_types(crime),
                    "stats": graph.stats(),
                    "disclaimer": (
                        "Learned priors are shrunk toward the expert ontology until a link "
                        "is well observed, and can never remove an artifact from collection "
                        "— only reorder it."
                    ),
                }
            )

        return jsonify(graph.to_dict())

    @app.post("/api/case/<case_id>/outcome")
    def case_outcome(case_id: str):
        """Record the examiner's confirmed outcome — which artifacts actually solved it.

        This supersedes the provisional, automatically-derived feedback the pipeline
        writes, and optionally promotes the case into the retrieval corpus so future
        similar cases can cite it.
        """
        from .intel import CaseProfile, promote_case_to_study, record_confirmed

        case = _open(cases_root, case_id)
        body = request.get_json(force=True) or {}

        yields = body.get("artifact_yields") or {}
        if not isinstance(yields, dict) or not yields:
            return (
                jsonify(
                    {
                        "error": "artifact_yields is required, e.g. "
                        '{"call_logs": "decisive", "media": "none"}'
                    }
                ),
                400,
            )

        profile_dict = case.read_derived("case_profile") or {}
        if not profile_dict:
            return (
                jsonify(
                    {
                        "error": "this case has no case profile — it was acquired without a "
                        "case description, so there is no crime type to learn against"
                    }
                ),
                400,
            )
        profile = CaseProfile(**profile_dict)

        case_number = (
            str(body.get("case_number", "")).strip() or profile.case_number or case_id
        )
        examiner = str(body.get("examiner", "")).strip()

        bank = _case_bank()
        graph = _knowledge_graph(cases_root, bank)
        learned = record_confirmed(
            graph,
            profile.crime_type,
            yields,
            case_number=case_number,
            examiner=examiner,
        )
        if learned.get("recorded"):
            _save_graph(graph)

        promoted = None
        if bool(body.get("add_to_case_bank", False)):
            study = promote_case_to_study(
                profile,
                yields,
                outcome=str(body.get("outcome", "")),
                lessons=[str(x) for x in (body.get("lessons") or [])],
                notes=body.get("notes") or {},
                examiner=examiner,
            )
            study.case_number = case_number
            bank.append_to_file(study, _local_corpus_path())
            promoted = study.to_dict()

        result = {
            "case_id": case_id,
            "learning": learned,
            "promoted_to_case_bank": promoted,
            "corpus_size": len(bank),
            "graph_stats": graph.stats(),
        }
        case.write_derived("case_outcome", result)
        case.log(
            "intel.outcome",
            f"Examiner-confirmed outcome recorded by {examiner or 'unspecified'}: "
            + ", ".join(
                f"{k}={v}" for k, v in sorted(learned.get("yields", {}).items())
            )
            + (f"; promoted to case bank as {case_number}" if promoted else ""),
            tier=Tier.TIER0.value,
        )
        return jsonify(result)

    @app.get("/api/nomenclature")
    def nomenclature_glossary():
        """The controlled forensic-role vocabulary, for the intake help panel."""
        from .intel import glossary

        return jsonify(
            {
                "roles": glossary(),
                "note": (
                    "'Suspect' and 'accused' are procedural statuses, not findings of "
                    "guilt. Use 'victim' or 'deceased' for the person harmed; avoid "
                    "'guilty' and 'innocent', which are trial outcomes."
                ),
            }
        )

    @app.post("/api/nomenclature/check")
    def nomenclature_check():
        """Validate a draft case description before acquisition starts."""
        from .intel import extract_roles, validate_description

        body = request.get_json(force=True) or {}
        description = str(body.get("description", ""))
        return jsonify(
            {
                "roles": [r.to_dict() for r in extract_roles(description)],
                "warnings": validate_description(description),
            }
        )

    @app.post("/api/case/<case_id>/analyze")
    def analyze_case_endpoint(case_id: str):

        case = _open(cases_root, case_id)

        body = request.get_json(silent=True) or {}

        from .intel import analyze_case, get_provider
        from .intel.planner import CaseProfile, build_plan, extract_profile

        provider = get_provider(str(body.get("llm_provider", "")) or None)

        description = str(body.get("description", "")).strip()

        if description:

            profile = extract_profile(description, provider=provider)

            case.write_derived("case_profile", profile.to_dict())

            revised = build_plan(profile, graph=_knowledge_graph(cases_root))

            if case.read_derived("collection_plan"):

                case.write_derived("collection_plan_revised", revised.to_dict())

            else:

                case.write_derived("collection_plan", revised.to_dict())

        else:

            stored = case.read_derived("case_profile")

            if not stored:

                return jsonify({"error": "no case profile available"}), 400

            profile = CaseProfile(**stored)

        bundle = analyze_case(case, profile, provider=provider)

        return jsonify(bundle)
        # ---------------------------------------------------------

    @app.post("/api/case/<case_id>/investigate")
    def investigate_case_endpoint(case_id: str):
        """(Re-)run the deep investigation pass against this case's current
        ``ai_findings`` — see triage/intel/investigator.py. Requires a case profile
        (run /analyze first, or supply one this run already has)."""
        case = _open(cases_root, case_id)
        body = request.get_json(silent=True) or {}

        from .intel import get_provider
        from .intel.investigator import investigate_case
        from .intel.planner import CaseProfile, CollectionPlan

        stored_profile = case.read_derived("case_profile")
        if not stored_profile:
            return jsonify({"error": "no case profile available — run /analyze first"}), 400
        profile = CaseProfile(**stored_profile)

        stored_plan = case.read_derived("collection_plan")
        plan = CollectionPlan.from_dict(stored_plan) if stored_plan else None

        provider = get_provider(str(body.get("llm_provider", "")) or None)
        bundle = investigate_case(case, profile, plan=plan, provider=provider)
        return jsonify(bundle)

    @app.post("/api/case/<case_id>/ask")
    def ask_case_endpoint(case_id: str):
        """"Ask this case" — free-text Q&A over the case's own already-collected
        evidence. Local retrieval always runs; a grounded LLM synthesis on top is
        added only when a model is configured, and it is instructed to answer
        strictly from the retrieved passages — see triage/intel/case_qa.py.
        """
        from .intel import get_provider
        from .intel.case_qa import answer_question, build_passages

        case = _open(cases_root, case_id)
        body = request.get_json(silent=True) or {}
        question = str(body.get("question", "")).strip()
        if not question:
            return jsonify({"error": "a question is required"}), 400

        derived = {
            name: case.read_derived(name)
            for name in ("messages", "recovered", "calls", "browser", "locations", "contacts")
        }
        passages = build_passages(derived)
        provider = get_provider(str(body.get("llm_provider", "")) or None)
        embedder = _embedder() if bool(body.get("use_embeddings", True)) else None
        bundle = answer_question(
            question,
            passages,
            embedder=embedder,
            provider=provider,
            top_k=int(body.get("top_k", 6)),
        )
        bundle["passages_available"] = len(passages)
        return jsonify(bundle)

    @app.get("/api/case/<case_id>/linked-cases")
    def linked_cases_endpoint(case_id: str):
        """Other cases on this installation sharing a phone number, UPI ID, or email
        with this one — see triage/registry.py's find_linked_cases and
        triage/forensics/case_reference.py for exactly what is and isn't extracted.

        Every acquisition since this feature shipped indexes its own identifiers as
        part of run_acquisition(); a case acquired before that (or re-opened after new
        artifacts were added without a fresh acquisition) is indexed here, lazily, on
        first request — mirroring how registry.sync_registry() backfills the case list
        itself. Cheap: an upsert-and-query, not a re-scan of every other case.
        """
        from .forensics.case_reference import extract_case_identifiers

        case = _open(cases_root, case_id)
        contacts = case.read_derived("contacts") or []
        messages = case.read_derived("messages") or []
        calls = case.read_derived("calls") or []
        identifiers = extract_case_identifiers(contacts, messages, calls)
        registry.upsert_case_identifiers(cases_root, case_id, identifiers)

        return jsonify(
            {
                "case_id": case_id,
                "linked_cases": registry.find_linked_cases(cases_root, case_id),
                "disclaimer": (
                    "A shared identifier is a fact about this installation's case "
                    "history, not evidence linking two investigations. Every match "
                    "cites the exact artifact it came from in both cases — verify "
                    "against those artifacts before relying on it."
                ),
            }
        )

    # ACQUISITION
    # ---------------------------------------------------------

    @app.post("/api/acquire")
    def acquire():

        if state["running"]:
            return jsonify({"error": "an acquisition is already running"}), 409

        body = request.get_json(force=True) or {}

        # ------ Validate case_id ------
        raw_case_id = body.get("case_id") or _auto_case_id(cases_root)
        try:
            case_id = validate_case_id(str(raw_case_id))
        except ValueError as exc:
            return jsonify({"error": f"invalid case_id: {exc}"}), 400

        # ------ Validate examiner / authority / scope ------
        try:
            examiner = validate_text_field(str(body.get("examiner", "Unknown Examiner")), "examiner")
            authority = validate_text_field(str(body.get("authority", "")), "authority")
            scope = validate_text_field(str(body.get("scope", "")), "scope")
            webhook = validate_webhook_url(str(body.get("notify_webhook_url", "") or ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        # ------ Device source selection ------
        if body.get("mock"):
            try:
                mock_path = validate_mock_path(
                    str(body["mock"]),
                    corpus_root=Path("_corpus").resolve(),
                )
            except (ValueError, FileNotFoundError) as exc:
                return jsonify({"error": f"invalid mock path: {exc}"}), 400
            source = MockDeviceSource(mock_path)
        else:
            raw_serial = body.get("serial") or ""
            if raw_serial:
                try:
                    raw_serial = validate_serial(str(raw_serial))
                except ValueError as exc:
                    return jsonify({"error": f"invalid serial: {exc}"}), 400
            adb = Adb(serial=raw_serial or None)
            if not adb.available:
                return jsonify({"error": "adb not available; supply a mock path"}), 400
            source = RealDeviceSource(adb)

        # -------------------------------
        # Pipeline configuration
        # -------------------------------

        cfg = PipelineConfig(
            case_id=case_id,
            examiner=examiner,
            legal_authority=authority,
            scope_note=scope,
            cases_root=cases_root,
            tier1_contacts=bool(body.get("tier1_contacts", False)),
            tier1_calllog=bool(body.get("tier1_calllog", False)),
            tier1_sms=bool(body.get("tier1_sms", False)),
            tier1_collect_all=bool(body.get("tier1_collect_all", False)),
            tier2_telegram=bool(body.get("tier2_telegram", False)),
            tier2_instagram=bool(body.get("tier2_instagram", False)),
            tier2_snapchat=bool(body.get("tier2_snapchat", False)),
            tier2_wifi=bool(body.get("tier2_wifi", False)),
            tier2_whatsapp_backup=bool(body.get("tier2_whatsapp_backup", False)),
            tier2_whatsapp_backup_max_files=int(
                body.get("tier2_whatsapp_backup_max_files", 5)
            ),
            # Deep artifact stages. The Tier-0 ones default ON because they are
            # read-only and cost nothing; every Tier-2 stage defaults OFF because it
            # requires root and is opt-in by policy.
            wifi_live=bool(body.get("wifi_live", True)),
            scan_encrypted_apps=bool(body.get("scan_encrypted_apps", True)),
            run_self_validation=bool(body.get("run_self_validation", True)),
            tier2_bt_config=bool(body.get("tier2_bt_config", False)),
            tier2_app_presence=bool(body.get("tier2_app_presence", False)),
            tier2_antiforensics=bool(body.get("tier2_antiforensics", False)),
            tier2_recent_tasks=bool(body.get("tier2_recent_tasks", False)),
            tier2_browser_history=bool(body.get("tier2_browser_history", False)),
            tier2_maps_location=bool(body.get("tier2_maps_location", False)),
            case_description=str(body.get("case_description", "") or ""),
            run_ai_analysis=bool(body.get("run_ai_analysis", True)),
            llm_provider=str(body.get("llm_provider", "") or ""),
            case_number=str(body.get("case_number", "") or ""),
            use_case_bank=bool(body.get("use_case_bank", True)),
            plan_allow_tier2=bool(body.get("plan_allow_tier2", True)),
            use_local_corpus=bool(body.get("use_local_corpus", True)),
            learn_from_case=bool(body.get("learn_from_case", True)),
            # Opt-in completion notification (off unless the caller supplies types).
            notify_on_complete=bool(body.get("notify_on_complete", False)),
            notify_types=list(body.get("notify_types") or []),
            notify_recipients=list(body.get("notify_recipients") or []),
            notify_webhook_url=webhook,
        )

        # ------ Cancellation token ------
        cancel_token = CancellationToken()
        state["cancel_token"] = cancel_token

        # ------ Socket progress emitter ------
        def emit(stage: str, pct: float, detail: str):
            if socketio:
                socketio.emit(
                    "progress",
                    {"stage": stage, "pct": pct, "detail": detail, "case_id": case_id},
                )

        # ------ Background worker ------
        def worker():
            state["running"] = True
            try:
                summary = run_acquisition(
                    source, cfg, progress=emit, socketio=socketio,
                    cancel_token=cancel_token,
                )
                state["last_case"] = case_id
                # Emit complete IMMEDIATELY so the examiner can review results
                if socketio:
                    socketio.emit(
                        "complete",
                        {"case_id": case_id, "counts": summary.get("counts", {})},
                    )
            except AcquisitionCancelled:
                state["last_case"] = case_id
                if socketio:
                    socketio.emit("cancelled", {"case_id": case_id, "partial": True})
            except Exception as exc:
                if socketio:
                    socketio.emit("failed", {"case_id": case_id, "error": str(exc)})
            finally:
                state["running"] = False
                state["cancel_token"] = None
                # Background report generation — does NOT block the complete event
                _launch_bg_report(case_id)

        def _launch_bg_report(cid: str) -> None:
            """Generate the HTML report in a daemon thread after acquisition."""
            case_path = cases_root / cid
            if not case_path.exists():
                return
            state["report_generating"] = True

            def _bg():
                try:
                    from .report import generate_report
                    generate_report(case_path)
                    _finalize_report(Case.open(case_path), trigger="acquisition")
                    state["report_ready_cases"].add(cid)
                    if socketio:
                        socketio.emit(
                            "report_ready",
                            {"case_id": cid, "report_url": f"/api/case/{cid}/report"},
                        )
                except Exception:
                    pass
                finally:
                    state["report_generating"] = False

            threading.Thread(target=_bg, daemon=True).start()

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"case_id": case_id, "started": True})

    @app.post("/api/acquire/cancel")
    def acquire_cancel():
        """Request cancellation of the running acquisition.

        The cancellation is cooperative: the pipeline will finish its current
        I/O operation and check the token between stages.  The case folder is
        left in a consistent, auditable partial state.
        """
        token: CancellationToken | None = state.get("cancel_token")
        if token is None or not state.get("running"):
            return jsonify({"error": "no acquisition is currently running"}), 409
        token.cancel()
        return jsonify({"cancelling": True, "case_id": state.get("last_case")})

    @app.get("/api/case/<case_id>/report_status")
    def report_status(case_id: str):
        """Return whether the HTML report for *case_id* is ready.

        The report is generated in a background thread after acquisition
        completes so the examiner can view results immediately.  Poll this
        endpoint or wait for the ``report_ready`` Socket.IO event.
        """
        try:
            validate_case_id(case_id)
        except ValueError:
            return jsonify({"error": "invalid case_id"}), 400
        ready = case_id in state["report_ready_cases"]
        generating = state["report_generating"] and state.get("last_case") == case_id
        report_path = cases_root / case_id / "report.html"
        return jsonify({
            "case_id": case_id,
            "ready": ready or report_path.exists(),
            "generating": generating,
        })

    # CASE DATA
    # ---------------------------------------------------------

    @app.get("/api/cases/<case_id>/activity")
    def case_activity(case_id: str):
        """Return acquisition activity events from the audit log for a given case.

        Filters the hash-chained audit trail to entries whose ``action`` field
        starts with ``acq.`` and returns them as a JSON array, newest last.
        """
        case_dir = cases_root / case_id
        audit_path = case_dir / "audit.jsonl"
        if not audit_path.exists():
            return jsonify({"error": "case not found"}), 404
        events = []
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if str(row.get("action", "")).startswith("acq."):
                    events.append(row)
            except Exception:
                continue
        return jsonify({"events": events})

    @app.get("/api/cases")

    def list_cases():

        out = []

        for d in sorted(cases_root.iterdir()) if cases_root.exists() else []:

            if (d / "case.json").exists():

                meta = json.loads((d / "case.json").read_text())

                out.append(
                    {
                        "case_id": meta["case_id"],
                        "examiner": meta["examiner"],
                        "created_at": meta.get("created_at"),
                        "device": meta.get("device", {}).get("model", ""),
                    }
                )

        return jsonify(out)

    # ---------------------------------------------------------
    # CASE REGISTRY  (SQLite-backed history of every case + every report generated)
    # ---------------------------------------------------------

    @app.get("/api/registry/cases")
    def registry_cases():
        """The full case history, DB-backed: searchable, sortable, with report counts.

        This is the "just like a database" surface — ``/api/cases`` above stays as the
        light dropdown source for starting a new acquisition; this one powers the Case
        History view.
        """
        q = str(request.args.get("q", "")).strip()
        sort = str(request.args.get("sort", "-updated_at")).strip()
        limit = int(request.args.get("limit", 500))
        # Index anything that appeared since startup. The engine is not the only thing
        # that creates case folders — `python -m triage.cli acquire` writes one too, and
        # so does copying a case in from another workstation. Syncing only at boot meant
        # those cases were missing from Case History with no indication they existed,
        # which for a case index is the one failure that matters. The call only touches
        # folders with no row, so the steady-state cost is a single SELECT.
        registry.sync_registry(cases_root)
        return jsonify(
            {
                "cases": registry.list_cases(cases_root, q=q, sort=sort, limit=limit),
                "stats": registry.registry_stats(cases_root),
            }
        )

    @app.get("/api/registry/stats")
    def registry_stats_endpoint():
        registry.sync_registry(cases_root)
        return jsonify(registry.registry_stats(cases_root))

    @app.get("/api/case/<case_id>/reports")
    def case_report_history(case_id: str):
        """Every report ever generated for this case, most recent first."""
        _open(cases_root, case_id)  # 404s if the case doesn't exist
        return jsonify(registry.list_reports(cases_root, case_id))

    @app.get("/api/case/<case_id>/reports/<path:report_file>")
    def case_report_snapshot(case_id: str, report_file: str):
        """Serve one historical report snapshot from ``<case>/reports/``.

        Cases indexed before the ``reports/`` history existed (see
        ``registry.sync_registry``'s backfill) have a single entry pointing at the
        case-root ``report.html`` instead of a snapshot file — fall back to that.
        """
        case = _open(cases_root, case_id)
        name = Path(report_file).name  # strip any directory component — no traversal
        path = (case.root / "reports" / name).resolve()
        if case.root.resolve() not in path.parents or not path.exists():
            if name == "report.html" and (case.root / "report.html").exists():
                return send_file((case.root / "report.html").resolve(), mimetype="text/html")
            abort(404)
        return send_file(path, mimetype="text/html")

    @app.delete("/api/case/<case_id>")
    def delete_case(case_id: str):
        """Delete a case folder and its registry rows. Irreversible — the dashboard
        confirms with the examiner before calling this."""
        case = _open(cases_root, case_id)
        shutil.rmtree(case.root)
        registry.delete_case_row(cases_root, case_id)
        return jsonify({"deleted": case_id})

    @app.get("/api/case/<case_id>")
    def case_overview(case_id: str):

        case = _open(cases_root, case_id)

        summary = case.custody_summary()

        summary["counts"] = {
            name: len(case.read_derived(name))
            for name in (
                "messages",
                "contacts",
                "calls",
                "media",
                "locations",
                "recovered",
                "flags",
                "timeline",
                "browser",
                "screenshots",
                "media_inventory",
                "apps",
                "accounts",
                "calendar",
                "usage",
                "instagram",
                "snapchat",
                "wifi",
                "whatsapp_backup_messages",
                "whatsapp_backup_media",
                "location_traces",
                "shared_locations",
                "url_locations",
            )
        }

        discovered = case.read_derived("discovered_chats") or {}

        summary["discovered_chat_count"] = len(discovered.get("messages", []))

        summary["risk"] = case.read_derived("risk")

        summary["throughput"] = case.read_derived("throughput")

        graph = case.read_derived("graph")

        summary["graph_stats"] = (
            graph.get("stats", {}) if isinstance(graph, dict) else {}
        )

        summary["media_inventory_summary"] = (
            case.read_derived("media_inventory_summary") or {}
        )

        # Location roll-up. The overview shows the split, not just a total: "42 locations" is
        # misleading when 39 of them are map links the user browsed and only 3 place the device.
        summary["location_trace_summary"] = (
            case.read_derived("location_trace_summary") or {}
        )

        summary["location_anomaly_count"] = len(
            case.read_derived("location_impossible_travel") or []
        )

        apps = case.read_derived("apps") or []

        summary["notable_apps"] = [
            a for a in apps if isinstance(a, dict) and a.get("notable")
        ]

        summary["tag_count"] = len(case.read_tags())

        ai = case.read_derived("ai_findings")

        summary["case_profile"] = case.read_derived("case_profile") or {}

        summary["ai_findings_summary"] = (
            ai.get("counts", {}) if isinstance(ai, dict) else {}
        )

        return jsonify(summary)

    @app.get("/api/case/<case_id>/capabilities")
    def case_capabilities_route(case_id: str):
        """Per-dataset state: populated / empty / not_collected / inaccessible / planned.

        Registered before the generic ``/<dataset>`` route so "capabilities" resolves
        here rather than being looked up as a derived file.
        """
        from .capabilities import case_capabilities

        case = _open(cases_root, case_id)
        config = getattr(case.meta, "acquisition_config", None) or {}
        return jsonify(case_capabilities(case.root, config))

    @app.get("/api/llm/status")
    def llm_status():
        """Which case-intelligence back-ends this workstation can actually use.

        Answered live from the local Ollama daemon and the engine environment, so the
        dashboard's provider picker offers what exists rather than what is theoretically
        supported. ``refresh=1`` also re-probes the embedding model.
        """
        from .intel.llm import provider_status

        status = provider_status()
        embedder = _embedder(refresh=request.args.get("refresh") in ("1", "true"))
        status["embedding"] = (
            embedder.status() if embedder else {"available": False, "mode": "disabled"}
        )
        return jsonify(status)

    @app.get("/api/capabilities")
    def capabilities_catalogue():
        """The catalogue with no case attached — what this build can and cannot do."""
        from .capabilities import CATALOGUE

        return jsonify(
            {
                "items": [
                    {
                        "dataset": cap.dataset,
                        "label": cap.label,
                        "tier": cap.tier,
                        "requires": cap.requires,
                        "flag": cap.flag,
                        "planned": cap.planned,
                        "planned_note": cap.planned_note,
                    }
                    for cap in CATALOGUE.values()
                ]
            }
        )

    @app.get("/api/case/<case_id>/<dataset>")
    def case_dataset(case_id: str, dataset: str):

        list_sets = {
            "messages",
            "contacts",
            "calls",
            "media",
            "locations",
            "recovered",
            "flags",
            "timeline",
            "rowid_gaps",
            "browser",
            "screenshots",
            "media_inventory",
            "apps",
            "accounts",
            "calendar",
            "usage",
            "instagram",
            "instagram_users",
            "snapchat",
            "snapchat_users",
            "telegram_recovery",
            "telegram_users",
            "telegram_chats",
            "telegram_media",
            "telegram_conversations",
            "wifi",
            "whatsapp_backup_messages",
            "whatsapp_backup_media",
            # Dumpsys-derived Tier-1 datasets. The pipeline has been writing these
            # since the notification/bluetooth/celltower parsers landed; they were
            # unreachable over the API until now.
            "notifications",
            "bluetooth",
            "celltower",
            # P1-7: parsers that shipped dead (no call site in run_acquisition) and are
            # now wired in — screen/power events, per-app usage, Google accounts, search
            # history and Maps location history.
            "screen_events",
            "screen_app_usage",
            "usage_patterns",
            "google_accounts",
            "search_history",
            "maps_locations",
            "maps_location_anomalies",
            # P1-3: root-tier Bluetooth bond store (bt_config.conf), the OPP transfer
            # log (the only Bluetooth artifact with a real wall-clock time) and the
            # Android 11+ connection-recency ranking.
            "bluetooth_bonds",
            "bluetooth_transfers",
            "bluetooth_connection_order",
            # P3-1/P3-2/P3-3/P3-4: persistent app-presence, anti-forensics, encrypted-app
            # reporting and recent tasks.
            "app_presence",
            "usage_events",
            "packages",
            "android_users",
            "antiforensic_findings",
            "encrypted_apps",
            "fcm_records",
            "recent_tasks",
            "task_snapshots",
            "deletion_evidence",
            # Location forensics (engine/triage/forensics/).
            "media_locations",
            "location_places",
            "location_anomalies",
            "location_timeline",
            # Unified location trace: every source merged and categorised by evidential
            # meaning, plus the sources that feed it.
            "location_traces",
            "location_impossible_travel",
            "shared_locations",
            "url_locations",
            # Helper-APK radio artifacts (BSSIDs and bonded devices are location trails).
            "collector_wifi",
            "collector_bluetooth",
            "whatsapp_media",
            "aleapp",
        }

        obj_sets = {
            "graph",
            "risk",
            "throughput",
            "media_inventory_summary",
            "instagram_conversations",
            "snapchat_conversations",
            "discovered_chats",
            "ai_findings",
            # Deep investigation: bounded hypothesis pass cross-linking findings
            # analyze_derived's single flat scoring pass can't correlate on its own.
            "investigation_trace",
            "case_profile",
            "collection_plan",
            # Re-analysis writes its re-ranking here rather than over the plan that
            # drove the acquisition, so both readings stay available to the dashboard.
            "collection_plan_revised",
            "case_learning",
            "case_outcome",
            "location_summary",
            "advanced",
            "mediastore_trash",
            "whatsapp_backup_summary",
            # P1-4 summaries (defined but never rendered until now).
            "bluetooth_summary",
            "celltower_summary",
            "screen_time_summary",
            "search_summary",
            "maps_location_summary",
            # P1-1 encryption posture, P2-3 pre/post device state, P1-3 bond report,
            # P1-7 Signal, P3-* structured reports.
            "encryption_state",
            "device_state",
            "wifi_live",
            "bluetooth_bond_report",
            "bluetooth_transfer_summary",
            "signal",
            # Honest "what happened" record for Tier-2 Telegram — written on every
            # exit path (success, root unavailable, BFU-gated, mock source) so a run
            # where nothing was recovered never reads as "Telegram was not there".
            "telegram_presence",
            "app_presence_summary",
            "antiforensics_summary",
            "encrypted_apps_summary",
            "recent_tasks_summary",
            "deletion_evidence_summary",
            "validation_report",
            # Unified location trace summaries + a GeoJSON export for mapping tools.
            "location_trace_summary",
            "location_trace_geojson",
            "shared_location_summary",
            "url_location_summary",
            # Per-run helper-APK audit record: what ran, what was denied, and why.
            "collector_manifest",
        }

        if dataset not in (list_sets | obj_sets):

            abort(404)

        case = _open(cases_root, case_id)

        return jsonify(case.read_derived(dataset))

    # ---------------------------------------------------------
    # TELEGRAM
    # ---------------------------------------------------------

    @app.get("/api/case/<case_id>/telegram/conversations")
    def telegram_conversations(case_id: str):

        case = _open(cases_root, case_id)

        return jsonify(case.read_derived("telegram_conversations") or {})

    @app.get("/api/case/<case_id>/telegram/conversations/<chat_id>")
    def telegram_conversation_detail(case_id: str, chat_id: str):

        case = _open(cases_root, case_id)

        data = case.read_derived("telegram_conversations") or {}

        conv = data.get(chat_id)

        if conv is None:

            abort(404)

        return jsonify(conv)

    # ---------------------------------------------------------
    # WHATSAPP BACKUP
    # ---------------------------------------------------------

    @app.get("/api/case/<case_id>/whatsapp_backup/messages")
    def whatsapp_backup_messages(case_id: str):

        case = _open(cases_root, case_id)

        return jsonify(case.read_derived("whatsapp_backup_messages") or [])

    @app.get("/api/case/<case_id>/whatsapp_backup/media")
    def whatsapp_backup_media(case_id: str):

        case = _open(cases_root, case_id)

        return jsonify(case.read_derived("whatsapp_backup_media") or [])

    @app.get("/api/case/<case_id>/whatsapp_backup/summary")
    def whatsapp_backup_summary(case_id: str):

        case = _open(cases_root, case_id)

        return jsonify(case.read_derived("whatsapp_backup_summary") or {})
        # ---------------------------------------------------------

    # DATA EXPORT IMPORT
    # Instagram / Snapchat / Telegram (non-root acquisition path)
    # ---------------------------------------------------------

    @app.post("/api/case/<case_id>/import/<app_name>")
    def import_export(case_id: str, app_name: str):

        if app_name not in ("instagram", "snapchat", "telegram"):

            abort(404)

        case = _open(cases_root, case_id)

        upload = request.files.get("file")

        if upload is None or not upload.filename:

            return jsonify({"error": "no file uploaded"}), 400

        from .parsers import (
            parse_instagram_export,
            parse_snapchat_export,
            parse_telegram_export,
            thread_conversations,
            build_conversations,
        )

        from .report import generate_report

        suffix = Path(upload.filename).suffix or ".zip"

        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix=f"{app_name}_export_")

        os.close(fd)

        tmp = Path(tmp_path)

        upload.save(str(tmp))

        try:

            if app_name == "telegram":

                result = parse_telegram_export(tmp)

                if not result.get("available"):

                    return jsonify({"error": result.get("error", "parse failed")}), 400

                messages = result.get("messages", [])

                merged = list(case.read_derived("telegram_recovery") or []) + messages

                case.write_derived("telegram_recovery", merged)

                users = list(case.read_derived("telegram_users") or []) + result.get(
                    "users", []
                )

                case.write_derived("telegram_users", users)

                chats = list(case.read_derived("telegram_chats") or []) + result.get(
                    "chats", []
                )

                case.write_derived("telegram_chats", chats)

                case.write_derived(
                    "telegram_conversations",
                    build_conversations(messages=merged, users=users, chats=chats),
                )

            else:

                parser = (
                    parse_instagram_export
                    if app_name == "instagram"
                    else parse_snapchat_export
                )

                result = parser(tmp)

                if not result.get("available"):

                    return jsonify({"error": result.get("error", "parse failed")}), 400

                messages = result.get("messages", [])

                existing = case.read_derived(app_name) or []

                merged = list(existing) + messages

                case.write_derived(app_name, merged)

                users = (case.read_derived(f"{app_name}_users") or []) + result.get(
                    "users", []
                )

                case.write_derived(f"{app_name}_users", users)

                case.write_derived(
                    f"{app_name}_conversations", thread_conversations(merged, users)
                )

            try:

                generate_report(case.root)

                _finalize_report(case, trigger=f"import:{app_name}")

            except Exception:

                pass

            return jsonify({"imported": len(messages), "total": len(merged)})

        finally:

            if tmp.exists():

                tmp.unlink()

    # ---------------------------------------------------------
    # TAGS
    # ---------------------------------------------------------

    @app.get("/api/case/<case_id>/tags")
    def get_tags(case_id: str):

        return jsonify(_open(cases_root, case_id).read_tags())

    @app.post("/api/case/<case_id>/tags")
    def add_tag(case_id: str):

        case = _open(cases_root, case_id)

        body = request.get_json(force=True) or {}

        tag = case.add_tag(
            ref=body.get("ref", ""),
            kind=body.get("kind", "artifact"),
            label=body.get("label", "Tagged"),
            note=body.get("note", ""),
            by=body.get("by", ""),
        )

        return jsonify(tag)

    @app.delete("/api/case/<case_id>/tags/<tag_id>")
    def delete_tag(case_id: str, tag_id: str):

        ok = _open(cases_root, case_id).remove_tag(tag_id)

        return jsonify({"removed": ok})

    # ---------------------------------------------------------
    # EVIDENCE EXPORT
    # ---------------------------------------------------------

    @app.post("/api/case/<case_id>/export")
    def export_case_endpoint(case_id: str):

        from .export import export_case

        case = _open(cases_root, case_id)

        out = export_case(case.root)

        return jsonify({"path": str(out), "name": out.name, "size": out.stat().st_size})

    @app.get("/api/case/<case_id>/export/download")
    def export_download(case_id: str):

        from .export import export_case

        case = _open(cases_root, case_id)

        out = export_case(case.root)

        return send_file(out.resolve(), as_attachment=True, download_name=out.name)

    @app.get("/api/case/<case_id>/manifest")
    def manifest(case_id: str):

        case = _open(cases_root, case_id)

        return jsonify([r.to_dict() for r in case.manifest])

    @app.get("/api/case/<case_id>/audit")
    def audit(case_id: str):

        return jsonify(_open(cases_root, case_id).read_audit())

    # ---------------------------------------------------------
    # REPORT ENDPOINT
    # ---------------------------------------------------------

    @app.get("/api/case/<case_id>/report")
    def case_report(case_id: str):

        path = cases_root / _safe(case_id) / "report.html"

        if not path.exists():
            # Return a minimal 404 JSON so the UI can show a helpful message
            # rather than the default Flask HTML error page.
            return jsonify({"error": "report not yet generated", "hint": "POST to /report/regenerate"}), 404

        return send_file(path.resolve(), mimetype="text/html")

    @app.post("/api/case/<case_id>/report/regenerate")
    def regenerate_report(case_id: str):
        """(Re-)generate the HTML triage report for an existing case.

        Called by the Report view's "Generate Report" button.  Safe to call
        multiple times — each call overwrites ``report.html`` in the case dir.
        """
        from .report import generate_report

        case = _open(cases_root, case_id)

        try:
            generate_report(case.root)
            _finalize_report(case, trigger="manual")
            return jsonify({"ok": True, "path": str(case.root / "report.html")})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ---------------------------------------------------------
    # MEDIA
    # ---------------------------------------------------------

    @app.get("/api/case/<case_id>/media/<artifact_id>")
    def media(case_id: str, artifact_id: str):

        case = _open(cases_root, case_id)

        for rec in case.manifest:

            if rec.artifact_id == artifact_id:

                path = (case.root / rec.stored_path).resolve()

                return send_file(path)

        abort(404)

    app.config["SOCKETIO"] = socketio

    return app, socketio


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------


def _open(cases_root: Path, case_id: str):

    path = cases_root / _safe(case_id)

    if not (path / "case.json").exists():

        abort(404)

    return Case.open(path)


def _safe(case_id: str):

    return "".join(c for c in case_id if c.isalnum() or c in "-_ ")


def _auto_case_id(cases_root: Path):

    n = len(list(cases_root.glob("CASE-*"))) + 1

    return f"CASE-{n:04d}"


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------


def main():

    import argparse

    parser = argparse.ArgumentParser(description="SNAGR triage local service")

    parser.add_argument("--host", default="127.0.0.1")

    parser.add_argument("--port", type=int, default=5057)

    parser.add_argument("--cases", default="cases")

    args = parser.parse_args()

    app, socketio = create_app(Path(args.cases))

    print(f"{TOOL_NAME} v{__version__} " f"— http://{args.host}:{args.port}")

    if socketio:

        socketio.run(app, host=args.host, port=args.port, allow_unsafe_werkzeug=True)

    else:

        app.run(host=args.host, port=args.port)


if __name__ == "__main__":

    main()
