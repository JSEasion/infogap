"""Web 接口测试：TestClient 全路由（规则模式，不触网）。"""
from fastapi.testclient import TestClient

from market_lens.extract import structure
from market_lens.sample_data import SAMPLE_JOBS
from market_lens.web.app import app

client = TestClient(app, follow_redirects=False)


def _use(tmp_db, monkeypatch):
    """tmp_db 夹具已把 db.DB_PATH 指向临时库；Web 每请求经 get_conn 重连，无需额外处理。"""
    return


def test_healthz():
    assert client.get("/healthz").json() == {"ok": True}


def test_pages_render(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    for url in ["/", "/jobs", "/import", "/reports", "/profile", "/news", "/qa", "/settings"]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.status_code)
        assert "<title>信息差" in r.text   # 顶部名称固定，不随导航变化
        assert 'href="/static/favicon.svg"' in r.text


def test_empty_dashboard_banner(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    r = client.get("/")
    assert "规则模式" in r.text          # 无 LLM 通道提示可见
    assert "0" in r.text


def test_import_paste_flow(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    jd = "Agent 工程师\n城市：北京\n薪资：20-40K·14薪\n公司：测试科技\n要求：熟悉 LangGraph RAG"
    r = client.post("/import/paste", data={"text": jd, "use_llm": ""})
    assert r.status_code == 302
    r2 = client.get("/jobs")
    assert "Agent 工程师" in r2.text
    assert "20-40K·14薪" in r2.text


def test_import_sample(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    assert client.post("/import/sample").status_code == 302
    r = client.get("/jobs")
    assert r.text.count("<tr") >= 30


def test_job_detail_and_delete(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    structure(tmp_db, SAMPLE_JOBS[:1], "sample", use_llm=False)
    jid = tmp_db.execute("SELECT id FROM jobs").fetchone()["id"]
    r = client.get(f"/jobs/{jid}")
    assert r.status_code == 200
    assert "匹配度分解" in r.text and "WLB 信号" in r.text
    assert client.post(f"/jobs/{jid}/delete").json()["ok"] is True   # v0.2 起返回 JSON（fetch 局部更新）
    assert client.get(f"/jobs/{jid}").status_code == 302   # 已删除 → 跳回列表


def test_report_generate_rule_mode(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    structure(tmp_db, SAMPLE_JOBS[:10], "sample", use_llm=False)
    r = client.post("/reports/generate", data={"kind": "market"})
    assert r.status_code == 302
    loc = r.headers["location"]
    assert "/reports/" in loc
    view = client.get(loc)
    assert "行情报告" in view.text and "市场总览" in view.text
    gap = client.post("/reports/generate", data={"kind": "gap"})
    assert "/reports/" in gap.headers["location"]


def test_profile_save_recalc(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    structure(tmp_db, SAMPLE_JOBS[:6], "sample", use_llm=False)
    r = client.post("/profile", data={
        "name": "测试者", "years_exp": 6, "background": "背景", "skills": "python,rag",
        "target_role": "Agent工程师", "salary_floor_k": "26", "cities": "北京,远程",
        "overtime_max": "1", "work_mode": "hybrid", "company_types": "foreign,unicorn",
        "tech_like": "langgraph,mcp", "tech_avoid": "", "w_wlb": 5, "w_work_mode": 3,
        "w_company_type": 2, "w_tech_fit": 4, "w_salary_margin": 3})
    assert r.status_code == 302
    page = client.get("/profile")
    assert "测试者" in page.text
    from market_lens.models import Profile
    import json as _json
    from market_lens import db as _db
    p = Profile.model_validate(_json.loads(_db.load_profile(tmp_db)))
    assert p.hard.salary_floor_k == 26 and p.hard.overtime_max == 1


def test_qa_flow(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    structure(tmp_db, SAMPLE_JOBS[:8], "sample", use_llm=False)
    r = client.get("/qa", params={"q": "北京的 Agent 岗位要求什么技能"})
    assert r.status_code == 200
    assert "引用的岗位" in r.text


def test_jobs_filters(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    structure(tmp_db, SAMPLE_JOBS, "sample", use_llm=False)
    r = client.get("/jobs", params={"city": "北京", "skill": "mcp"})
    assert r.status_code == 200
    assert "北京" in r.text
    r2 = client.get("/jobs", params={"source": "sample"})
    assert "sample" in r2.text


# ---------------- 隔离区复核端点 ----------------

def _seed_quarantined(conn, *, kind="job", title="灰区岗位", source="sample",
                      gold=60.0, body="岗位：Agent 工程师\n城市：北京\n薪资：20-40K") -> int:
    from market_lens import db as _db
    rid, _new = _db.upsert_raw(conn, {"kind": kind, "source": source, "title": title,
                                      "body": body, "source_url": ""})
    conn.execute(
        "UPDATE raw_items SET status='quarantined', gold_score=?, score_json=? WHERE id=?",
        (gold, '{"gold": %s, "dims": [{"key": "credibility", "score": 60.0, "weight": 0.2}]}' % gold,
         rid))
    conn.commit()
    return rid


def test_quarantine_promote_endpoint(tmp_db, monkeypatch, no_llm):
    """端到端打 HTTP 端点（曾因 app 层参数传反恒 500，此用例防回归）。"""
    _use(tmp_db, monkeypatch)
    rid = _seed_quarantined(tmp_db)
    r = client.post(f"/api/quarantine/{rid}/promote")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    row = tmp_db.execute("SELECT status, curated_id FROM raw_items WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "curated" and row["curated_id"]
    assert client.get("/quarantine").status_code == 200


def test_quarantine_discard_endpoint(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    rid = _seed_quarantined(tmp_db, title="低质岗位", gold=41.0)
    r = client.post(f"/api/quarantine/{rid}/discard")
    assert r.status_code == 200, r.text
    row = tmp_db.execute("SELECT status, review_action FROM raw_items WHERE id=?", (rid,)).fetchone()
    assert row["status"] == "rejected" and row["review_action"].startswith("discard@")


def test_quarantine_promote_missing_id_400_not_500(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    r = client.post("/api/quarantine/999999/promote")
    assert r.status_code == 200 and r.json()["message"] == "条目不存在"


def test_quarantine_page_sorted_with_actions(tmp_db, monkeypatch, no_llm):
    """页面：gold 降序、批量操作/短板/LLM 横幅可见。"""
    _use(tmp_db, monkeypatch)
    for i in range(3):
        _seed_quarantined(tmp_db, title=f"复核岗{i}", gold=50.0 + i * 10)
    r = client.get("/quarantine")
    assert r.status_code == 200
    assert "一键恢复" in r.text and "短板" in r.text
    assert "LLM 裁决通道未生效" in r.text          # judge 从未成功 → 横幅
    pos_hi, pos_lo = r.text.find("复核岗2"), r.text.find("复核岗0")
    assert -1 < pos_hi < pos_lo                    # 高分在前
    assert client.get("/quarantine", params={"kind": "job"}).status_code == 200


def test_quarantine_batch_endpoint_validation(tmp_db, monkeypatch, no_llm):
    _use(tmp_db, monkeypatch)
    bad1 = client.post("/api/quarantine/batch", json={"action": "bogus", "ids": [1]})
    assert bad1.status_code == 400
    bad2 = client.post("/api/quarantine/batch", json={"action": "discard"})
    assert bad2.status_code == 400                # 缺范围
    ok = client.post("/api/quarantine/batch",
                     json={"action": "discard", "score_mode": "below_lo"})
    assert ok.status_code == 200 and "task_id" in ok.json()
