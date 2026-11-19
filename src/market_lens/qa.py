"""数据问答：FTS5（jieba 分词）召回 + LLM 带引用作答；无 LLM 时返回召回列表。
同时为 SSE 流式输出提供 build_prompt / rule_answer。"""
from __future__ import annotations

import json
import sqlite3

import jieba

from .gateway import LLMGateway, LLMError

_QA_SYSTEM = (
    "你是岗位数据分析师，只根据给定的岗位库检索结果回答，禁止编造。"
    "回答用中文 Markdown；引用岗位时标注编号如 [J3]。若检索结果不足以回答，直说数据不足。")


def _match_expr(query: str) -> str:
    tokens = [t.strip().strip('"') for t in jieba.cut(query) if len(t.strip()) > 1]
    seen, out = set(), []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(f'"{t}"')
    return " OR ".join(out)


def fts_search(conn: sqlite3.Connection, query: str, limit: int = 12) -> list[dict]:
    expr = _match_expr(query)
    rows: list[dict] = []
    if expr:
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT j.* FROM jobs_fts f JOIN jobs j ON j.id=f.rowid "
                "WHERE jobs_fts MATCH ? ORDER BY rank LIMIT ?", (expr, limit)).fetchall()]
        except Exception:  # noqa: BLE001 FTS 语法异常退化为 LIKE
            rows = []
    if not rows:
        like = f"%{query.strip()[:20]}%"
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM jobs WHERE title LIKE ? OR description LIKE ? OR company LIKE ? "
            "LIMIT ?", (like, like, like, limit)).fetchall()]
    return rows


def build_prompt(question: str, hits: list[dict]) -> str:
    blocks = []
    for i, j in enumerate(hits, 1):
        skills = ",".join(json.loads(j.get("skills") or "[]"))[:60]
        summary = j.get("description_zh") or (j.get("description") or "")
        blocks.append(
            f"[J{i}] id={j['id']} {j['title']}｜{j['company']}｜{j['city']}｜"
            f"{j['salary_raw'] or '薪资未标'}｜{j['experience']}｜技能:{skills}\n"
            f"岗位摘要：{summary[:300]}")
    return (f"问题：{question}\n\n岗位库检索结果（共{len(hits)}条）：\n"
            + "\n---\n".join(blocks))


def rule_answer(question: str, hits: list[dict]) -> str:
    lines = [f"（未配置模型，以下是与「{question}」最相关的 {len(hits)} 条岗位召回）", ""]
    for i, j in enumerate(hits, 1):
        lines.append(f"- [J{i}] {j['title']}｜{j['company']}｜{j['city']}｜{j['salary_raw']}")
    return "\n".join(lines)


def answer(conn: sqlite3.Connection, gateway: LLMGateway | None, question: str) -> dict:
    hits = fts_search(conn, question)
    if not hits:
        return {"answer_md": "岗位库里暂时没有能匹配这个问题的数据。先去「数据导入」导入一些 JD，"
                             "或用搜索采集/RSS 补充数据。", "cited": [], "llm_used": False}
    if gateway and gateway.settings.llm_ready("qa"):
        try:
            res = gateway.chat("qa", _QA_SYSTEM, build_prompt(question, hits), max_tokens=8000)
            return {"answer_md": res.text, "cited": hits, "llm_used": True}
        except LLMError:
            pass
    return {"answer_md": rule_answer(question, hits), "cited": hits, "llm_used": False}
