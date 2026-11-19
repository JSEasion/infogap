"""统计层 / 问答召回 / 报告（规则模式）。"""
from market_lens import db
from market_lens.extract import structure
from market_lens.match import match_job
from market_lens.models import DEFAULT_PROFILE
from market_lens.qa import answer, fts_search
from market_lens.reports import gen_digest, gen_gap_report, gen_market_report
from market_lens.sample_data import SAMPLE_JOBS, SAMPLE_NEWS
from market_lens.stats import market_stats


def _load(tmp_db):
    structure(tmp_db, SAMPLE_JOBS, "sample", use_llm=False)
    for n in SAMPLE_NEWS:
        db.upsert_news(tmp_db, {**n, "fetched_at": db.now()})


def test_market_stats(tmp_db):
    _load(tmp_db)
    st = market_stats(tmp_db)
    assert st["total"] == len(SAMPLE_JOBS)
    assert st["companies"] > 5
    assert any(s["id"] == "rag" for s in st["skills_top"])
    assert st["salary_pct"]["p50"] and st["salary_pct"]["p50"] > 10
    assert sum(b["count"] for b in st["salary_hist"]) > 0
    assert st["wlb_dist"]


def test_market_stats_city_filter(tmp_db):
    _load(tmp_db)
    st = market_stats(tmp_db, city="北京")
    assert 0 < st["total"] < len(SAMPLE_JOBS)


def test_fts_recall(tmp_db):
    _load(tmp_db)
    hits = fts_search(tmp_db, "北京 Agent MCP")
    assert hits, "FTS 召回应有结果"
    hits2 = fts_search(tmp_db, "远程办公的岗位")
    assert len(hits2) >= 1


def test_fts_sync_on_update(tmp_db):
    """更新岗位后 FTS 触发器保持同步。"""
    _load(tmp_db)
    tmp_db.execute("UPDATE jobs SET title='唯一标记词ZZZ' WHERE id=(SELECT MIN(id) FROM jobs)")
    tmp_db.commit()
    assert fts_search(tmp_db, "唯一标记词ZZZ")


def test_reports_rule_mode(tmp_db, no_llm):
    from market_lens.gateway import LLMGateway
    _load(tmp_db)
    gw = LLMGateway(no_llm, tmp_db)
    rid = gen_market_report(tmp_db, gw, DEFAULT_PROFILE)
    r = db.get_report(tmp_db, rid)
    assert "行情报告" in r["title"] and "市场总览" in r["content_md"]
    rid2 = gen_gap_report(tmp_db, gw, DEFAULT_PROFILE)
    r2 = db.get_report(tmp_db, rid2)
    assert "缺口" in r2["content_md"] or "覆盖" in r2["content_md"]
    rid3 = gen_digest(tmp_db, gw)
    assert db.get_report(tmp_db, rid3)["content_md"]


def test_qa_rule_mode(tmp_db, no_llm):
    from market_lens.gateway import LLMGateway
    _load(tmp_db)
    res = answer(tmp_db, LLMGateway(no_llm, tmp_db), "北京的 Agent 岗位有哪些")
    assert res["cited"] and not res["llm_used"]
    assert "J1" in res["answer_md"]


def test_qa_no_data(tmp_db, no_llm):
    from market_lens.gateway import LLMGateway
    res = answer(tmp_db, LLMGateway(no_llm, tmp_db), "任何问题")
    assert res["cited"] == [] and "没有" in res["answer_md"]


def test_match_all_samples(tmp_db):
    """全部示例岗位匹配不崩溃，产出可解释维度。"""
    _load(tmp_db)
    jobs = db.list_jobs(tmp_db)
    passed = 0
    for j in jobs:
        m = match_job(j, DEFAULT_PROFILE)
        assert m.verdict in ("pass", "veto")
        assert len(m.dims) == 5
        passed += m.verdict == "pass"
    assert 0 < passed < len(jobs), "默认偏好下应既有关注项也有否决项"


def test_market_stats_salary_grounding(tmp_db):
    """薪资统计只认「有原文依据」的行：公告类聚合页与有数无据的残留不参与。"""
    _load(tmp_db)
    st = market_stats(tmp_db)
    assert st["salary_n"] > 0
    base_n, base_p50 = st["salary_n"], st["salary_pct"]["p50"]
    # 公告类聚合页（N 岗一帖）：有数值也不算单岗薪资
    tmp_db.execute(
        "INSERT INTO jobs(source,title,company,city,salary_raw,salary_min_k,salary_max_k,"
        " salary_months,description,skills,dedup_hash)"
        " VALUES('gxannounce','2026年公开招聘公告（7岗招115人）','某学院','重庆','20-100万/年',"
        "16.7,83.3,12,'公告正文','[]','test-ann1')")
    # 有数无据残留：raw 为空但数值非空
    tmp_db.execute(
        "INSERT INTO jobs(source,title,company,city,salary_raw,salary_min_k,salary_max_k,"
        " salary_months,description,skills,dedup_hash)"
        " VALUES('gaoxiaojob','水土保持设计岗','某院','重庆','',15.0,25.0,12,'正文','[]','test-ann2')")
    tmp_db.commit()
    st2 = market_stats(tmp_db)
    assert st2["total"] == st["total"] + 2        # 岗位样本数仍如实统计
    assert st2["salary_n"] == base_n              # 薪资样本不增
    assert st2["salary_pct"]["p50"] == base_p50   # 分位数不被带偏
