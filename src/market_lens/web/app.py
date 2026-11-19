"""Web 层：FastAPI + Jinja2 + ECharts（服务端渲染，无前端工程链）。

v0.2：统一异步任务框架（/api/tasks）、质量门隔离区、URL 智能导入、
模型接入配置中心（保存即热生效）、混合检索问答、信号卡资讯页。
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import markdown as md_lib
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db, reports
from ..config import ROOT, get_settings, reset_live_config, save_live_config, save_secret
from ..dsh_engine import dsh_sdk_available
from ..extract import split_paste, structure
from ..gateway import EmbeddingClient, LLMGateway, LLMError, RerankClient
from ..match import match_job, score_badge
from ..models import DEFAULT_PROFILE, Profile
from ..quality import DEFAULT_GATES, GROUP_DESC, SOURCE_META
from ..sample_data import SAMPLE_JOBS, SAMPLE_NEWS
from ..sources import rss as rss_source, searchapi
from ..stats import market_stats
from ..textfmt import to_paragraphs
from .. import scheduler as scheduler_mod
from . import tasks as tasks_mod
from .qa_sessions import qa_manager, render_md

WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def md(text: str) -> str:
    return md_lib.markdown(text or "", extensions=["tables", "nl2br", "fenced_code"])


def _fromjson(value: str | None, default="[]"):
    try:
        return json.loads(value or default)
    except json.JSONDecodeError:
        return json.loads(default)


def _toquery(d: dict) -> str:
    from urllib.parse import urlencode
    return urlencode({k: v for k, v in d.items() if v})


templates.env.filters["md"] = md
templates.env.filters["fromjson"] = _fromjson
templates.env.filters["toquery"] = _toquery
templates.env.filters["to_paragraphs"] = to_paragraphs

# 来源中文名（英文渠道在 UI 一律显示中文）
_SOURCE_ZH = {"arxiv": "arXiv 论文", "ghtrend": "GitHub Trending", "hfpapers": "HF 每日论文",
              "radar": "技术雷达", "sample": "示例"}


def source_zh(s: str) -> str:
    if not s:
        return "未知来源"
    if s.startswith("rss:"):
        return s[4:]
    if s.startswith("tg:"):
        return f"TG·{s[3:]}"
    return _SOURCE_ZH.get(s, s)


templates.env.filters["srczh"] = source_zh

_CJK_TEST = re.compile(r"[\u4e00-\u9fff]")
templates.env.filters["hascjk"] = lambda s: bool(_CJK_TEST.search(s or ""))


def redirect(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    """带 flash 消息的重定向（中文安全编码）。PRG 模式。"""
    sep = "&" if "?" in path else "?"
    if msg:
        path += f"{sep}msg={quote(msg)}"
        sep = "&"
    if err:
        path += f"{sep}err={quote(err)}"
    return RedirectResponse(path, status_code=302)


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect()
    db.init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def get_profile(conn: sqlite3.Connection) -> Profile:
    raw = db.load_profile(conn)
    if not raw:
        return DEFAULT_PROFILE.model_copy(deep=True)
    try:
        return Profile.model_validate(json.loads(raw))
    except Exception:  # noqa: BLE001
        return DEFAULT_PROFILE.model_copy(deep=True)


def get_gateway(conn: sqlite3.Connection) -> LLMGateway:
    return LLMGateway(get_settings(), conn)


def render(request: Request, template: str, context: dict | None = None,
           conn: sqlite3.Connection | None = None) -> HTMLResponse:
    settings = get_settings()
    ctx = {
        "request": request,
        "msg": request.query_params.get("msg", ""),
        "err": request.query_params.get("err", ""),
        "llm_ready": settings.llm_ready("analysis"),
        "search_ready": searchapi.available(settings),
        "nav": template.split(".")[0],
        "md": md,
        "scheduler_next": "",
        "quarantine_n": 0,
    }
    if conn is not None:
        ctx["quarantine_n"] = db.count_raw(conn, status="quarantined")
    sch = scheduler_mod.status()
    if sch.get("enabled") and sch.get("next_run"):
        ctx["scheduler_next"] = sch["next_run"][11:16] if "T" in sch["next_run"] else sch["next_run"]
    ctx.update(context or {})
    return templates.TemplateResponse(request, template, ctx)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks_mod.manager.recover_orphans()
    tasks_mod.manager.start(workers=2)
    scheduler_mod.start()
    yield


app = FastAPI(title="信息差 market-lens", docs_url=None, redoc_url=None,
              lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


# ==================== 仪表盘 ====================

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    stats = market_stats(conn)
    profile = get_profile(conn)
    jobs = db.list_jobs(conn, limit=1000)
    matches = [match_job(j, profile) for j in jobs]
    passed = sum(1 for m in matches if m.verdict == "pass")
    # 机会流：gold × 匹配
    from ..consolidate import channel_uniqueness
    from ..snapshots import skill_trend
    opportunities = []
    for j, m in zip(jobs, matches):
        if m.verdict == "veto":
            continue
        gold = j.get("gold_score") or 0
        label, cls = score_badge(m.score, m.verdict)
        opportunities.append({"job": j, "badge": label, "badge_cls": cls,
                              "_k": gold * (m.score or 0)})
    opportunities.sort(key=lambda x: -x["_k"])
    funnel_raw = db.funnel_stats(conn)
    funnel = {k: sum(v.values()) for k, v in funnel_raw.items()}
    trend = skill_trend(conn)
    return render(request, "dashboard.html", {
        "stats": stats, "passed": passed, "total": len(jobs),
        "veto": len(jobs) - passed, "latest": db.list_reports(conn, limit=6),
        "opportunities": opportunities[:10], "funnel": funnel,
        "uniqueness": channel_uniqueness(conn)[:8],
        "rising": (trend[1]["rising"] if len(trend) > 1 else []),
        "chart_data": json.dumps({
            "skills": [[s["id"], s["count"]] for s in stats["skills_top"][:15]],
            "salary": [[b["label"], b["count"]] for b in stats["salary_hist"]],
            "city": [[c["city"], c["n"]] for c in stats["by_city"][:8]],
            "wlb": [[w["label"], w["n"]] for w in stats["wlb_dist"]],
        }, ensure_ascii=False),
    }, conn=conn)


# ==================== 岗位库 ====================

@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, conn: sqlite3.Connection = Depends(get_conn),
              q: str = "", city: str = "", source: str = "", skill: str = "",
              salary_min: str = "", education: str = "", min_gold: str = "",
              sort: str = "new", page: int = 1):
    profile = get_profile(conn)
    salary_min_v = float(salary_min) if salary_min else None
    min_gold_v = float(min_gold) if min_gold else None
    rows = db.list_jobs(conn, q=q, city=city, source=source, skill=skill,
                        salary_min=salary_min_v, education=education,
                        min_gold=min_gold_v, limit=2000)
    items = []
    for j in rows:
        m = match_job(j, profile)
        label, cls = score_badge(m.score, m.verdict)
        items.append({"job": j, "match": m, "badge": label, "badge_cls": cls,
                      "sort_key": m.score if m.score is not None else -1})
    if sort == "match":
        items.sort(key=lambda x: -x["sort_key"])
    per, page = 50, max(1, page)
    items_paged = items[(page - 1) * per: page * per]
    sources = [dict(r)["source"] for r in conn.execute(
        "SELECT DISTINCT source FROM jobs").fetchall()]
    return render(request, "jobs.html", {
        "items": items_paged, "total": len(items), "page": page,
        "pages": max(1, (len(items) + per - 1) // per),
        "filters": {"q": q, "city": city, "source": source, "skill": skill,
                    "salary_min": salary_min, "education": education,
                    "min_gold": min_gold, "sort": sort},
        "sources": sources,
    }, conn=conn)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    job = db.get_job(conn, job_id)
    if not job:
        return redirect("/jobs", err="岗位不存在")
    profile = get_profile(conn)
    m = match_job(job, profile)
    label, cls = score_badge(m.score, m.verdict)
    try:
        gold = json.loads(job.get("gold_json") or "{}") or None
    except json.JSONDecodeError:
        gold = None
    if gold and not gold.get("dims"):
        gold = None
    return render(request, "job_detail.html", {
        "job": job, "match": m, "badge": label, "badge_cls": cls, "gold": gold,
        "skills": json.loads(job["skills"] or "[]"),
        "signals": json.loads(job["wlb_signals"] or "[]"),
    }, conn=conn)


@app.post("/jobs/{job_id}/delete")
def job_delete(job_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    db.delete_job(conn, job_id)
    return {"ok": True}


# ==================== 数据接入中心 ====================

def _channel_cards(conn) -> list[dict]:
    """渠道卡片数据（分组/tier/scarcity/上次采集量/健康度）。"""
    from ..sources.realjobs import CHANNELS
    cards = []
    last_counts = {r["source"]: r["n"] for r in conn.execute(
        "SELECT source, COUNT(*) n FROM jobs GROUP BY source")}
    errors_by_src: dict[str, int] = {}
    for r in conn.execute(
            "SELECT stages_json FROM pipeline_runs ORDER BY id DESC LIMIT 3"):
        for st in json.loads(r["stages_json"] or "[]"):
            rep = st.get("report") or {}
            if isinstance(rep, dict):
                for k, v in rep.items():
                    if isinstance(v, str) and v.startswith("失败"):
                        errors_by_src[k] = errors_by_src.get(k, 0) + 1
    # 旧 8 渠道
    for cid, desc in CHANNELS.items():
        meta = SOURCE_META.get(cid, {"tier": 0.6, "scarcity": 0.5, "group": "community"})
        cards.append({"id": cid, "name": cid, "desc": desc[:40],
                      "tier": meta.get("tier"), "scarcity": meta.get("scarcity"),
                      "group": meta.get("group", "community"),
                      "group_desc": GROUP_DESC.get(meta.get("group", "community"), "社区"),
                      "last_n": last_counts.get(cid),
                      "health_cls": "bad" if errors_by_src.get(cid, 0) >= 2 else
                                    ("warn" if errors_by_src.get(cid) else ""),
                      "health": "近期失败" if errors_by_src.get(cid) else "正常",
                      "collectable": True})
    # 新渠道组
    settings = get_settings()
    tg = settings.channels_cfg.get("telegram") or []
    for name in tg:
        cards.append({"id": f"tg:{name}", "name": f"tg:{name}", "desc": "Telegram 公开频道（信息差）",
                      "tier": 0.55, "scarcity": 0.9, "group": "scarce", "group_desc": "信息差",
                      "last_n": None, "health_cls": "", "health": "未采集", "collectable": False})
    new_groups = [("premium", "官方直连板（Greenhouse/Lever）", 1.0, 0.3, "official", "官方直连"),
                  ("tg", "Telegram 频道 + GitHub 内推 + 圈内板块", 0.55, 0.9, "scarce", "信息差"),
                  ("techfront", "arXiv / HF Papers / GitHub Trending / 雷达", 0.9, 0.5, "tech", "前沿技术"),
                  ("rss", "RSS 资讯源（量子位/InfoQ/TechCrunch…）", 0.7, 0.3, "community", "社区")]
    for cid, desc, tier, scar, grp, gdesc in new_groups:
        cards.append({"id": cid, "name": cid, "desc": desc, "tier": tier, "scarcity": scar,
                      "group": grp, "group_desc": gdesc,
                      "last_n": last_counts.get(cid), "health_cls": "", "health": "正常",
                      "collectable": cid in ("premium", "techfront")})
    return cards


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return render(request, "import.html", {
        "channel_cards": _channel_cards(conn),
        "task_history": db.list_tasks(conn, limit=12),
        "last_pipeline": db.last_pipeline_run(conn),
    }, conn=conn)


# ==================== 隔离区 ====================

def _gate_thresholds() -> tuple[dict[str, float], dict[str, float]]:
    """各 kind 的 (低线, 门中值)：默认门 + config 覆盖。"""
    cfg_gates = get_settings().quality.get("gates") or {}
    gates = {**DEFAULT_GATES, **{k: tuple(v) for k, v in cfg_gates.items()}}
    los = {k: float(v[0]) for k, v in gates.items()}
    mids = {k: round((float(v[0]) + float(v[1])) / 2, 1) for k, v in gates.items()}
    return los, mids


@app.get("/quarantine", response_class=HTMLResponse)
def quarantine_page(request: Request, conn: sqlite3.Connection = Depends(get_conn),
                    kind: str = "", page: int = 1):
    page, per = max(1, page), 50
    los, mids = _gate_thresholds()
    total = db.count_raw(conn, status="quarantined", kind=kind or "")
    items = db.list_raw(conn, status="quarantined", kind=kind or "", limit=per,
                        offset=(page - 1) * per, order="gold")
    # 分带统计：≥门中值（大概率可直接恢复）/ <低线（低质，建议丢弃）
    hist = conn.execute(
        "SELECT kind, CAST(gold_score AS INT) g, COUNT(*) n FROM raw_items "
        "WHERE status='quarantined' GROUP BY kind, g").fetchall()
    promotable = low = 0
    for r in hist:
        if r["g"] >= mids.get(r["kind"], 60.0):
            promotable += r["n"]
        elif r["g"] < los.get(r["kind"], 55.0):
            low += r["n"]
    judge_ok = conn.execute(
        "SELECT 1 FROM llm_usage WHERE task='judge' AND ok=1 LIMIT 1").fetchone()
    return render(request, "quarantine.html", {
        "items": items, "kind": kind or "", "page": page,
        "pages": max(1, (total + per - 1) // per),
        "stats": {"total": total, "promotable": promotable, "low": low},
        "judge_ok": bool(judge_ok),
    }, conn=conn)


@app.post("/api/quarantine/{raw_id}/promote")
def quarantine_promote(raw_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    from ..pipeline import promote_raw
    try:
        msg = promote_raw(conn, raw_id, get_gateway(conn))
    except Exception as e:  # noqa: BLE001 复核失败给可读原因，而非裸 500
        raise HTTPException(400, f"恢复失败：{e}") from e
    return {"ok": True, "message": msg}


@app.post("/api/quarantine/{raw_id}/discard")
def quarantine_discard(raw_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    from ..pipeline import discard_raw
    try:
        msg = discard_raw(conn, raw_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"丢弃失败：{e}") from e
    return {"ok": True, "message": msg}


@app.post("/api/quarantine/batch")
def quarantine_batch(payload: dict):
    """批量复核走异步任务（promote 含发布/正文抓取，耗时不可同步等）。"""
    action = payload.get("action")
    if action not in ("promote", "discard"):
        raise HTTPException(400, "action 必须是 promote 或 discard")
    if not (payload.get("ids") or payload.get("score_mode")
            or payload.get("score_min") is not None or payload.get("score_max") is not None):
        raise HTTPException(400, "缺少批量范围（ids / score_mode / score_min / score_max）")
    tid = tasks_mod.manager.submit(
        "quarantine_batch", "批量恢复进精选" if action == "promote" else "批量丢弃", payload)
    return {"task_id": tid}


# ==================== 异步任务 API ====================

@app.post("/api/tasks")
def api_create_task(payload: dict):
    try:
        tid = tasks_mod.manager.submit(payload.get("kind", ""), payload.get("title") or
                                       payload.get("kind", ""), payload.get("params") or {})
        return {"task_id": tid}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/tasks")
def api_list_tasks(limit: int = 12, active: int = 0):
    conn = db.connect()
    db.init_db(conn)
    try:
        return db.list_tasks(conn, active_only=bool(active), limit=limit)
    finally:
        conn.close()


@app.get("/api/tasks/{task_id}")
def api_get_task(task_id: int):
    conn = db.connect()
    db.init_db(conn)
    try:
        t = db.get_task(conn, task_id)
        if not t:
            raise HTTPException(404, "任务不存在")
        return t
    finally:
        conn.close()


@app.post("/api/tasks/{task_id}/cancel")
def api_cancel_task(task_id: int):
    ok = tasks_mod.manager.cancel(task_id)
    return {"ok": ok}


# ==================== 设置：模型接入配置中心 ====================

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    s = get_settings()
    providers = s.all_providers()
    emb_key_set = bool(s.embedding.get("base_url"))
    channel_tiers = [{"source": src, **meta} for src, meta in
                     sorted(SOURCE_META.items(), key=lambda x: -(x[1].get("tier", 0)))]
    for ct in channel_tiers:
        ct["group_desc"] = GROUP_DESC.get(ct.get("group"), "社区")
    return render(request, "settings.html", {
        "providers": providers,
        "providers_json": json.dumps(providers, ensure_ascii=False),
        "routing": s.routing,
        "usage": db.usage_summary(conn),
        "budget": s.budget_monthly_cny,
        "dsh_available": dsh_sdk_available(),
        "embedding": {**s.embedding, "ready": s.embedding_ready(), "key_set": emb_key_set},
        "rerank": {**s.rerank, "ready": s.rerank_ready(), "key_set": bool(s.rerank.get("base_url"))},
        "retrieval": {"vector": s.retrieval_cfg.get("vector", True),
                      "rerank": s.retrieval_cfg.get("rerank", True)},
        "quality": {"gates": s.quality.get("gates") or
                    {"job": [55, 70], "news": [50, 65], "tech": [50, 65]},
                    "judge": s.quality.get("judge", True)},
        "channel_tiers": channel_tiers,
        "scheduler": {"enabled": s.scheduler_cfg.get("enabled", True),
                      "hour": s.scheduler_cfg.get("hour", 8),
                      **scheduler_mod.status()},
    }, conn=conn)


@app.post("/api/settings/save")
def api_settings_save(payload: dict):
    """通用保存：{section, patch, secret?} → config_live.yaml（热生效）。"""
    section = payload.get("section", "")
    patch = payload.get("patch") or {}
    secret = payload.get("secret") or None
    if not section or not isinstance(patch, dict):
        raise HTTPException(400, "参数错误")
    allowed = {"llm", "embedding", "rerank", "retrieval", "quality", "scheduler", "search", "rss"}
    if section not in allowed:
        raise HTTPException(400, f"不支持的配置段 {section}")
    save_live_config({section: patch})
    if secret and secret.get("env") and secret.get("value"):
        env = secret["env"].strip().upper().replace(" ", "_")
        import re as _re
        if not _re.match(r"^[A-Z0-9_]+$", env):
            raise HTTPException(400, "环境变量名非法")
        save_secret(env, secret["value"])
    return {"ok": True}


@app.post("/api/settings/llm")
def api_settings_llm(payload: dict):
    """新增/更新 LLM 通道（密钥写 .env，结构写 live 层）。"""
    pid = (payload.get("id") or "").strip()
    base_url = (payload.get("base_url") or "").strip().rstrip("/")
    models = [m.strip() for m in (payload.get("models") or []) if m.strip()]
    api_key = (payload.get("api_key") or "").strip()
    if not pid or not base_url or not models:
        raise HTTPException(400, "id / base_url / models 必填")
    import re as _re
    if not _re.match(r"^[a-z0-9][a-z0-9-_.]*$", pid):
        raise HTTPException(400, "通道 id 只能是小写字母/数字/-/_/.")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "base_url 需以 http(s):// 开头")
    env_name = f"KEY_{pid.upper().replace('-', '_')}"
    provider_cfg = {"base_url": base_url, "api_key_env": env_name,
                    "models": models, "json_mode": payload.get("json_mode", "auto")}
    save_live_config({"llm": {"providers": {pid: provider_cfg}}})
    if api_key:
        save_secret(env_name, api_key)
    else:
        import os
        if env_name not in os.environ:
            save_secret(env_name, "")   # 本地端点可无 key：写空占位
    return {"ok": True, "provider": pid}


@app.post("/api/settings/test")
def api_settings_test(provider: str = "", kind: str = ""):
    """通道测试（LLM / embedding / rerank）。带 conn → 结果落 llm_usage，通道健康可见。"""
    conn = db.connect()
    db.init_db(conn)
    try:
        if kind == "embedding":
            ok, message, latency = EmbeddingClient(get_settings(), conn).test()
            return {"ok": ok, "message": message, "latency": latency}
        if kind == "rerank":
            ok, message, latency = RerankClient(get_settings(), conn).test()
            return {"ok": ok, "message": message, "latency": latency}
        gw = LLMGateway(get_settings(), conn)
        ok, message, latency = gw.test_provider(provider)
        return {"ok": ok, "message": message, "latency": latency}
    finally:
        conn.close()


@app.post("/api/settings/reset")
def api_settings_reset():
    reset_live_config()
    return {"ok": True}


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ==================== 旧版兼容路由（过渡期保留） ====================

@app.post("/import/paste")
def import_paste(conn: sqlite3.Connection = Depends(get_conn),
                 text: str = Form(...), use_llm: bool = Form(False)):
    texts = split_paste(text)
    if not texts:
        return redirect("/import", err="没有可导入的内容")
    s = structure(conn, texts, "manual", get_gateway(conn) if use_llm else None, use_llm)
    return redirect("/import", msg=f"导入完成：新增 {s.added}，重复 {s.duplicates}")


@app.post("/import/rss")
def import_rss(conn: sqlite3.Connection = Depends(get_conn)):
    result = rss_source.fetch_rss(conn, get_settings())
    err = "；".join(result["errors"])[:150] if result["errors"] else ""
    return redirect("/news", msg=f"RSS抓取：新增 {result['added']} 条资讯", err=err)


@app.post("/import/sample")
def import_sample(conn: sqlite3.Connection = Depends(get_conn)):
    s = structure(conn, SAMPLE_JOBS, "sample", None, use_llm=False)
    news_added = sum(db.upsert_news(conn, {**n, "fetched_at": db.now()}) for n in SAMPLE_NEWS)
    return redirect("/import", msg=f"示例数据已载入：岗位新增 {s.added}，资讯 {news_added} 条")


# ==================== 资讯 / 前沿技术 ====================

def _ungoverned_n(conn: sqlite3.Connection, kind: str) -> int:
    """未治理条目数（规则卡/英文直出），用于「重新治理」按钮提示。"""
    return conn.execute(
        "SELECT COUNT(*) c FROM news WHERE kind=? AND url != '' "
        "AND (card_method IS NULL OR card_method != 'llm')", (kind,)).fetchone()["c"]


@app.get("/news", response_class=HTMLResponse)
def news_page(request: Request, conn: sqlite3.Connection = Depends(get_conn),
              q: str = "", signal: str = "", sort: str = "hot", page: int = 1):
    data = db.query_news(conn, kind="news", q=q, signal=signal, sort=sort,
                         page=page, per=20)
    return render(request, "news.html", {
        "items": data["items"], "total": data["total"], "page": data["page"],
        "pages": data["pages"], "filters": {"q": q, "signal": signal, "sort": sort},
        "ungoverned": _ungoverned_n(conn, "news"),
    }, conn=conn)


@app.get("/tech", response_class=HTMLResponse)
def tech_page(request: Request, conn: sqlite3.Connection = Depends(get_conn),
              q: str = "", sort: str = "hot", source: str = "", page: int = 1):
    data = db.query_news(conn, kind="tech", q=q, source=source, sort=sort,
                         page=page, per=15)
    sources = [{"id": r["source"], "name": source_zh(r["source"]), "n": r["n"]}
               for r in conn.execute(
                   "SELECT source, COUNT(*) n FROM news WHERE kind='tech' "
                   "GROUP BY source ORDER BY n DESC").fetchall()]
    from ..snapshots import skill_trend
    trend = skill_trend(conn)
    return render(request, "tech.html", {
        "items": data["items"], "total": data["total"], "page": data["page"],
        "pages": data["pages"], "sources": sources,
        "filters": {"q": q, "sort": sort, "source": source},
        "rising": (trend[1]["rising"] if len(trend) > 1 else []),
        "ungoverned": _ungoverned_n(conn, "tech"),
    }, conn=conn)


# ==================== 报告 ====================

@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return render(request, "reports.html", {"reports": db.list_reports(conn, limit=100)}, conn=conn)


@app.post("/reports/generate")
def reports_generate(kind: str = Form(...), conn: sqlite3.Connection = Depends(get_conn)):
    profile = get_profile(conn)
    gateway = get_gateway(conn)
    try:
        if kind == "market":
            rid = reports.gen_market_report(conn, gateway, profile)
        elif kind == "gap":
            rid = reports.gen_gap_report(conn, gateway, profile)
        elif kind == "digest":
            rid = reports.gen_digest(conn, gateway)
        else:
            return redirect("/reports", err="未知报告类型")
        return redirect(f"/reports/{rid}", msg="报告已生成")
    except LLMError as e:
        return redirect("/reports", err=f"生成失败：{str(e)[:150]}")


# ==================== SSE 流式输出（报告 / 问答） ====================

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/reports/stream")
def reports_stream(kind: str = "market"):
    """报告生成流式版：事件流 step/think/answer → done(带 report_id)。"""
    import queue as _queue
    import threading as _threading

    def gen():
        q: _queue.Queue = _queue.Queue()

        def progress(k: str, text: str) -> None:
            q.put((k, text))

        def work() -> None:
            conn = db.connect()
            db.init_db(conn)
            try:
                profile = get_profile(conn)
                gw = get_gateway(conn)
                if kind == "market":
                    rid = reports.gen_market_report(conn, gw, profile, progress)
                elif kind == "gap":
                    rid = reports.gen_gap_report(conn, gw, profile, progress)
                elif kind == "digest":
                    rid = reports.gen_digest(conn, gw, progress=progress)
                else:
                    q.put(("error", "未知报告类型"))
                    return
                q.put(("done", str(rid)))
            except Exception as e:  # noqa: BLE001
                q.put(("error", f"{type(e).__name__}: {e}"))
            finally:
                conn.close()

        _threading.Thread(target=work, daemon=True).start()
        while True:
            k, text = q.get()
            if k in ("done", "error"):
                yield _sse({"type": k, "report_id": text} if k == "done"
                           else {"type": "error", "message": text})
                return
            yield _sse({"type": k, "text": text})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _sse_events(sid: str, from_seq: int = 0) -> StreamingResponse:
    """订阅某个问答会话的事件流：回放 + 跟随 + 心跳。"""
    s = qa_manager.get(sid)
    if not s:
        return JSONResponse({"detail": "会话不存在（服务可能已重启），历史记录仍保留"},
                            status_code=404)

    def gen():
        for ev in s.subscribe(from_seq):
            if ev.get("type") == "ping":
                yield ": ping\n\n"   # SSE 注释行心跳，客户端自动忽略
            else:
                yield _sse(ev)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/qa/stream")
def qa_stream(q: str = ""):
    """问答流式版（旧入口，兼容保留）：建会话 → 订阅事件。生成在后台线程，
    与本连接无关；前端已改用 /api/qa/ask + /qa/events/{sid} 以支持断线续传。"""
    question = q.strip()
    if not question:
        return redirect("/qa", err="请输入问题")
    s = qa_manager.create(question)
    return _sse_events(s.sid)


@app.post("/api/qa/ask")
def api_qa_ask(payload: dict):
    """提问 → 创建后台会话。生成与连接解耦：切页/断线不影响，完成即入历史。"""
    question = (payload.get("q") or "").strip()
    if not question:
        raise HTTPException(400, "问题不能为空")
    if len(question) > 500:
        raise HTTPException(400, "问题过长（≤500 字）")
    s = qa_manager.create(question)
    return {"sid": s.sid, "question": s.question}


@app.get("/qa/events/{sid}")
def qa_events(sid: str, from_seq: int = 0):
    """会话事件流（SSE）。from_seq 断点续传：页面切回/网络闪断后从上次位置继续。"""
    return _sse_events(sid, from_seq)


@app.get("/api/qa/active")
def api_qa_active():
    """进行中的问答会话（页面加载时重连用）。"""
    return qa_manager.active()


@app.get("/api/qa/history")
def api_qa_history(limit: int = 30):
    """历史问答（qa_logs，持久化）。answer_html 为服务端渲染好的 Markdown。"""
    conn = db.connect()
    db.init_db(conn)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, question, answer_md, cited_ids, created_at FROM qa_logs "
            "WHERE answer_md != '' ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 200)),)).fetchall()]
    finally:
        conn.close()
    for r in rows:
        try:
            r["cited_n"] = len(json.loads(r.get("cited_ids") or "[]"))
        except json.JSONDecodeError:
            r["cited_n"] = 0
        r["answer_html"] = render_md(r.get("answer_md") or "")
    return rows


@app.delete("/api/qa/history/{log_id}")
def api_qa_history_delete(log_id: int):
    conn = db.connect()
    db.init_db(conn)
    try:
        conn.execute("DELETE FROM qa_logs WHERE id=?", (log_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/reports/{rid}", response_class=HTMLResponse)
def report_view(request: Request, rid: int, conn: sqlite3.Connection = Depends(get_conn)):
    r = db.get_report(conn, rid)
    if not r:
        return redirect("/reports", err="报告不存在")
    return render(request, "report_view.html", {"report": r}, conn=conn)


# ==================== 画像与偏好 ====================

@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return render(request, "profile.html", {"p": get_profile(conn)}, conn=conn)


@app.post("/profile")
def profile_save(conn: sqlite3.Connection = Depends(get_conn),
                 name: str = Form("我"), years_exp: int = Form(6),
                 background: str = Form(""), skills: str = Form(""),
                 target_role: str = Form(""),
                 salary_floor_k: str = Form(""),
                 cities: str = Form("北京,上海,深圳,杭州,远程"),
                 overtime_max: str = Form("2"),
                 work_mode: str = Form("any"),
                 company_types: str = Form(""),
                 tech_like: str = Form(""), tech_avoid: str = Form(""),
                 w_wlb: int = Form(5), w_work_mode: int = Form(3),
                 w_company_type: int = Form(2), w_tech_fit: int = Form(4),
                 w_salary_margin: int = Form(3)):
    def split_csv(s: str) -> list[str]:
        return [x.strip() for x in s.replace("，", ",").split(",") if x.strip()]

    p = get_profile(conn)
    p.name = name.strip() or "我"
    p.years_exp, p.background, p.target_role = years_exp, background, target_role
    p.skills = split_csv(skills)
    p.hard.salary_floor_k = float(salary_floor_k) if salary_floor_k.strip() else None
    p.hard.cities = split_csv(cities)
    p.hard.overtime_max = int(overtime_max) if overtime_max.strip() != "" else None
    p.prefs.work_mode = work_mode if work_mode in {"any", "remote", "hybrid", "onsite"} else "any"
    p.prefs.company_types = split_csv(company_types)
    p.prefs.tech_like, p.prefs.tech_avoid = split_csv(tech_like), split_csv(tech_avoid)
    p.prefs.weights = {"wlb": w_wlb, "work_mode": w_work_mode, "company_type": w_company_type,
                       "tech_fit": w_tech_fit, "salary_margin": w_salary_margin}
    db.save_profile(conn, p.model_dump_json())
    return redirect("/profile", msg="画像与偏好已保存，岗位库匹配度已更新")


# ==================== 问答 ====================

@app.get("/qa", response_class=HTMLResponse)
def qa_page(request: Request, q: str = "", conn: sqlite3.Connection = Depends(get_conn)):
    """问答页：提问/流式输出/历史全部走 /api/qa/* 接口（支持切页续看与断线续传）。"""
    return render(request, "qa.html", {"question": q.strip()}, conn=conn)
