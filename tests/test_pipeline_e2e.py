"""pipeline 端到端（mock 采集，不触网）：raw 落地 → 门 → 发布 → 整合 → 快照。"""
import json

from market_lens import db, pipeline


def _fake_items():
    return [
        {"kind": "job", "source": "greenhouse:openai", "source_url": "https://gh/1",
         "title": "AI Agent 工程师",
         "body": "AI Agent 平台开发。要求 Python/LLM/RAG/Agent。薪资 40-70K·15薪。双休。" + "x" * 350,
         "published_at": "2026-11-17"},
        {"kind": "job", "source": "tg:dejob", "source_url": "https://t/2",
         "title": "内推 AI 平台工程师",
         "body": "朋友团队内推 AI 平台工程师，Python/LLM/RAG，薪资 35-60K，投递 a@corp.com" + "，" * 80,
         "published_at": "2026-11-18"},
        {"kind": "job", "source": "search", "source_url": "https://s/3",
         "title": "", "body": "广告", "published_at": "2025-01-01"},
        {"kind": "news", "source": "rss:量子位", "source_url": "https://n/4",
         "title": "智谱完成 30 亿元融资",
         "body": "智谱完成 30 亿元 B 轮融资，资金用于 GLM-6 训练，同时在杭州扩招 200 名工程师。" * 2,
         "published_at": "2026-11-18"},
        {"kind": "tech", "source": "arxiv", "source_url": "https://a/5",
         "title": "AgentRL: Training LLM Agents",
         "body": "We propose a new reinforcement learning framework for LLM agents. " * 3,
         "published_at": "2026-11-18"},
    ]


def test_full_pipeline_no_collect(tmp_db, no_llm, monkeypatch):
    """预置 pending raw → run(collect=False) 全链路。"""
    for it in _fake_items():
        db.upsert_raw(tmp_db, it)
    stages = []
    s = pipeline.run(tmp_db, None, progress=lambda p, st, m: stages.append(st),
                     collect_enabled=False, trigger="test")
    assert s["curated"] >= 2                       # 官方岗 + 资讯 + 技术至少过门
    assert s["quarantined"] >= 1                   # search 广告进隔离
    # 精选岗位回链 + gold 落库
    job = tmp_db.execute(
        "SELECT raw_id, gold_score, gold_json FROM jobs WHERE source LIKE 'greenhouse%'").fetchone()
    assert job and job["raw_id"] and job["gold_score"]
    assert json.loads(job["gold_json"])["dims"]
    # 资讯信号卡落库（无 LLM → 规则卡）
    news = tmp_db.execute("SELECT one_liner, signal_type, kind FROM news WHERE raw_id IS NOT NULL").fetchall()
    assert any(n["one_liner"] for n in news)
    kinds = {n["kind"] for n in news}
    assert "news" in kinds and "tech" in kinds
    # 快照落库
    assert tmp_db.execute("SELECT COUNT(*) c FROM snapshots").fetchone()["c"] >= 2
    # 流水线可观测
    run = db.last_pipeline_run(tmp_db)
    assert run and run["stages"]
    # 隔离区可见
    assert db.count_raw(tmp_db, status="quarantined") >= 1


def test_scam_rejected_never_published(tmp_db, no_llm):
    db.upsert_raw(tmp_db, {"kind": "job", "source": "tg:bad", "source_url": "https://t/9",
                           "title": "付费内推",
                           "body": "缴纳内推服务费 8000 元，包入职大厂", "published_at": ""})
    s = pipeline.run(tmp_db, None, collect_enabled=False, trigger="test")
    assert s["rejected"] == 1
    assert tmp_db.execute("SELECT COUNT(*) c FROM jobs WHERE source='tg:bad'").fetchone()["c"] == 0


def test_promote_and_discard(tmp_db, no_llm):
    db.upsert_raw(tmp_db, {"kind": "job", "source": "search", "source_url": "https://s/1",
                           "title": "AI 助理",
                           "body": "AI 助理开发，要求 Python LLM。薪资面议。" + "岗" * 60,
                           "published_at": ""})
    pipeline.run(tmp_db, None, collect_enabled=False, trigger="test")
    q = db.list_raw(tmp_db, status="quarantined")
    if not q:   # 该条直接过门时补一条必然低分的
        db.upsert_raw(tmp_db, {"kind": "job", "source": "search", "source_url": "https://s/2",
                               "title": "", "body": "垃圾", "published_at": ""})
        pipeline.run(tmp_db, None, collect_enabled=False, trigger="test")
        q = db.list_raw(tmp_db, status="quarantined")
    assert q
    rid = q[0]["id"]
    assert pipeline.promote_raw(tmp_db, rid, None) == "已恢复进精选"
    assert db.get_raw(tmp_db, rid)["status"] == "curated"
    assert "review_action" in db.get_raw(tmp_db, rid) or db.get_raw(tmp_db, rid)["review_action"]
    db.upsert_raw(tmp_db, {"kind": "job", "source": "search", "source_url": "https://s/3",
                           "title": "垃圾", "body": "垃圾", "published_at": ""})
    pipeline.run(tmp_db, None, collect_enabled=False, trigger="test")
    q2 = [r for r in db.list_raw(tmp_db, status="quarantined") if r["source_url"] == "https://s/3"]
    if q2:
        assert pipeline.discard_raw(tmp_db, q2[0]["id"]) == "已永久丢弃"
        assert db.get_raw(tmp_db, q2[0]["id"])["status"] == "rejected"


