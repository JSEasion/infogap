"""混合检索三段式：召回（FTS5+同义词 ∥ 向量）→ RRF 融合 → rerank 精排 → gold×时效。

降级链（每层记录日志，检索永不挂）：
- rerank 端点不可达 → 仅 RRF 融合排序
- embedding 端点不可达 → 仅 FTS5+同义词
- FTS5 异常 → LIKE 兜底
千条级数据用 numpy 暴力余弦最优；数据量上到十万级再换 sqlite-vec（接口不变）。
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3

import jieba

from . import db, taxonomy
from .config import get_settings
from .gateway import EmbeddingClient, LLMError, RerankClient

# 查询同义词扩展（复用 taxonomy 技能词典别名 + 领域固定映射）
_FIXED_SYNONYMS = {
    "大模型": ["llm", "gpt", "基础模型", "预训练"],
    "远程": ["remote", "wfh", "居家"],
    "智能体": ["agent", "ai agent", "multi_agent"],
    "外企": ["foreign", "欧美"],
    "国企": ["soe", "央企", "事业单位"],
    "加班": ["996", "大小周", "wlb"],
    "薪资": ["salary", "待遇", "薪酬"],
    "裁员": ["layoff", "优化", "缩编"],
    "融资": ["funding", "投资", "获投"],
}


def expand_query(query: str) -> list[str]:
    """jieba 切词 + 同义词扩展（搜索「大模型」也能命中只写 LLM 的行）。"""
    words = [w.strip() for w in jieba.cut_for_search(query) if len(w.strip()) >= 2]
    out = list(dict.fromkeys(words))
    lowered = query.lower()
    # 同义词：分词命中 或 原查询包含整词（jieba 可能把「大模型」切成「模型」）
    for key, syns in _FIXED_SYNONYMS.items():
        if key in words or key in query or key in lowered:
            for syn in syns:
                if syn not in out:
                    out.append(syn)
    # 技能词典别名（llm↔大模型 等）
    for sid, _group, aliases in taxonomy.DEFAULT_SKILLS:
        if sid in lowered or any(a.lower() in lowered for a in aliases):
            out.append(sid)
    return out[:16] or [query]


# ---------------- 召回：FTS5 ----------------

def fts_recall(conn: sqlite3.Connection, query: str, item_type: str = "job",
               limit: int = 30) -> list[tuple[int, float]]:
    """FTS5 召回（jobs_fts / news_fts），返回 [(id, rank_score)]。异常降级 LIKE。"""
    terms = expand_query(query)
    match = " OR ".join(f'"{t}"' for t in terms)
    try:
        if item_type == "job":
            rows = conn.execute(
                "SELECT rowid AS id, rank FROM jobs_fts WHERE jobs_fts MATCH ? "
                "ORDER BY rank LIMIT ?", (match, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT rowid AS id, rank FROM news_fts WHERE news_fts MATCH ? "
                "ORDER BY rank LIMIT ?", (match, limit)).fetchall()
        return [(r["id"], -float(r["rank"])) for r in rows]
    except Exception:  # noqa: BLE001 FTS 语法异常 → LIKE 兜底
        like = f"%{terms[0]}%"
        if item_type == "job":
            rows = conn.execute(
                "SELECT id FROM jobs WHERE title LIKE ? OR company LIKE ? "
                "LIMIT ?", (like, like, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM news WHERE title LIKE ? OR one_liner LIKE ? "
                "LIMIT ?", (like, like, limit)).fetchall()
        return [(r["id"], 1.0) for r in rows]


# ---------------- 召回：向量 ----------------

def _item_text(row: dict) -> str:
    if "salary_raw" in row:   # job
        return " ".join(filter(None, [row.get("title", ""), row.get("company", ""),
                                      row.get("city", ""), row.get("skills_text", ""),
                                      (row.get("description") or "")[:1500]]))
    return " ".join(filter(None, [row.get("title", ""), row.get("one_liner", ""),
                                  " ".join(row.get("key_points") or []),
                                  (row.get("fulltext") or "")[:1500]]))


def _text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def vector_recall(conn: sqlite3.Connection, query: str, item_type: str = "job",
                  limit: int = 30, embedder: EmbeddingClient | None = None) -> list[tuple[int, float]]:
    """向量余弦召回。embedding 未配置/失败 → 返回空（降级链一环）。"""
    import numpy as np
    embedder = embedder or EmbeddingClient(get_settings(), conn)
    if not embedder.ready:
        return []
    model = embedder.cand.model
    vecs = db.load_vectors(conn, model)
    vecs = [v for v in vecs if v["item_type"] == item_type]
    if not vecs:
        return []
    try:
        q = embedder.embed([query])[0]
    except LLMError:
        return []
    qv = np.asarray(q, dtype=np.float32)
    qv /= (np.linalg.norm(qv) + 1e-9)
    mat = np.frombuffer(b"".join(v["vec"] for v in vecs), dtype=np.float32)
    mat = mat.reshape(len(vecs), -1).astype(np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
    sims = (mat / norms) @ qv
    top = np.argsort(-sims)[:limit]
    return [(vecs[i]["item_id"], float(sims[i])) for i in top]


# ---------------- RRF 融合 + rerank ----------------

def _rrf(rankings: list[list[tuple[int, float]]], k: float = 60.0) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion：多路召回合流。"""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for pos, (iid, _s) in enumerate(ranking):
            scores[iid] = scores.get(iid, 0.0) + 1.0 / (k + pos + 1)
    return sorted(scores.items(), key=lambda x: -x[1])


