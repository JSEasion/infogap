"""搜索 API 采集：Tavily / 博查（bocha），把搜索结果作为 JD 线索结构化入库。"""
from __future__ import annotations

import os
import sqlite3

import httpx

from ..config import Settings, get_settings
from ..extract import IngestSummary, structure


def _tavily(key: str, query: str, k: int) -> list[dict]:
    r = httpx.post("https://api.tavily.com/search", timeout=30, json={
        "api_key": key, "query": query, "max_results": k, "search_depth": "basic"})
    r.raise_for_status()
    return [{"title": h.get("title", ""), "url": h.get("url", ""),
             "snippet": h.get("content", "")[:1500]} for h in r.json().get("results", [])]


def _bocha(key: str, query: str, k: int) -> list[dict]:
    r = httpx.post("https://api.bochaai.com/v1/web-search", timeout=30,
                   headers={"Authorization": f"Bearer {key}"},
                   json={"query": query, "count": k, "summary": True})
    r.raise_for_status()
    pages = (r.json().get("data") or {}).get("webPages") or {}
    return [{"title": h.get("name", ""), "url": h.get("url", ""),
             "snippet": (h.get("summary") or h.get("snippet") or "")[:1500]}
            for h in pages.get("value", [])]


def available(settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    if os.environ.get(settings.search.get("tavily_key_env", "TAVILY_API_KEY"), "").strip():
        return "tavily"
    if os.environ.get(settings.search.get("bocha_key_env", "BOCHA_API_KEY"), "").strip():
        return "bocha"
    return None


def search(provider: str, query: str, k: int = 10) -> list[dict]:
    settings = get_settings()
    if provider == "tavily":
        return _tavily(os.environ[settings.search.get("tavily_key_env", "TAVILY_API_KEY")], query, k)
    return _bocha(os.environ[settings.search.get("bocha_key_env", "BOCHA_API_KEY")], query, k)


def collect(conn: sqlite3.Connection, gateway, queries: list[str] | None = None,
            settings: Settings | None = None) -> IngestSummary:
    """搜索 → 结果页标题+摘要 作为 JD 线索 → 结构化入库（source=search）。"""
    settings = settings or get_settings()
    provider = available(settings)
    if not provider:
        s = IngestSummary()
        s.errors.append("未配置搜索 API key（.env 里设 TAVILY_API_KEY 或 BOCHA_API_KEY）")
        return s
    queries = queries or settings.search.get("queries") or ["AI Agent 开发工程师 招聘"]
    k = int(settings.search.get("max_results", 10))
    texts: list[str] = []
    for q in queries:
        try:
            for hit in search(provider, q, k):
                if hit["title"] and hit["snippet"]:
                    texts.append(f"{hit['title']}\n{hit['snippet']}\n来源:{hit['url']}")
        except Exception as e:  # noqa: BLE001
            pass
    return structure(conn, texts, source="search", gateway=gateway)
