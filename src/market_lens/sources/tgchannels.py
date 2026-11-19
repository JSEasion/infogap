"""Telegram 公开频道采集：t.me/s/<频道名> 网页预览，无需账号与 API。

信息差组核心渠道：大量内推/远程岗/加密圈+AI 岗位信息首发于 TG 频道，
公网搜索引擎难以索引。频道通过 config channels.telegram 配置（设置页可改）。

合规：只抓公开预览页，限速，个人研究。tier 低（0.55）起步，
靠交叉验证与反诈骗规则兜底（见 quality.py）。
"""
from __future__ import annotations

import re
import time

import httpx
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) xinxicha/0.1 (personal research)"

# 岗位/内推相关性过滤（频道里也混着大量闲聊）
_KEEP = re.compile(
    r"招聘|内推|急招|岗位|hiring|job|远程|remote|工程师|开发|算法|实习|校招|社招|"
    r"AI|LLM|Agent|大模型|ML|数据|后端|前端|全栈|Rust|Go\b|Python", re.I)
_SCAM = re.compile(r"缴纳|保证金|培训贷|包入职|付费内推", re.I)


def channel_url(name: str) -> str:
    name = name.strip().lstrip("@")
    return f"https://t.me/s/{name}"


def _meaningful_line(text: str) -> str:
    """首个有内容的行做标题（跳过 # 标签行/过短行/纯符号行）。"""
    for ln in text.splitlines():
        s = ln.strip().lstrip("#").strip()
        if len(s) >= 4 and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", s):
            return s[:80]
    return ""


def parse_channel(html: str, channel: str) -> list[dict]:
    """解析 t.me/s 预览页的消息列表 → RawItem 列表。"""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for msg in soup.select("div.tgme_widget_message"):
        text_node = msg.select_one("div.tgme_widget_message_text")
        if not text_node:
            continue
        text = text_node.get_text("\n", strip=True)
        if len(text) < 40 or not _KEEP.search(text) or _SCAM.search(text):
            continue
        link = ""
        a = msg.select_one("a.tgme_widget_message_date")
        if a and a.get("href"):
            link = a["href"]
        pub = ""
        t = msg.select_one("time")
        if t and t.get("datetime"):
            pub = t["datetime"]
        title = _meaningful_line(text)
        if not title:
            continue
        out.append({
            "kind": "job",
            "source": f"tg:{channel.strip().lstrip('@')}",
            "source_url": link,
            "title": title,
            "body": text[:4000],
            "published_at": pub,
            "channel_meta": {"channel": channel},
        })
    return out


def fetch_channel(channel: str, limit: int = 40) -> list[dict]:
    """抓取单个公开频道最近消息。频道未开预览/不存在 → 返回空。"""
    try:
        with httpx.Client(timeout=20, follow_redirects=True,
                          headers={"User-Agent": UA}) as c:
            r = c.get(channel_url(channel))
            r.raise_for_status()
            if "tgme_widget_message" not in r.text:
                return []
            return parse_channel(r.text, channel)[:limit]
    except Exception:  # noqa: BLE001
        return []


def collect(channels: list[str] | None = None) -> dict:
    """采集配置的频道。返回 {items, errors}。频道间限速 1.2s（礼貌）。"""
    from ..config import get_settings
    names = channels or get_settings().channels_cfg.get("telegram") or []
    items: list[dict] = []
    errors: list[str] = []
    for name in names:
        got = fetch_channel(name)
        if got:
            items += got
        else:
            errors.append(f"tg:{name} 无公开消息（未开预览或不存在）")
        time.sleep(1.2)
    return {"items": items, "errors": errors}