def test_rescore_regates_quarantine_backlog(tmp_db, no_llm):
    """rescore 重放：未复核的隔离条目重过门（灰区按中值分流清积压），
    人工复核过的条目保持原结论不被覆盖。"""
    good = ("AI Agent 平台开发工程师。要求 Python/LLM/RAG/Agent 编排。"
            "薪资 40-70K·15薪。双休弹性工作。" + "x" * 400)
    weak = "招工程师 Python 薪资面议 " + "x" * 80
    hi, _ = db.upsert_raw(tmp_db, {"kind": "job", "source": "wwr", "source_url": "https://q/1",
                                   "title": "AI Agent 工程师", "body": good,
                                   "published_at": "2026-11-17"})
    lo, _ = db.upsert_raw(tmp_db, {"kind": "job", "source": "remoteok", "source_url": "https://q/2",
                                   "title": "工程师", "body": weak, "published_at": "2026-11-17"})
    kept, _ = db.upsert_raw(tmp_db, {"kind": "job", "source": "wwr", "source_url": "https://q/3",
                                     "title": "AI Agent 工程师", "body": good,
                                     "published_at": "2026-11-17"})
    # 模拟旧全隔离策略积压：三条直接置 quarantined，其中 kept 已人工复核
    for rid in (hi, lo, kept):
        tmp_db.execute("UPDATE raw_items SET status='quarantined', gold_score=58.0 WHERE id=?",
                       (rid,))
    tmp_db.execute("UPDATE raw_items SET review_action='promote@2026-11-01T00:00:00' WHERE id=?",
                   (kept,))
    tmp_db.commit()

    s = pipeline.run(tmp_db, None, collect_enabled=False, rescore_only=True, trigger="rescore")
    assert s["curated"] >= 1
    assert db.get_raw(tmp_db, hi)["status"] == "curated"
    assert tmp_db.execute("SELECT 1 FROM jobs WHERE raw_id=?", (hi,)).fetchone()  # 已发布
    assert db.get_raw(tmp_db, lo)["status"] == "quarantined"
    rk = db.get_raw(tmp_db, kept)
    assert rk["status"] == "quarantined"          # 人工结论不被重放覆盖
    assert rk["review_action"].startswith("promote@")


def test_quarantine_auto_expire(tmp_db, no_llm):
    """自动过期：超期未复核 → rejected；人工复核过的与未超期的豁免；0=关闭。"""
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc).astimezone() - timedelta(days=20)
           ).isoformat(timespec="seconds")
    ids = []
    for i, (url, reviewed) in enumerate((("https://e/1", False),   # 超期未复核 → 过期
                                         ("https://e/2", True),    # 超期但已复核 → 豁免
                                         ("https://e/3", False))):  # 未超期 → 豁免
        rid, _ = db.upsert_raw(tmp_db, {"kind": "job", "source": "wwr", "source_url": url,
                                        "title": f"岗位{i}", "body": "Python 岗位 " + "x" * 200,
                                        "published_at": ""})
        tmp_db.execute("UPDATE raw_items SET status='quarantined', fetched_at=? WHERE id=?",
                       (old if i < 2 else db.now(), rid))
        if reviewed:
            tmp_db.execute("UPDATE raw_items SET review_action='promote@x' WHERE id=?", (rid,))
        ids.append(rid)
    tmp_db.commit()

    assert db.expire_quarantined(tmp_db, 14) == 1
    st = {rid: db.get_raw(tmp_db, rid)["status"] for rid in ids}
    assert st == {ids[0]: "rejected", ids[1]: "quarantined", ids[2]: "quarantined"}
    assert db.get_raw(tmp_db, ids[0])["review_action"].startswith("expire@")
    assert db.expire_quarantined(tmp_db, 0) == 0          # 关闭开关


def test_consolidate_lead_days_and_boost(tmp_db, no_llm):
    """同岗两渠道（冷门先发）→ gold 回填 + 领先天数。"""
    db.upsert_raw(tmp_db, {"kind": "job", "source": "tg:dejob", "source_url": "https://t/a",
                           "title": "AI Agent 工程师",
                           "body": "AI Agent 工程师\n公司:同一家\n薪资:40-70K\nAI Agent 平台开发。Python/LLM/RAG。双休。" + "x" * 350,
                           "published_at": "2026-11-10"})
    db.upsert_raw(tmp_db, {"kind": "job", "source": "v2ex", "source_url": "https://r/b",
                           "title": "AI Agent 工程师",
                           "body": "AI Agent 工程师\n公司:同一家\n薪资:40-70K\nAI Agent 平台开发。Python/LLM/RAG。双休。" + "x" * 350,
                           "published_at": "2026-11-16"})
    pipeline.run(tmp_db, None, collect_enabled=False, trigger="test")
    from market_lens.consolidate import channel_uniqueness
    uniq = channel_uniqueness(tmp_db)
    tg = [u for u in uniq if u["source"] == "tg:dejob"]
    assert tg and tg[0]["lead_max"] > 0     # 冷门渠道领先 6 天
