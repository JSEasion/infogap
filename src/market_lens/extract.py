"""JD 结构化：规则抽取打底 + LLM 增强补充，合并去重入库。"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

import jieba

from . import db, taxonomy
from .gateway import LLMGateway, LLMError
from .salary import parse_salary
from .wlb import analyze_wlb

BIGTECH_NAMES = ["字节", "抖音", "阿里", "淘宝", "天猫", "蚂蚁", "腾讯", "微信", "百度", "美团",
                 "京东", "小米", "华为", "网易", "拼多多", "快手", "b站", "bilibili", "滴滴",
                 "携程", "微软", "谷歌", "亚马逊", "oracle", "苹果"]

SALARY_LINE = re.compile(
    r"(\d+(?:\.\d+)?(?:[-~到至]\d+(?:\.\d+)?)?\s*[Kk万]\S{0,6}(?:薪)?|\d+(?:\.\d+)?[-~到至]\d+(?:\.\d+)?万?/年|\d+-\d+元/天|面议)")


@dataclass
class IngestSummary:
    total: int = 0
    added: int = 0
    duplicates: int = 0
    llm_used: bool = False
    errors: list[str] = field(default_factory=list)


# ---------- 规则抽取 ----------

def guess_company_type(text: str) -> str:
    t = text.lower()
    if any(n in t for n in BIGTECH_NAMES):
        return "bigtech"
    if re.search(r"外企|外资|欧美|合资", t):
        return "foreign"
    if re.search(r"国企|央企|事业单位|国有", t):
        return "soe"
    if re.search(r"独角兽|上市公司|已上市", t):
        return "unicorn"
    if re.search(r"创业|早期|天使轮|种子轮|pre-?[aab]|初创", t):
        return "startup"
    if re.search(r"银行|保险|券商|地产|制造业|传统|零售|国企背景", t):
        return "traditional"
    return "unknown"


def guess_work_mode(text: str) -> str:
    t = text.lower()
    if re.search(r"混合办公|hybrid|每周[一二三四五1-5]天.{0,4}远程", t):
        return "hybrid"
    if re.search(r"远程办公|纯远程|居家办公|全程远程|全远程|wfh|work from home|支持远程|可远程", t):
        return "remote"
    if re.search(r"坐班|到岗办公", t):
        return "onsite"
    return "unknown"


def _find_salary_raw(text: str) -> str:
    hits: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 40:
            continue
        m = SALARY_LINE.search(line)
        if m and re.search(r"[Kk万]|元/天|面议", line):
            hits.append(m.group(1))
    # 多岗位列表页（高校人才网/招聘公告）会罗列 N 个岗位各自的薪资区间，
    # 第一个区间大概率不属于本条标题——≥3 个不同区间时视为列表页，宁缺毋假
    if len({h for h in hits if h != "面议"}) >= 3:
        return ""
    return hits[0] if hits else ""


def extract_rules(text: str) -> dict[str, Any]:
    """从原始 JD 文本做规则抽取（零 LLM，永远可用）。"""
    w = analyze_wlb(text)
    salary_raw = _find_salary_raw(text)
    salary = parse_salary(salary_raw)
    skills = taxonomy.match_skills(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    title = lines[0][:40] if lines else ""
    if re.search(r"职责|要求|职位|岗位", title) and len(title) > 18:
        title = ""
    company_m = re.search(r"(?:公司|企业)[:：\s]*([^\s,，。;；]{2,20})", text)
    city_m = re.search(r"(?:城市|工作地点|地点)[:：\s]*((?!薪资|公司|地点|来源|经验|学历|要求)"
                       r"[\u4e00-\u9fa5]{2,4}|远程)", text)
    city = city_m.group(1) if city_m else ""
    if not city:
        for c in ("北京", "上海", "深圳", "广州", "杭州", "成都", "武汉", "南京",
                  "西安", "苏州", "天津", "重庆", "长沙", "合肥", "厦门"):
            if c in text:
                city = c
                break
    return {
        "title": title,
        "company": company_m.group(1) if company_m else "",
        "city": city,
        "salary_raw": salary_raw,
        "salary_min_k": salary.min_k if salary else None,
        "salary_max_k": salary.max_k if salary else None,
        "salary_months": salary.months if salary else 12,
        "annual_min_wan": salary.annual_min_wan if salary else None,
        "annual_max_wan": salary.annual_max_wan if salary else None,
        "experience": (re.search(r"(\d+[-~到至]\d+年|\d+年以上|应届|经验不限)", text) or [None, ""])[1]
        if re.search(r"\d+[-~到至]\d+年|\d+年以上|应届|经验不限", text) else "",
        "education": (re.search(r"(本科及以上|硕士及以上|大专及以上|本科|硕士|博士)", text).group(1)
                      if re.search(r"本科及以上|硕士及以上|大专及以上|博士|硕士|本科", text) else ""),
        "work_mode": guess_work_mode(text),
        "company_type": guess_company_type(text),
        "wlb_level": w.level,
        "wlb_signals": json.dumps(
            [{"term": s.term, "kind": s.kind, "label": s.label, "quote": s.quote} for s in w.signals],
            ensure_ascii=False),
        "wlb_basis": w.basis,
        "skills": json.dumps(skills, ensure_ascii=False),
        "skills_raw": "[]",
        "extract_method": "rules",
        "structured_ok": 1 if (title and salary) else 0,
    }


# ---------- LLM 增强 ----------

_EXTRACT_SYSTEM = """你是招聘 JD 结构化专家。只输出 JSON 数组，禁止输出任何解释文字。
每条 JD 对应一个元素，字段如下（信息不存在就填空串/null）：
{"idx": 序号, "title": "职位名", "company": "公司名", "city": "城市(含'远程'若支持)", "salary_raw": "薪资原文如 15-25K·14薪",
 "experience": "经验要求原文", "education": "学历要求",
 "work_mode": "remote|hybrid|onsite|unknown",
 "company_type": "bigtech大厂|unicorn独角兽|startup创业|foreign外企|soe国企|traditional传统|unknown",
 "wlb_level": 0到4的整数或null, "wlb_evidence": "判断WLB的原文引用",
 "skills": [技能id...], "skills_raw": [词典外的原文技能词],
 "desc_zh": "中文摘要：中文JD给50字内要点；外文JD翻译为300字内中文摘要（做什么/要求/地点薪资）"}
