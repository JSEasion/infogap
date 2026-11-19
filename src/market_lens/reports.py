"""报告生成：行情报告 / 差距分析 / 资讯周报。LLM 不可用时自动退化为规则模板。
progress 回调（"step"|"think"|"answer", text）用于 SSE 流式输出；None 则走普通调用。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Callable

from . import db
from .gateway import LLMGateway, LLMError
from .match import match_job
from .models import Profile
from .stats import market_stats

ProgressFn = Callable[[str, str], None]


def _llm_narrative(gateway: LLMGateway, task: str, system: str, user: str,
                   progress: ProgressFn | None = None, max_tokens: int = 8000) -> str:
    """LLM 正文生成。返回文本（失败/无通道返回空串，调用方回落规则模板）。"""
    if progress is None:
        try:
            return gateway.chat(task, system, user, max_tokens=max_tokens).text
        except LLMError:
            return ""
    parts: list[str] = []
    try:
        for ev in gateway.chat_stream(task, system, user, max_tokens=max_tokens):
            if ev["type"] == "think":
                progress("think", ev["text"])
            elif ev["type"] == "answer":
                parts.append(ev["text"])
                progress("answer", ev["text"])
            elif ev["type"] == "error":
                raise LLMError(ev["message"])
    except LLMError:
        pass
    return "".join(parts)


def _stats_md(stats: dict) -> str:
    lines = [f"岗位样本 {stats['total']} 条 / 公司 {stats['companies']} 家",
             f"月薪分位：p25={stats['salary_pct']['p25']}K p50={stats['salary_pct']['p50']}K "
             f"p75={stats['salary_pct']['p75']}K（中位口径，n={stats['salary_n']}/{stats['total']} 条含薪资原文，"
             f"仅代表该子集；公告类聚合页已排除）",
             "技能需求 TOP："]
    for s in stats["skills_top"][:15]:
        lines.append(f"  {s['id']}({s['group']}) {s['count']}次 {s['pct']}%")
    lines.append("城市分布：" + "，".join(f"{c['city']}×{c['n']}(中位{c['median']}K)"
                                       for c in stats["by_city"][:8] if c["city"] != "未知"))
    lines.append("经验要求：" + "，".join(f"{t['label']}×{t['n']}" for t in stats["exp_dist"][:5]))
    lines.append("WLB 分布：" + "，".join(f"{t['label']}×{t['n']}" for t in stats["wlb_dist"]))
    return "\n".join(lines)


def _my_slice(conn: sqlite3.Connection, profile: Profile) -> dict:
    """按硬门槛过滤后的「为我定制」切片。"""
    jobs = db.list_jobs(conn, limit=1000)
    results = [(j, match_job(j, profile)) for j in jobs]
    passed = [j for j, m in results if m.verdict == "pass"]
    scores = sorted((m.score for _j, m in results if m.score is not None), reverse=True)
    veto_n = sum(1 for _j, m in results if m.verdict == "veto")
    mids = sorted((j["salary_min_k"] or 0) for j in passed if j.get("salary_min_k"))
    med = mids[len(mids) // 2] if mids else None
    return {"total": len(jobs), "passed": len(passed), "veto": veto_n,
            "veto_rate": round(100 * veto_n / len(jobs), 1) if jobs else 0,
            "salary_median_floor_k": med,
            "top_matches": [
                {"id": j["id"], "title": j["title"], "company": j["company"],
                 "city": j["city"], "salary_raw": j["salary_raw"], "score": m.score}
                for (j, m) in sorted(results, key=lambda x: -(x[1].score or -1))[:8]
                if m.verdict == "pass"]}


def gen_market_report(conn: sqlite3.Connection, gateway: LLMGateway | None,
                      profile: Profile, progress: ProgressFn | None = None) -> int:
    def step(text: str) -> None:
        if progress:
            progress("step", text)

    step("统计岗位库…")
    stats = market_stats(conn)
    step(f"统计完成：{stats['total']} 条岗位 / {stats['companies']} 家公司，切片分析…")
    sl = _my_slice(conn, profile)
    jobs = db.list_jobs(conn, limit=200)
    excerpts = "\n".join(
        f"- {j['title']}｜{j['company']}｜{j['city']}｜{j['salary_raw'] or '薪资未标'}"
        f"｜{j['experience']}｜技能:{','.join(json.loads(j['skills'] or '[]'))[:80]}"
        for j in jobs[:25])

    llm_md = ""
    used_llm = False
    if gateway and gateway.settings.llm_ready("analysis"):
        step("调用模型生成分析（思考流 + 正文流式输出）")
        system = ("你是资深 AI 行业招聘分析师。基于给定的真实统计数据撰写行情报告，禁止编造数据，"
                  "输出 Markdown。用中文，观点要具体、给结论，不要空话。")
        user = f"""## 统计数据
{_stats_md(stats)}

