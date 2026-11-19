"""央国企 / 事业单位 / 高校科研 真实招聘渠道（官方与公开招聘平台，无反爬，静态可解析）。

- gaoxiaojob 高校人才网：高校/科研院所/事业单位/国企招聘聚合（静态 HTML，含 /job/detail 详情页）
  覆盖：人工智能/前后端开发/数据库开发/测试/电子工程/国企数据分析/银行数据分析 等类目
说明：国聘(iguopin)为 SPA 无公开 API、人社部公共招聘网 IT 子站返回 500，暂列浏览器采集路径（v0.2）。
"""
from __future__ import annotations

import html as html_mod
import re
import time

import httpx

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) infogap/0.1 (personal research)",
      "Referer": "https://www.gaoxiaojob.com/"}

BASE = "https://www.gaoxiaojob.com"

# 类目 → 名称（可按需增删；全部为高校人才网公开类目页）
CATEGORIES = {
    # IT/研发
    "rengongzhineng": "人工智能",
    "qianhouduankaifa": "前后端开发",
    "shujukukaifa": "数据库开发",
    "ceshi": "测试",
    "dianzigongcheng": "电子工程",
    # 央国企/事业单位/军队文职/军工
    "jundaiwenzhi": "军队文职",
    "bingqigongcheng": "兵器工程（军工）",
    "chuanbosheji": "船舶设计（军工）",
    "guoqi_shujufenxi": "国企·数据分析",
    "boshihou": "博士后（高校科研）",
    # 银行/金融
    "yinhanggang": "银行岗",
    "yinhang_shujufenxi": "银行·数据分析",
    "zhengquanqihuo": "证券期货",
    "jinrongguanli": "金融管理",
    # 重点城市（事业单位/国企本地岗）
    "beijing": "北京",
    "shanghai": "上海",
    "shenzhen": "深圳",
    "hangzhou": "杭州",
}

_JOB_LINK = re.compile(r'href="(/job/detail/(\d+)\.html)"[^>]*>(.*?)</a>', re.S)
_TITLE_RE = re.compile(r"<title>([^<]+)</title>")


