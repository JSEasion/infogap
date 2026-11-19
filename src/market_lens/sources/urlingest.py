"""URL 智能导入：看到好网站 → 贴链接 → 自动识别页面类型并抓取（不豁免质量门）。

页面类型识别 classify()：
- 招聘列表/详情页（Boss直聘/猎聘/国聘/高校人才网…）→ 复用 browser.py 站点适配器
- 资讯/文章页 → news_pipeline.extract_fulltext 正文抽取，走信号卡链路
- 通用页 → 规则判 kind（岗位特征词密度 vs 文章特征）

JS 渲染/需登录站点：httpx 拿不到有效内容 → Playwright 无头渲染兜底（capture extra）；
仍失败 → 明确提示走浏览器半自动采集（另存 HTML）。合规：单页、限速、个人研究。
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from ..news_pipeline import extract_fulltext
from ..textfmt import normalize_text
from . import browser as browser_cards

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) infogap/0.1 (personal research)"
_TIMEOUT = 25.0

# 已知招聘站点 → 站点适配名（tier 映射见 quality.SOURCE_META）
_JOB_HOSTS = {
    "zhipin.com": "zhipin", "liepin.com": "liepin", "lagou.com": "lagou",
    "iguopin.com": "iguopin", "gaoxiaojob.com": "gaoxiaojob", "51job.com": "51job",
    "zhaopin.com": "zhaopin", "greenhouse.io": "greenhouse", "lever.co": "lever",
}
# 通用「岗位特征」与「文章特征」词密度（通用页分类）
_JOB_SIGNS = re.compile(r"岗位职责|任职要求|职位描述|工作职责|招聘|薪资|本科及以上|经验|"
                        r"job description|responsibilities|requirements|投递简历", re.I)
_ARTICLE_SIGNS = re.compile(r"<article|<time|published_time|og:title|author|日期|来源[:：]|阅读\(|评论\(")


def fetch_page(url: str) -> tuple[str, str, bool]:
    """抓取页面。返回 (html, final_url, rendered_by_playwright)。"""
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": UA}) as c:
        r = c.get(url)
        r.raise_for_status()
        html = r.text
    if _looks_js_shell(html):
        rendered = _render_with_playwright(str(r.url))
        if rendered:
            return rendered, str(r.url), True
    return html, str(r.url), False


def _looks_js_shell(html: str) -> bool:
    """JS 空壳判定：可见文本极少但挂了大 bundle。"""
    if len(html) < 2000:
        return False
    text = re.sub(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    visible = len(re.sub(r"\s", "", text))
    return visible < 400


def _render_with_playwright(url: str) -> str:
    """Playwright 无头渲染兜底（可选依赖 capture extra）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_page(user_agent=UA)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            html = page.content()
            b.close()
            return html
    except Exception:  # noqa: BLE001
        return ""


def _published_at(html: str) -> str:
    for pat in (r'property=["\']article:published_time["\']\s+content=["\']([^"\']+)',
                r'<time[^>]+datetime=["\']([^"\']+)',
                r'itemprop=["\']datePublished["\'][^>]+content=["\']([^"\']+)',
                r'"publishTime"\s*:\s*"([^"]+)"', r'"published_at"\s*:\s*"([^"]+)"'):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    return ""


def _page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    og = re.search(r'property=["\']og:title["\']\s+content=["\']([^"\']+)', html, re.I)
    if og:
        return og.group(1).strip()[:120]
    return (m.group(1).strip()[:120] if m else "")


def _site_source(url: str) -> str:
    host = urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    for known, name in _JOB_HOSTS.items():
        if known in host:
            return name
    return f"urlimport:{host}"


def classify(url: str, html: str) -> str:
    """页面类型：jobs（岗位列表/详情）| news（文章）| unknown。"""
    source = _site_source(url)
    if source in _JOB_HOSTS.values():
        return "jobs"
    n_job = len(_JOB_SIGNS.findall(html))
    n_art = len(_ARTICLE_SIGNS.findall(html))
    if n_job >= 3 and n_job > n_art:
        return "jobs"
    if n_art >= 2 or "<article" in html.lower():
        return "news"
    return "news" if n_art >= n_job else "unknown"


def ingest_url(url: str) -> list[dict]:
    """抓取单个 URL 并转换为 RawItem 列表（kind/source/title/body/published_at）。
    抛 ValueError 表示需要人工（登录态/反爬）。"""
    url = url.strip()
    if not re.match(r"^https?://", url):
        raise ValueError("链接需以 http(s):// 开头")
    html, final_url, rendered = fetch_page(url)
    source = _site_source(final_url)
    kind = classify(final_url, html)
    pub = _published_at(html)
    title = _page_title(html)

    items: list[dict] = []
    if kind == "jobs":
        cards = browser_cards.parse_html(html)
        if not cards:   # 招聘详情页单条
            body = normalize_text(extract_fulltext(html)) or \
                normalize_text(re.sub(r"<[^>]+>", " ", html))
            if len(body) < 120:
                raise ValueError("该页未解析出岗位内容（可能需要登录态，请用「浏览器半自动采集」）")
            items.append({"kind": "job", "source": source, "source_url": final_url,
                          "title": title, "body": body, "published_at": pub})
        for i, card in enumerate(cards[:60]):
            first = card.split("\n", 1)[0][:60]
            items.append({"kind": "job", "source": source,
                          "source_url": f"{final_url}#c{i}" if i else final_url,
                          "title": first, "body": normalize_text(card), "published_at": pub})
    else:
        body = extract_fulltext(html)
        if len(body) < 150:
            raise ValueError("未能从该页抽取正文（可能是 JS 动态页或需登录，"
                             "请用「浏览器半自动采集」）")
        items.append({"kind": "news", "source": source, "source_url": final_url,
                      "title": title or body[:40], "body": body, "published_at": pub})
    for it in items:
        it["channel_meta"] = {"url_import": True, "rendered": rendered}
    return items