## 我的画像（报告需面向此人给建议）
{profile.background}
目标岗位：{profile.target_role}；{profile.years_exp}年经验
硬门槛：月薪≥{profile.hard.salary_floor_k}K，城市{profile.hard.cities}，加班容忍≤L{profile.hard.overtime_max}

## 为我筛选后的切片
共 {sl['total']} 条 → 通过硬门槛 {sl['passed']} 条（否决率 {sl['veto_rate']}%）

## 岗位摘录（部分样本）
{excerpts}

请输出报告，章节：
## 一、市场总览与热度判断
## 二、技能需求解读（结合TOP技能，指出哪些是门槛、哪些是加分）
## 三、薪资行情（分位解读、城市差异）
## 四、WLB 与公司类型信号（哪些信号值得警惕）
## 五、给「6年后端转Agent」的切入建议（结合我的画像与切片否决率，具体到行动）"""
        llm_md = _llm_narrative(gateway, "analysis", system, user, progress)
        used_llm = bool(llm_md.strip())
    if llm_md.startswith("#"):   # 去掉模型自带的重复大标题，保留章节结构
        llm_md = llm_md.split("\n", 1)[1].lstrip("\n") if "\n" in llm_md else ""

    head = (f"# AI Agent 方向行情报告（{datetime.now():%Y-%m-%d}）\n\n"
            f"> 数据：{stats['total']} 条岗位 · 公司 {stats['companies']} 家 · "
            f"通过我的硬门槛 {sl['passed']} 条 · 生成方式：{'LLM 分析' if used_llm else '规则统计（未配置模型）'}\n\n")
    rule_md = f"""## 一、市场总览（规则统计）
{_stats_md(stats)}

## 二、为我定制的切片
- 通过硬门槛：{sl['passed']}/{sl['total']}（否决率 {sl['veto_rate']}%）
- 通过岗位的薪资下限中位：{sl['salary_median_floor_k'] or '样本不足'}K
- 当前最匹配岗位：
"""
    for t in sl["top_matches"]:
        rule_md += f"  - [{t['score']}分] {t['title']}｜{t['company']}｜{t['city']}｜{t['salary_raw']}\n"

    body = llm_md if used_llm else rule_md
    if used_llm:
        body += "\n\n---\n\n<details><summary>原始统计（规则层，可展开核对）</summary>\n\n```\n" \
                + _stats_md(stats) + "\n```\n</details>\n"
    return db.save_report(conn, "market", f"行情报告 {datetime.now():%Y-%m-%d %H:%M}",
                          head + body, {**stats, "my_slice": sl})


