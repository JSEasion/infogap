"""文本规范化：清除 HTML→文本转换伪影，输出可排版段落。

抓取层把 HTML 转成纯文本时会留下伪影：标签替换产生的汉字间空格
（`<b>负责</b>开发` → `负责 开发`）、块级元素转换产生的连续空行、
行首缩进与行尾空白、过短碎行。JD 原文、资讯正文入库前统一过这里。
"""
from __future__ import annotations

import re

# CJK 字符与中文标点（用于「伪影空格」判定：两侧都是这类字符时，中间的空格是转换伪影）
_CJK = (
    "\u4e00-\u9fff"        # 汉字
    "\u3400-\u4dbf"        # 扩展A
    "\u3000-\u303f"        # 中文标点 ，。、；：！？（）【】《》…
    "\uff00-\uffef"        # 全角形式 ！，：；？（）－
)
# 空格两侧为 CJK/中文标点 → 伪影，删除
_CJK_SPACE = re.compile(rf"(?<=[{_CJK}]) +(?=[{_CJK}])")
# 全角空格：CJK 环境删除，其他场景折半角
_FW_SPACE = "\u3000"
# 连续空白（非换行）→ 单空格
_MULTI_SPACE = re.compile(r"[^\S\n]+")
# 3+ 连续换行 → 2（一个空行 = 段落边界）
_MULTI_NL = re.compile(r"\n{3,}")
# 列表符归一（CJK 圆点后空格可省；ASCII 连字符后须空格或紧跟 CJK，避免误伤 -able）
_BULLET = re.compile(rf"^[ \t]*(?:([•●◦▪‣∙·＊*])[ \t]*|([-\u2013\u2014])[ \t]+"
                     rf"|([-\u2013\u2014])(?=[{_CJK}]))")
_BULLET_MAP = {"＊": "·", "*": "·"}
# 句末标点（碎行合并判定：上一行以这些结尾时不与下一行合并）
_TERMINAL = tuple("。！？；：!?;:")
# 行首「不可合并」特征：列表项 / 编号项 / 引用
_UNMERGEABLE_HEAD = re.compile(r"^(\d+[.、）)]|[①②③④⑤⑥⑦⑧⑨⑩]|·|>)")
# 标题行：短且以冒号结尾（如「岗位职责：」）
_HEADLIKE = re.compile(r"^[^，。！？\n]{1,25}[：:]$")


def normalize_text(text: str) -> str:
    """清除 HTML→文本伪影：汉字间空格、连续空行、行首尾空白、碎行合并、列表符归一。"""
    if not text:
        return ""
    # 1) 全角空格处理
    text = text.replace(_FW_SPACE, " ")
    # 2) 汉字/中文标点之间的伪影空格
    text = _CJK_SPACE.sub("", text)
    # 3) 行内多空白 → 单空格（保留换行）
    lines = [_MULTI_SPACE.sub(" ", ln).strip() for ln in text.splitlines()]
    # 4) 列表符归一（•●◦… → ·；保持缩进平齐）
    lines = [_norm_bullet(ln) for ln in lines]
    # 5) 去掉空行段（连续空行只留一个边界）
    out: list[str] = []
    blank = 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(ln)
    # 6) 碎行合并：上一行非标题/非列表、无句末标点，两行都像「正文延续」→ 并为一段
    merged: list[str] = []
    for ln in out:
        prev = merged[-1] if merged else ""
        if (prev and ln and prev != "" and not _UNMERGEABLE_HEAD.match(ln)
                and not _UNMERGEABLE_HEAD.match(prev)
                and not _HEADLIKE.match(prev) and len(prev) < 60
                and not prev.endswith(_TERMINAL) and not prev.rstrip().endswith("。")
                and _is_prose(prev) and _is_prose(ln)):
            merged[-1] = prev + ln
        else:
            merged.append(ln)
    body = "\n".join(merged).strip("\n")
    return _MULTI_NL.sub("\n\n", body).strip()


def _is_prose(line: str) -> bool:
    """碎行合并保守判定：至少 6 字且以汉字/字母收尾（避免合并数据行/标签行）。"""
    return len(line) >= 6 and bool(re.search(rf"[{_CJK}A-Za-z0-9\"」』)）]$", line))


def _norm_bullet(line: str) -> str:
    m = _BULLET.match(line)
    if not m:
        return line
    mark_src = next(g for g in m.groups() if g)
    mark = _BULLET_MAP.get(mark_src, "·")
    return f"{mark} {line[m.end():].lstrip()}"


def to_paragraphs(text: str) -> list[str]:
    """规范化后按空行切段，供模板分段渲染 <p>。"""
    body = normalize_text(text)
    if not body:
        return []
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


def clean_inline(text: str, limit: int = 0) -> str:
    """单行清洗（资讯摘要等短文本）：去伪影空格/换行折叠为空格。"""
    body = normalize_text(text or "").replace("\n", " ")
    body = _MULTI_SPACE.sub(" ", body).strip()
    return body[:limit] if limit else body


# 标题里混入的结构化字段（老渠道把「公司:…城市:…」整行当首行）
_TITLE_FIELD = re.compile(r"(公司|城市|地点|薪资|经验|学历|单位类型|来源)[:：]")


def clean_title(title: str, limit: int = 40) -> str:
    """标题清洗：遇结构化字段标记截断，去杂符号。"""
    t = (title or "").strip()
    m = _TITLE_FIELD.search(t)
    if m:
        t = t[:m.start()].strip()
    t = re.sub(r"[|｜#]+$", "", t).strip()
    return t[:limit] if limit else t


# ---------- 无效摘要检测（资讯含金量闭环第一环） ----------

# 无信息量摘要黑名单（config quality.bad_summary_patterns 可追加）
BAD_SUMMARY_PATTERNS = [
    r"点击查看原文", r"点击阅读全文", r"阅读全文", r"点击蓝字关注", r"关注公众号",
    r"长按识别二维码", r"扫描二维码", r"点击查看详情", r"查看详情", r"点击进入",
    r"详情请见", r"本文来自", r"广告", r"推广", r"了解更多请", r"点击文末",
    r"全文完", r"END", r"^https?://\S+$", r"^\s*$",
]
_BAD_SUMMARY_RE = re.compile("|".join(BAD_SUMMARY_PATTERNS), re.I)


def is_bad_summary(summary: str, title: str = "") -> tuple[bool, str]:
    """判定 RSS 摘要是否无信息量。返回 (是否无效, 命中原因)。"""
    s = clean_inline(summary)
    if not s or len(s) < 30:
        return True, f"过短（{len(s)}字）"
    m = _BAD_SUMMARY_RE.search(s)
    if m and m.group(0).lower() == s.strip().lower():
        return True, f"黑名单命中：{m.group(0)}"
    if m and len(s) < 60:  # 短摘要里命中黑名单词，基本全是引导语
        return True, f"黑名单命中：{m.group(0)}"
    t = clean_inline(title)
    if t and len(t) >= 8:
        # 与标题高度重复（包含或重复率>0.8）→ 摘要无增量信息
        if s in t or t in s:
            return True, "与标题重复"
        import difflib
        if difflib.SequenceMatcher(None, t, s).ratio() > 0.8:
            return True, "与标题重复"
    if not re.search(rf"[{_CJK}A-Za-z]", s):
        return True, "无实词"
    return False, ""
