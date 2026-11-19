"""news_pipeline：正文抽取 / 规则信号卡 / 降级链 / 落库。"""
import json

from market_lens import db, news_pipeline as np


_HTML = """<html><body><nav>首页 关于 联系</nav><script>evil()</script>
<article><p>智谱完成 30 亿元 B 轮融资，由国资基金领投。</p>
<p>资金将用于 GLM-6 大模型训练与 Agent 平台建设。</p>
<p>公司同时在杭州扩招 200 名工程师，含 50 个校招名额。</p></article>
<footer>版权所有</footer></body></html>"""


def test_extract_fulltext_removes_noise():
    ft = np.extract_fulltext(_HTML)
    assert "智谱" in ft and "200 名工程师" in ft
    assert "evil()" not in ft and "首页" not in ft and "版权所有" not in ft


def test_fallback_card_signal_detection():
    ft = np.extract_fulltext(_HTML)
    card = np.fallback_card("智谱完成 30 亿元融资", ft)
    assert card.signal_type == "funding"
    assert card.one_liner
    assert any("30 亿" in p or "200" in p for p in card.key_points)
    assert "智谱" in card.entities
    layoff = np.fallback_card("某公司裁员", "该公司今日宣布裁员 500 人，涉及多个部门。")
    assert layoff.signal_type == "layoff"


def test_build_cards_llm_failure_falls_back():
    cards = np.build_signal_cards(None, [{"id": 1, "title": "智谱完成 30 亿元融资",
                                          "body": np.extract_fulltext(_HTML)}])
    assert cards[1].one_liner and cards[1].signal_type == "funding"


def test_publish_news_no_raw_summary(tmp_db):
    """铁律：落库后 summary 为空（UI 只展示信号卡）。"""
    ft = np.extract_fulltext(_HTML)
    card = np.fallback_card("智谱完成 30 亿元融资", ft)
    nid = np.publish_news(tmp_db, {"source": "rss:量子位", "title": "智谱完成 30 亿元融资",
                                   "source_url": "https://x/1", "body": ft,
                                   "published_at": "2026-11-18"}, card, 75.0, "{}", raw_id=9)
    row = tmp_db.execute("SELECT * FROM news WHERE id=?", (nid,)).fetchone()
    assert row["summary"] == "" and row["one_liner"] == card.one_liner
    assert row["signal_type"] == "funding"
    fts = tmp_db.execute("SELECT COUNT(*) c FROM news_fts WHERE news_fts MATCH '智谱'").fetchone()
    assert fts["c"] == 1
    sig = tmp_db.execute("SELECT importance FROM news_signals WHERE news_id=?", (nid,)).fetchone()
    assert sig["importance"] > 0


def test_needs_fulltext():
    assert np.needs_fulltext({"title": "t", "body": "点击查看原文"})
    assert np.needs_fulltext({"title": "t", "body": "短"})
    assert not np.needs_fulltext({"title": "t", "body": "字" * 400})


# ---------------- 中文治理 / 热度 / 回填 ----------------

class _FakeGW:
    """假网关：对含 'English' 的批次抛错（模拟失败），其余返回中文卡 JSON。"""

    def __init__(self, replies=None):
        self.replies = replies or []
        self.calls = 0

    def chat(self, task, system, user, **kw):
        import json as _json
        self.calls += 1
        if "English-only" in user:
            raise RuntimeError("truncated")

        class _R:
            text = _json.dumps(self.replies.pop(0), ensure_ascii=False)
        return _R


def test_card_from_obj_rejects_english():
    """LLM 输出英文一句话 → 视为无效（系统全中文铁律）。"""
    assert np._card_from_obj({"one_liner": "OpenAI releases new model"}) is None
    card = np._card_from_obj({"one_liner": "OpenAI 发布新模型，推理成本降 50%",
                              "key_points": ["成本减半"], "signal_type": "release"})
    assert card and card.method == "llm" and np._has_cjk(card.one_liner)


