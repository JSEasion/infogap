"""匹配引擎：硬门槛一票否决 + 软偏好加权打分，每个结论可解释。纯规则。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .models import Profile, COMPANY_TYPE_DESC, WORK_MODE_DESC
from .wlb import WLB_LEVEL_DESC

_WEIGHT_DEFAULTS = {"wlb": 5, "work_mode": 3, "company_type": 2, "tech_fit": 4, "salary_margin": 3}


@dataclass
class MatchDim:
    key: str
    name: str
    score: float          # 0-1
    note: str


@dataclass
class MatchResult:
    score: int | None     # 0-100；veto 时为 None
    verdict: str          # pass | veto
    reasons: list[str] = field(default_factory=list)   # 否决理由 + 数据缺失提醒
    dims: list[MatchDim] = field(default_factory=list)


def match_job(job: dict, profile: Profile) -> MatchResult:
    hard, soft = profile.hard, profile.prefs
    skills = set(json.loads(job.get("skills") or "[]"))
    vetoes: list[str] = []
    warns: list[str] = []

    # ---------- 硬门槛（一票否决） ----------
    floor = hard.salary_floor_k
    smin = job.get("salary_min_k")
    if floor:
        if smin is not None and smin < floor:
            vetoes.append(f"薪资下限 {smin:g}K 低于底线 {floor:g}K")
        elif smin is None:
            warns.append("薪资未标注，无法核对底线")

    cities = [c.strip() for c in (hard.cities or []) if c.strip()]
    city = (job.get("city") or "").strip()
    if cities:
        remote_ok = "远程" in cities and job.get("work_mode") == "remote"
        if city:
            if not remote_ok and city not in cities and not any(c in city for c in cities):
                vetoes.append(f"城市「{city}」不在目标城市内")
        else:
            warns.append("城市未知，无法核对")

    omax = hard.overtime_max
    lvl = job.get("wlb_level")
    if omax is not None and lvl is not None and lvl > omax:
        vetoes.append(f"加班程度 {WLB_LEVEL_DESC.get(lvl, f'L{lvl}')} 超过容忍上限 L{omax}")

    # ---------- 软偏好（加权） ----------
    weights = dict(_WEIGHT_DEFAULTS)
    weights.update({k: v for k, v in (soft.weights or {}).items() if k in _WEIGHT_DEFAULTS})
    dims: list[MatchDim] = []

    # 工作强度
    if lvl is None:
        dims.append(MatchDim("wlb", "工作强度", 0.5, "JD 无 WLB 信号，未知"))
    else:
        dims.append(MatchDim("wlb", "工作强度", (4 - lvl) / 4, WLB_LEVEL_DESC.get(lvl, f"L{lvl}")))

    # 工作模式
    pref_mode, jm = soft.work_mode, (job.get("work_mode") or "unknown")
    if pref_mode == "any":
        dims.append(MatchDim("work_mode", "工作模式", 1.0, f"不限（岗位：{WORK_MODE_DESC.get(jm, jm)}）"))
    elif jm == "unknown":
        dims.append(MatchDim("work_mode", "工作模式", 0.5, "岗位未标注"))
    else:
        s = 1.0 if jm == pref_mode else (0.55 if {pref_mode, jm} == {"remote", "hybrid"} else 0.2)
        dims.append(MatchDim("work_mode", "工作模式", s,
                             f"偏好{WORK_MODE_DESC.get(pref_mode, pref_mode)}，岗位{WORK_MODE_DESC.get(jm, jm)}"))

    # 公司类型
    ctypes, jt = soft.company_types or [], (job.get("company_type") or "unknown")
    if jt == "unknown":
        dims.append(MatchDim("company_type", "公司类型", 0.5, "JD 未体现"))
    elif not ctypes:
        dims.append(MatchDim("company_type", "公司类型", 0.7, f"{COMPANY_TYPE_DESC.get(jt, jt)}（偏好未设置）"))
    elif jt in ctypes:
        dims.append(MatchDim("company_type", "公司类型", 1.0, f"{COMPANY_TYPE_DESC.get(jt, jt)}，命中偏好"))
    else:
        dims.append(MatchDim("company_type", "公司类型", 0.3,
                             f"{COMPANY_TYPE_DESC.get(jt, jt)}，偏好 {'/'.join(ctypes)}"))

    # 技术方向
    like, avoid = soft.tech_like or [], soft.tech_avoid or []
    like_hits = [t for t in like if t in skills]
    avoid_hits = [t for t in avoid if t in skills]
    if not like and not avoid:
        dims.append(MatchDim("tech_fit", "技术方向", 0.5, "偏好未设置"))
    else:
        base = len(like_hits) / len(like) if like else 0.5
        s = max(0.0, min(1.0, base - 0.35 * len(avoid_hits)))
        note = f"命中 {len(like_hits)}/{len(like)}（{','.join(like_hits) or '无'}）" if like else "无正向偏好"
        if avoid_hits:
            note += f"；踩雷 {len(avoid_hits)} 项（{','.join(avoid_hits)}）"
        dims.append(MatchDim("tech_fit", "技术方向", s, note))

    # 薪资余量
    if floor and smin is not None and floor > 0:
        ratio = smin / floor
        s = 1.0 if ratio >= 1.3 else 0.85 if ratio >= 1.1 else 0.7
        dims.append(MatchDim("salary_margin", "薪资余量", s, f"下限为底线的 {ratio:.0%}"))
    else:
        dims.append(MatchDim("salary_margin", "薪资余量", 0.5, "无底线或薪资未知"))

    if vetoes:
        return MatchResult(None, "veto", vetoes + warns, dims)

    score = round(100 * sum(weights[d.key] * d.score for d in dims) / sum(weights[d.key] for d in dims))
    return MatchResult(score, "pass", warns, dims)


def score_badge(score: int | None, verdict: str) -> tuple[str, str]:
    if verdict == "veto" or score is None:
        return "否决", "badge-veto"
    if score >= 80:
        return f"{score}", "badge-good"
    if score >= 60:
        return f"{score}", "badge-mid"
    return f"{score}", "badge-weak"
