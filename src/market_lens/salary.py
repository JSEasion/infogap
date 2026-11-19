"""薪资字符串解析：把「15-25K·14薪」「2-4万」「20-40万/年」「300-500元/天」等
统一解析为 月薪区间(K) + 年薪(万)。纯规则，零 LLM。"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 全角转半角 + 去空白
_TRANS = str.maketrans("０１２３４５６７８９．－～－＋Ｋｋ万元：", "0123456789.-~-+Kk万元:")


@dataclass
class SalaryParsed:
    min_k: float | None      # 月薪下限（千元/月）
    max_k: float | None      # 月薪上限（千元/月）
    months: int              # 年薪月数（默认12）
    annual_min_wan: float | None
    annual_max_wan: float | None
    note: str = ""

    @property
    def mid_k(self) -> float | None:
        if self.min_k is None and self.max_k is None:
            return None
        if self.min_k is None:
            return self.max_k
        if self.max_k is None:
            return self.min_k
        return round((self.min_k + self.max_k) / 2, 1)


def _mk(min_k, max_k, months, note="") -> SalaryParsed:
    a_min = round(min_k * months / 10, 1) if min_k is not None else None
    a_max = round(max_k * months / 10, 1) if max_k is not None else None
    return SalaryParsed(min_k, max_k, months, a_min, a_max, note)


def parse_salary(raw: str | None) -> SalaryParsed | None:
    """解析失败或「面议」返回 None。"""
    if not raw:
        return None
    text = raw.translate(_TRANS)
    text = re.sub(r"\s+", "", text)
    if not text or "面议" in text or "薪资面谈" in text or "negotiable" in text.lower():
        return None

    # ·14薪 / *14薪 / x14薪
    months = 12
    m = re.search(r"[·*xX](\d{1,2})薪", text)
    if m:
        months = int(m.group(1))
        text = text[:m.start()] + text[m.end():]

    # 外币（USD/EUR/GBP/JPY；PHP 等不支持币种直接放弃）。必须在去空格前的原文上识别：
    # "70k-100k EUR" 去空格后单词边界丢失，"美元/欧元" 等中文词也不在符号表里
    cur_m = re.search(r"[$€£]|(?<![a-zA-Z])(usd|eur|gbp|jpy|php|peso)(?![a-zA-Z])"
                      r"|美元|美金|英镑|欧元|日元|比索", raw, re.I)
    if cur_m:
        token = (cur_m.group(1) or cur_m.group(0)).lower()
        if token in ("php", "peso", "比索"):
            return None  # 比索等罕见币种不折算，宁缺毋假
        rate, cur_name = {"$": (7.2, "USD"), "€": (7.8, "EUR"), "£": (9.1, "GBP"),
                          "usd": (7.2, "USD"), "美元": (7.2, "USD"), "美金": (7.2, "USD"),
                          "eur": (7.8, "EUR"), "欧元": (7.8, "EUR"),
                          "gbp": (9.1, "GBP"), "英镑": (9.1, "GBP"),
                          "jpy": (0.049, "JPY"), "日元": (0.049, "JPY")}[token]
        m = (re.search(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*k?\s*[-–~to]+\s*[$€£]?\s*"
                       r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*k", text, re.I) or
             re.search(r"(\d{1,3}(?:,\d{3})+)\s*[-–~to]+\s*[$€£]?\s*(\d{1,3}(?:,\d{3})+)",
                       text, re.I))
        if m:
            lo = float(m.group(1).replace(",", ""))
            hi = float(m.group(2).replace(",", ""))
            k_suffix = bool(re.search(rf"{re.escape(m.group(2))}\s*k", text, re.I))
            if k_suffix:                       # $120k-$160k：K 单位 → 年薪数值
                lo, hi = lo * 1000, hi * 1000
            if re.search(r"/年|/yr|/year|a\s*year", text, re.I) or hi >= 30000:
                return _mk(round(lo * rate / 12 / 1000, 1), round(hi * rate / 12 / 1000, 1),
                           12, f"{cur_name}年薪折算人民币月薪")
            return _mk(round(lo * rate / 1000, 1), round(hi * rate / 1000, 1), months,
                       f"{cur_name}月薪折算人民币")
        # 单值下限：$120,000+ / £90k+（K 写法或 ≥3万 视为年薪；小额外值月薪/奖金歧义大，放弃）
        m = re.search(r"[$€£]\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k?)\s*(?:\+|以上)?", text)
        if m:
            v = float(m.group(1).replace(",", ""))
            if m.group(2):
                v *= 1000
            if v >= 30000:
                return _mk(round(v * rate / 12 / 1000, 1), None, 12,
                           f"{cur_name}年薪折算人民币月薪")
        return None  # 外币无法可靠定夺时绝不落回人民币口径

    # X,000–Y,000元/月（月薪按元计，含全角/EN横线）
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d{3,6})\s*[-–~到至]\s*(\d{1,3}(?:,\d{3})+|\d{3,6})"
                  r"\s*元(?:人民币)?\s*[/每]?月", text)
    if m:
        lo = float(m.group(1).replace(",", "")) / 1000
        hi = float(m.group(2).replace(",", "")) / 1000
        return _mk(round(lo, 1), round(hi, 1), months, "元/月直读")

    # 万/年（年薪）：20-40万/年、20万-40万/年、年薪40万
    m = re.search(r"(\d+(?:\.\d+)?)万?[-~到至](\d+(?:\.\d+)?)万/年", text) or \
        re.search(r"年薪(\d+(?:\.\d+)?)(?:万[-~到至](\d+(?:\.\d+)?))?万?/年?", text)
    if m:
        a, b = m.group(1), m.group(2)
        lo, hi = float(a), float(b) if b else float(a)
        if "年薪" in text and not b:  # 年薪40万 单值
            hi = lo
        return _mk(round(lo / 12 * 10, 1), round(hi / 12 * 10, 1), 12, "年薪折算月薪")

    # X万- Y万 / X-Y万 / X万~Y万（月薪，万为单位）
    m = re.search(r"(\d+(?:\.\d+)?)万[-~到至](\d+(?:\.\d+)?)万?", text) or \
        re.search(r"(\d+(?:\.\d+)?)[-~到至](\d+(?:\.\d+)?)万", text)
    if m:
        lo, hi = float(m.group(1)) * 10, float(m.group(2)) * 10
        return _mk(lo, hi, months)

    # 15-25K / 15K-25k / 15K以上 / 25K+
    m = re.search(r"(\d+(?:\.\d+)?)k?[-~到至](\d+(?:\.\d+)?)k", text, re.I)
    if m:
        return _mk(float(m.group(1)), float(m.group(2)), months)
    m = re.search(r"(\d+(?:\.\d+)?)k(?:以上|\+)", text, re.I)
    if m:
        return _mk(float(m.group(1)), None, months, "下限以上")

    # 300-500元/天（按 21.75 天/月折算）
    m = re.search(r"(\d+(?:\.\d+)?)[-~到至](\d+(?:\.\d+)?)元/天", text) or \
        re.search(r"(\d+(?:\.\d+)?)(?:[-~到至](\d+(?:\.\d+)?))?元/天", text)
    if m:
        a = float(m.group(1))
        b = float(m.group(2)) if m.group(2) else a
        return _mk(round(a * 21.75 / 1000, 1), round(b * 21.75 / 1000, 1), 12, "日薪折算")

    # 元/小时 不折算月薪，直接放弃（信息不足）
    return None
