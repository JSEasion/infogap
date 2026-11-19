"""资讯含金量闭环：无效摘要检测 → 正文补全 → LLM 信号卡。

铁律：UI 永不展示原始摘要。每条精选资讯必须以「信号卡」形态呈现
（徽章 + 一句话 + 要点 + 影响）；LLM 不可用时降级为规则信号卡（正文摘录），
绝不降级为垃圾原文。extract_fulltext 同时服务 URL 智能导入。
"""
from __future__ import annotations

import re

import httpx

from . import db
from .models import NewsSignalCard
from .textfmt import clean_inline, is_bad_summary, normalize_text

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) market-lens/0.1 (personal research)"

# ---------------- 正文抽取（bs4 启发式，URL 导入复用） ----------------

_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "noscript",
               "form", "iframe", "svg", "button")


def extract_fulltext(html: str) -> str:
    """启发式正文抽取：剔除噪音标签，取文本密度最大的容器。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()
    best, best_len = "", 0
    for node in soup.find_all(["article", "div", "section", "main", "td"]):
        ps = node.find_all(["p", "li", "pre", "blockquote"], recursive=True)
        if not ps:
            continue
        text = normalize_text("\n\n".join(p.get_text(" ", strip=True) for p in ps))
        # 惩罚链接 farms：链接文字占比过高的容器
        links = node.find_all("a")
        link_len = sum(len(a.get_text(strip=True)) for a in links)
        if len(text) > best_len and len(text) > max(120, link_len * 1.5):
            best, best_len = text, len(text)
    if not best:   # 兜底：<p> 全集
        ps = soup.find_all("p")
        best = normalize_text("\n\n".join(p.get_text(" ", strip=True) for p in ps))
    return best


def fetch_fulltext(url: str, timeout: float = 20.0) -> str:
    """抓原文页并抽取正文；失败返回空串。"""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": UA}) as c:
            r = c.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            return ""
        return extract_fulltext(r.text)
    except Exception:  # noqa: BLE001
        return ""


# ---------------- 规则信号卡（LLM 不可用时的降级，永不展示原始摘要） ----------------

_SIG_RULES = [
    (re.compile(r"融资|获投|完成.{0,4}(轮|亿|万)美元?|领投|B\+?轮|[ABC]轮融资", re.I), "funding"),
    (re.compile(r"裁员|优化|毕业|缩减|停招|冻编|降薪", re.I), "layoff"),
    (re.compile(r"发布|上线|推出|开源|开放|亮相|available|release|launch", re.I), "release"),
    (re.compile(r"政策|法规|意见|条例|备案|监管|合规|准入", re.I), "policy"),
    (re.compile(r"招聘|扩招|内推|校招|社招|headcount|开放岗位", re.I), "hiring"),
    (re.compile(r"论文|突破|SOTA|benchmark|模型|架构|训练|推理加速", re.I), "tech"),
]
_ENT_KNOWN = re.compile(
    r"(OpenAI|Anthropic|Google|DeepMind|Meta|微软|谷歌|字节|阿里|腾讯|百度|华为|美团|京东|"
    r"拼多多|快手|小红书|智谱|月之暗面|DeepSeek|零一|阶跃|Minimax|讯飞|商汤|Mistral|xAI|"
    r"特斯拉|英伟达|NVIDIA|苹果|亚马逊|GPT-\d+|Claude|Gemini|LLaMA|Qwen|GLM-\d+|DeepSeek-V\d)")


def fallback_card(title: str, fulltext: str) -> NewsSignalCard:
    """规则信号卡：one_liner 取标题，key_points 取正文前几句。"""
    one = clean_inline(title, limit=40)
    body = fulltext or ""
    sents = [s.strip() for s in re.split(r"(?<=[。！？!?])", body) if len(s.strip()) >= 8]
    points = [clean_inline(s, limit=80) for s in sents[:3]]
    sig = "none"
    blob = f"{title} {body[:300]}"
    for rex, name in _SIG_RULES:
        if rex.search(blob):
            sig = name
            break
    ents = list(dict.fromkeys(_ENT_KNOWN.findall(blob)))[:6]
    return NewsSignalCard(one_liner=one, key_points=points, signal_type=sig,  # type: ignore[arg-type]
                          entities=ents, impact="")


# ---------------- LLM 信号卡 ----------------

_CARD_SYSTEM = """你是行业资讯编辑，为 AI 方向工程师求职者过滤信息差。只输出 JSON 数组，禁止解释文字。
铁律：所有输出字段一律用简体中文；原文是英文的必须翻译成中文
（专有名词如 GPT/Transformer/公司名可保留英文）。
对每条内容生成「信号卡」：
{"idx": 序号, "one_liner": "40字内中文说清发生了什么（主体+事件+关键数字）",
 "key_points": ["2-4条中文要点：核心事实/方法/关键数字/结论，每条≤60字"],
 "signal_type": "funding融资|layoff裁员|release产品发布|policy政策|hiring招聘动向|tech技术突破|none",
 "entities": ["涉及的公司/产品/技术/人物"],
 "impact": "20-40字中文：对 AI 工程师求职/技能方向的具体含义"}
