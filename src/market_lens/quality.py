"""质量门：七维含金量评分 + 反诈骗硬规则 + 灰区 LLM 裁决。

设计原则（数据为王）：
- 每个分数可解释：维度分 + 理由 + 原文证据，全部落 score_json
- 冷门渠道低 tier 起步，靠交叉验证回填可信度，靠反诈骗规则兜底
- 规则优先零成本；只有灰区条目才花 LLM 裁决（judge 路由走廉价通道）
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import taxonomy
from .models import ScoreDimension, ScoreResult

# ---------------- 渠道档案：可信 tier × 稀缺 scarcity（config quality.tiers 可覆盖） ----------------
# tier  0-1：信息源本身的可靠性（官方 API=1.0，爬取聚合=0.6，TG/匿名频道起步低）
# scarcity 0-1：信息差系数——大家随便一搜就能搜到的=0.2，冷门独家的=0.9
SOURCE_META: dict[str, dict] = {
    # 官方直连组
    "greenhouse":  {"tier": 1.0, "scarcity": 0.30, "group": "official"},
    "lever":       {"tier": 1.0, "scarcity": 0.30, "group": "official"},
    "iguopin":     {"tier": 0.85, "scarcity": 0.40, "group": "official"},
    "gaoxiaojob":  {"tier": 0.80, "scarcity": 0.50, "group": "official"},
    "gxannounce":  {"tier": 0.80, "scarcity": 0.60, "group": "official"},
    "campus":      {"tier": 0.55, "scarcity": 0.85, "group": "scarce"},
    # 社区组
    "v2ex":        {"tier": 0.70, "scarcity": 0.50, "group": "community"},
    "eleduck":     {"tier": 0.70, "scarcity": 0.60, "group": "community"},
    "hn":          {"tier": 0.75, "scarcity": 0.55, "group": "community"},
    "remotive":    {"tier": 0.60, "scarcity": 0.20, "group": "community"},
    "remoteok":    {"tier": 0.60, "scarcity": 0.20, "group": "community"},
    "niche":       {"tier": 0.65, "scarcity": 0.80, "group": "scarce"},
    "wwr":         {"tier": 0.60, "scarcity": 0.30, "group": "community"},
    "web3":        {"tier": 0.60, "scarcity": 0.35, "group": "community"},
    # 信息差组（前缀匹配 tg:xxx / gh:owner/repo）
    "tg":          {"tier": 0.55, "scarcity": 0.90, "group": "scarce"},
    "gh":          {"tier": 0.60, "scarcity": 0.85, "group": "scarce"},
    "ruanyf":      {"tier": 0.80, "scarcity": 0.65, "group": "scarce"},
    # 通用/兜底
    "manual":      {"tier": 0.90, "scarcity": 0.70, "group": "community"},
    "browser":     {"tier": 0.90, "scarcity": 0.60, "group": "community"},
    "urlimport":   {"tier": 0.60, "scarcity": 0.60, "group": "community"},
    "rss":         {"tier": 0.70, "scarcity": 0.30, "group": "community"},
    "search":      {"tier": 0.40, "scarcity": 0.30, "group": "community"},
    "sample":      {"tier": 0.30, "scarcity": 0.10, "group": "community"},
    "techfront":   {"tier": 0.90, "scarcity": 0.50, "group": "tech"},
    "arxiv":       {"tier": 0.95, "scarcity": 0.55, "group": "tech"},
    "hfpapers":    {"tier": 0.90, "scarcity": 0.50, "group": "tech"},
    "ghtrend":     {"tier": 0.85, "scarcity": 0.45, "group": "tech"},
    "radar":       {"tier": 0.85, "scarcity": 0.60, "group": "tech"},
}

GROUP_DESC = {"official": "官方直连", "community": "社区", "scarce": "信息差", "tech": "前沿技术"}

# 反诈骗硬规则（命中直接 rejected，不进灰区）
SCAM_PATTERNS: list[tuple[str, str]] = [
    (r"(付费|收费|缴纳|先交|收取|支付).{0,8}(内推|推荐费|保证金|押金|服务费|报名费|岗位费)", "付费内推/保证金"),
    (r"培训贷", "培训贷"),
    (r"包(就业|入职|拿?offer)", "包入职承诺"),
    (r"(入职|录用|offer).{0,6}(前|之前).{0,8}(缴|交|付|转).{0,4}(费|款|钱|定金)", "入职前缴费"),
    (r"内部渠道.{0,10}(费用|收费|付费)", "付费内部渠道"),
]
_SCAM_RES = [(re.compile(p, re.I), name) for p, name in SCAM_PATTERNS]

# 资讯相关性关键词（news 的 relevance 维）
_NEWS_RELEVANT = re.compile(
    r"AI|人工智能|大模型|LLM|GPT|Agent|智能体|算法|裁员|扩招|融资|裁员潮|招聘|薪资|"
    r"程序员|工程师|开发者|开源|模型|推理|多模态|数字化|互联网|科技公司", re.I)

# 七维权重（按 kind；config quality.weights 可覆盖）
DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "job": {"credibility": .20, "scarcity": .15, "completeness": .15, "evidence": .20,
            "freshness": .10, "relevance": .10, "cross": .10},
    "news": {"credibility": .15, "scarcity": .10, "signal": .35, "evidence": .05,
             "freshness": .25, "relevance": .10},
    "tech": {"credibility": .15, "scarcity": .15, "completeness": .10, "evidence": .05,
             "freshness": .25, "relevance": .30},
}

# 门阈值（config quality.gates 可覆盖）：[低线, 高线]
# ≥高线 → curated；灰区 → LLM judge（不可用时按 quality.judge_fallback 回退）；
# <低线 → quarantined；诈骗 → rejected
DEFAULT_GATES: dict[str, tuple[float, float]] = {
    "job": (55.0, 70.0), "news": (50.0, 65.0), "tech": (50.0, 65.0),
}


def source_meta(source: str, overrides: dict | None = None) -> dict:
    """渠道档案：支持前缀匹配（tg:xxx → tg）与 config 覆盖。"""
    if overrides and source in overrides:
        o = overrides[source]
        base = dict(SOURCE_META.get(_root(source), {}))
        base.update({k: v for k, v in o.items() if k in ("tier", "scarcity", "group")})
        return base
    return dict(SOURCE_META.get(_root(source), {"tier": 0.5, "scarcity": 0.5, "group": "community"}))


def _root(source: str) -> str:
    s = (source or "").split(":", 1)[0].strip().lower()
    return s if s in SOURCE_META else (source or "").strip().lower()


@dataclass
class GateResult:
    raw_id: int
    status: str                      # curated | quarantined | rejected
    gold: float
    score: ScoreResult
    judge_used: bool = False


# ---------------- 时效 ----------------

def parse_time(s: str) -> datetime | None:
    """宽松时间解析：ISO / 2026-11-18 / 中文相对时间（3天前）。"""
    s = (s or "").strip()
    if not s:
        return None
    m = re.match(r"(\d+)\s*(分钟|小时|天|日|周|月)前", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"分钟": timedelta(minutes=n), "小时": timedelta(hours=n), "天": timedelta(days=n),
                 "日": timedelta(days=n), "周": timedelta(weeks=n), "月": timedelta(days=30 * n)}[unit]
        return datetime.now() - delta
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:26].strip(), fmt)
        except ValueError:
            continue
    return None


def freshness_score(published_at: str, fetched_at: str = "") -> tuple[float, str]:
    """时效分：<3d=100 → 7d=85 → 14d=65 → 30d=40 → 60d+≤15；无时间用 fetched_at 兜底。"""
    t = parse_time(published_at) or parse_time(fetched_at)
    if not t:
        return 50.0, "无发布时间，按抓取时间附近估分"
    days = (datetime.now().astimezone() - t.astimezone()).days if t.tzinfo else \
        (datetime.now() - t).days
    days = max(days, 0)
    if days <= 3:
        sc = 100 - days * 3
        why = f"{days}天内"
    elif days <= 7:
        sc, why = 85, f"{days}天前"
    elif days <= 14:
        sc, why = 65, f"{days}天前"
    elif days <= 30:
        sc, why = 40, f"{days}天前"
    elif days <= 60:
        sc, why = 20, f"{days}天前（偏旧）"
    else:
        sc, why = 8, f"超过{days}天（过期）"
    return sc, why


# ---------------- 七维评分 ----------------

def _dim(key: str, score: float, weight: float, reason: str) -> ScoreDimension:
    return ScoreDimension(key=key, score=max(0.0, min(100.0, score)), weight=weight, reason=reason)


def score_item(item: dict, kind: str = "job", *, weights: dict | None = None,
               overrides: dict | None = None, cross_channels: int = 1,
               profile_skills: list[str] | None = None) -> ScoreResult:
    """规则层七维评分（零 LLM）。item 为 raw_items 行（title/body/source/published_at…）。
    cross_channels>1 表示同岗/同事件在多渠道出现（交叉验证，由 consolidate 回填重放）。"""
    w = {**DEFAULT_WEIGHTS.get(kind, DEFAULT_WEIGHTS["job"]), **(weights or {})}
    meta = source_meta(item.get("source", ""), overrides)
    body = (item.get("body") or "") + " " + (item.get("title") or "")
    dims: list[ScoreDimension] = []

    # 1) credibility 来源可信
    tier = float(meta.get("tier", 0.5))
    dims.append(_dim("credibility", tier * 100, w.get("credibility", .2),
                     f"渠道 {item.get('source')}：{GROUP_DESC.get(meta.get('group'), '?')}"
                     f"（tier={tier}）"))

    # 2) scarcity 信息差（越稀缺越高；全网都有=0.2）
    scar = float(meta.get("scarcity", 0.5))
    dims.append(_dim("scarcity", scar * 100, w.get("scarcity", .1),
                     f"稀缺系数 {scar}（{'冷门独家' if scar >= .7 else '常见渠道' if scar <= .3 else '中等'}）"))

    # 3) completeness / signal 完备度（news 用 signal：信号卡要素）
    if kind == "news":
        n_entities = len(re.findall(r"[\u4e00-\u9fffA-Za-z]{2,}",
                                    f"{item.get('title', '')} {(item.get('body') or '')[:120]}"))
        has_numbers = bool(re.search(r"\d+(\.\d+)?\s*[%万亿Kk亿圆美]|\$\d|\d+ ?[千万亿]", body))
        has_fulltext = len(item.get("body") or "") >= 120
        sc = (40 * has_fulltext + 35 * has_numbers + 25 * min(n_entities, 4) / 4)
        dims.append(_dim("signal", sc, w.get("signal", .35),
                         f"{'有正文' if has_fulltext else '缺正文'}；"
                         f"{'含关键数字' if has_numbers else '无数字'}；实体{min(n_entities, 4)}个"))
    else:
        n_filled = sum(bool((item.get(k) or "").strip()) for k in ("title", "source_url"))
        body_len = len(item.get("body") or "")
        bl = min(body_len / 600, 1.0) * 100   # 600 字以上算满分
        sc = 0.3 * (n_filled / 2 * 100) + 0.7 * bl
        dims.append(_dim("completeness", sc, w.get("completeness", .15),
                         f"正文 {body_len} 字"))

    # 4) evidence 证据链（宁缺毋假：有依据才给分）
    if kind == "job":
        has_salary_raw = bool(re.search(r"\d+\s*[Kk万]|\d+-\d+元/天|面议|\$\d", body))
        has_title = bool((item.get("title") or "").strip())
        sc = 50 * has_title + 50 * has_salary_raw
        dims.append(_dim("evidence", sc, w.get("evidence", .2),
                         f"{'有标题' if has_title else '缺标题'}；"
                         f"{'薪资有原文' if has_salary_raw else '无薪资原文'}"))
    elif kind == "news":
        from .textfmt import is_bad_summary
        bad, why = is_bad_summary(item.get("body", "")[:200] or item.get("title", ""), "")
        dims.append(_dim("evidence", 30 if bad else 90, w.get("evidence", .05),
                         f"摘要质量：{why or '有效'}"))
    else:
        dims.append(_dim("evidence", 80, w.get("evidence", .05), "技术源自带原文链接"))

    # 5) freshness 时效
    fs, fwhy = freshness_score(item.get("published_at", ""), item.get("fetched_at", ""))
    dims.append(_dim("freshness", fs, w.get("freshness", .1), fwhy))

    # 6) relevance 相关性（taxonomy 命中 + 画像技能 + 领域词）
    hits = taxonomy.match_skills(body)
    n_hits = len(hits)
    extra = 0.0
    if profile_skills:
        extra = 30.0 if any(s.lower() in body.lower() for s in profile_skills) else 0.0
    domain = 15.0 if (kind == "news" and _NEWS_RELEVANT.search(body)) else 0.0
    if kind == "tech":   # 技术源：论文/仓库标题里的 AI 术语也算相关
        domain = 40.0 if _NEWS_RELEVANT.search(f"{item.get('title', '')} {body[:200]}") else 0.0
    rel = min(100.0, 20 + min(n_hits, 5) * 13 + domain + extra)
    dims.append(_dim("relevance", rel, w.get("relevance", .1),
                     f"技能命中 {n_hits} 项{'；画像相关' if extra else ''}"
                     f"{'；领域相关' if domain else ''}"))

    # 7) cross 交叉验证（多渠道印证回填可信度；冷门渠道靠这个翻盘）
    if kind != "news":
        if cross_channels <= 1:
            cs, cwhy = 40.0, "单渠道出现，待交叉验证"
        else:
            cs = min(100.0, 55 + (cross_channels - 1) * 20)
            cwhy = f"{cross_channels} 渠道互相印证"
        dims.append(_dim("cross", cs, w.get("cross", .1), cwhy))

    gold = sum(d.score * d.weight for d in dims) / max(sum(d.weight for d in dims), 1e-6)
    return ScoreResult(gold=round(gold, 1), dims=dims)


# ---------------- 反诈骗 ----------------

def scam_check(item: dict) -> list[str]:
    """硬规则：命中即 rejected。返回命中的规则名列表。"""
    text = f"{item.get('title', '')} {item.get('body', '')}"
    return [name for rex, name in _SCAM_RES if rex.search(text)]


# ---------------- 灰区 LLM 裁决 ----------------

_JUDGE_SYSTEM = """你是招聘/资讯数据质量审核员。只输出 JSON 数组，禁止解释文字。
对每条数据判断：
1) real 可信度 1-10：是真实岗位/真实事件，还是营销水文、诈骗、模板垃圾？
   可靠性信号：具体公司名/职位名/薪资数字/邮箱域名/职责细节；企业邮箱；具体日期。
   危险信号：付费内推、要求转账、 vague 到无法验证、纯广告。
