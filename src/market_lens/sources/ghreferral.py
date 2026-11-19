"""GitHub 内推仓库采集：Search API 发现活跃内推仓库 + Issues API 拉取内推条目。

社区惯例：员工把内推码/岗位发在年度内推仓库的 Issues 里（如 Wsparer 系列年度仓库）。
这些仓库公网搜索权重低，但信息真实（有 GitHub 身份背书）、时效强。

可信度信号：仓库 star 数、最近 push 时间 → 进 channel_meta，
quality 层可据此对 gh: 渠道细分调 tier。
"""
from __future__ import annotations

import re
import time

import httpx

UA = {"User-Agent": "infogap/0.1 (personal research)"}
API = "https://api.github.com"

# 内推条目相关性（AI/研发方向）
_KEEP = re.compile(r"内推|招聘|校招|社招|实习|岗位|referral|hiring", re.I)
_AI_KEEP = re.compile(r"AI|LLM|Agent|大模型|算法|机器学习|后端|前端|全栈|开发|研发|"
                      r"engineer|developer|backend|frontend|infra|数据", re.I)


def _headers() -> dict:
    import os
    h = dict(UA)
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        h["Authorization"] = f"Bearer {tok}"   # 可选：配置后限速从 60/h 提到 5000/h
    h["Accept"] = "application/vnd.github+json"
    return h


def find_active_repos(queries: list[str], max_repos: int = 8) -> list[dict]:
    """按相关度搜索内推仓库（默认排序=best match；按 updated 会捞到垃圾仓库）。
    活跃判定：近 120 天有 push，或 star≥200 的口碑仓库。"""
    out: list[dict] = []
    seen: set[str] = set()
    with httpx.Client(timeout=20, headers=_headers()) as c:
        for q in queries:
            try:
                r = c.get(f"{API}/search/repositories",
                          params={"q": q, "per_page": 10})
                r.raise_for_status()
                for repo in (r.json() or {}).get("items", []):
                    full = repo.get("full_name", "")
                    if full in seen:
                        continue
                    pushed = repo.get("pushed_at") or ""
                    if pushed < "2000-01-01":
                        continue
                    seen.add(full)
                    out.append({"full_name": full, "stars": repo.get("stargazers_count", 0),
                                "pushed_at": pushed, "url": repo.get("html_url", "")})
            except Exception:  # noqa: BLE001
                continue
            time.sleep(6.5)   # 未鉴权 search 限速 10/min
            if len(out) >= max_repos * 2:
                break
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 120 * 86400))
    fresh = [r for r in out if r["pushed_at"] >= cutoff and r["stars"] >= 5]
    known = [r for r in out if r["stars"] >= 200]     # 口碑仓库即使停更也留观察
    merged: list[dict] = []
    seen2: set[str] = set()
    for r in fresh + known:
        if r["full_name"] not in seen2:
            seen2.add(r["full_name"])
            merged.append(r)
    return merged[:max_repos]


def fetch_repo_issues(repo: dict, limit: int = 20) -> list[dict]:
    """拉取仓库开放 Issues 中的内推条目 → RawItem 列表。"""
    with httpx.Client(timeout=20, headers=_headers()) as c:
        try:
            r = c.get(f"{API}/repos/{repo['full_name']}/issues",
                      params={"state": "open", "per_page": limit, "sort": "updated",
                              "direction": "desc"})
            r.raise_for_status()
            issues = r.json()
        except Exception:  # noqa: BLE001
            return []
    out: list[dict] = []
    for it in issues if isinstance(issues, list) else []:
        if "pull_request" in it:
            continue
        title = it.get("title") or ""
        body = it.get("body") or ""
        if not _KEEP.search(title + body):
            continue
        text = f"{title}\n{body}"
        out.append({
            "kind": "job",
            "source": f"gh:{repo['full_name']}",
            "source_url": it.get("html_url", ""),
            "title": title[:100],
            "body": text[:6000],
            "published_at": it.get("created_at", ""),
            "channel_meta": {"repo_stars": repo["stars"], "repo_pushed": repo["pushed_at"]},
        })
    return out


def fetch_repo_readme(repo: dict, limit: int = 20) -> list[dict]:
    """README 里的内推信息（大量内推仓库把岗位表写在 README 而非 Issues）。"""
    import base64
    with httpx.Client(timeout=20, headers=_headers()) as c:
        try:
            r = c.get(f"{API}/repos/{repo['full_name']}/readme")
            r.raise_for_status()
            text = base64.b64decode(r.json().get("content", "")).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            return []
    text = re.sub(r"<[^>]+>", " ", text)
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip().strip("|").strip()
        if len(line) < 12 or not _KEEP.search(line):
            continue
        out.append({
            "kind": "job",
            "source": f"gh:{repo['full_name']}",
            "source_url": repo["url"],
            "title": re.sub(r"[*#\[\]`]", "", line)[:80],
            "body": line[:1200],
            "published_at": repo["pushed_at"],
            "channel_meta": {"repo_stars": repo["stars"], "from": "readme"},
        })
        if len(out) >= limit:
            break
    return out


def collect(queries: list[str] | None = None, max_repos: int = 8) -> dict:
    """主入口：发现仓库 → 拉 Issues + README。返回 {items, repos, errors}。"""
    from ..config import get_settings
    cfg = get_settings().channels_cfg.get("github_referral") or {}
    queries = queries or cfg.get("queries") or ["内推", "校招 内推"]
    max_repos = max_repos or cfg.get("max_repos", 8)
    repos = find_active_repos(queries, max_repos)
    items: list[dict] = []
    for repo in repos:
        items += fetch_repo_issues(repo)
        items += fetch_repo_readme(repo, limit=12)
        time.sleep(1.5)
    # 相关性二次过滤（保留 AI/研发向）
    items = [i for i in items if _AI_KEEP.search(i["body"] + i["title"])][:80]
    return {"items": items, "repos": repos, "errors": []}
