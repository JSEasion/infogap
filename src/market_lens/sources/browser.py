"""浏览器半自动采集：解析你自己在招聘站登录后另存的搜索结果 HTML。

合规边界：只解析「你本人账号正常搜索/浏览」得到的页面，供个人分析，不做规模化爬取。
选择器会随站点改版漂移——这里做多策略兜底，失效时优先怀疑选择器。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup


def _cards_zhipin(soup: BeautifulSoup) -> list[str]:
    out = []
    for li in soup.select("li.job-card-wrapper, div.job-card-wrapper, li.job-card-left"):
        title = li.select_one(".job-name, .job-title, span[class*=job-name]")
        salary = li.select_one(".salary, .job-salary")
        company = li.select_one(".company-name, .boss-name")
        area = li.select_one(".job-area, .company-location")
        tags = [t.get_text(strip=True) for t in li.select(".tag-list li, .job-tags span")][:8]
        if not (title or salary):
            continue
        out.append("\n".join(filter(None, [
            title.get_text(strip=True) if title else "",
            f"薪资：{salary.get_text(strip=True)}" if salary else "",
            company.get_text(strip=True) if company else "",
            area.get_text(strip=True) if area else "",
            " ".join(tags),
        ])))
    return out


def _cards_liepin(soup: BeautifulSoup) -> list[str]:
    out = []
    for card in soup.select(".job-card-pc-container, .job-card-box, li[data-job-id]"):
        title = card.select_one(".job-title, .job-info .title, h3")
        salary = card.select_one(".job-salary, .salary")
        company = card.select_one(".company-name, .employer-name")
        tags = [t.get_text(strip=True) for t in card.select(".job-tags span, .tag-list li")][:8]
        if not (title or salary):
            continue
        out.append("\n".join(filter(None, [
            title.get_text(strip=True) if title else "",
            f"薪资：{salary.get_text(strip=True)}" if salary else "",
            company.get_text(strip=True) if company else "",
            " ".join(tags),
        ])))
    return out


def parse_html(html: str) -> list[str]:
    """从另存的招聘站搜索结果页提取 JD 卡片文本（Boss直聘/猎聘）。"""
    soup = BeautifulSoup(html, "html.parser")
    cards = _cards_zhipin(soup) or _cards_liepin(soup)
    if not cards:
        # 兜底：尝试页面内嵌 JSON（Boss 等站点会把列表数据打进 window 状态）
        for pat in (r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});",
                    r'window\.__ZONE__\s*=\s*(\{.*?\});'):
            m = re.search(pat, html, re.S)
            if m:
                try:
                    data = json.loads(m.group(1))

                    def walk(node):
                        if isinstance(node, dict):
                            keys = set(node)
                            if {"jobName", "salaryDesc"} <= keys or {"name", "salary"} <= keys:
                                return [json.dumps(node, ensure_ascii=False)]
                            out = []
                            for v in node.values():
                                out += walk(v)
                            return out
                        if isinstance(node, list):
                            out = []
                            for v in node:
                                out += walk(v)
                            return out
                        return []

                    cards = walk(data)[:50]
                    if cards:
                        break
                except json.JSONDecodeError:
                    continue
    return cards


def parse_dir(html_dir: Path) -> list[str]:
    """批量解析目录下所有 .html/.htm 文件。"""
    texts: list[str] = []
    for f in sorted(html_dir.glob("*.html")) + sorted(html_dir.glob("*.htm")):
        try:
            texts += parse_html(f.read_text(encoding="utf-8", errors="ignore"))
        except Exception:  # noqa: BLE001
            continue
    return texts