【全中文化规则】若 JD 主要为外文（英文等）：title/company/city/experience/education 必须翻译为中文
（公司名与技术专有名词可保留原文，薪资数字与币种不变）。系统要求所有展示字段为中文。
WLB 刻度：L0=965/弹性/双休/不打卡，L1=常规偶尔加班，L2=常态加班，L3=大小周/995/单休，L4=996及以上。
wlb_level 必须与 wlb_evidence 一致：「弹性工作/双休/不加班/年假多」是正面信号→低L值；「大小周/996/高强度/抗压」→高L值。
skills 必须且只能从下面词典的 id 中选择（可多选），词典没有的写进 skills_raw：
{dictionary}"""


def _parse_extract_item(item: dict, texts_len: int, valid: set) -> dict | None:
    if not isinstance(item, dict):
        return None
    try:
        idx = int(item.get("idx"))
    except (TypeError, ValueError):
        return None
    if not 0 <= idx < texts_len:
        return None
    skills = [s for s in (item.get("skills") or []) if s in valid]
    return {
        "idx": idx,
        "title": str(item.get("title") or "").strip(),
        "company": str(item.get("company") or "").strip(),
        "city": str(item.get("city") or "").strip(),
        "salary_raw": str(item.get("salary_raw") or "").strip(),
        "experience": str(item.get("experience") or "").strip(),
        "education": str(item.get("education") or "").strip(),
        "work_mode": item.get("work_mode") if item.get("work_mode") in
                     {"remote", "hybrid", "onsite"} else None,
        "company_type": item.get("company_type") if item.get("company_type") in
                        {"bigtech", "unicorn", "startup", "foreign", "soe", "traditional"} else None,
        "wlb_level": item.get("wlb_level") if item.get("wlb_level") in (0, 1, 2, 3, 4) else None,
        "wlb_evidence": str(item.get("wlb_evidence") or "").strip(),
        "skills": skills,
        "skills_raw": [str(s)[:30] for s in (item.get("skills_raw") or [])][:15],
        "desc_zh": str(item.get("desc_zh") or "").strip()[:600],
    }


def extract_llm(gateway: LLMGateway, texts: list[str], batch_size: int = 5) -> list[dict | None]:
    """LLM 批量结构化；批量缺失的条目逐条重试；仍失败返回 None（回落规则结果）。"""
    out: list[dict | None] = [None] * len(texts)
    valid = taxonomy.valid_ids()
    dictionary = json.dumps({sid: group for sid, group, _ in taxonomy.DEFAULT_SKILLS},
                            ensure_ascii=False)
    system = _EXTRACT_SYSTEM.replace("{dictionary}", dictionary)

    for start in range(0, len(texts), batch_size):
        chunk = texts[start:start + batch_size]
        user = ("以下是 %d 条 JD，用 --- 分隔：\n\n%s\n\n"
                "输出 %d 个元素的 JSON 数组，idx 分别为 %s。" % (
                    len(chunk),
                    "\n---\n".join(f"[{start + i}] {t[:2500]}" for i, t in enumerate(chunk)),
                    len(chunk), list(range(start, start + len(chunk)))))
        try:
            data, _res = gateway.chat_json("extraction", system, user)
        except LLMError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            parsed = _parse_extract_item(item, len(texts), valid)
            if parsed:
                out[parsed["idx"]] = parsed

    # 批量缺失的（json_object 模式下部分模型只吐单个对象）→ 逐条重试
    missing = [i for i, r in enumerate(out) if r is None]
    for i in missing[:40]:
        try:
            data, _res = gateway.chat_json(
                "extraction", system,
                f"以下是 1 条 JD：\n\n[{i}] {texts[i][:2500]}\n\n输出仅含这 1 个元素的 JSON 数组，idx={i}。")
        except LLMError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            parsed = _parse_extract_item(item, len(texts), valid)
            if parsed and parsed["idx"] == i:
                out[i] = parsed
                break
    return out


# ---------- 合并入库 ----------

def _tokenize_for_fts(*parts: str) -> str:
    text = " ".join(p for p in parts if p)
    return " ".join(jieba.cut_for_search(text))


def structure(conn: sqlite3.Connection, texts: list[str], source: str,
              gateway: LLMGateway | None = None, use_llm: bool = True) -> IngestSummary:
    texts = [t.strip() for t in texts if t and t.strip()]
    summary = IngestSummary(total=len(texts))
    llm_results: list[dict | None] = [None] * len(texts)
    if use_llm and gateway and gateway.settings.llm_ready("extraction"):
        try:
            llm_results = extract_llm(gateway, texts)
            summary.llm_used = any(r is not None for r in llm_results)
        except Exception as e:  # noqa: BLE001 LLM 失败不影响规则入库
            summary.errors.append(f"LLM 抽取失败：{e}")

    for text, llm in zip(texts, llm_results):
        job = extract_rules(text)
        if llm:
            # LLM 结果优先，规则补缺
            for k in ("title", "company", "city", "experience", "education"):
                if llm.get(k):
                    job[k] = llm[k]
            # 薪资必须「原文可溯」：LLM 的 salary_raw 可解析时 raw 与数值一起更新，
            # 否则整体保留规则层结果（防止 raw 被覆盖成「面议」而数值残留）
            llm_raw = (llm.get("salary_raw") or "").strip()
            if llm_raw:
                salary = parse_salary(llm_raw)
                if salary:
                    job["salary_raw"] = llm_raw
                    job.update(salary_min_k=salary.min_k, salary_max_k=salary.max_k,
                               salary_months=salary.months,
                               annual_min_wan=salary.annual_min_wan,
                               annual_max_wan=salary.annual_max_wan)
            for k in ("work_mode", "company_type"):
                if llm.get(k):
                    job[k] = llm[k]
            # WLB：规则层有显式信号时优先（正则可靠），LLM 仅在无信号时兜底
            rule_signals = json.loads(job["wlb_signals"])
            if llm.get("wlb_level") is not None and not rule_signals:
                job["wlb_level"] = llm["wlb_level"]
                if llm.get("wlb_evidence"):
                    job["wlb_signals"] = json.dumps(
                        [{"term": "llm", "kind": "llm", "label": "模型判断",
                          "quote": llm["wlb_evidence"]}], ensure_ascii=False)
                    job["wlb_basis"] = f"模型判断 L{llm['wlb_level']}"
            if llm.get("skills"):
                merged = list(dict.fromkeys(json.loads(job["skills"]) + llm["skills"]))
                job["skills"] = json.dumps(merged, ensure_ascii=False)
            if llm.get("skills_raw"):
                job["skills_raw"] = json.dumps(llm["skills_raw"], ensure_ascii=False)
            if llm.get("desc_zh"):
                job["description_zh"] = llm["desc_zh"]
            job["extract_method"] = "hybrid"
            job["structured_ok"] = 1
        job["source"] = source
        job["description"] = text[:8000]
        job["skills_text"] = taxonomy.skills_text(json.loads(job["skills"]))
        job["desc_tokens"] = _tokenize_for_fts(job["title"], job["company"], job["city"],
                                               job["skills_text"], text)
        job["fetched_at"] = db.now()
        job["created_at"] = db.now()
        job["dedup_hash"] = db.dedup_hash(job["title"], job["company"], job["city"],
                                          job["salary_raw"], job["description"])
        try:
            _id, is_new = db.upsert_job(conn, job)
            if is_new:
                summary.added += 1
            else:
                summary.duplicates += 1
        except Exception as e:  # noqa: BLE001
            summary.errors.append(f"入库失败（{job.get('title', '?')}）：{e}")
    return summary


def split_paste(text: str) -> list[str]:
    """粘贴的多条 JD 按 --- 分隔线切分；无分隔线则视为整条。"""
    parts = [p.strip() for p in re.split(r"^\s*[-=*]{3,}\s*$", text, flags=re.M) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])
