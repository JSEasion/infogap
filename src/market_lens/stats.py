"""统计层：行情聚合，纯规则零成本。"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter

from .taxonomy import skill_group

BUCKETS = [(0, 15, "<15K"), (15, 20, "15-20K"), (20, 25, "20-25K"),
           (25, 30, "25-30K"), (30, 40, "30-40K"), (40, 50, "40-50K"), (50, 10 ** 9, "50K+")]

# 公告/简章类聚合页（N 岗一帖）的薪资区间不代表任何单一岗位，排除出薪资统计
_ANNOUNCE_TITLE = re.compile(r"公告|简章|招贤|人才引进|统一招聘|招\d+岗")


def _mid(job: dict) -> float | None:
    lo, hi = job.get("salary_min_k"), job.get("salary_max_k")
    if lo is None and hi is None:
        return None
    if lo is None:
        return hi
    if hi is None:
        return lo
    return (lo + hi) / 2


def _pct(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    k = max(0, min(len(sorted_vals) - 1, round(p * (len(sorted_vals) - 1))))
    return round(sorted_vals[k], 1)


def exp_bucket(text: str) -> str:
    t = text or ""
    if re.search(r"应届|校招|实习", t):
        return "应届/校招"
    if re.search(r"10年|十年|10\+|10 年以上", t):
        return "10年以上"
    if re.search(r"5[-—~到至]?10|5年以上|经验5", t):
        return "5-10年"
    if re.search(r"3[-—~到至]?5|3年以上", t):
        return "3-5年"
    if re.search(r"1[-—~到至]?3|1年以上|经验不限|不限", t):
        return "1-3年/不限"
    if not t:
        return "未标注"
    return "其他"


def edu_bucket(text: str) -> str:
    t = text or ""
    if re.search(r"博士|phd", t, re.I):
        return "博士"
    if re.search(r"硕士|研究生|master", t, re.I):
        return "硕士优先"
    if re.search(r"本科|bachelor", t, re.I):
        return "本科"
    if re.search(r"大专|专科", t):
        return "大专"
    return "不限/未标注"


def _salary_rows(rows: list[dict]) -> list[dict]:
    """参与薪资统计的行：有数值、有 salary_raw 原文依据、且非公告类聚合页。"""
    return [r for r in rows
            if (r["salary_min_k"] is not None or r["salary_max_k"] is not None)
            and (r["salary_raw"] or "").strip()
            and r["source"] != "gxannounce"
            and not _ANNOUNCE_TITLE.search(r["title"] or "")]


def market_stats(conn: sqlite3.Connection, city: str = "") -> dict:
    where, vals = "1=1", []
    if city:
        where, vals = "city LIKE ?", [f"%{city}%"]
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM jobs WHERE {where}", vals).fetchall()]

    total = len(rows)
    companies = {r["company"] for r in rows if r["company"]}
    skill_counter: Counter[str] = Counter()
    for r in rows:
        for sid in json.loads(r["skills"] or "[]"):
            skill_counter[sid] += 1

    srows = _salary_rows(rows)
    mids = sorted(m for m in (_mid(r) for r in srows) if m is not None)
    salary_hist = [{"label": label, "count": 0} for *_unused, label in BUCKETS]
    for m in mids:
        for i, (lo, hi, label) in enumerate(BUCKETS):
            if lo <= m < hi:
                salary_hist[i]["count"] += 1
                break

    city_counter = Counter((r["city"] or "未知") for r in rows)
    by_city = []
    for c, n in city_counter.most_common(10):
        cm = sorted(m for r in srows if (r["city"] or "未知") == c
                    for m in [_mid(r)] if m is not None)
        by_city.append({"city": c, "n": n, "median": _pct(cm, 0.5)})

    wlb_counter = Counter()
    for r in rows:
        lvl = r["wlb_level"]
        wlb_counter["未知" if lvl is None else f"L{lvl}"] += 1

    def dist(counter: Counter) -> list[dict]:
        return [{"label": k, "n": n} for k, n in counter.most_common()]

    return {
        "total": total,
        "companies": len(companies),
        "skills_top": [
            {"id": sid, "group": skill_group(sid), "count": n, "pct": round(100 * n / total, 1)}
            for sid, n in skill_counter.most_common(20) if total],
        "salary_n": len(mids),
        "salary_hist": salary_hist,
        "salary_pct": {"p25": _pct(mids, 0.25), "p50": _pct(mids, 0.5), "p75": _pct(mids, 0.75)},
        "by_city": by_city,
        "exp_dist": dist(Counter(exp_bucket(r["experience"]) for r in rows)),
        "edu_dist": dist(Counter(edu_bucket(r["education"]) for r in rows)),
        "wlb_dist": dist(wlb_counter),
        "ctype_dist": dist(Counter((r["company_type"] or "unknown") for r in rows)),
        "workmode_dist": dist(Counter((r["work_mode"] or "unknown") for r in rows)),
        "source_dist": dist(Counter(r["source"] for r in rows)),
    }
