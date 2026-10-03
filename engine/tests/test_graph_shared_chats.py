"""Contact-to-contact links come only from group chats the device actually holds.

A call log or SMS thread records owner<->contact only. Two contacts are linked when the
same group chat (3+ participants) lists both; a 2-person chat is never read as a link.
"""

from triage.analysis import build_communication_graph
from triage.analysis.graph import shared_chat_links


def _graph():
    msgs = [{"app": "telegram", "sender": n} for n in ("Asha", "Bela", "Chet", "Dev")]
    return build_communication_graph(messages=msgs, calls=[], contacts=[])


def _chat(*names, cid="c1"):
    return {cid: {"chat_id": cid, "title": f"G {cid}", "participants": [{"id": n, "name": n} for n in names]}}


def test_group_chat_links_every_pair_of_members():
    links = shared_chat_links(_graph(), {"telegram": _chat("Asha", "Bela", "Chet")})
    pairs = {frozenset((e["source"], e["target"])) for e in links}
    assert len(pairs) == 3 and all(e["kind"] == "shared_chat" and e["weight"] == 1 for e in links)


def test_two_person_chat_is_not_a_link():
    assert shared_chat_links(_graph(), {"telegram": _chat("Asha", "Bela")}) == []


def test_repeat_groups_add_weight_and_unknown_names_are_ignored():
    convs = {"telegram": {**_chat("Asha", "Bela", "Chet", cid="a"), **_chat("Asha", "Bela", "Stranger", cid="b")}}
    links = shared_chat_links(_graph(), convs)
    ab = next(e for e in links if {e["source"], e["target"]} == {"name:Asha", "name:Bela"})
    assert ab["weight"] == 2
    assert all("Stranger" not in (e["source"] + e["target"]) for e in links)


def test_links_route_serves_edges(tmp_path):
    from tests.test_whatsapp_batch_import_route import _make_server_app
    from triage.custody import Case, CaseMeta

    app, _ = _make_server_app(tmp_path)
    case = Case.create(tmp_path, CaseMeta(case_id="L-1", examiner="Insp. Rao"))
    case.write_derived("graph", _graph())
    case.write_derived("telegram_conversations", _chat("Asha", "Bela", "Chet"))
    c = app.test_client()
    t = c.post("/api/auth/login", json={"username": "admin", "password": "snagr-demo"}).get_json()
    c.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {t['token']}"
    assert len(c.get("/api/case/L-1/graph/links").get_json()["edges"]) == 3
