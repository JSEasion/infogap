"""问答流式会话：后台生成与连接解耦、断点续传、历史持久化。"""
import time

from fastapi.testclient import TestClient

from market_lens.extract import structure
from market_lens.sample_data import SAMPLE_JOBS
from market_lens.web.app import app
from market_lens.web.qa_sessions import qa_manager

client = TestClient(app, follow_redirects=False)


def _wait_finished(sid: str, timeout: float = 10.0):
    s = qa_manager.get(sid)
    assert s, "会话不存在"
    deadline = time.time() + timeout
    while not s.finished and time.time() < deadline:
        time.sleep(0.05)
    assert s.finished, "会话未在时限内完成"
    return s


def _seed(tmp_db):
    structure(tmp_db, SAMPLE_JOBS[:6], "sample", use_llm=False)


def test_ask_creates_session_and_persists_without_subscriber(tmp_db, no_llm):
    """核心保证：生成在后台线程跑完，没有任何订阅者也完整落库。"""
    _seed(tmp_db)
    r = client.post("/api/qa/ask", json={"q": "北京的 Agent 岗位要求什么技能"})
    assert r.status_code == 200
    sid = r.json()["sid"]
    s = _wait_finished(sid)
    types = [e["type"] for e in s.events]
    assert "done" in types and "answer" in types
    row = tmp_db.execute("SELECT * FROM qa_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["question"] == "北京的 Agent 岗位要求什么技能"
    assert row["answer_md"]


def test_events_replay_after_finish_and_resume_from_seq(tmp_db, no_llm):
    """会话结束后仍可全量回放；from_seq 只取后续帧（断点续传）。"""
    _seed(tmp_db)
    sid = client.post("/api/qa/ask", json={"q": "远程岗位有哪些"}).json()["sid"]
    _wait_finished(sid)
    full = client.get(f"/qa/events/{sid}")
    assert full.status_code == 200
    assert '"type": "step"' in full.text and '"type": "done"' in full.text
    part = client.get(f"/qa/events/{sid}", params={"from_seq": 2})
    n_full = full.text.count("data: ")
    n_part = part.text.count("data: ")
    assert 0 < n_part < n_full
    assert '"seq": 3' in part.text   # 从第 3 帧开始，1、2 帧已跳过


def test_active_endpoint_lists_running_then_empty(tmp_db, no_llm, monkeypatch):
    """进行中的会话出现在 /api/qa/active，结束后消失。"""
    _seed(tmp_db)
    import market_lens.retrieval as retrieval
    orig = retrieval.search

    def slow_search(conn, query, *a, **kw):
        time.sleep(1.0)
        return orig(conn, query, *a, **kw)

    monkeypatch.setattr(retrieval, "search", slow_search)
    sid = client.post("/api/qa/ask", json={"q": "上海岗位"}).json()["sid"]
    act = client.get("/api/qa/active").json()
    assert any(a["sid"] == sid for a in act)
    _wait_finished(sid)
    assert all(a["sid"] != sid for a in client.get("/api/qa/active").json())


def test_history_and_delete(tmp_db, no_llm):
    _seed(tmp_db)
    sid = client.post("/api/qa/ask", json={"q": "杭州岗位"}).json()["sid"]
    _wait_finished(sid)
    rows = client.get("/api/qa/history").json()
    assert rows and rows[0]["question"] == "杭州岗位"
    assert rows[0]["answer_html"].lstrip().startswith("<")   # 服务端渲染好的 Markdown
    assert client.delete(f"/api/qa/history/{rows[0]['id']}").json()["ok"] is True
    assert all(r["id"] != rows[0]["id"] for r in client.get("/api/qa/history").json())


def test_events_unknown_sid_404():
    assert client.get("/qa/events/nope").status_code == 404


def test_ask_empty_question_400(tmp_db):
    assert client.post("/api/qa/ask", json={"q": "  "}).status_code == 400


def test_legacy_stream_endpoint(tmp_db, no_llm):
    """旧入口 /qa/stream 仍可用（建会话 + 转发事件）。"""
    _seed(tmp_db)
    r = client.get("/qa/stream", params={"q": "Agent 技能要求"})
    assert r.status_code == 200
    assert '"type": "done"' in r.text
    row = tmp_db.execute("SELECT answer_md FROM qa_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert row["answer_md"]


def test_qa_page_renders_history_panel(tmp_db, no_llm):
    r = client.get("/qa")
    assert r.status_code == 200
    assert "历史会话" in r.text and "引用的岗位" in r.text