论文/开源项目类（kind=tech）侧重：这项技术是什么、解决什么问题、关键结果数字、工程师为什么要关注。
要求：one_liner 必须有信息量（禁止「某公司发布重要公告」空话）；数字尽量保留；
绝不照抄英文原句，必须是提炼后的中文。"""

# 会话级熔断：连续 3 批失败后 10 分钟内直接走规则卡（单批失败不熔断）
_CARD_COOLDOWN = {"until": 0.0}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_BATCH = 3   # 小批：推理模型 think 时长随批量陡增，大批易超时


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _card_from_obj(obj: dict) -> NewsSignalCard | None:
    """LLM 输出 → 信号卡；无效（无中文 one_liner）返回 None。"""
    st = str(obj.get("signal_type") or "none")
    if st not in ("funding", "layoff", "release", "policy", "hiring", "tech", "none"):
        st = "none"
    card = NewsSignalCard(
        one_liner=clean_inline(str(obj.get("one_liner") or ""), limit=60),
        key_points=[clean_inline(str(p), limit=90) for p in (obj.get("key_points") or [])[:4]
                    if clean_inline(str(p))],
        signal_type=st,  # type: ignore[arg-type]
        entities=[str(e)[:30] for e in (obj.get("entities") or [])[:6]],
        impact=clean_inline(str(obj.get("impact") or ""), limit=70))
    if card.one_liner and _has_cjk(card.one_liner):   # 必须是有效中文一句话
        card.method = "llm"
        return card
    return None


def _judge_batch(gateway, batch: list[dict]) -> dict[int, dict]:
    """单批 LLM 调用：返回 {raw_id: obj}。失败抛异常（由调用方决定降级范围）。"""
    parts = []
    for i, it in enumerate(batch):
        parts.append(f"[{i}] kind:{it.get('kind', 'news')} 来源:{it.get('source', '')}\n"
                     f"标题:{(it.get('title') or '')[:160]}\n"
                     f"正文:{(it.get('body') or '')[:1000]}")
    user = (f"以下是 {len(batch)} 条内容，用 --- 分隔：\n\n" +
            "\n---\n".join(parts) +
            f"\n\n输出 {len(batch)} 个元素的 JSON 数组，idx 为 0..{len(batch) - 1}，全部中文。")
    # 注意：不能用 json_object 模式——它强制单对象，数组第二条会被吞掉。
    # 靠提示词约束 + extract_json 鲁棒提取。
    from .gateway import extract_json
    res = gateway.chat("summarize", _CARD_SYSTEM, user, json_mode=False,
                       max_tokens=6000, timeout=150.0)
    data = extract_json(res.text)
    if data is None:
        from .gateway import LLMError
        raise LLMError(f"信号卡输出无法解析为 JSON（前200字：{res.text[:200]!r}）")
    arr = data if isinstance(data, list) else [data]
    judged: dict[int, dict] = {}
    for obj in arr:
        if not isinstance(obj, dict):
            continue
        try:
            idx = int(obj.get("idx"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(batch):
            judged[batch[idx]["id"]] = obj
    return judged


def build_signal_cards(gateway, items: list[dict]) -> dict[int, NewsSignalCard]:
    """批量 LLM 生成信号卡（分批调用，单批失败只降级该批）。
    返回 {raw_id: card}。LLM 不可用/失败 → fallback_card 兜底。"""
    out: dict[int, NewsSignalCard] = {}
    if not items:
        return out
    import time as _time
    judged: dict[int, dict] = {}
    consec_fail = 0
    if gateway is not None:
        for s in range(0, len(items), _BATCH):
            if _time.monotonic() < _CARD_COOLDOWN["until"]:
                break
            batch = items[s:s + _BATCH]
            try:
                batch_ok = _judge_batch(gateway, batch)
                judged.update(batch_ok)
                consec_fail = 0
                _CARD_COOLDOWN["until"] = 0.0   # 成功即解除熔断
            except Exception:  # noqa: BLE001 单批失败：只降级该批
                consec_fail += 1
                if consec_fail >= 3:            # 连续 3 批失败才熔断
                    _CARD_COOLDOWN["until"] = _time.monotonic() + 600
                    break

    for it in items:
        obj = judged.get(it["id"])
        card = _card_from_obj(obj) if obj else None
        out[it["id"]] = card or fallback_card(it.get("title", ""), it.get("body") or "")
    return out


# ---------------- 热度提取（GitHub ⭐ / HF upvotes） ----------------

_STAR_RE = re.compile(r"⭐\s*([\d,.]+)\s*([kK万]?)")


def extract_heat(item: dict) -> int:
    """从渠道元数据/正文解析热度值：HF upvotes > GitHub stars > 0。"""
    meta = item.get("channel_meta") or {}
    try:
        if meta.get("upvotes"):
            return int(meta["upvotes"])
    except (TypeError, ValueError):
        pass
    m = _STAR_RE.search(item.get("body") or item.get("fulltext") or "")
    if m:
        num = float(m.group(1).replace(",", ""))
        mult = {"k": 1000, "K": 1000, "万": 10000}.get(m.group(2), 1)
        return int(num * mult)
    return 0


# ---------------- 资讯入库（信号卡 + FTS） ----------------

_SIGNAL_WEIGHT = {"funding": 1.0, "layoff": 1.0, "release": .8, "policy": .8,
                  "hiring": .9, "tech": .7, "none": .3}


def _tokenize_news(*parts: str) -> str:
    import jieba
    text = " ".join(p for p in parts if p)
    return " ".join(jieba.cut_for_search(text))


def publish_news(conn, item: dict, card: NewsSignalCard, gold: float,
                 score_json: str, raw_id: int | None = None,
                 item_kind: str = "news") -> int | None:
    """精选资讯/技术落库（信号卡字段 + FTS 分词 + 信号权重行）。返回 news.id。"""
    import json as _json
    fts = _tokenize_news(item.get("title", ""), card.one_liner,
                         " ".join(card.key_points), item.get("body") or "")
    db.upsert_news(conn, {
        "source": item.get("source", ""), "title": item.get("title", ""),
        "url": item.get("source_url") or item.get("url") or "",
        "summary": "",                      # 原始摘要不展示，留空防止直出
        "published_at": item.get("published_at", ""),
        "fetched_at": db.now(),
        "gold_score": gold, "gold_json": score_json, "raw_id": raw_id,
        "one_liner": card.one_liner,
        "key_points": _json.dumps(card.key_points, ensure_ascii=False),
        "entities_json": _json.dumps(card.entities, ensure_ascii=False),
        "impact": card.impact,
        "signal_type": card.signal_type,
        "card_method": card.method,
        "heat": extract_heat(item),
        "fulltext": (item.get("body") or "")[:20000],
        "fts_tokens": fts,
        "kind": item_kind,
    })
    row = conn.execute("SELECT id FROM news WHERE url=?",
                       ((item.get("source_url") or item.get("url") or ""),)).fetchone()
    if row:
        db.save_signal(conn, row["id"], card.signal_type, card.entities,
                       _SIGNAL_WEIGHT.get(card.signal_type, .3) * (gold / 100 + .3))
        return row["id"]
    return None


# ---------------- 存量回填治理（英文/规则卡 → 中文 LLM 信号卡） ----------------

def is_governed(row: dict) -> bool:
    """治理完成判定：LLM 卡 且 一句话为中文。"""
    return row.get("card_method") == "llm" and _has_cjk(row.get("one_liner") or "")


def regovern_news(conn, gateway, limit: int = 200, kinds: tuple[str, ...] = ("news", "tech"),
                  progress=None) -> dict:
    """把未治理（规则卡/英文直出）的资讯/技术条目重新提取为中文信号卡。
    幂等：只处理 card_method != 'llm' 或 one_liner 无中文的行；LLM 不可用时原样返回。"""
    import json as _json
    ph = ",".join("?" for _ in kinds)
    rows = conn.execute(
        f"SELECT * FROM news WHERE kind IN ({ph}) AND url != '' "
        f"AND (card_method IS NULL OR card_method != 'llm' "
        f"OR one_liner = '' OR one_liner = title) ORDER BY id DESC LIMIT ?",
        (*kinds, int(limit))).fetchall()
    items = [{"id": r["id"], "kind": r["kind"], "source": r["source"],
              "title": r["title"], "body": r["fulltext"] or r["title"],
              "channel_meta": {}, "gold": r["gold_score"] or 0} for r in rows]
    if not items:
        return {"pending": 0, "regoverned": 0}
    if progress:
        progress(5, "治理", f"待重新治理 {len(items)} 条…")
    cards = build_signal_cards(gateway, items)
    regoverned = 0
    for i, it in enumerate(items):
        card = cards.get(it["id"])
        if not card or card.method != "llm":   # 仍是规则卡 → 留待下次（LLM 恢复后重跑）
            continue
        fts = _tokenize_news(it["title"], card.one_liner, " ".join(card.key_points),
                             it["body"] or "")
        conn.execute(
            "UPDATE news SET one_liner=?, key_points=?, entities_json=?, impact=?, "
            "signal_type=?, card_method=?, heat=CASE WHEN heat>0 THEN heat ELSE ? END, "
            "fts_tokens=? WHERE id=?",
            (card.one_liner, _json.dumps(card.key_points, ensure_ascii=False),
             _json.dumps(card.entities, ensure_ascii=False), card.impact,
             card.signal_type, card.method, extract_heat(it), fts, it["id"]))
        db.save_signal(conn, it["id"], card.signal_type, card.entities,
                       _SIGNAL_WEIGHT.get(card.signal_type, .3) * (it["gold"] / 100 + .3))
        regoverned += 1
        if progress and (i % 6 == 5 or i == len(items) - 1):
            progress(5 + 90 * (i + 1) // len(items), "治理",
                     f"已治理 {regoverned}/{len(items)}")
    conn.commit()
    if progress:
        progress(100, "完成", f"重新治理 {regoverned}/{len(items)} 条")
    return {"pending": len(items), "regoverned": regoverned}


def needs_fulltext(item: dict) -> bool:
    """正文补全判定：摘要无效且正文过短。"""
    body = (item.get("body") or "").strip()
    if len(body) >= 300:
        return False
    bad, _why = is_bad_summary(body or item.get("title", ""), item.get("title", ""))
    return bad or len(body) < 300
