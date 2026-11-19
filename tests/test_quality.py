"""quality：七维评分 / 反诈骗硬规则 / 三态门 / 渠道档案。"""
from market_lens import quality


def _job(gold_fields=True, source="greenhouse:openai", body_extra=""):
    body = ("AI Agent 平台开发工程师。要求 Python/LLM/RAG/Agent 编排。"
            "薪资 40-70K·15薪。双休弹性工作。" + "x" * 400 + body_extra)
    return {"id": 1, "kind": "job", "source": source, "source_url": "https://gh/1",
            "title": "AI Agent 工程师" if gold_fields else "",
            "body": body if gold_fields else "广告",
            "published_at": "2026-11-17", "fetched_at": "2026-11-18"}


def test_score_dimensions_and_weights():
    r = quality.score_item(_job(), "job")
    keys = {d.key for d in r.dims}
    assert {"credibility", "scarcity", "completeness", "evidence",
            "freshness", "relevance", "cross"} <= keys
    total_w = sum(d.weight for d in r.dims)
    assert abs(total_w - 1.0) < 0.01        # 权重归一
    assert 0 <= r.gold <= 100


def test_official_beats_weak():
    assert quality.score_item(_job(), "job").gold > quality.score_item(_job(False, "search"), "job").gold


def test_freshness_decay():
    assert quality.freshness_score("2026-11-18")[0] >= 95
    assert quality.freshness_score("3天前")[0] >= 90
    assert quality.freshness_score("2026-09-01")[0] < quality.freshness_score("2026-11-17")[0]


def test_cross_validation_boost():
    solo = quality.score_item(_job(), "job", cross_channels=1)
    multi = quality.score_item(_job(), "job", cross_channels=3)
    assert multi.gold > solo.gold


def test_scam_hard_rules():
    assert quality.scam_check({"title": "付费内推", "body": "缴纳内推服务费 8000"})
    assert quality.scam_check({"title": "入职", "body": "培训贷包入职"})
    assert not quality.scam_check({"title": "正规岗", "body": "负责支付系统开发，涉及转账链路"})  # 正常支付业务不误伤


def test_gate_three_states_no_llm():
    items = [_job(),                                          # 高分 → curated
             _job(False, "search"),                           # 低分 → quarantined
             _job(True, "tg:bad", body_extra="缴纳内推服务费八千，包入职"),  # 诈骗 → rejected
             ]
    items[2]["id"] = 3
    items[1]["id"] = 2
    res = quality.gate_items(None, None, items, "job", use_judge=False)
    by = {r.raw_id: r.status for r in res}
    assert by == {1: "curated", 2: "quarantined", 3: "rejected"}


def test_gate_gray_fallback_midpoint():
    """judge 不可用：灰区按门中值自动分流（默认），不再全量进隔离区。
    渠道档案调档构造灰区对：tier 0.45 → gold≈68.7（≥中值62.5），tier 0.0 → ≈59.7（<中值）。"""
    items = [_job(source="xhi"), _job(source="xlo")]
    items[1]["id"] = 2
    cfg = {"tiers": {"xhi": {"tier": 0.45, "scarcity": 0.5},
                     "xlo": {"tier": 0.0, "scarcity": 0.5}}}
    res = quality.gate_items(None, None, items, "job", quality_cfg=cfg, use_judge=False)
    by = {r.raw_id: r for r in res}
    assert 55.0 <= by[1].gold < 70.0 and 55.0 <= by[2].gold < 70.0   # 双双落在灰区
    assert by[1].status == "curated" and by[1].score.fallback_used
    assert by[2].status == "quarantined" and by[2].score.fallback_used


def test_gate_gray_fallback_conservative():
    """judge_fallback=quarantine：保持旧行为，灰区全隔离且不打 fallback 标。"""
    items = [_job(source="x1"), _job(source="x2")]
    items[1]["id"] = 2
    cfg = {"judge_fallback": "quarantine"}
    res = quality.gate_items(None, None, items, "job", quality_cfg=cfg, use_judge=False)
    for r in res:
        if 55.0 <= r.gold < 70.0:
            assert r.status == "quarantined" and not r.score.fallback_used


def test_score_json_explainable():
    import json
    r = quality.score_item(_job(), "job")
    data = json.loads(quality.score_to_json(r))
    assert data["gold"] == r.gold and len(data["dims"]) == len(r.dims)
    assert all(d["reason"] for d in data["dims"])   # 每维都有理由


def test_source_meta_prefix_and_override():
    assert quality.source_meta("tg:somechan")["group"] == "scarce"
    m = quality.source_meta("v2ex", overrides={"v2ex": {"tier": 0.9}})
    assert m["tier"] == 0.9 and m["scarcity"] == 0.5   # 覆盖 tier 保留稀缺


def test_news_kind_has_signal_dim():
    r = quality.score_item({"id": 1, "source": "rss:量子位", "title": "t",
                            "body": "智谱完成 30 亿元融资 " + "字" * 300,
                            "published_at": "2026-11-18"}, "news")
    assert "signal" in {d.key for d in r.dims}
