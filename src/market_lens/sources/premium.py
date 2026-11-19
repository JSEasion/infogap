"""官方直连招聘板：Greenhouse / Lever 公开 API（tier=1.0，含金量最高）。

顶级 AI 公司普遍用 Greenhouse/Lever 托管官方招聘站，API 完全公开无需鉴权：
- Greenhouse: https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
- Lever:      https://api.lever.co/v0/postings/{company}?mode=json

公司清单在 config channels.premium_boards 配置（设置页可改）。
404 的 board 自动跳过（公司迁移招聘系统属常态）。
"""
from __future__ import annotations

import time

import httpx

from ..news_pipeline import extract_fulltext

UA = {"User-Agent": "xinxicha/0.1 (personal research)"}

# 默认 boards（2026-11 实测可用；公司迁移招聘系统属常态，404 自动跳过并记录）
_DEFAULT_GREENHOUSE = ["anthropic", "xai", "scaleai", "databricks", "gleanwork",
                       "imbue", "figma", "vercel"]
_DEFAULT_LEVER: list[str] = []   # lever 公开板实测均 404，留配置口子

_AI_KEEP = None  # 全量保留官方板岗位（官方源本身就是高信号）


def _gh_job_to_item(j: dict) -> dict:
    content = j.get("content") or ""
    body = extract_fulltext(content) or content.replace("<br>", "\n").replace("<p>", "\n")
    import re
    body = re.sub(r"<[^>]+>", " ", body)
    loc = (j.get("location") or {}).get("name", "")
    return {
        "kind": "job",
        "source": "greenhouse",
        "source_url": j.get("absolute_url", ""),
        "title": j.get("title", ""),
        "body": f"{j.get('title','')}\n公司:{(j.get('metadata') or {}) and ''}{loc}\n{body}"[:8000],
        "published_at": j.get("updated_at") or j.get("first_published", ""),
        "channel_meta": {"board": "greenhouse", "official": True},
    }


def fetch_greenhouse(board: str, limit: int = 40) -> list[dict]:
    try:
        with httpx.Client(timeout=20, headers=UA) as c:
            r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
                      params={"content": "true"})
            if r.status_code == 404:
                return []
            r.raise_for_status()
            jobs = (r.json() or {}).get("jobs") or []
    except Exception:  # noqa: BLE001
        return []
    out = [_gh_job_to_item(j) for j in jobs[:limit]]
    for it in out:
        it["source"] = f"greenhouse:{board}"
    return out


def fetch_lever(company: str, limit: int = 40) -> list[dict]:
    try:
        with httpx.Client(timeout=20, headers=UA) as c:
            r = c.get(f"https://api.lever.co/v0/postings/{company}",
                      params={"mode": "json"})
            if r.status_code == 404:
                return []
            r.raise_for_status()
            posts = r.json() or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for p in posts[:limit]:
        import re
        desc = p.get("text") or {}
        body = re.sub(r"<[^>]+>", " ", desc.get("description") or "")
        lists = " ".join(re.sub(r"<[^>]+>", " ", l.get("content") or "")
                         for l in desc.get("lists") or [])
        loc = (p.get("categories") or {}).get("location", "")
        out.append({
            "kind": "job",
            "source": f"lever:{company}",
            "source_url": p.get("hostedUrl", ""),
            "title": p.get("text", {}).get("title") or "",
            "body": f"{(desc.get('title') or '')}\n公司:{p.get('categories', {}).get('team','')}"
                    f"  地点:{loc}\n{body}\n{lists}"[:8000],
            "published_at": p.get("createdAt", ""),
            "channel_meta": {"board": "lever", "official": True},
        })
    return out


def collect(boards: dict | None = None) -> dict:
    """主入口：采集配置的全部官方板。返回 {items, boards_ok, errors}。"""
    from ..config import get_settings
    cfg = boards or get_settings().channels_cfg.get("premium_boards") or {}
    gh = cfg.get("greenhouse") or _DEFAULT_GREENHOUSE
    lv = cfg.get("lever") or _DEFAULT_LEVER
    items: list[dict] = []
    ok_boards: list[str] = []
    errors: list[str] = []
    for b in gh:
        got = fetch_greenhouse(b)
        if got:
            items += got
            ok_boards.append(f"greenhouse:{b}")
        else:
            errors.append(f"greenhouse:{b} 无数据（404 或迁移）")
        time.sleep(1.0)
    for b in lv:
        got = fetch_lever(b)
        if got:
            items += got
            ok_boards.append(f"lever:{b}")
        else:
            errors.append(f"lever:{b} 无数据（404 或迁移）")
        time.sleep(1.0)
    return {"items": items, "boards_ok": ok_boards, "errors": errors}