def gen_gap_report(conn: sqlite3.Connection, gateway: LLMGateway | None,
                   profile: Profile, progress: ProgressFn | None = None) -> int:
    def step(text: str) -> None:
        if progress:
            progress("step", text)

    step("统计技能需求分布…")
    stats = market_stats(conn)
    have = set(profile.skills)
    top = [(s["id"], s["count"], s["pct"]) for s in stats["skills_top"]]
    missing = [(i, c, p) for i, c, p in top if i not in have]
    covered = [i for i, _c, _p in top if i in have]

    llm_md, used_llm = "", False
    if gateway and gateway.settings.llm_ready("gap"):
        step("调用模型生成差距分析（流式）")
        system = ("你是技术职业发展顾问。基于真实统计数据做差距分析，禁止编造。"
                  "输出 Markdown，中文，建议要可执行、有优先级。")
        user = f"""## 我的画像
{profile.background}
已有技能（规范id）：{', '.join(profile.skills)}
目标岗位：{profile.target_role}（{profile.years_exp}年经验）
偏好：工作模式={profile.prefs.work_mode}；偏好公司={profile.prefs.company_types}；
想深耕={profile.prefs.tech_like}；想避开={profile.prefs.tech_avoid}

## 市场技能需求 TOP（{stats['total']}条岗位）
{chr(10).join(f'{i}: {c}次({p}%)' for i, c, p in top)}

## 我已覆盖的市场高频技能
{', '.join(covered) or '无'}

## 我缺失的高频技能（按需求量排序）
{chr(10).join(f'{i}: {c}次({p}%)' for i, c, p in missing[:15]) or '无'}

请输出：
## 一、竞争力定位（我的优势盘面 vs 短板，结合6年后端背景）
## 二、必须补的硬缺口（Top5 内说明为什么、学到什么程度够用）
## 三、4 周补齐路径（每周做什么、做什么验证项目）
## 四、简历与面试策略（关键词布局、如何包装后端→Agent 的转型叙事）"""
        llm_md = _llm_narrative(gateway, "gap", system, user, progress)
        used_llm = bool(llm_md.strip())

    head = (f"# 差距分析报告（{datetime.now():%Y-%m-%d}）\n\n"
            f"> 市场样本 {stats['total']} 条 · 生成方式：{'LLM 分析' if used_llm else '规则统计（未配置模型）'}\n\n")
    rule_md = f"""## 我的覆盖面
已覆盖高频技能：{', '.join(covered) or '无'}

## 缺口清单（按市场需求量排序）
| 技能 | 市场需求 | 需求占比 |
|---|---|---|
"""
    for i, c, p in missing[:15]:
        rule_md += f"| {i} | {c} 次 | {p}% |\n"
    rule_md += "\n> 配置模型 API 后可生成含补齐路径与简历策略的深度分析。\n"
    return db.save_report(conn, "gap", f"差距分析 {datetime.now():%Y-%m-%d %H:%M}",
                          head + (llm_md if used_llm else rule_md),
                          {"missing": missing, "covered": covered})


def gen_digest(conn: sqlite3.Connection, gateway: LLMGateway | None, days: int = 7,
               progress: ProgressFn | None = None) -> int:
    def step(text: str) -> None:
        if progress:
            progress("step", text)

    step("聚合近 7 天资讯…")
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    news = [n for n in db.list_news(conn, limit=200) if (n.get("fetched_at") or "") >= since]
    if not news:
        news = db.list_news(conn, limit=30)
    titles = "\n".join(f"- [{n['source']}] {n['title']}" for n in news[:40])
    step(f"资讯 {len(news)} 条，调用模型生成周报（流式）")

    llm_md, used_llm = "", False
    if gateway and gateway.settings.llm_ready("digest") and news:
        system = ("你是 AI 行业情报编辑。把资讯列表整理成周报，输出 Markdown 中文。"
                  "按主题聚类（模型发布/Agent框架/行业动态/招聘风向），每条给一句点评，最后给「对求职者的影响」。")
        llm_md = _llm_narrative(gateway, "digest", system, f"近{days}天资讯：\n{titles}",
                                progress, max_tokens=6000)
        used_llm = bool(llm_md.strip())

    head = f"# 行业资讯周报（{datetime.now():%Y-%m-%d}）\n\n> 资讯 {len(news)} 条 · 生成方式：{'LLM 摘要' if used_llm else '标题列表'}\n\n"
    body = llm_md if used_llm else titles
    return db.save_report(conn, "digest", f"资讯周报 {datetime.now():%Y-%m-%d %H:%M}",
                          head + body, {"news_count": len(news)})
