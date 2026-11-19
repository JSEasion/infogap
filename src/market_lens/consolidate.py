"""整合层：跨渠道同岗归并 → 独家判定 / 首发时间差 / 薪资矛盾 → 可信度回填。

信息差的量化就在这里：
- 独家岗位：某渠道独有（其他渠道从未出现）→ 该渠道的信息差贡献
- 首发领先天数：冷门渠道比主流平台早 N 天出现同一岗位 → 套利窗口
- 交叉验证：同岗多渠道 → gold 加成（冷门渠道靠这个翻盘）
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime

from . import db
from .quality import parse_time

# 主流渠道（大家都能搜到）vs 信息差渠道
MAINSTREAM = {"remotive", "remoteok", "iguopin", "gaoxiaojob", "gxannounce", "rss",
              "search", "v2ex", "eleduck", "hn"}
SCARCE_PREFIX = ("tg:", "gh:", "niche:", "urlimport:")

_TITLE_NOISE = re.compile(
    r"[【\[(（][^\])）】]*[\])）】]|急招|急聘|高薪|双休|五险一金|远程办公?|\s*\d+[-~]\d+[Kk]\S*")


def is_scarce(source: str) -> bool:
    root = source.split(":", 1)[0]
    return source.startswith(SCARCE_PREFIX) or root in {"ruanyf", "campus"}


def match_key(title: str, company: str, city: str) -> str:
    """归并键：标题去噪（去括号备注/卖点词）+ 公司 + 城市首段。"""
    t = _TITLE_NOISE.sub("", (title or "").strip()).strip().upper()[:24]
    c = (company or "").strip().upper()[:16]
    city = (city or "").split("-")[0].strip()[:4]
    return f"{t}|{c}|{city}"


def _salary_conflict(rows: list[dict]) -> str:
    """薪资矛盾检测：两个区间完全不重叠才算冲突（宽进严出）。"""
    rngs = [(r["salary_min_k"], r["salary_max_k"]) for r in rows
            if r["salary_min_k"] is not None and r["salary_max_k"] is not None]
    for i in range(len(rngs)):
        for j in range(i + 1, len(rngs)):
            a, b = rngs[i], rngs[j]
            if a[1] < b[0] * 0.8 or b[1] < a[0] * 0.8:   # 允许 20% 内的口径差异
                return f"薪资口径冲突：{a[0]}-{a[1]}K vs {b[0]}-{b[1]}K"
    return ""


def run_consolidate(conn: sqlite3.Connection, limit: int = 3000) -> dict:
    """跨渠道归并 + 信息差度量 + 可信度回填。

    归并基于 raw 层「渠道目击」：同岗多渠道采集时 jobs 表会去重成一行，
    但每个 raw 条目记录了一次渠道目击——实体/独家/首发领先都从这里度量。
    """
    # 每条精选 job raw = 一次渠道目击（带 jobs 行的结构化字段）
    sightings = [dict(r) for r in conn.execute(
        "SELECT raw.id AS raw_id, raw.source AS chan, raw.published_at AS chan_pub,"
        " raw.fetched_at AS chan_ft, raw.channel_meta AS meta,"
        " j.id AS job_id, j.title, j.company, j.city, j.salary_min_k, j.salary_max_k,"
        " j.gold_score, j.raw_id AS job_raw"
        " FROM raw_items raw JOIN jobs j ON j.id = raw.curated_id"
        " WHERE raw.kind='job' AND raw.status='curated' ORDER BY raw.id ASC LIMIT ?", (limit,))]

    stats = {"merged": 0, "boosted": 0, "conflicts": 0, "lead_updates": 0}
    by_key: dict[str, list[dict]] = {}
    for s in sightings:
        key = match_key(s["title"], s["company"], s["city"])
        by_key.setdefault(key, []).append(s)
        stats["merged"] += 1

    for key, group in by_key.items():
        sources = {g["chan"] for g in group}
        job_ids = sorted({g["job_id"] for g in group})
        # 首发目击（时间最早的渠道）
        def _t(g):
            return parse_time(g.get("chan_pub") or g.get("chan_ft") or "")
        first = min(group, key=lambda g: (_t(g) is None, _t(g) or datetime.max))
        first_at = _t(first)
        ent = db.upsert_entity(conn, key, job_ids[0], first["chan"],
                               first_at.isoformat() if first_at else db.now())
        # 补齐其余 job ids
        row = conn.execute("SELECT id, job_ids, sources FROM job_entities WHERE match_key=?",
                           (key,)).fetchone()
        ids = json.loads(row["job_ids"])
        srcs = json.loads(row["sources"])
        for jid in job_ids:
            if jid not in ids:
                ids.append(jid)
        for g in group:
            if g["chan"] not in srcs:
                srcs.append(g["chan"])
        conn.execute("UPDATE job_entities SET job_ids=?, sources=? WHERE id=?",
                     (json.dumps(ids), json.dumps(srcs), row["id"]))
        conn.commit()

        n = len(sources)
        # 交叉验证加成（gold 上限 100）+ raw 层 cross 标记
        if n >= 2:
            for g in group:
                boost = min(10.0, (n - 1) * 5.0)
                if boost and g["gold_score"] is not None:
                    new_gold = min(100.0, g["gold_score"] + boost)
                    if new_gold > g["gold_score"]:
                        conn.execute("UPDATE jobs SET gold_score=? WHERE id=?",
                                     (new_gold, g["job_id"]))
                        stats["boosted"] += 1
                meta = json.loads(g["meta"] or "{}")
                meta["cross"] = n
                conn.execute("UPDATE raw_items SET channel_meta=? WHERE id=?",
                             (json.dumps(meta, ensure_ascii=False), g["raw_id"]))
            conn.commit()
            # 薪资矛盾（同实体内任两目击区间不重叠）
            conflict = _salary_conflict(group)
            # 首发领先：冷门渠道目击早于主流渠道目击
            scarce_ts = [_t(g) for g in group if is_scarce(g["chan"])]
            main_ts = [_t(g) for g in group if not is_scarce(g["chan"])]
            lead = 0.0
            if scarce_ts and main_ts:
                s_min = min(t for t in scarce_ts if t)
                m_min = min(t for t in main_ts if t)
                if s_min and m_min:
                    lead = max(0.0, (m_min - s_min).total_seconds() / 86400)
            db.update_entity_conflict(conn, row["id"], lead, conflict)
            if conflict:
                stats["conflicts"] += 1
            if lead > 0:
                stats["lead_updates"] += 1
    return stats


def channel_uniqueness(conn: sqlite3.Connection) -> list[dict]:
    """渠道信息差贡献：各渠道「独家岗位数」（该渠道独有实体数）。"""
    rows = conn.execute("SELECT job_ids, sources, first_seen_source, lead_days "
                        "FROM job_entities").fetchall()
    per_source: dict[str, dict] = {}
    for r in rows:
        sources = json.loads(r["sources"] or "[]")
        first = r["first_seen_source"] or (sources[0] if sources else "?")
        d = per_source.setdefault(first, {"source": first, "unique": 0, "shared": 0,
                                          "lead_total": 0.0, "lead_max": 0.0})
        if len(sources) == 1:
            d["unique"] += 1
        else:
            d["shared"] += 1
        d["lead_total"] += r["lead_days"] or 0
        d["lead_max"] = max(d["lead_max"], r["lead_days"] or 0)
    out = sorted(per_source.values(), key=lambda x: -(x["unique"] + x["shared"]))
    for d in out:
        d["lead_avg"] = round(d["lead_total"] / max(d["unique"] + d["shared"], 1), 1)
        del d["lead_total"]
    return out
