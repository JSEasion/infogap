"""RSS 资讯采集：feedparser + httpx，完全合规的公开源。

新架构：fetch_rss_items() 返回 RawItem 列表（走 raw→质量门→信号卡链路）。
fetch_rss() 保留为兼容入口（直写 news，不推荐）。
"""
from __future__ import annotations

import html
import re
import sqlite3

import feedparser
import httpx

from .. import db
from ..config import Settings
from ..textfmt import clean_inline

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) xinxicha/0.1 (personal research)"


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def fetch_rss_items(settings: Settings) -> dict:
    """采集全部 RSS 源 → RawItem 列表（kind=news）。"""
    result: dict = {"items": [], "sources": {}, "errors": []}
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": UA}) as client:
        for src in settings.rss_sources:
            name, url = src.get("name", "?"), src.get("url", "")
            if not url:
                continue
            try:
                resp = client.get(url)
                resp.raise_for_status()
                feed = feedparser.parse(resp.text)
                got = 0
                for entry in feed.entries[:30]:
                    summary = _strip_html(entry.get("summary", ""))[:600]
                    result["items"].append({
                        "kind": "news", "source": f"rss:{name}",
                        "source_url": entry.get("link", ""),
                        "title": clean_inline(entry.get("title", ""), limit=200),
                        "body": summary,
                        "published_at": entry.get("published", "") or entry.get("updated", ""),
                        "channel_meta": {"feed": name},
                    })
                    got += 1
                result["sources"][name] = got
            except Exception as e:  # noqa: BLE001 单源失败不影响其他源
                result["errors"].append(f"{name}: {type(e).__name__}: {e}")
    return result


def fetch_rss(conn: sqlite3.Connection, settings: Settings) -> dict:
    """兼容入口：直写 news 表（无信号卡链路，仅旧路由过渡用）。"""
    r = fetch_rss_items(settings)
    added = sum(db.upsert_news(conn, it) for it in r["items"])
    return {"added": added, "sources": r["sources"], "errors": r["errors"]}
