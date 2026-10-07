"""A case whose run never finished has no verdict or graph. The overview must say so with
``null``/``{}``, not ``[]`` (truthy, no ``.reasons``) — the dashboard read it and went blank."""

from tests.test_whatsapp_batch_import_route import _make_server_app
from triage.custody import Case, CaseMeta


def test_overview_of_a_case_with_no_derived_data(tmp_path):
    app, _ = _make_server_app(tmp_path)
    Case.create(tmp_path, CaseMeta(case_id="U-1", examiner="Insp. Rao"))
    c = app.test_client()
    t = c.post("/api/auth/login", json={"username": "admin", "password": "snagr-demo"}).get_json()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {t['token']}"
    body = c.get("/api/case/U-1").get_json()
    assert body["risk"] is None
    assert body["graph_stats"] == {}
