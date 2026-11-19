"""retrieval：同义词扩展 / FTS 召回 / RRF 融合 / 降级链 / 增量向量化（mock embedding）。"""
import struct

import pytest

from market_lens import db, retrieval
from market_lens.news_pipeline import publish_news
from market_lens.models import NewsSignalCard


def _seed_jobs(conn):
    from market_lens.extract import structure
    structure(conn, [
        "AI Agent 开发工程师\n公司:甲智能\n城市:北京\n薪资:40-70K·15薪\n"
        "职责：负责大模型应用与 Agent 编排平台开发。要求 Python/LLM/RAG。",
        "后端开发工程师\n公司:乙网络\n城市:杭州\n薪资:25-40K\n"
        "职责：Java 微服务开发，维护电商交易链路。",
    ], "test", use_llm=False)
    for r in conn.execute("SELECT id FROM jobs"):
        conn.execute("UPDATE jobs SET gold_score=80 WHERE id=?", (r["id"],))
    conn.commit()


def test_expand_query_synonyms():
    terms = retrieval.expand_query("大模型 远程")
    assert "llm" in terms and "remote" in terms


def test_fts_recall(tmp_db):
    _seed_jobs(tmp_db)
    hits = retrieval.fts_recall(tmp_db, "Agent 编排")
    assert hits and hits[0][0] > 0
    # 同义词：搜「大模型」命中写 LLM 的行
    hits2 = retrieval.fts_recall(tmp_db, "大模型")
    assert hits2


def test_search_fts_only_degrades(tmp_db, monkeypatch):
    """embedding 未配置 → 纯 FTS 检索仍返回结果（降级链）。"""
    _seed_jobs(tmp_db)
    monkeypatch.setattr(retrieval, "vector_recall",
                        lambda *a, **k: pytest.fail("不应触向量"))
    rows = retrieval.search(tmp_db, "Agent 开发", kinds=("job",))
    assert rows and rows[0]["_kind"] == "job"
    assert rows[0]["_score"] > 0


def test_rrf_fusion():
    a = [(1, 0.9), (2, 0.8), (3, 0.7)]
    b = [(2, 0.95), (1, 0.9), (4, 0.5)]
    fused = retrieval._rrf([a, b])
    top_ids = [i for i, _ in fused[:2]]
    assert set(top_ids) == {1, 2}          # 两路都命中的排前


class _MockEmbedder:
    """确定性伪向量：词袋哈希 → 32 维，模拟 bge 语义相近。"""
    ready = True
    cand = type("C", (), {"model": "mock-bge"})()

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 32
            for ch in t:
                v[ord(ch) % 32] += 1.0
            out.append(v)
        return out


def test_vector_recall_and_hybrid(tmp_db, monkeypatch):
    _seed_jobs(tmp_db)
    # 预置向量（用同一 mock 语义）
    import market_lens.retrieval as R
    from market_lens.gateway import EmbeddingClient
    rows = tmp_db.execute("SELECT id, title, description FROM jobs").fetchall()
    for r in rows:
        text = f"{r['title']} {r['description']}"
        v = _MockEmbedder().embed([text])[0]
        blob = struct.pack(f"{len(v)}f", *v)
        db.upsert_vector(tmp_db, "job", r["id"], "mock-bge", len(v), blob,
                         R._text_hash(text))
    monkeypatch.setattr(EmbeddingClient, "__new__",
                        lambda cls, *a, **k: _MockEmbedder())
    hits = R.vector_recall(tmp_db, "AI Agent 开发工程师 大模型应用与 Agent 编排平台开发",
                           "job", embedder=_MockEmbedder())
    assert hits, "向量召回应命中"
    # 混合检索：mock embedder 注入
    monkeypatch.setattr(R, "vector_recall",
                        lambda conn, q, it="job", limit=30, embedder=None: hits)
    rows = R.search(tmp_db, "Agent 开发", kinds=("job",))
    assert rows


def test_ensure_vectors_skips_without_config(tmp_db, monkeypatch):
    _seed_jobs(tmp_db)
    class _Empty:
        ready = False
    import market_lens.retrieval as R
    from market_lens.gateway import EmbeddingClient
    monkeypatch.setattr(EmbeddingClient, "__new__", lambda cls, *a, **k: _Empty())
    out = R.ensure_vectors(tmp_db)
    assert "skipped" in out


def test_search_includes_news(tmp_db):
    card = NewsSignalCard(one_liner="智谱完成 30 亿元融资", signal_type="funding")
    publish_news(tmp_db, {"source": "rss:x", "title": "智谱融资", "source_url": "https://x/9",
                          "body": "智谱完成 30 亿元融资，用于大模型训练。", "published_at": ""},
                 card, 80, "{}")
    rows = retrieval.search(tmp_db, "融资 智谱", kinds=("news",))
    assert rows and rows[0]["_kind"] == "news"