2) value 含金量 1-10：对一名 AI 方向工程师求职者的信息价值（稀缺机会/关键行业信号=高分；
   随处可见的普通岗位/水文=低分）。
3) scam 布尔：是否疑似诈骗。
输出：{"idx": 序号, "real": 1-10, "value": 1-10, "scam": false, "note": "10字内理由"}"""


# 会话级熔断：judge 端点失败一次后 10 分钟内不再尝试（避免逐批 120s 超时拖垮流水线）
_JUDGE_COOLDOWN = {"until": 0.0}


def judge_gray(conn, gateway, items: list[dict], kind: str = "job") -> dict[int, dict]:
    """灰区条目批量 LLM 裁决。返回 {raw_id: {real, value, scam, note}}。失败返回空（保持规则分）。"""
    if not items:
        return {}
    import time as _time
    if _time.monotonic() < _JUDGE_COOLDOWN["until"]:
        return {}
    user_parts = []
    for i, it in enumerate(items):
        user_parts.append(
            f"[{i}] 标题:{(it.get('title') or '')[:80]}\n"
            f"渠道:{it.get('source')}\n正文:{(it.get('body') or '')[:600]}")
    user = (f"以下是 {len(items)} 条{'资讯' if kind == 'news' else '岗位/技术'}数据，"
            f"用 --- 分隔：\n\n" + "\n---\n".join(user_parts) +
            f"\n\n输出 {len(items)} 个元素的 JSON 数组，idx 为 0..{len(items) - 1}。")
    try:
        data, _res = gateway.chat_json("judge", _JUDGE_SYSTEM, user, timeout=30.0)
    except Exception:  # noqa: BLE001 LLM 不可用 → 熔断 + 灰区按低线处理（quarantine 待复核）
        _JUDGE_COOLDOWN["until"] = _time.monotonic() + 600
        return {}
    items_out = data if isinstance(data, list) else [data]
    out: dict[int, dict] = {}
    for obj in items_out:
        if not isinstance(obj, dict):
            continue
        try:
            idx = int(obj.get("idx"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(items):
            continue
        out[items[idx]["id"]] = {
            "real": max(1, min(10, int(obj.get("real") or 5))),
            "value": max(1, min(10, int(obj.get("value") or 5))),
            "scam": bool(obj.get("scam")),
            "note": str(obj.get("note") or "")[:60],
        }
    return out


# ---------------- 质量门 ----------------

def _fallback_decide(r: "GateResult", lo: float, hi: float, mode: str, why: str) -> None:
    """灰区无 LLM 裁决时的回退（quality.judge_fallback）：
    midpoint = 按门中值自动分流（与 judge 生效时判据一致，默认）；
    quarantine = 保守全隔离待人工复核。"""
    if mode == "midpoint":
        r.status = "curated" if r.gold >= (lo + hi) / 2 else "quarantined"
        r.score.fallback_used = True
        r.score.judge_note = f"{why}，按门中值自动分流"
    else:
        r.status = "quarantined"


def gate_items(conn, gateway, items: list[dict], kind: str = "job", *,
               quality_cfg: dict | None = None, use_judge: bool = True) -> list[GateResult]:
    """对 pending 条目过门：诈骗→rejected；≥高线→curated；灰区→LLM 裁决；<低线→quarantined。
    只做裁决计算，不写库（pipeline 统一回写）。"""
    cfg = quality_cfg or {}
    weights = cfg.get("weights", {}).get(kind)
    overrides = cfg.get("tiers")
    gates = cfg.get("gates", {}).get(kind) or DEFAULT_GATES[kind]
    lo, hi = float(gates[0]), float(gates[1])
    fallback = str(cfg.get("judge_fallback", "midpoint")).lower()

    results: list[GateResult] = []
    gray: list[tuple[dict, GateResult]] = []
    for it in items:
        meta = it.get("channel_meta") or {}
        if isinstance(meta, str):   # SQL 直读时 channel_meta 是 JSON 字符串
            try:
                meta = json.loads(meta or "{}")
            except json.JSONDecodeError:
                meta = {}
        score = score_item(it, kind, weights=weights, overrides=overrides,
                           cross_channels=int(meta.get("cross", 1)))
        scams = scam_check(it)
        if scams:
            score.scam_risk, score.scam_rules = True, scams
            score.gold = min(score.gold, 5.0)
            results.append(GateResult(it["id"], "rejected", score.gold, score))
            continue
        r = GateResult(it["id"], "", score.gold, score)
        if score.gold >= hi:
            r.status = "curated"
            results.append(r)
        elif score.gold < lo:
            r.status = "quarantined"
            results.append(r)
        else:
            gray.append((it, r))
            results.append(r)

    if gray and use_judge and gateway is not None:
        judged = judge_gray(conn, gateway, [g[0] for g in gray], kind)
        for it, r in gray:
            j = judged.get(it["id"])
            if not j:
                _fallback_decide(r, lo, hi, fallback, "judge 不可用")
                continue
            r.judge_used = True
            r.score.judge_used = True
            r.score.judge_note = j["note"]
            jgold = (j["real"] * 0.6 + j["value"] * 0.4) * 10
            r.gold = round(r.score.gold * 0.5 + jgold * 0.5, 1)   # 规则分与裁决分各半
            r.score.gold = r.gold
            if j["scam"] and j["real"] <= 3:
                r.status = "rejected"
                r.score.scam_risk, r.score.scam_rules = True, [f"judge: {j['note']}"]
            else:
                r.status = "curated" if r.gold >= (lo + hi) / 2 else "quarantined"
    else:
        for it, r in gray:
            _fallback_decide(r, lo, hi, fallback, "judge 未启用")
    return results


def score_to_json(score: ScoreResult) -> str:
    return json.dumps({
        "gold": score.gold,
        "dims": [{"key": d.key, "score": round(d.score, 1), "weight": d.weight,
                  "reason": d.reason} for d in score.dims],
        "judge_used": score.judge_used, "fallback_used": score.fallback_used,
        "judge_note": score.judge_note,
        "scam_risk": score.scam_risk, "scam_rules": score.scam_rules,
    }, ensure_ascii=False)