def search(conn: sqlite3.Connection, query: str, kinds: tuple[str, ...] = ("job",),
           limit: int = 12) -> list[dict]:
    """混合检索主入口。返回行 dict（含 _score/_sources），按最终分排序。"""
    settings = get_settings()
    use_vec = bool(settings.retrieval_cfg.get("vector", True)) and \
        settings.embedding_ready()
    use_rr = bool(settings.retrieval_cfg.get("rerank", True)) and \
        settings.rerank_ready()
    rows_map: dict[int, dict] = {}
    all_rankings: list[list[tuple[int, float]]] = []

    for kind in kinds:
        fts = fts_recall(conn, query, "job" if kind == "job" else "news", limit=30)
        if fts:
            all_rankings.append([i for i in (fts)])
            for iid, _s in fts:
                if iid not in rows_map:
                    row = _load_row(conn, kind, iid)
                    if row:
                        rows_map[iid] = row
        if use_vec:
            vr = vector_recall(conn, query, "job" if kind == "job" else "news", limit=30)
            if vr:
                all_rankings.append(vr)
                for iid, _s in vr:
                    if iid not in rows_map:
                        row = _load_row(conn, kind, iid)
                        if row:
                            rows_map[iid] = row
    if not all_rankings:
        return []
    fused = _rrf(all_rankings)[:48]

    # rerank 精排（对 top 候选）
    if use_rr and fused:
        try:
            rr = RerankClient(settings, conn)
            docs = [_item_text(rows_map[iid]) for iid, _ in fused if iid in rows_map]
            ids = [iid for iid, _ in fused if iid in rows_map]
            reranked = rr.rerank(query, docs, top_k=len(docs))
            order = {ids[idx]: score for idx, score in reranked}
            maxs = max(order.values()) or 1.0
            final = sorted(((iid, order.get(iid, 0.0) / maxs) for iid, _ in fused),
                          key=lambda x: -x[1])
        except LLMError:
            final = fused
    else:
        final = fused

    # gold × 新鲜度微调（高含金量优先，同分靠新）
    out: list[dict] = []
    maxf = final[0][1] or 1.0
    for iid, s in final[:limit]:
        row = rows_map.get(iid)
        if not row:
            continue
        gold = (row.get("gold_score") or 50.0) / 100.0
        row["_score"] = round(0.7 * (s / maxf) + 0.3 * gold, 4)
        out.append(row)
    out.sort(key=lambda r: -r["_score"])
    return out


def _load_row(conn, kind: str, iid: int) -> dict | None:
    if kind == "job":
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (iid,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM news WHERE id=?", (iid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if kind != "job":
        d["key_points"] = json.loads(d.get("key_points") or "[]")
    d["_kind"] = "job" if kind == "job" else ("tech" if d.get("kind") == "tech" else "news")
    return d


# ---------------- 增量向量化（精选层） ----------------

def ensure_vectors(conn: sqlite3.Connection, progress=None) -> dict:
    """精选层增量向量化：只嵌新/变更行；模型变更自动失效重建。"""
    settings = get_settings()
    if not settings.embedding_ready():
        return {"skipped": "embedding 未配置"}
    embedder = EmbeddingClient(settings, conn)
    model = embedder.cand.model
    stats = {"embedded": 0, "skipped": 0}
    jobs = [dict(r) for r in conn.execute(
        "SELECT id, title, company, city, skills_text, description FROM jobs "
        "WHERE gold_score IS NOT NULL").fetchall()]
    news = [dict(r) for r in conn.execute(
        "SELECT id, title, one_liner, fulltext FROM news WHERE gold_score IS NOT NULL"
    ).fetchall()]
    import struct
    todo: list[tuple[str, int, str, str]] = []
    for j in jobs:
        text = _item_text({**j, "salary_raw": ""})
        h = _text_hash(text)
        if db.vector_needs_update(conn, "job", j["id"], model, h):
            todo.append(("job", j["id"], text, h))
        else:
            stats["skipped"] += 1
    for n in news:
        n.setdefault("key_points", [])
        text = _item_text(n)
        h = _text_hash(text)
        if db.vector_needs_update(conn, "news", n["id"], model, h):
            todo.append(("news", n["id"], text, h))
        else:
            stats["skipped"] += 1
    for i in range(0, len(todo), embedder.batch):
        batch = todo[i:i + embedder.batch]
        try:
            vecs = embedder.embed([t[2] for t in batch])
        except LLMError:
            return {**stats, "error": "embedding 调用失败（保留待重试）"}
        for (itype, iid, _text, h), v in zip(batch, vecs):
            dim = len(v)
            blob = struct.pack(f"{dim}f", *v)
            db.upsert_vector(conn, itype, iid, model, dim, blob, h)
            stats["embedded"] += 1
        if progress:
            progress(92 + min(7.0, i / max(len(todo), 1) * 7), "向量化",
                     f"嵌入 {min(i + embedder.batch, len(todo))}/{len(todo)}")
    # 模型变更：清掉旧模型向量
    conn.execute("DELETE FROM vectors WHERE model != ?", (model,))
    conn.commit()
    return stats
