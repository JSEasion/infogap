"""WLB（工作生活平衡）隐性信号抽取。

加班程度刻度（与偏好「加班容忍度」同一把尺）：
  L0 = 965/弹性/双休不打卡      L1 = 常规偶尔加班（日均≤1h）
  L2 = 常态加班                 L3 = 大小周/995/单休
  L4 = 996 及以上
JD 很少直说，靠话术信号判断；每个结论都带原文引用，可追溯。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

@dataclass
class Signal:
    term: str
    kind: str      # pos | neg
    label: str
    quote: str

@dataclass
class WlbResult:
    level: int | None                 # 0-4；None=无信号无法判断
    signals: list[Signal] = field(default_factory=list)
    basis: str = ""                   # 结论依据说明


# (regex, kind, label, explicit_level)
_RULES: list[tuple[str, str, str, int | None]] = [
    (r"996|9116|9127|早9晚9", "neg", "996类作息", 4),
    (r"大小周", "neg", "大小周", 3),
    (r"995", "neg", "995作息", 3),
    (r"单休", "neg", "单休", 3),
    (r"965|10[-－—·]7[-－—·]5|早9晚5|朝九晚五", "pos", "965类作息", 0),
    (r"不加班|没有加班|无加班", "pos", "明确不加班", 0),
    (r"双休|做五休二|周末双休|五天工作制", "pos", "双休", 0),
    (r"弹性(工作|时间|打卡|上班)", "pos", "弹性工作", 0),
    (r"不打卡", "pos", "不打卡", 0),
    (r"远程办公|居家办公|wfh|work[- ]from[- ]home", "pos", "远程办公", 0),
    (r"工作生活平衡|work[- ]life", "pos", "工作生活平衡", 0),
    (r"1[0-9]天年假|1[0-9]天带薪年假", "pos", "长年假", 0),
    # 无明确等级的负面话术（加班重灾区暗示）
    (r"抗压(能力)?(强|性强|能力强)?", "neg", "「抗压」话术", None),
    (r"高强度", "neg", "高强度", None),
    (r"快节奏|节奏快", "neg", "快节奏", None),
    (r"狼性", "neg", "狼性文化", None),
    (r"创业(氛围|心态|热情)", "neg", "创业氛围话术", None),
    (r"能承受(工作)?压力", "neg", "承受压力", None),
    (r"接受加班|适应加班|加班多|经常加班|需要加班|加班文化", "neg", "明示加班", None),
    (r"加班(费|补贴|餐补)", "neg", "有加班费（暗示常加班）", None),
    (r"奋斗者", "neg", "奋斗者话术", None),
    (r"大小周.?工作", "neg", "大小周", 3),
]

_NEGATION = "不非无没拒避"


def _negated(text: str, start: int) -> bool:
    """向前看 4 个字符内是否出现否定词（不强制/无/不搞/避免…）。"""
    return any(c in text[max(0, start - 4):start] for c in _NEGATION)


def analyze_wlb(text: str) -> WlbResult:
    if not text:
        return WlbResult(None)
    t = text.lower()
    signals: list[Signal] = []
    explicit_levels: list[int] = []
    pos = neg = 0
    seen_spans: list[tuple[int, int]] = []

    for pattern, kind, label, level in _RULES:
        for m in re.finditer(pattern, t):
            # 被「不/无/拒绝」修饰的负面信号 → 翻转为正面（如「不强制996」）
            s, e = m.span()
            if any(s < ps <= s + 4 and pe >= e for ps, pe in seen_spans):
                continue
            quote = text[max(0, s - 8):min(len(text), e + 8)].replace("\n", " ")
            eff_kind = kind
            if kind == "neg" and _negated(t, s):
                eff_kind, level_eff = "pos", None
                label_eff = f"否定形式({label})"
            else:
                label_eff, level_eff = label, level
            if level_eff is not None:
                explicit_levels.append(level_eff)
            if eff_kind == "pos":
                pos += 1
            else:
                neg += 1
            signals.append(Signal(m.group(0), eff_kind, label_eff, quote))
            seen_spans.append((s, e))

    if explicit_levels:
        level = max(explicit_levels)   # 就高不就低（保守估计）
        basis = f"出现明示等级信号，就高取 L{level}"
    elif pos == 0 and neg == 0:
        return WlbResult(None, signals, "无 WLB 信号")
    elif neg > pos:
        level = 2
        basis = "负面话术多于正面信号，估 L2（常态加班）"
    elif neg > 0:
        level = 1
        basis = "正负信号兼有，估 L1（常规加班）"
    else:
        level = 1 if pos == 1 else 0
        basis = "仅正面信号，估 L0-L1"
    return WlbResult(level, signals, basis)


WLB_LEVEL_DESC = {
    0: "L0 · 965/弹性/双休",
    1: "L1 · 常规偶尔加班",
    2: "L2 · 常态加班",
    3: "L3 · 大小周/995",
    4: "L4 · 996及以上",
}
