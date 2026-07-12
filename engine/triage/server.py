"""Local Flask + SocketIO service the Electron dashboard talks to.

Everything runs on localhost only — this is a field tool, not a networked service. The
acquisition runs in a background thread and streams progress over SocketIO so the UI can
render a live 5–10-minute countdown, while REST endpoints serve the finished case data.
"""
from __future__ import annotations

import json
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
from .config import ACQUISITION_DISCLAIMER
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
            tier1_sms=bool(body.get("tier1_sms", False)))
        

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
                              "browser", "screenshots")}
        # Analysis blocks (objects, not lists).
        summary["risk"] = case.read_derived("risk")
        summary["throughput"] = case.read_derived("throughput")
        summary["graph_stats"] = (case.read_derived("graph") or {}).get("stats", {}) \
            if isinstance(case.read_derived("graph"), dict) else {}
        summary["tag_count"] = len(case.read_tags())
        return jsonify(summary)

    @app.get("/api/case/<case_id>/<dataset>")
    def case_dataset(case_id: str, dataset: str):
        list_sets = {"messages", "contacts", "calls", "media", "locations",
                     "recovered", "flags", "timeline", "rowid_gaps", "browser",
                     "screenshots"}
        obj_sets = {"graph", "risk", "throughput"}
        if dataset not in list_sets | obj_sets:
            abort(404)
        case = _open(cases_root, case_id)
        return jsonify(case.read_derived(dataset))

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
