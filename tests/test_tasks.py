"""任务框架：提交 → 进度 → 完成 → 取消 → 持久化 → API。"""
import time

import pytest
from fastapi.testclient import TestClient

from market_lens import db
from market_lens.web import tasks as tm
from market_lens.web.app import app

client = TestClient(app, follow_redirects=False)


@pytest.fixture()
def mgr(tmp_db, monkeypatch):
    m = tm.TaskManager()
    yield m
    m.stop()


def _wait_done(m, tid, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        conn = db.connect()
        t = db.get_task(conn, tid)
        conn.close()
        if t["status"] in ("done", "failed", "cancelled"):
            return t
        time.sleep(0.05)
    raise TimeoutError("任务未完成")


def test_submit_and_complete(mgr, tmp_db, no_llm):
    tid = mgr.submit("sample", "载入示例")
    t = _wait_done(mgr, tid)
    assert t["status"] == "done"
    assert t["result"]["added"] >= 1


def test_progress_persisted(mgr, tmp_db, no_llm):
    """import_text 任务走质量门流水线，进度/阶段落库。"""
    jd = ("AI Agent 开发工程师\n公司:测试科技\n城市:北京\n薪资:20-40K·14薪\n"
          "职责：负责 Agent 编排与 RAG 检索平台开发。\n"
          "要求：Python/Fastapi/Langchain/RAG/MCP，熟悉 LLM 应用与向量检索，"
          "有 multi_agent 编排经验优先。双休弹性，五险一金。" + "细节补充。" * 40)
    tid = mgr.submit("import_text", "粘贴导入", {"texts": [jd]})
    t = _wait_done(mgr, tid)
    assert t["status"] == "done"
    assert t["result"]["curated"] >= 1
    jobs = tmp_db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
    assert jobs >= 1
    raw = db.list_raw(tmp_db, status="curated")
    assert raw and raw[0]["gold_score"] is not None


def test_unknown_kind_rejected(mgr):
    with pytest.raises(ValueError):
        mgr.submit("no_such_kind", "x")


def test_cancel_flow(mgr, tmp_db, no_llm):
    """取消标记 → 进度回调抛 TaskCancelled → 状态 cancelled。"""
    @tm.handler("slow_stub")
    def _slow(conn, gateway, params, progress):
        for i in range(50):
            progress(i * 2, "step", f"{i}")
            time.sleep(0.05)
        return {}

    tid = mgr.submit("slow_stub", "慢任务")
    time.sleep(0.3)
    assert mgr.cancel(tid)
    t = _wait_done(mgr, tid)
    assert t["status"] == "cancelled"
    del tm.HANDLERS["slow_stub"]


def test_orphan_recovery(tmp_db, no_llm):
    db.create_task(tmp_db, "pipeline", "遗留")
    db.update_task(tmp_db, 1, status="running")
    m = tm.TaskManager(str(db.DB_PATH))
    assert m.recover_orphans() == 1
    t = db.get_task(tmp_db, 1)
    assert t["status"] == "orphaned"


def test_tasks_api(tmp_db, no_llm):
    """Web API：创建 → 列表 → 详情。"""
    r = client.post("/api/tasks", json={"kind": "sample", "title": "API示例"})
    assert r.status_code == 200 and r.json()["task_id"] > 0
    tid = r.json()["task_id"]
    for _ in range(100):
        t = client.get(f"/api/tasks/{tid}").json()
        if t["status"] == "done":
            break
        time.sleep(0.05)
    assert t["status"] == "done"
    lst = client.get("/api/tasks?limit=5").json()
    assert any(x["id"] == tid for x in lst)
    bad = client.post("/api/tasks", json={"kind": "nope"})
    assert bad.status_code == 400


# ---------------- 隔离区批量复核 ----------------

def _seed_q(conn, url: str, gold: float) -> int:
    rid, _ = db.upsert_raw(conn, {"kind": "job", "source": "wwr", "source_url": url,
                                  "title": "AI Agent 工程师",
                                  "body": "岗位：Agent 工程师\n城市：北京\n薪资：20-40K " + "x" * 200,
                                  "published_at": ""})
    import json as _json
    conn.execute("UPDATE raw_items SET status='quarantined', gold_score=?, score_json=? "
                 "WHERE id=?",
                 (gold, _json.dumps({"gold": gold, "dims": [
                     {"key": "credibility", "score": 60.0, "weight": 0.2}]}), rid))
    conn.commit()
    return rid


def test_quarantine_batch_by_score_mode(tmp_db, no_llm):
    """score_mode 圈选：≥门中值批量恢复（含发布），<低线批量丢弃。
    直接调 handler——经 TaskManager 会被早前测试泄漏的全局 worker 抢跑（测试基建问题，非产品）。"""
    hi = _seed_q(tmp_db, "https://b/1", 66.0)     # ≥ job 门中值 62.5
    lo = _seed_q(tmp_db, "https://b/2", 45.0)     # < 低线 55
    r = tm._h_quarantine_batch(tmp_db, tm._gw(tmp_db),
                               {"action": "promote", "score_mode": "above_mid"},
                               lambda *a: None)
    assert r["promoted"] == 1 and r["total"] == 1
    assert db.get_raw(tmp_db, hi)["status"] == "curated"
    assert tmp_db.execute("SELECT 1 FROM jobs WHERE raw_id=?", (hi,)).fetchone()
    assert db.get_raw(tmp_db, lo)["status"] == "quarantined"

    r2 = tm._h_quarantine_batch(tmp_db, tm._gw(tmp_db),
                                {"action": "discard", "score_mode": "below_lo"},
                                lambda *a: None)
    assert r2["discarded"] == 1
    assert db.get_raw(tmp_db, lo)["status"] == "rejected"
