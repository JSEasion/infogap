"""前沿技术采集（kind=tech）：arXiv / HF Daily Papers / GitHub Trending / 技术雷达。

对上「技术迭代」信息差：新技术在论文/开源仓库里冒头到进入 JD，
通常有 6-18 个月窗口——在这里提前看到，就是求职市场的先手。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup

UA = {"User-Agent": "infogap/0.1 (personal research)"}


# ---------------- arXiv（官方 Atom API） ----------------

def fetch_arxiv(max_items: int = 20) -> list[dict]:
    """cs.AI / cs.CL / cs.LG 当日新论文。"""
    import feedparser
    q = "cat:cs.AI OR cat:cs.CL OR cat:cs.LG"
    try:
        with httpx.Client(timeout=30, headers=UA) as c:
            r = c.get("https://export.arxiv.org/api/query",
                      params={"search_query": q, "sortBy": "submittedDate",
                              "sortOrder": "descending", "max_results": max_items})
            r.raise_for_status()
        feed = feedparser.parse(r.text)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in feed.entries[:max_items]:
        summary = re.sub(r"\s+", " ", e.get("summary", "")).strip()
        out.append({
            "kind": "tech", "source": "arxiv",
            "source_url": next((l.href for l in e.get("links", []) if l.get("type") == "text/html"),
                               e.get("link", "")),
            "title": e.get("title", "").strip()[:200],
            "body": f"{e.get('title','')}\n{summary}"[:3000],
            "published_at": e.get("published", ""),
            "channel_meta": {"official": True},
        })
    return out


# ---------------- Hugging Face Daily Papers ----------------

def fetch_hf_papers(max_items: int = 15) -> list[dict]:
    try:
        with httpx.Client(timeout=20, headers=UA) as c:
            r = c.get("https://huggingface.co/api/daily-papers")
            r.raise_for_status()
            papers = r.json() or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for p in papers[:max_items]:
        paper = p.get("paper") or {}
        pid = paper.get("id", "") or paper.get("arxiv_id", "")
        out.append({
            "kind": "tech", "source": "hfpapers",
            "source_url": f"https://huggingface.co/papers/{pid}" if pid else "",
            "title": (paper.get("title") or "").strip()[:200],
            "body": f"{paper.get('title','')}\n👍{p.get('paper', {}).get('upvotes', '?')}\n"
                    f"{(paper.get('summary') or '')[:1500]}",
            "published_at": p.get("publishedAt", ""),
            "channel_meta": {"upvotes": p.get("paper", {}).get("upvotes", 0)},
        })
    return out


# ---------------- GitHub Trending（页解析，无官方 API） ----------------

def fetch_github_trending(lang: str = "python", max_items: int = 15) -> list[dict]:
    try:
        with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as c:
            r = c.get(f"https://github.com/trending/{lang}?since=daily")
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:  # noqa: BLE001
        return []
    out = []
    today = datetime.utcnow().strftime("%Y-%m-%d")
    for row in soup.select("article.Box-row")[:max_items]:
        a = row.select_one("h2 a")
        if not a:
            continue
        href = a.get("href", "")
        name = href.strip("/")
        desc_node = row.select_one("p")
        desc = desc_node.get_text(strip=True) if desc_node else ""
        stars_node = row.select_one("a[href$='/stargazers']")
        stars = stars_node.get_text(strip=True) if stars_node else ""
        lang_node = row.select_one("[itemprop='programmingLanguage']")
        lang_txt = lang_node.get_text(strip=True) if lang_node else lang
        out.append({
            "kind": "tech", "source": "ghtrend",
            "source_url": f"https://github.com{name}",
            "title": f"[trending:{lang_txt}] {name}",
            "body": f"{name}（{lang_txt}，⭐{stars}）\n{desc}"[:1500],
            "published_at": today,
            "channel_meta": {"trending": True},
        })
    return out


# ---------------- ThoughtWorks 技术雷达 ----------------

def fetch_radar() -> list[dict]:
    try:
        with httpx.Client(timeout=20, headers=UA) as c:
            r = c.get("https://www.thoughtworks.com/radar.json")
            r.raise_for_status()
            data = r.json() or {}
    except Exception:  # noqa: BLE001
        return []
    quadrant_names = {q.get("id"): q.get("name", "") for q in data.get("quadrants", [])}
    out = []
    for blip in data.get("blips", [])[:40]:
        out.append({
            "kind": "tech", "source": "radar",
            "source_url": blip.get("link", ""),
            "title": f"[雷达:{blip.get('ring','')}] {blip.get('name','')}",
            "body": f"{blip.get('name','')}：{blip.get('description','')}\n"
                    f"象限：{quadrant_names.get(blip.get('quadrant'), '?')}；环：{blip.get('ring','')}"[:1500],
            "published_at": "",
            "channel_meta": {"ring": blip.get("ring", "")},
        })
    return out


def collect() -> dict:
    """主入口：默认源 arXiv + GitHub Trending。
    hfpapers 端点 2026-11 起需登录（401）、radar.json 已下线（404）——保留函数但不进默认轮换。"""
    items: list[dict] = []
    errors: list[str] = []
    for name, fn in [("arxiv", fetch_arxiv), ("ghtrend", fetch_github_trending)]:
        got = fn()
        if got:
            items += got
        else:
            errors.append(f"techfront:{name} 无数据")
        time.sleep(1.0)
    return {"items": items, "errors": errors}
