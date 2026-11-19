"""真实数据渠道（全部公开接口，已逐个实测可用，无需付费 key）：

- v2ex    V2EX 酷工作（中文，Atom RSS）        https://www.v2ex.com/feed/jobs.xml
- eleduck 电鸭社区·远程工作（中文，公开 API）    https://svc.eleduck.com/api/v1/posts?category=5
- hn      Hacker News 月度 Who is Hiring（Algolia API，英文/国际远程）
- remotive Remotive 远程职位（公开 API）         https://remotive.com/api/remote-jobs
- remoteok RemoteOK 远程职位（公开 API）         https://remoteok.com/api
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import httpx

from .. import db
from ..extract import structure

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) market-lens/0.1 (personal research)"}

CHANNELS = {
    "v2ex": "V2EX 酷工作（中文开发者社区招聘）",
    "eleduck": "电鸭社区（中文远程工作）",
    "hn": "Hacker News · 当月 Who is Hiring（国际/远程）",
    "remotive": "Remotive（国际远程职位库）",
    "remoteok": "RemoteOK（国际远程职位库）",
    "gaoxiaojob": "高校人才网（央国企/事业单位/高校科研/军队文职，官方公开无反爬）",
    "iguopin": "国聘·央企国企官方招聘平台（官方API直连）",
    "gxannounce": "事业单位/高校编制招聘公告流（双一流/本科院校/党校/博士引进）",
}

# 英文 JD 相关性过滤（hn/remoteok 全量太大，先按关键词筛）
_EN_KEEP = re.compile(r"ai|ml|llm|agent|machine learning|data|python|backend|engineer|developer|gpt|rag", re.I)


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"[ \t]+", " ", text)).strip()


def _client() -> httpx.Client:
    return httpx.Client(timeout=25, follow_redirects=True, headers=UA)


def fetch_v2ex() -> list[str]:
    with _client() as c:
        feed = c.get("https://www.v2ex.com/feed/jobs.xml").text
    import feedparser
    parsed = feedparser.parse(feed)
    out = []
    for e in parsed.entries[:25]:
        title = _strip_html(e.get("title", ""))
        summary = _strip_html(e.get("summary", ""))[:1800]
        link = e.get("link", "")
        if title:
            out.append(f"{title}\n{summary}\n来源:{link}")
    return out


def fetch_eleduck(pages: int = 3) -> list[str]:
    out = []
    with _client() as c:
        for page in range(1, pages + 1):
            try:
                r = c.get(f"https://svc.eleduck.com/api/v1/posts?category=5&page={page}")
                posts = (r.json() or {}).get("posts") or []
            except Exception:  # noqa: BLE001
                break
            if not posts:
                break
            for p in posts:
                pid = p.get("id", "")
                title = _strip_html(p.get("title", ""))
                summary = _strip_html(p.get("summary", ""))[:1800]
                if title:
                    out.append(f"{title}\n{summary}\n来源:https://eleduck.com/posts/{pid}")
    return out


def _latest_hiring_thread_id() -> str | None:
    with _client() as c:
        r = c.get("https://hn.algolia.com/api/v1/search_by_date",
                  params={"query": "Ask HN: Who is hiring", "tags": "story", "hitsPerPage": 5})
    for h in r.json().get("hits", []):
        if re.match(r"Ask HN: Who is hiring", h.get("title", ""), re.I):
            return h["objectID"]
    return None


def fetch_hn(max_items: int = 80) -> list[str]:
    sid = _latest_hiring_thread_id()
    if not sid:
        return []
    with _client() as c:
        item = c.get(f"https://hn.algolia.com/api/v1/items/{sid}").json()
    out = []
    for child in item.get("children", []):
        text = _strip_html(child.get("text") or "")
        if len(text) < 120 or not _EN_KEEP.search(text):
            continue
        head = text.split("\n", 1)[0][:60]
        out.append(f"{head}\n{text[:2200]}\n来源:https://news.ycombinator.com/item?id={child.get('id','')}")
        if len(out) >= max_items:
            break
    return out


def fetch_remotive(searches: tuple[str, ...] = ("ai agent", "llm", "machine learning")) -> list[str]:
    out, seen = [], set()
    with _client() as c:
        for q in searches:
            try:
                r = c.get("https://remotive.com/api/remote-jobs",
                          params={"search": q, "limit": 30})
                jobs = (r.json() or {}).get("jobs") or []
            except Exception:  # noqa: BLE001
                continue
            for j in jobs:
                url = j.get("url") or j.get("id")
                if url in seen:
                    continue
                seen.add(url)
                salary = j.get("salary") or ""
                loc = j.get("candidate_required_location") or ""
                comp = j.get("company_name") or ""
                desc = _strip_html(j.get("description") or "")[:2200]
                out.append(f"{j.get('title','')}\n公司:{comp}  地点:{loc}  薪资:{salary}\n{desc}\n来源:{url}")
    return out[:60]


def fetch_remoteok(max_items: int = 50) -> list[str]:
    with _client() as c:
        r = c.get("https://remoteok.com/api")
    data = r.json()
    out = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        blob = " ".join([j.get("position", ""), _strip_html(j.get("description") or "")[:400]])
        if not _EN_KEEP.search(blob):
            continue
        salary = j.get("salary_min") or ""
        if salary:
            salary = f"${salary}+"
        meta = "  ".join(x for x in [f"公司:{j.get('company','')}" if j.get('company') else "",
                                     f"地点:{j.get('location','')}" if j.get('location') else "",
                                     f"薪资:{salary}" if salary else ""] if x)
        out.append(f"{j.get('position','')}\n{meta}\n"
                   f"{_strip_html(j.get('description') or '')[:2200]}\n来源:{j.get('url','')}")
        if len(out) >= max_items:
            break
    return out


def _fetch_gaoxiaojob():
    from .soe import fetch_gaoxiaojob as _f
    return _f()


def _fetch_iguopin():
    from .soe import fetch_iguopin as _f
    return _f()


def _fetch_gxannounce():
    from .soe import fetch_gxannounce as _f
    return _f()


FETCHERS = {"v2ex": fetch_v2ex, "eleduck": fetch_eleduck, "hn": fetch_hn,
            "remotive": fetch_remotive, "remoteok": fetch_remoteok,
            "gaoxiaojob": _fetch_gaoxiaojob, "iguopin": _fetch_iguopin,
            "gxannounce": _fetch_gxannounce}


def collect(conn: sqlite3.Connection, gateway, channels: list[str] | None = None) -> dict:
    """规则模式秒级入库（真实数据立即可见），返回新增岗位 id 列表供后台 LLM 增强。"""
    channels = [c for c in (channels or list(FETCHERS)) if c in FETCHERS]
    result = {"channels": {}, "new_ids": [], "errors": []}
    for ch in channels:
        try:
            texts = FETCHERS[ch]()
            s = structure(conn, texts, source=ch, gateway=None, use_llm=False)
            ids = [row["id"] for row in conn.execute(
                "SELECT id FROM jobs WHERE source=? ORDER BY id DESC LIMIT ?", (ch, s.added))]
            result["channels"][ch] = {"fetched": s.total, "added": s.added, "dup": s.duplicates}
            result["new_ids"] += ids
        except Exception as e:  # noqa: BLE001 单渠道失败不影响其他
            result["errors"].append(f"{ch}: {type(e).__name__}: {e}")
    return result


def _enrich_one(db_path, gateway, job_id: int, text: str) -> bool:
    """单条 LLM 增强并回写（工作线程内用独立连接）。"""
    from ..extract import extract_llm
    from ..salary import parse_salary
    from ..taxonomy import skills_text as fmt_skills
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        outs = extract_llm(gateway, [text])
        llm = outs[0]
        if not llm:
            return False
        # 薪资必须「原文可溯」：salary_raw 与四个数值字段同进退，
        # 否则会出现 raw 被置空而旧数值残留的「有数无据」行（多岗位页张冠李戴的根源之一）
        raw = (llm.get("salary_raw") or "").strip()
        sal = parse_salary(raw) if raw else None
        if sal:
            salary_vals = (raw, sal.min_k, sal.max_k, sal.months,
                           sal.annual_min_wan, sal.annual_max_wan)
        else:  # LLM 未给出可解析薪资 → 保留入库时规则层的结果（raw 与数值一起保留）
            old = conn.execute(
                "SELECT salary_raw, salary_min_k, salary_max_k, salary_months,"
                " annual_min_wan, annual_max_wan FROM jobs WHERE id=?", (job_id,)).fetchone()
            salary_vals = tuple(old) if old else ("", None, None, 12, None, None)
        conn.execute(
            "UPDATE jobs SET title=?, company=?, city=?,"
            " salary_raw=?, salary_min_k=?, salary_max_k=?, salary_months=?,"
            " annual_min_wan=?, annual_max_wan=?,"
            " experience=?, education=?,"
            " work_mode=?, company_type=?, skills=?, skills_text=?, description_zh=?,"
            " extract_method='hybrid', structured_ok=1 WHERE id=?",
            (llm["title"], llm["company"], llm["city"],
             *salary_vals,
             llm["experience"], llm["education"], llm["work_mode"] or "unknown",
             llm["company_type"] or "unknown",
             json.dumps(llm["skills"], ensure_ascii=False),
             fmt_skills(llm["skills"]), llm.get("desc_zh", ""), job_id))
        conn.commit()
        return True
    finally:
        conn.close()


def enrich_background(db_path, gateway, job_ids: list[int], workers: int = 4) -> dict:
    """后台并行 LLM 增强指定岗位（每线程独立 sqlite 连接与独立网关）。"""
    from ..config import get_settings
    from ..gateway import LLMGateway
    done = 0
    if not job_ids:
        return {"done": 0, "total": 0}
    texts = {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for jid in job_ids:
        row = conn.execute("SELECT description FROM jobs WHERE id=?", (jid,)).fetchone()
        if row:
            texts[jid] = row["description"]
    conn.close()

    def work(item):
        jid, text = item
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        gw = LLMGateway(get_settings(), conn)
        try:
            return _enrich_one(db_path, gw, jid, text)
        except Exception:  # noqa: BLE001
            return False
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, it): it for it in texts.items()}
        for fut in as_completed(futures):
            try:
                done += 1 if fut.result() else 0
            except Exception:  # noqa: BLE001
                pass
    return {"done": done, "total": len(texts)}