def test_build_cards_batching_partial_failure():
    """分批：一批失败只降级该批，其余批次仍拿到 LLM 卡。"""
    items = [{"id": i, "kind": "news", "title": f"中文资讯{i}", "body": "正文" * 50}
             for i in range(1, 13)]
    items[1]["title"] = "English-only paper on agents"   # 第 1 批触发网关抛错

    def reply():
        return [{"idx": j, "one_liner": f"第{j}条中文一句话概要", "key_points": ["要点"],
                 "signal_type": "tech", "entities": [], "impact": ""} for j in range(3)]
    gw = _FakeGW(replies=[reply(), reply(), reply()])
    cards = np.build_signal_cards(gw, items)
    llm_n = sum(1 for c in cards.values() if c.method == "llm")
    rule_n = sum(1 for c in cards.values() if c.method == "rule")
    assert llm_n == 9 and rule_n == 3      # 失败批 3 条降级，成功 3 批 9 条 LLM 卡
    assert gw.calls == 4                   # 12 条分 4 批，批1失败批2-4成功


def test_extract_heat():
    assert np.extract_heat({"channel_meta": {"upvotes": 42}, "body": ""}) == 42
    assert np.extract_heat({"channel_meta": {}, "body": "repo（Python，⭐12,345）desc"}) == 12345
    assert np.extract_heat({"channel_meta": {}, "body": "repo（Rust，⭐3.4k）"}) == 3400
    assert np.extract_heat({"channel_meta": {}, "body": "no stars here"}) == 0


def test_regovern_updates_rows(tmp_db):
    """回填治理：规则卡行 → LLM 中文卡；LLM 不可用时保持原样。"""
    ft = np.extract_fulltext(_HTML)
    card = np.fallback_card("智谱完成 30 亿元融资", ft)
    np.publish_news(tmp_db, {"source": "rss:量子位", "title": "智谱完成 30 亿元融资",
                             "source_url": "https://x/rg", "body": ft,
                             "published_at": "2026-11-18"}, card, 75.0, "{}")
    row = tmp_db.execute("SELECT card_method FROM news WHERE url='https://x/rg'").fetchone()
    assert row["card_method"] == "rule"
    # LLM 不可用 → 不动
    r0 = np.regovern_news(tmp_db, None)
    assert r0["regoverned"] == 0
    # LLM 可用 → 中文卡回填
    gw = _FakeGW(replies=[[{"idx": 0, "one_liner": "智谱完成 30 亿元融资，国资领投",
                            "key_points": ["30 亿元 B 轮"], "signal_type": "funding",
                            "entities": ["智谱"], "impact": "GLM 系岗位利好"}]])
    r1 = np.regovern_news(tmp_db, gw)
    assert r1["regoverned"] == 1
    row = tmp_db.execute("SELECT * FROM news WHERE url='https://x/rg'").fetchone()
    assert row["card_method"] == "llm" and "智谱" in row["one_liner"]
    assert row["impact"] == "GLM 系岗位利好"
    # 幂等：治理过的行不再进待治理队列
    assert np.regovern_news(tmp_db, gw)["pending"] == 0


def test_query_news_search_sort_page(tmp_db):
    """统一查询：关键字搜索 + 排序 + 分页。"""
    ft = np.extract_fulltext(_HTML)
    for i, (t, u, heat) in enumerate([("智谱融资", "https://x/a", 10),
                                      ("OpenAI 发布", "https://x/b", 500),
                                      (" Anthropic 论文 ", "https://x/c", 0)]):
        np.publish_news(tmp_db, {"source": "rss:x", "title": t, "source_url": u,
                                 "body": ft + t, "published_at": f"2026-11-1{i}",
                                 "channel_meta": {}}, np.fallback_card(t, ft),
                        60 + i * 10, "{}", item_kind="tech")
        tmp_db.execute("UPDATE news SET heat=? WHERE url=?", (heat, u))
        tmp_db.commit()
    d = db.query_news(tmp_db, kind="tech", q="OpenAI")
    assert d["total"] == 1 and "OpenAI" in d["items"][0]["title"]
    d = db.query_news(tmp_db, kind="tech", sort="hot")
    assert d["items"][0]["heat"] == 500           # 热度优先
    d = db.query_news(tmp_db, kind="tech", sort="gold")
    assert d["items"][0]["gold_score"] == 80      # 含金优先
    d = db.query_news(tmp_db, kind="tech", per=2, page=2)
    assert d["pages"] == 2 and len(d["items"]) == 1


def test_invalid_summary_never_displayed(tmp_db):
    """无效摘要 + 抓不到正文 → signal 维度崩、门拦截（端到端防垃圾直出）。"""
    from market_lens import quality
    bad = {"id": 2, "kind": "news", "source": "rss:坏源", "title": "点开看",
           "body": "点击查看原文", "published_at": "2026-11-18"}
    res = quality.gate_items(None, None, [bad], "news", use_judge=False)
    assert res[0].status == "quarantined"
