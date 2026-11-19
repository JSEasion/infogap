"""周级统计快照：技能频次 / 薪资分位 / 岗位量 / 渠道信息差贡献 → 趋势时序。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from . import db
from .consolidate import channel_uniqueness


def week_period(dt: datetime | None = None) -> str:
    """ISO 周标识，如 2026-W46。"""
    dt = dt or datetime.now()
    return f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    k = int(round((len(sorted_vals) - 1) * p / 100))
    return sorted_vals[k]


def snapshot_weekly(conn: sqlite3.Connection) -> dict:
    """落本周四类快照（幂等覆盖）。"""
    period = week_period()
    out = {}

    # 1) 技能频次（精选层）
    counter: dict[str, int] = {}
    for row in conn.execute("SELECT skills FROM jobs"):
        try:
            for s in json.loads(row["skills"] or "[]"):
                counter[s] = counter.get(s, 0) + 1
        except json.JSONDecodeError:
            continue
    skills = dict(sorted(counter.items(), key=lambda x: -x[1])[:60])
    db.save_snapshot(conn, period, "skills", skills)
    out["skills"] = len(skills)

    # 2) 薪资分位（有 salary_raw 原文依据的精选行）
    vals = sorted(r[0] for r in conn.execute(
        "SELECT salary_min_k FROM jobs WHERE salary_min_k IS NOT NULL AND salary_raw != ''"))
    sal = {"n": len(vals),
           "p25": _percentile(vals, 25), "p50": _percentile(vals, 50),
           "p75": _percentile(vals, 75)}
    db.save_snapshot(conn, period, "salary", sal)
    out["salary_p50"] = sal["p50"]

    # 3) 库容量（各类型总量 + 按渠道）
    volume = {"jobs": conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"],
              "news": conn.execute("SELECT COUNT(*) c FROM news WHERE kind='news'").fetchone()["c"],
              "tech": conn.execute("SELECT COUNT(*) c FROM news WHERE kind='tech'").fetchone()["c"]}
    by_source = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(*) n FROM jobs GROUP BY source")}
    volume["by_source"] = by_source
    db.save_snapshot(conn, period, "volume", volume)
    out["jobs"] = volume["jobs"]

    # 4) 渠道信息差贡献
    uniq = channel_uniqueness(conn)
    db.save_snapshot(conn, period, "channels", {"channels": uniq[:30]})
    out["channels"] = len(uniq)
    return out


def skill_trend(conn: sqlite3.Connection, top: int = 12) -> list[dict]:
    """技能趋势：对比最近两期快照，输出 rising / falling。"""
    snaps = db.list_snapshots(conn, "skills", limit=8)
    if not snaps:
        return []
    snaps.reverse()   # 旧 → 新
    latest: dict = snaps[-1]["stats"]
    prev: dict = snaps[-2]["stats"] if len(snaps) >= 2 else {}
    rows = []
    for sid, n in latest.items():
        p = prev.get(sid, 0)
        rows.append({"id": sid, "now": n, "prev": p,
                     "delta": n - p, "ratio": round(n / p, 2) if p else None})
    rows.sort(key=lambda x: -x["now"])
    hot = [r for r in rows[:top]]
    rising = sorted([r for r in rows if r["delta"] > 0], key=lambda x: -x["delta"])[:8]
    falling = sorted([r for r in rows if r["delta"] < 0], key=lambda x: x["delta"])[:8]
    return [{"top": hot}, {"rising": rising}, {"falling": falling}]