def _strip(text: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S)
    text = re.sub(r"<br\s*/?>|</p>|</li>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    return re.sub(r"[ \t\u3000]+", " ", text)


def _list_page(client: httpx.Client, slug: str) -> list[tuple[str, str]]:
    """类目页 → [(detail_path, 标题)]（_JOB_LINK 三组：路径/ID/锚文本）"""
    r = client.get(f"{BASE}/rczhaopin/{slug}")
    out, seen = [], set()
    for path, _id, inner in _JOB_LINK.findall(r.text):
        title = re.sub(r"<[^>]+>", "", inner)
        title = re.sub(r"\s+", " ", title).strip()
        if path in seen or not title:
            continue
        seen.add(path)
        out.append((path, title))
    return out


def _detail(client: httpx.Client, path: str, list_title: str) -> str | None:
    """详情页 → JD 文本块（标题/公司取自 <title>，正文取清洗后的主体）"""
    r = client.get(f"{BASE}{path}")
    m = _TITLE_RE.search(r.text)
    job_title, company = list_title, ""
    if m:
        # 格式：{岗位}招聘_{公司}招聘-高校人才网|高才网
        head = m.group(1).split("-高校人才网")[0]
        parts = head.split("_")
        if len(parts) >= 2:
            job_title = parts[0].replace("招聘", "").strip() or list_title
            company = parts[1].replace("招聘", "").strip()
    text = _strip(r.text)
    # 截掉导航与页脚
    for anchor in ("专享推荐", "关于我们 | 联系我们"):
        if anchor in text:
            text = text.split(anchor, 1)[-1] if anchor == "专享推荐" else text.split(anchor, 1)[0]
    text = text.strip()
    # 取信息最浓的一段（含岗位/要求/待遇关键词的窗口）
    if len(text) > 4000:
        kw = [text.find(k) for k in ("岗位职责", "任职要求", "岗位要求", "招聘条件", "工作职责")]
        pos = [p for p in kw if p > 0]
        if pos:
            start = max(0, min(pos) - 800)
            text = text[start:start + 3800]
        else:
            text = text[:3800]
    if len(text) < 60:
        return None
    head = f"{job_title}\n公司:{company}\n单位类型:高校/科研/国企事业单位（高校人才网）"
    return f"{head}\n{text}\n来源:{BASE}{path}"


def fetch_gaoxiaojob(per_category: int = 12) -> list[str]:
    """抓取各类目岗位详情，组装为 JD 文本块（礼貌限速：详情页间隔 0.8s）。"""
    out: list[str] = []
    with httpx.Client(timeout=25, follow_redirects=True, headers=UA) as client:
        for slug in CATEGORIES:
            try:
                items = _list_page(client, slug)[:per_category]
            except Exception:  # noqa: BLE001
                continue
            for path, title in items:
                try:
                    jd = _detail(client, path, title)
                    if jd:
                        out.append(jd)
                    time.sleep(0.8)   # 温和限速：个人研究用途
                except Exception:  # noqa: BLE001
                    continue
    return out


# ---------------- 国聘（央企国企官方招聘平台，公开 JSON API 直连） ----------------

IGP_API = "https://gp-api.iguopin.com/api/jobs/v1/recom-job"
IGP_KEYWORDS = ["人工智能", "大模型", "Agent", "算法工程师", "软件开发", "数据分析",
                "军工", "航天", "核工业", "网络安全"]

# 事业单位/高校编制公告栏目（公告制招聘：一公告多岗）
ANNOUNCE_COLUMNS = {
    "272": "双一流院校人才招聘",
    "273": "本科院校人才招聘",
    "274": "党校（行政学院）人才招聘",
    "275": "博士人才引进",
}
_ANN_LINK = re.compile(r'href="(/announcement/detail/(\d+)\.html)"[^>]*>([^<]{6,60})')


def fetch_gxannounce(per_column: int = 8) -> list[str]:
    """事业单位/高校编制公告流：栏目页公告列表 → 公告正文（单位/岗位/人数/学历/截止）。
    公告制招聘一公告含多岗，作为公告型条目入库，LLM 增强抽取关键要素。"""
    out: list[str] = []
    with httpx.Client(timeout=25, follow_redirects=True, headers=UA) as client:
        for col, col_name in ANNOUNCE_COLUMNS.items():
            try:
                r = client.get(f"{BASE}/column/{col}.html")
                items = _ANN_LINK.findall(r.text)
            except Exception:  # noqa: BLE001
                continue
            seen = set()
            taken = 0
            for path, _id, title in items:   # 组1=路径 组2=ID 组3=标题
                if not path.startswith("/announcement") or path in seen or taken >= per_column:
                    continue
                seen.add(path)
                taken += 1
                try:
                    detail = client.get(f"{BASE}{path}")
                    text = _strip(detail.text)
                    if "关于我们 | 联系我们" in text:
                        text = text.split("关于我们 | 联系我们", 1)[0]
                    # 定位正文最浓段（含招聘要素关键词）
                    pos = [text.find(k) for k in ("招聘", "引进", "遴选")]
                    pos = [p for p in pos if p > 0]
                    if pos:
                        start = max(0, min(pos) - 100)
                        text = text[start:start + 3500]
                    if len(text) < 80:
                        continue
                    out.append(f"【{col_name}·招聘公告】{title.strip()}\n{text}\n来源:{BASE}{path}")
                    time.sleep(0.8)
                except Exception:  # noqa: BLE001
                    continue
    return out


def fetch_iguopin(per_keyword: int = 15) -> list[str]:
    """国聘官方公开 API（浏览器同源接口，无鉴权），关键词检索央企国企岗位。
    返回已结构化的 JD 文本块，入库后规则层即可精确解析。"""
    out: list[str] = []
    seen: set[str] = set()
    headers = {"User-Agent": UA["User-Agent"], "Origin": "https://www.iguopin.com",
               "Referer": "https://www.iguopin.com/", "Content-Type": "application/json"}
    with httpx.Client(timeout=25, headers=headers) as client:
        for kw in IGP_KEYWORDS:
            try:
                r = client.post(IGP_API, json={
                    "search": {"page": 1, "page_size": per_keyword, "keyword": kw},
                    "recom": {"update_time": True}})
                jobs = ((r.json() or {}).get("data") or {}).get("list") or []
            except Exception:  # noqa: BLE001
                continue
            for j in jobs:
                jid = str(j.get("job_id") or "")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                dl = j.get("district_list") or []
                city = ""
                if dl and isinstance(dl[0], dict):
                    city = str(dl[0].get("name_cn") or dl[0].get("name") or "").strip()
                if j.get("is_negotiable") or not j.get("min_wage"):
                    salary = "面议"
                else:
                    unit = j.get("wage_unit_cn") or "元/月"
                    lo, hi = j.get("min_wage"), j.get("max_wage") or j.get("min_wage")
                    if "K" in unit or "k" in unit:
                        salary = f"{lo}-{hi}K"
                    else:
                        salary = f"{lo}-{hi}元/月"
                    if j.get("months") and j["months"] not in (0, 12):
                        salary += f"·{j['months']}薪"
                out.append(
                    f"{j.get('job_name','')}\n"
                    f"公司:{j.get('company_name','')}  城市:{city}  薪资:{salary}\n"
                    f"经验:{j.get('experience_cn','')}  学历:{j.get('education_cn','')}\n"
                    f"单位性质:央企国企（国聘官方平台·{j.get('nature_cn','')}/{j.get('recruitment_type_cn','')}）\n"
                    f"{(j.get('contents') or '')[:2500]}\n"
                    f"来源:https://www.iguopin.com/job/detail?id={jid}")
            time.sleep(0.6)
    return out
