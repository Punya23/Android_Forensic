"""
Local Flask + SocketIO service the Electron dashboard talks to.

Everything runs on localhost only — this is a field tool, not a networked service.
The acquisition runs in a background thread and streams progress over SocketIO so the UI
can render a live countdown, while REST endpoints serve finished case data.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_file, abort
from flask_cors import CORS

try:
    from flask_socketio import SocketIO
    _HAVE_SOCKETIO = True
except Exception:
    _HAVE_SOCKETIO = False


from . import TOOL_NAME, __version__
from .acquire import MockDeviceSource, RealDeviceSource
from .adb import Adb
from .config import ACQUISITION_DISCLAIMER, Tier
from .custody import Case
from .pipeline import PipelineConfig, run_acquisition


# Keep this during testing
CASES_ROOT = Path("_test_output")


def create_app(cases_root: Path = CASES_ROOT):

    app = Flask(__name__)

    CORS(app)

    cases_root.mkdir(
        parents=True,
        exist_ok=True
    )


    socketio = (
        SocketIO(
            app,
            cors_allowed_origins="*",
            async_mode="threading"
        )
        if _HAVE_SOCKETIO
        else None
    )


    state: dict[str, Any] = {
        "running": False,
        "last_case": None
    }


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
                "running": state["running"]
            }
        )



    @app.get("/api/devices")
    def devices():

        real = Adb.list_devices()

        mocks = []

        corpus_root = Path("_corpus")

        if corpus_root.exists():

            for d in sorted(corpus_root.iterdir()):

                if (d / "_device.json").exists():

                    meta = json.loads(
                        (d / "_device.json").read_text()
                    )

                    mocks.append(
                        {
                            "id": str(d),
                            "kind": "mock",
                            "label": meta.get(
                                "device",
                                {}
                            ).get(
                                "model",
                                d.name
                            )
                        }
                    )


        return jsonify(
            {
                "real": real,
                "mock": mocks
            }
        )



    # ---------------------------------------------------------
    # CASE INTELLIGENCE
    # ---------------------------------------------------------

    @app.post("/api/plan")
    def plan_preview():

        body = request.get_json(force=True) or {}

        description = str(
            body.get(
                "description",
                ""
            )
        ).strip()


        if not description:

            return jsonify(
                {
                    "error":
                    "a case description is required"
                }
            ), 400


        from .intel import plan_case, get_provider


        provider = get_provider(
            str(
                body.get(
                    "llm_provider",
                    ""
                )
            )
            or None
        )


        profile, plan = plan_case(
            description,
            provider=provider,
            allow_tier2=bool(
                body.get(
                    "allow_tier2",
                    True
                )
            )
        )


        return jsonify(
            {
                "profile": profile.to_dict(),
                "plan": plan.to_dict(),
                "provider": provider.name
            }
        )



    @app.post("/api/case/<case_id>/analyze")
    def analyze_case_endpoint(case_id: str):

        case = _open(
            cases_root,
            case_id
        )


        body = request.get_json(
            silent=True
        ) or {}


        from .intel import analyze_case, get_provider
        from .intel.planner import (
            CaseProfile,
            build_plan,
            extract_profile
        )


        provider = get_provider(
            str(
                body.get(
                    "llm_provider",
                    ""
                )
            )
            or None
        )


        description = str(
            body.get(
                "description",
                ""
            )
        ).strip()


        if description:

            profile = extract_profile(
                description,
                provider=provider
            )


            case.write_derived(
                "case_profile",
                profile.to_dict()
            )


            case.write_derived(
                "collection_plan",
                build_plan(profile).to_dict()
            )

        else:

            stored = case.read_derived(
                "case_profile"
            )


            if not stored:

                return jsonify(
                    {
                        "error":
                        "no case profile available"
                    }
                ),400


            profile = CaseProfile(
                **stored
            )


        bundle = analyze_case(
            case,
            profile,
            provider=provider
        )


        return jsonify(bundle)
            # ---------------------------------------------------------
    # ACQUISITION
    # ---------------------------------------------------------

    @app.post("/api/acquire")
    def acquire():

        if state["running"]:

            return jsonify(
                {
                    "error":
                    "an acquisition is already running"
                }
            ), 409


        body = request.get_json(
            force=True
        ) or {}


        case_id = body.get(
            "case_id"
        ) or _auto_case_id(
            cases_root
        )


        examiner = body.get(
            "examiner",
            "Unknown Examiner"
        )



        # -------------------------------
        # Device source selection
        # -------------------------------

        if body.get("mock"):

            source = MockDeviceSource(
                Path(
                    body["mock"]
                )
            )

        else:

            adb = Adb(
                serial=body.get(
                    "serial"
                )
            )


            if not adb.available:

                return jsonify(
                    {
                        "error":
                        "adb not available; supply a mock path"
                    }
                ),400


            source = RealDeviceSource(
                adb
            )



        # -------------------------------
        # Pipeline configuration
        # -------------------------------

        cfg = PipelineConfig(

            case_id=case_id,

            examiner=examiner,

            legal_authority=body.get(
                "authority",
                ""
            ),

            scope_note=body.get(
                "scope",
                ""
            ),

            cases_root=cases_root,


            tier1_contacts=bool(
                body.get(
                    "tier1_contacts",
                    False
                )
            ),

            tier1_calllog=bool(
                body.get(
                    "tier1_calllog",
                    False
                )
            ),

            tier1_sms=bool(
                body.get(
                    "tier1_sms",
                    False
                )
            ),

            tier1_collect_all=bool(
                body.get(
                    "tier1_collect_all",
                    False
                )
            ),


            tier2_telegram=bool(
                body.get(
                    "tier2_telegram",
                    False
                )
            ),

            tier2_instagram=bool(
                body.get(
                    "tier2_instagram",
                    False
                )
            ),

            tier2_snapchat=bool(
                body.get(
                    "tier2_snapchat",
                    False
                )
            ),

            tier2_wifi=bool(
                body.get(
                    "tier2_wifi",
                    False
                )
            ),


            tier2_whatsapp_backup=bool(
                body.get(
                    "tier2_whatsapp_backup",
                    False
                )
            ),


            tier2_whatsapp_backup_max_files=int(
                body.get(
                    "tier2_whatsapp_backup_max_files",
                    5
                )
            ),


            case_description=str(
                body.get(
                    "case_description",
                    ""
                )
                or ""
            ),


            run_ai_analysis=bool(
                body.get(
                    "run_ai_analysis",
                    True
                )
            ),


            llm_provider=str(
                body.get(
                    "llm_provider",
                    ""
                )
                or ""
            )

        )



        # -------------------------------
        # Socket progress emitter
        # -------------------------------

        def emit(
            stage: str,
            pct: float,
            detail: str
        ):

            if socketio:

                socketio.emit(
                    "progress",
                    {
                        "stage": stage,
                        "pct": pct,
                        "detail": detail,
                        "case_id": case_id
                    }
                )



        # -------------------------------
        # Background worker
        # -------------------------------

        def worker():

            state["running"] = True


            try:

                summary = run_acquisition(
                    source,
                    cfg,
                    progress=emit
                )



                # FIX:
                # Generate report after acquisition finishes

                from .report import generate_report


                case_path = (
                    cases_root /
                    case_id
                )


                if case_path.exists():

                    generate_report(
                        case_path
                    )



                state["last_case"] = case_id



                if socketio:

                    socketio.emit(
                        "complete",
                        {
                            "case_id": case_id,
                            "counts": summary.get(
                                "counts",
                                {}
                            )
                        }
                    )



            except Exception as exc:


                if socketio:

                    socketio.emit(
                        "failed",
                        {
                            "case_id": case_id,
                            "error": str(exc)
                        }
                    )



            finally:

                state["running"] = False




        # IMPORTANT:
        # thread start must be OUTSIDE worker()

        threading.Thread(
            target=worker,
            daemon=True
        ).start()



        return jsonify(
            {
                "case_id": case_id,
                "started": True
            }
        )
            # ---------------------------------------------------------
    # CASE DATA
    # ---------------------------------------------------------

    @app.get("/api/cases")
    def list_cases():

        out = []


        for d in sorted(cases_root.iterdir()) if cases_root.exists() else []:

            if (d / "case.json").exists():

                meta = json.loads(
                    (d / "case.json").read_text()
                )


                out.append(
                    {
                        "case_id": meta["case_id"],
                        "examiner": meta["examiner"],
                        "created_at": meta.get("created_at"),
                        "device": meta.get("device", {}).get(
                            "model",
                            ""
                        )
                    }
                )


        return jsonify(out)



    @app.get("/api/case/<case_id>")
    def case_overview(case_id: str):

        case = _open(
            cases_root,
            case_id
        )


        summary = case.custody_summary()


        summary["counts"] = {

            name: len(
                case.read_derived(name)
            )

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
                "whatsapp_backup_media"
            )
        }



        discovered = (
            case.read_derived(
                "discovered_chats"
            )
            or {}
        )


        summary["discovered_chat_count"] = len(
            discovered.get(
                "messages",
                []
            )
        )



        summary["risk"] = case.read_derived(
            "risk"
        )


        summary["throughput"] = case.read_derived(
            "throughput"
        )



        graph = case.read_derived(
            "graph"
        )


        summary["graph_stats"] = (

            graph.get(
                "stats",
                {}
            )

            if isinstance(graph, dict)

            else {}

        )



        summary["media_inventory_summary"] = (

            case.read_derived(
                "media_inventory_summary"
            )
            or {}

        )



        apps = case.read_derived(
            "apps"
        ) or []


        summary["notable_apps"] = [

            a

            for a in apps

            if isinstance(a, dict)
            and a.get("notable")

        ]



        summary["tag_count"] = len(
            case.read_tags()
        )



        ai = case.read_derived(
            "ai_findings"
        )


        summary["case_profile"] = (

            case.read_derived(
                "case_profile"
            )
            or {}

        )


        summary["ai_findings_summary"] = (

            ai.get(
                "counts",
                {}
            )

            if isinstance(ai, dict)

            else {}

        )


        return jsonify(summary)




    @app.get("/api/case/<case_id>/<dataset>")
    def case_dataset(
        case_id: str,
        dataset: str
    ):


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
            "whatsapp_backup_media"

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
            "case_profile",
            "collection_plan",

            "whatsapp_backup_summary"

        }



        if dataset not in (
            list_sets | obj_sets
        ):

            abort(404)



        case = _open(
            cases_root,
            case_id
        )


        return jsonify(
            case.read_derived(
                dataset
            )
        )




    # ---------------------------------------------------------
    # TELEGRAM
    # ---------------------------------------------------------

    @app.get("/api/case/<case_id>/telegram/conversations")
    def telegram_conversations(case_id: str):

        case = _open(
            cases_root,
            case_id
        )


        return jsonify(

            case.read_derived(
                "telegram_conversations"
            )
            or {}

        )




    @app.get("/api/case/<case_id>/telegram/conversations/<chat_id>")
    def telegram_conversation_detail(
        case_id: str,
        chat_id: str
    ):


        case = _open(
            cases_root,
            case_id
        )


        data = case.read_derived(
            "telegram_conversations"
        ) or {}



        conv = data.get(
            chat_id
        )


        if conv is None:

            abort(404)



        return jsonify(conv)




    # ---------------------------------------------------------
    # WHATSAPP BACKUP
    # ---------------------------------------------------------


    @app.get("/api/case/<case_id>/whatsapp_backup/messages")
    def whatsapp_backup_messages(case_id: str):

        case = _open(
            cases_root,
            case_id
        )


        return jsonify(

            case.read_derived(
                "whatsapp_backup_messages"
            )
            or []

        )



    @app.get("/api/case/<case_id>/whatsapp_backup/media")
    def whatsapp_backup_media(case_id: str):

        case = _open(
            cases_root,
            case_id
        )


        return jsonify(

            case.read_derived(
                "whatsapp_backup_media"
            )
            or []

        )



    @app.get("/api/case/<case_id>/whatsapp_backup/summary")
    def whatsapp_backup_summary(case_id: str):

        case = _open(
            cases_root,
            case_id
        )


        return jsonify(

            case.read_derived(
                "whatsapp_backup_summary"
            )
            or {}

        )
            # ---------------------------------------------------------
    # DATA EXPORT IMPORT
    # Instagram / Snapchat
    # ---------------------------------------------------------

    @app.post("/api/case/<case_id>/import/<app_name>")
    def import_export(
        case_id: str,
        app_name: str
    ):

        if app_name not in (
            "instagram",
            "snapchat"
        ):

            abort(404)



        case = _open(
            cases_root,
            case_id
        )



        upload = request.files.get(
            "file"
        )


        if upload is None or not upload.filename:

            return jsonify(
                {
                    "error":
                    "no file uploaded"
                }
            ),400



        from .parsers import (
            parse_instagram_export,
            parse_snapchat_export,
            thread_conversations
        )

        from .report import generate_report



        suffix = Path(
            upload.filename
        ).suffix or ".zip"



        fd, tmp_path = tempfile.mkstemp(
            suffix=suffix,
            prefix=f"{app_name}_export_"
        )


        os.close(fd)



        tmp = Path(
            tmp_path
        )


        upload.save(
            str(tmp)
        )



        try:


            parser = (

                parse_instagram_export

                if app_name == "instagram"

                else parse_snapchat_export

            )


            result = parser(
                tmp
            )


            if not result.get(
                "available"
            ):

                return jsonify(
                    {
                        "error":
                        result.get(
                            "error",
                            "parse failed"
                        )
                    }
                ),400



            messages = result.get(
                "messages",
                []
            )



            existing = case.read_derived(
                app_name
            ) or []



            merged = (
                list(existing)
                +
                messages
            )



            case.write_derived(
                app_name,
                merged
            )



            users = (

                case.read_derived(
                    f"{app_name}_users"
                )
                or []

            ) + result.get(
                "users",
                []
            )



            case.write_derived(
                f"{app_name}_users",
                users
            )



            case.write_derived(
                f"{app_name}_conversations",
                thread_conversations(
                    merged,
                    users
                )
            )



            try:

                generate_report(
                    case.root
                )

            except Exception:

                pass



            return jsonify(
                {
                    "imported":
                    len(messages),

                    "total":
                    len(merged)
                }
            )



        finally:

            if tmp.exists():

                tmp.unlink()



    # ---------------------------------------------------------
    # TAGS
    # ---------------------------------------------------------


    @app.get("/api/case/<case_id>/tags")
    def get_tags(case_id: str):

        return jsonify(

            _open(
                cases_root,
                case_id
            ).read_tags()

        )



    @app.post("/api/case/<case_id>/tags")
    def add_tag(case_id: str):

        case = _open(
            cases_root,
            case_id
        )


        body = request.get_json(
            force=True
        ) or {}



        tag = case.add_tag(

            ref=body.get(
                "ref",
                ""
            ),

            kind=body.get(
                "kind",
                "artifact"
            ),

            label=body.get(
                "label",
                "Tagged"
            ),

            note=body.get(
                "note",
                ""
            ),

            by=body.get(
                "by",
                ""
            )

        )


        return jsonify(tag)



    @app.delete("/api/case/<case_id>/tags/<tag_id>")
    def delete_tag(
        case_id: str,
        tag_id: str
    ):

        ok = _open(
            cases_root,
            case_id
        ).remove_tag(
            tag_id
        )


        return jsonify(
            {
                "removed": ok
            }
        )



    # ---------------------------------------------------------
    # EVIDENCE EXPORT
    # ---------------------------------------------------------


    @app.post("/api/case/<case_id>/export")
    def export_case_endpoint(
        case_id: str
    ):

        from .export import export_case


        case = _open(
            cases_root,
            case_id
        )


        out = export_case(
            case.root
        )


        return jsonify(
            {
                "path": str(out),
                "name": out.name,
                "size": out.stat().st_size
            }
        )



    @app.get("/api/case/<case_id>/export/download")
    def export_download(
        case_id: str
    ):

        from .export import export_case


        case = _open(
            cases_root,
            case_id
        )


        out = export_case(
            case.root
        )


        return send_file(
            out.resolve(),
            as_attachment=True,
            download_name=out.name
        )



    @app.get("/api/case/<case_id>/manifest")
    def manifest(
        case_id: str
    ):

        case = _open(
            cases_root,
            case_id
        )


        return jsonify(
            [
                r.to_dict()
                for r in case.manifest
            ]
        )



    @app.get("/api/case/<case_id>/audit")
    def audit(
        case_id: str
    ):

        return jsonify(

            _open(
                cases_root,
                case_id
            ).read_audit()

        )



    # ---------------------------------------------------------
    # REPORT ENDPOINT
    # ---------------------------------------------------------


    @app.get("/api/case/<case_id>/report")
    def case_report(
        case_id: str
    ):

        path = (

            cases_root
            /
            case_id
            /
            "report.html"

        )


        if not path.exists():

            abort(404)



        return send_file(
            path.resolve()
        )



    # ---------------------------------------------------------
    # MEDIA
    # ---------------------------------------------------------


    @app.get("/api/case/<case_id>/media/<artifact_id>")
    def media(
        case_id: str,
        artifact_id: str
    ):


        case = _open(
            cases_root,
            case_id
        )


        for rec in case.manifest:


            if rec.artifact_id == artifact_id:


                path = (
                    case.root
                    /
                    rec.stored_path
                ).resolve()



                return send_file(
                    path
                )



        abort(404)



    app.config["SOCKETIO"] = socketio


    return app, socketio





# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------


def _open(
    cases_root: Path,
    case_id: str
):

    path = (
        cases_root
        /
        _safe(case_id)
    )


    if not (
        path / "case.json"
    ).exists():

        abort(404)



    return Case.open(
        path
    )





def _safe(
    case_id: str
):

    return "".join(

        c

        for c in case_id

        if c.isalnum()
        or c in "-_"

    )





def _auto_case_id(
    cases_root: Path
):

    n = len(
        list(
            cases_root.glob(
                "CASE-*"
            )
        )
    ) + 1



    return f"CASE-{n:04d}"





# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------


def main():

    import argparse


    parser = argparse.ArgumentParser(
        description="eRakshak triage local service"
    )


    parser.add_argument(
        "--host",
        default="127.0.0.1"
    )


    parser.add_argument(
        "--port",
        type=int,
        default=5057
    )


    parser.add_argument(
        "--cases",
        default="_test_output"
    )



    args = parser.parse_args()



    app, socketio = create_app(
        Path(
            args.cases
        )
    )



    print(
        f"{TOOL_NAME} v{__version__} "
        f"— http://{args.host}:{args.port}"
    )



    if socketio:

        socketio.run(
            app,
            host=args.host,
            port=args.port,
            allow_unsafe_werkzeug=True
        )

    else:

        app.run(
            host=args.host,
            port=args.port
        )





if __name__ == "__main__":

    main()