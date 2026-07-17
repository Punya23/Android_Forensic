"""Local Flask + SocketIO service the Electron dashboard talks to.

Everything runs on localhost only — this is a field tool, not a networked service. The
acquisition runs in a background thread and streams progress over SocketIO so the UI can
render a live 5–10-minute countdown, while REST endpoints serve the finished case data.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, request, send_file, abort
from flask_cors import CORS

try:
    from flask_socketio import SocketIO
    _HAVE_SOCKETIO = True
except Exception:  # pragma: no cover
    _HAVE_SOCKETIO = False

from . import TOOL_NAME, __version__
from .acquire import MockDeviceSource, RealDeviceSource
from .adb import Adb
from .config import ACQUISITION_DISCLAIMER, Tier
from .custody import Case
from .pipeline import PipelineConfig, run_acquisition

CASES_ROOT = Path("cases")


def create_app(cases_root: Path = CASES_ROOT):
    app = Flask(__name__)
    CORS(app)
    cases_root.mkdir(parents=True, exist_ok=True)
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading") \
        if _HAVE_SOCKETIO else None

    state: dict[str, Any] = {"running": False, "last_case": None}

    # -- meta ---------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return jsonify({"tool": TOOL_NAME, "version": __version__,
                        "disclaimer": ACQUISITION_DISCLAIMER,
                        "adb": Adb().available, "running": state["running"]})

    @app.get("/api/devices")
    def devices():
        """List connected ADB devices plus any mock corpora under _corpus/."""
        real = Adb.list_devices()
        mocks = []
        corpus_root = Path("_corpus")
        if corpus_root.exists():
            for d in sorted(corpus_root.iterdir()):
                if (d / "_device.json").exists():
                    meta = json.loads((d / "_device.json").read_text())
                    mocks.append({"id": str(d), "kind": "mock",
                                  "label": meta.get("device", {}).get("model", d.name)})
        return jsonify({"real": real, "mock": mocks})

    # -- case intelligence --------------------------------------------------
    @app.post("/api/plan")
    def plan_preview():
        """Preview a targeted collection plan from a plain-language case brief.

        Pure preview — no device, no case folder, no side effects. The officer reviews
        (and can override) the profile + plan before actually acquiring.
        """
        body = request.get_json(force=True) or {}
        description = str(body.get("description", "") or "").strip()
        if not description:
            return jsonify({"error": "a case description is required"}), 400
        from .intel import plan_case, get_provider
        provider = get_provider(str(body.get("llm_provider", "") or "") or None)
        profile, plan = plan_case(
            description, provider=provider,
            allow_tier2=bool(body.get("allow_tier2", True)))
        return jsonify({"profile": profile.to_dict(), "plan": plan.to_dict(),
                        "provider": provider.name})

    @app.post("/api/case/<case_id>/analyze")
    def analyze_case_endpoint(case_id: str):
        """(Re-)run the AI findings analysis over an existing case's collected artifacts.

        Uses the case's stored profile, or a fresh description supplied in the body. Useful
        after importing more data, or to switch LLM providers without re-acquiring.
        """
        case = _open(cases_root, case_id)
        body = request.get_json(silent=True) or {}
        from .intel import analyze_case, get_provider
        from .intel.planner import CaseProfile, build_plan, extract_profile

        provider = get_provider(str(body.get("llm_provider", "") or "") or None)
        description = str(body.get("description", "") or "").strip()
        if description:
            profile = extract_profile(description, provider=provider)
            case.write_derived("case_profile", profile.to_dict())
            case.write_derived("collection_plan", build_plan(profile).to_dict())
        else:
            stored = case.read_derived("case_profile")
            if not stored or not isinstance(stored, dict):
                return jsonify({"error": "no case profile on file; supply a description"}), 400
            profile = CaseProfile(**stored)
        bundle = analyze_case(case, profile, provider=provider)
        case.log("intel.findings",
                 f"AI leads re-run: {bundle.get('counts', {}).get('total', 0)} "
                 f"({bundle.get('analysis_method')})", tier=Tier.TIER0.value)
        return jsonify(bundle)

    # -- acquisition --------------------------------------------------------
    @app.post("/api/acquire")
    def acquire():
        if state["running"]:
            return jsonify({"error": "an acquisition is already running"}), 409
        body = request.get_json(force=True) or {}
        case_id = body.get("case_id") or _auto_case_id(cases_root)
        examiner = body.get("examiner", "Unknown Examiner")

        if body.get("mock"):
            source = MockDeviceSource(Path(body["mock"]))
        else:
            adb = Adb(serial=body.get("serial"))
            if not adb.available:
                return jsonify({"error": "adb not available; supply a mock path"}), 400
            source = RealDeviceSource(adb)

        cfg = PipelineConfig(
            case_id=case_id, examiner=examiner,
            legal_authority=body.get("authority", ""),
            scope_note=body.get("scope", ""), cases_root=cases_root,
            tier1_contacts=bool(body.get("tier1_contacts", False)),
            tier1_calllog=bool(body.get("tier1_calllog", False)),
            tier1_sms=bool(body.get("tier1_sms", False)),
            tier1_collect_all=bool(body.get("tier1_collect_all", False)),
            tier2_telegram=bool(body.get("tier2_telegram", False)),
            tier2_instagram=bool(body.get("tier2_instagram", False)),
            tier2_snapchat=bool(body.get("tier2_snapchat", False)),
            tier2_wifi=bool(body.get("tier2_wifi", False)),
            case_description=str(body.get("case_description", "") or ""),
            run_ai_analysis=bool(body.get("run_ai_analysis", True)),
            llm_provider=str(body.get("llm_provider", "") or ""))


        def emit(stage: str, pct: float, detail: str) -> None:
            if socketio:
                socketio.emit("progress", {"stage": stage, "pct": pct, "detail": detail,
                                           "case_id": case_id})

        def worker():
            state["running"] = True
            try:
                summary = run_acquisition(source, cfg, progress=emit)
                state["last_case"] = case_id
                if socketio:
                    socketio.emit("complete", {"case_id": case_id,
                                               "counts": summary["counts"]})
            except Exception as exc:  # surface failure to the UI, don't hang
                if socketio:
                    socketio.emit("failed", {"case_id": case_id, "error": str(exc)})
            finally:
                state["running"] = False

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"case_id": case_id, "started": True})

    # -- case data ----------------------------------------------------------
    @app.get("/api/cases")
    def list_cases():
        out = []
        for d in sorted(cases_root.iterdir()) if cases_root.exists() else []:
            if (d / "case.json").exists():
                meta = json.loads((d / "case.json").read_text())
                out.append({"case_id": meta["case_id"], "examiner": meta["examiner"],
                            "created_at": meta.get("created_at"),
                            "device": meta.get("device", {}).get("model", "")})
        return jsonify(out)

    @app.get("/api/case/<case_id>")
    def case_overview(case_id: str):
        case = _open(cases_root, case_id)
        summary = case.custody_summary()
        summary["counts"] = {name: len(case.read_derived(name)) for name in
                             ("messages", "contacts", "calls", "media",
                              "locations", "recovered", "flags", "timeline",
                              "browser", "screenshots",
                              # expanded Tier-1 collection datasets
                              "media_inventory", "apps", "accounts", "calendar", "usage",
                              # app-chat recovery datasets
                              "instagram", "snapchat",
                              # Tier-2 datasets
                              "wifi")}
        summary["discovered_chat_count"] = len(
            (case.read_derived("discovered_chats") or {}).get("messages", []))
        # Analysis blocks (objects, not lists).
        summary["risk"] = case.read_derived("risk")
        summary["throughput"] = case.read_derived("throughput")
        summary["graph_stats"] = (case.read_derived("graph") or {}).get("stats", {}) \
            if isinstance(case.read_derived("graph"), dict) else {}
        summary["media_inventory_summary"] = case.read_derived("media_inventory_summary") or {}
        # A quick "apps of interest" roll-up for the Overview.
        apps = case.read_derived("apps") or []
        summary["notable_apps"] = [a for a in apps if isinstance(a, dict) and a.get("notable")]
        summary["tag_count"] = len(case.read_tags())
        # Case-intelligence roll-up for the Overview.
        ai = case.read_derived("ai_findings")
        summary["case_profile"] = case.read_derived("case_profile") or {}
        summary["ai_findings_summary"] = (ai or {}).get("counts", {}) if isinstance(ai, dict) else {}
        return jsonify(summary)

    @app.get("/api/case/<case_id>/<dataset>")
    def case_dataset(case_id: str, dataset: str):
        list_sets = {"messages", "contacts", "calls", "media", "locations",
                     "recovered", "flags", "timeline", "rowid_gaps", "browser",
                     "screenshots",
                     # expanded Tier-1 collection datasets
                     "media_inventory", "apps", "accounts", "calendar", "usage",
                     # Instagram / Snapchat / generic app-finder datasets
                     "instagram", "instagram_users", "snapchat", "snapchat_users",
                     # Telegram deep-recovery datasets
                     "telegram_recovery", "telegram_users", "telegram_chats",
                     "telegram_media", "telegram_conversations",
                     # Tier-2 Wi-Fi credentials
                     "wifi"}
        obj_sets = {"graph", "risk", "throughput", "media_inventory_summary",
                    "instagram_conversations", "snapchat_conversations", "discovered_chats",
                    # case-intelligence datasets
                    "ai_findings", "case_profile", "collection_plan"}
        if dataset not in list_sets | obj_sets:
            abort(404)
        case = _open(cases_root, case_id)
        return jsonify(case.read_derived(dataset))

    # -- Telegram conversation endpoints ------------------------------------
    @app.get("/api/case/<case_id>/telegram/conversations")
    def telegram_conversations(case_id: str):
        """Serve the full telegram_conversations.json (dict keyed by chat_id)."""
        case = _open(cases_root, case_id)
        data = case.read_derived("telegram_conversations") or {}
        return jsonify(data)

    @app.get("/api/case/<case_id>/telegram/conversations/<chat_id>")
    def telegram_conversation_detail(case_id: str, chat_id: str):
        """Serve a single conversation thread by chat_id."""
        case = _open(cases_root, case_id)
        all_convs = case.read_derived("telegram_conversations") or {}
        conv = all_convs.get(chat_id)
        if conv is None:
            abort(404)
        return jsonify(conv)

    # -- data-export ingest (Instagram / Snapchat "Download Your Data") -------
    @app.post("/api/case/<case_id>/import/<app_name>")
    def import_export(case_id: str, app_name: str):
        """Ingest an Instagram/Snapchat data-export (ZIP/JSON) into the case — a non-root path.

        The uploaded file is hashed and recorded as a Tier-1 (consent/cloud) evidence artifact,
        its messages are parsed and merged into the app's derived dataset, conversations are
        rebuilt, and the report is regenerated. Every step is written to the audit trail.
        """
        if app_name not in ("instagram", "snapchat"):
            abort(404)
        case = _open(cases_root, case_id)
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"error": "no file uploaded (multipart form field 'file')"}), 400

        from .parsers import (parse_instagram_export, parse_snapchat_export,
                              thread_conversations)
        from .report import generate_report

        suffix = Path(upload.filename).suffix or ".zip"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix=f"{app_name}_export_")
        os.close(fd)
        tmp = Path(tmp_path)
        upload.save(str(tmp))
        try:
            parser = parse_instagram_export if app_name == "instagram" else parse_snapchat_export
            result = parser(tmp)
            if not result.get("available"):
                return jsonify({"error": result.get("error", "export parse failed")}), 400
            new_msgs = result.get("messages", [])
            if not new_msgs:
                return jsonify({"error": "no messages found in export "
                                        "(is this the right data-export file?)"}), 400

            # Record the export file itself as a hashed Tier-1 artifact (chain of custody).
            rec = case.ingest_file(tmp, source_path=f"data-export/{upload.filename}",
                                   tier=Tier.TIER1, method="data-export",
                                   category="app-export", app=app_name,
                                   flags=["data-export"], move=True)
            # Merge into the app's derived dataset and rebuild conversations.
            existing = case.read_derived(app_name) or []
            merged = list(existing) + new_msgs
            case.write_derived(app_name, merged)
            users = (case.read_derived(f"{app_name}_users") or []) + result.get("users", [])
            case.write_derived(f"{app_name}_conversations", thread_conversations(merged, users))
            case.log(f"import.{app_name}",
                     f"imported {len(new_msgs)} {app_name} message(s) from data export "
                     f"'{upload.filename}'",
                     tier=Tier.TIER1.value, alters_device=False, artifact_id=rec.artifact_id)
            try:
                generate_report(case.root)
            except Exception:
                pass
            return jsonify({"imported": len(new_msgs), "total": len(merged),
                            "counts": result.get("counts"), "artifact_id": rec.artifact_id})
        finally:
            if tmp.exists():
                tmp.unlink()

    # -- tags / bookmarks ----------------------------------------------------
    @app.get("/api/case/<case_id>/tags")
    def get_tags(case_id: str):
        return jsonify(_open(cases_root, case_id).read_tags())

    @app.post("/api/case/<case_id>/tags")
    def add_tag(case_id: str):
        case = _open(cases_root, case_id)
        body = request.get_json(force=True) or {}
        tag = case.add_tag(ref=body.get("ref", ""), kind=body.get("kind", "artifact"),
                           label=body.get("label", "Tagged"), note=body.get("note", ""),
                           by=body.get("by", ""))
        return jsonify(tag)

    @app.delete("/api/case/<case_id>/tags/<tag_id>")
    def del_tag(case_id: str, tag_id: str):
        ok = _open(cases_root, case_id).remove_tag(tag_id)
        return jsonify({"removed": ok})

    # -- evidence export -----------------------------------------------------
    @app.post("/api/case/<case_id>/export")
    def export(case_id: str):
        from .export import export_case
        case = _open(cases_root, case_id)
        out = export_case(case.root)
        case.log("evidence.export", f"sealed evidence package written to {out.name}")
        return jsonify({"path": str(out), "name": out.name,
                        "size": out.stat().st_size})

    @app.get("/api/case/<case_id>/export/download")
    def export_download(case_id: str):
        from .export import export_case
        case = _open(cases_root, case_id)
        out = export_case(case.root)
        return send_file(out.resolve(), as_attachment=True, download_name=out.name)

    @app.get("/api/case/<case_id>/manifest")
    def case_manifest(case_id: str):
        case = _open(cases_root, case_id)
        return jsonify([r.to_dict() for r in case.manifest])

    @app.get("/api/case/<case_id>/audit")
    def case_audit(case_id: str):
        case = _open(cases_root, case_id)
        return jsonify(case.read_audit())

    @app.get("/api/case/<case_id>/report")
    def case_report(case_id: str):
        path = cases_root / case_id / "report.html"
        if not path.exists():
            abort(404)
        return send_file(path.resolve())

    @app.get("/api/case/<case_id>/media/<artifact_id>")
    def case_media(case_id: str, artifact_id: str):
        """Serve a pulled media file by artifact id (for the gallery thumbnails)."""
        case = _open(cases_root, case_id)
        for rec in case.manifest:
            if rec.artifact_id == artifact_id:
                path = (case.root / rec.stored_path).resolve()
                # Guard against path escape.
                if case.root.resolve() in path.parents:
                    return send_file(path)
        abort(404)

    app.config["SOCKETIO"] = socketio
    return app, socketio


def _open(cases_root: Path, case_id: str) -> Case:
    path = cases_root / _safe(case_id)
    if not (path / "case.json").exists():
        abort(404)
    return Case.open(path)


def _safe(case_id: str) -> str:
    return "".join(c for c in case_id if c.isalnum() or c in "-_")


def _auto_case_id(cases_root: Path) -> str:
    n = len(list(cases_root.glob("CASE-*"))) + 1
    return f"CASE-{n:04d}"


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="eRakshak triage local service")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5057)
    p.add_argument("--cases", default="cases")
    args = p.parse_args()
    app, socketio = create_app(Path(args.cases))
    print(f"{TOOL_NAME} v{__version__} — http://{args.host}:{args.port}")
    if socketio:
        socketio.run(app, host=args.host, port=args.port, allow_unsafe_werkzeug=True)
    else:  # pragma: no cover
        app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
