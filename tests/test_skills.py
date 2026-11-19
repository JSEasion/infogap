"""技能归一词典。"""
from market_lens.taxonomy import match_skills, skill_group, valid_ids


def test_basic_match():
    s = match_skills("熟悉 LangGraph 与 MCP 协议，有 RAG 落地经验，用 FastAPI")
    assert {"langgraph", "mcp", "rag", "fastapi"} <= set(s)


def test_chinese_alias():
    s = match_skills("负责向量数据库建设与检索增强")
    assert "vector_db" in s
    assert "rag" in s


def test_short_alias_word_boundary():
    """go 不应匹配 google。"""
    assert "go" not in match_skills("使用 google cloud 部署")
    assert "go" in match_skills("精通 Go 语言/golang")


def test_all_ids_have_group():
    for sid in valid_ids():
        assert skill_group(sid) != "其他", sid
