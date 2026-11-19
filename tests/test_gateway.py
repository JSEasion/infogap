"""网关纯函数：think 剥离 / JSON 提取 / 流式分离器 / 路由配置。"""
from market_lens.config import Settings
from market_lens.gateway import (BudgetExceeded, LLMError, ThinkSplitter,
                                 extract_json, strip_think)


def test_think_splitter_basic():
    s = ThinkSplitter()
    out = list(s.feed("<think>思考</think>答案"))
    assert out == [("think", "思考"), ("answer", "答案")]


def test_think_splitter_tag_across_chunks():
    """标签被切在任意 chunk 边界都不能丢字。"""
    s = ThinkSplitter()
    out = []
    for chunk in ["<thi", "nk>思考", "中…</th", "ink>结论", "是42"]:
        out += list(s.feed(chunk))
    out += list(s.flush())
    assert "".join(t for k, t in out if k == "think") == "思考中…"
    assert "".join(t for k, t in out if k == "answer") == "结论是42"


def test_think_splitter_close_tag_across_chunks():
    s = ThinkSplitter()
    out = []
    for chunk in ["abc</thin", "k>def"]:
        out += list(s.feed(chunk))
    out += list(s.flush())
    assert "".join(t for k, t in out) == "abcdef"
    assert out[0][0] == "answer"


def test_think_splitter_answer_only_and_unterminated():
    s = ThinkSplitter()
    out = list(s.feed("直接回答")) + list(s.flush())
    assert all(k == "answer" for k, _ in out)
    s2 = ThinkSplitter()
    out2 = list(s2.feed("<think>没想完")) + list(s2.flush())
    assert "".join(t for k, t in out2) == "没想完"
    assert all(k == "think" for k, _ in out2)


def test_strip_think_closed():
    assert strip_think("<think>推理中…</think>{\"ok\":1}") == '{"ok":1}'


def test_strip_think_unclosed():
    assert strip_think("<think>还没想完") == ""


def test_strip_think_noop():
    assert strip_think("普通文本") == "普通文本"


def test_extract_json_object_first():
    """回归：对象开头不应被内层数组抢占。"""
    assert extract_json('{"items":[1,2,3],"ok":true}') == {"items": [1, 2, 3], "ok": True}


def test_extract_json_array():
    assert extract_json("```json\n[1,2]\n```") == [1, 2]


def test_extract_json_with_prose():
    assert extract_json("结果如下：{\"a\": 1} 完毕") == {"a": 1}


def test_extract_json_trailing_comma():
    assert extract_json('{"a":1,}') == {"a": 1}


def test_extract_json_failure():
    assert extract_json("完全没有JSON") is None


def test_routing_env_expansion(tmp_path, monkeypatch):
    """${ENV:默认} 展开 + 未配置通道自动跳过。"""
    cfg_file = tmp_path / "config.yaml"
    monkeypatch.setenv("TEST_LLM_URL", "http://10.0.0.9:1234/v1")
    monkeypatch.delenv("ABSENT_URL", raising=False)
    cfg_file.write_text("""
llm:
  providers:
    a: {base_url: "${TEST_LLM_URL}", api_key_env: K, models: [m1], json_mode: auto}
    b: {base_url: "${ABSENT_URL:}", api_key_env: K, models: [m2], json_mode: auto}
  routing:
    analysis: [a/m1, b, a]
""", encoding="utf-8")
    import market_lens.config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", cfg_file)
    s = Settings()
    cands = s.candidates("analysis")
    assert [c.provider for c in cands] == ["a", "a"]      # b 的 base_url 为空被跳过
    assert cands[0].base_url == "http://10.0.0.9:1234/v1"
    assert cands[1].model == "m1"                          # "a" 不带模型 → 取第一个


def test_budget_exceeded(tmp_db, monkeypatch):
    from market_lens import db
    from market_lens.gateway import LLMGateway
    from datetime import datetime
    for _ in range(3):
        db.log_llm(tmp_db, "analysis", "deepseek-official", "deepseek-chat", 100000, 50000, 30.0, True)
    import market_lens.config as cfg
    s = Settings.__new__(Settings)
    s.raw = {}
    s.providers = {"x": {"base_url": "http://127.0.0.1:9/v1", "models": ["m"]}}
    s.routing = {"analysis": ["x"]}
    s.budget_monthly_cny = 50.0
    s.dsh_enabled = False
    s.search = {}
    s.rss_sources = []
    s.prefs_default = {}
    gw = LLMGateway(s, tmp_db)
    try:
        gw.chat("analysis", "s", "u")
        raised = False
    except BudgetExceeded:
        raised = True
    assert raised
