"""圈内垂直板块 + 阮一峰「谁在招人」栏目（信息差组）。

这些板块在各自技术圈内口碑传播、圈外鲜为人知：
- jobs.rust-lang.org（Rust 官方职位板，RSS）
- Ruby China /jobs（Ruby 圈，HTML）
- Elixir Forum jobs（Discourse 分类 RSS）
- 阮一峰科技周刊「谁在招人」栏目（已有 RSS，抽取该栏目段落）

清单 config channels.niche_boards 可扩展。单板失败不影响其他。
"""
from __future__ import annotations

import re
import time

import httpx
from bs4 import BeautifulSoup

from ..textfmt import normalize_text

UA = {"User-Agent": "infogap/0.1 (personal research)"}


# ---------------- 通用 RSS 板块 ----------------

def fetch_board_rss(name: str, url: str, limit: int = 25) -> list[dict]:
    try:
        import feedparser
        with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as c:
            r = c.get(url)
            r.raise_for_status()
        feed = feedparser.parse(r.text)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in feed.entries[:limit]:
        summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
        summary = re.sub(r"\s+", " ", summary).strip()
        out.append({
            "kind": "job", "source": f"niche:{name}",
            "source_url": e.get("link", ""),
            "title": e.get("title", "").strip()[:120],
            "body": f"{e.get('title','')}\n{summary}"[:3000],
            "published_at": e.get("published", "") or e.get("updated", ""),
            "channel_meta": {},
        })
    return out


# ---------------- Ruby China（HTML 板块） ----------------

def fetch_ruby_china(limit: int = 25) -> list[dict]:
    try:
        with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as c:
            r = c.get("https://ruby-china.org/jobs")
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:  # noqa: BLE001
        return []
    out = []
    for node in soup.select("div.topic")[:limit]:   # class="topic media topic-xxxx"
        a = node.select_one("a.title, a.topic-link, a[href^='/topics/']")
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a.get("href", "")
        if href.startswith("/"):
            href = "https://ruby-china.org" + href
        if not title:
            continue
        out.append({
            "kind": "job", "source": "niche:ruby-china",
            "source_url": href, "title": title[:120], "body": title,
            "published_at": "", "channel_meta": {},
        })
    return out


# ---------------- 阮一峰周刊「谁在招人」栏目 ----------------

_WHO_HIRING = re.compile(r"谁在招人")
_H2_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)


def extract_who_is_hiring(entry_html: str, entry_url: str = "") -> list[dict]:
    """从周刊条目 HTML 中抽取「谁在招人」栏目的招聘信息（取该 h2 到下一个 h2 之间的内容）。"""
    if not entry_html or not _WHO_HIRING.search(entry_html):
        return []
    h2s = list(_H2_RE.finditer(entry_html))
    hiring_html = ""
    for idx, m in enumerate(h2s):
        if _WHO_HIRING.search(m.group(1)):
            start = m.end()
            end = h2s[idx + 1].start() if idx + 1 < len(h2s) else len(entry_html)
            hiring_html = entry_html[start:end]
            break
    if not hiring_html:
        return []
    text = normalize_text(re.sub(r"<[^>]+>", "\n", hiring_html))
    items = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 10:
            continue
        m = re.match(r"^(.{2,25})[:：]\s*(.{8,300})$", line)
        if m:
            title, body = m.group(1).strip()[:80], line[:600]
        elif re.search(r"招聘|内推|实习|hiring|工程师|岗", line) and len(line) >= 12:
            title, body = line[:40], line[:600]
        else:
            continue
        items.append({
            "kind": "job", "source": "ruanyf",
            "source_url": entry_url,
            "title": title,
            "body": body,
            "published_at": "", "channel_meta": {"column": "谁在招人"},
        })
    return items[:30]


def fetch_ruanyf_hiring(limit_comments: int = 25) -> list[dict]:
    """阮一峰《谁在招人》：企业免费发布、阮一峰人工审核的招聘栏目。
    每期是 ruanyf/weekly 仓库的一个 Issue，评论区即岗位列表（圈内口碑渠道）。"""
    try:
        with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as c:
            # 仓库 issue 太活跃（自荐帖刷屏），列表翻页到不了栏目帖——用 search 定位最新一期
            r = c.get("https://api.github.com/search/issues",
                      params={"q": "repo:ruanyf/weekly 谁在招人", "sort": "created",
                              "order": "desc", "per_page": 5})
            r.raise_for_status()
            items = (r.json() or {}).get("items") or []
            target = next((it for it in items
                           if "谁在招人" in (it.get("title") or "")), None)
            if not target:
                return []
            comments = c.get(target["comments_url"],
                             params={"per_page": limit_comments})
            comments.raise_for_status()
            data = comments.json()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for cm in data if isinstance(data, list) else []:
        body = cm.get("body") or ""
        first = body.split("\n", 1)[0].strip()[:80]
        if len(body) < 30 or not re.search(r"招聘|岗位|工程师|内推|实习|简历|job", body, re.I):
            continue
        out.append({
            "kind": "job", "source": "ruanyf",
            "source_url": target.get("html_url", ""),
            "title": first or "《谁在招人》岗位",
            "body": body[:4000],
            "published_at": cm.get("created_at", ""),
            "channel_meta": {"column": "谁在招人", "issue": target.get("number")},
        })
    return out


# ---------------- 汇总 ----------------

# 默认 RSS 板块（2026-11 实测：rust-jobs 域名失效、elixirforum rss 路径 404，均已移除；
# 更多板块走 config channels.niche_boards 追加：{name, url})
_DEFAULT_RSS_BOARDS: list[dict] = []


def collect() -> dict:
    items: list[dict] = []
    errors: list[str] = []
    for board in _DEFAULT_RSS_BOARDS:
        got = fetch_board_rss(board["name"], board["url"])
        if got:
            items += got
        else:
            errors.append(f"niche:{board['name']} 无数据")
        time.sleep(1.0)
    got = fetch_ruby_china()
    if got:
        items += got
    else:
        errors.append("niche:ruby-china 无数据")
    time.sleep(1.0)
    got = fetch_ruanyf_hiring()
    if got:
        items += got
    else:
        errors.append("ruanyf:近期期刊无「谁在招人」栏目")
    return {"items": items, "errors": errors}
