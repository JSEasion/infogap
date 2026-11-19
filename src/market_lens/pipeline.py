"""流水线编排：采集 → raw 落地 → 质量门 → 精选发布 → 整合 → 快照 → 向量化。

幂等可重放：规则/阈值调整后可对 raw 层重跑打分（rescore_only）。
每次运行落 pipeline_runs（可观测）。progress 回调统一驱动任务中心进度条。
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from typing import Callable

from . import db, quality
from .config import get_settings
from .textfmt import clean_title, normalize_text

ProgressFn = Callable[[float | None, str, str], None]


def _noop_progress(pct, stage, msg):
    pass


# 增强通道探活缓存（一次运行内只探一次；不可达 → 全程规则模式）
_EXTRACT_PROBE: dict[str, bool] = {}


def _extraction_ready(gateway) -> bool:
    if "ok" not in _EXTRACT_PROBE:
        _EXTRACT_PROBE["ok"] = gateway.quick_check("extraction")
    return _EXTRACT_PROBE["ok"]


# ---------------- 采集：全部渠道 → RawItem ----------------

def _legacy_texts_to_items(texts: list[str], source: str) -> list[dict]:
    """旧版 fetcher（返回纯文本块）→ RawItem。文本内嵌「来源:链接」行。"""
    import re
    out = []
    for t in texts:
        t = t.strip()
        if not t:
            continue
        m = re.search(r"来源[:：]\s*(https?://\S+)", t)
        out.append({
            "kind": "job", "source": source, "source_url": m.group(1) if m else "",
            "title": t.split("\n", 1)[0][:80], "body": normalize_text(t)[:8000],
            "published_at": "", "channel_meta": {"legacy": True},
        })
    return out


def collect_all(progress: ProgressFn = _noop_progress, channels: list[str] | None = None) -> dict:
    """全部渠道采集 → RawItem 列表。单渠道失败不中断。"""
    from .sources import ghreferral, niche_boards, premium, rss as rss_source, techfront, tgchannels
    items: list[dict] = []
    report: dict = {}

    # 1) 旧版 8 渠道（realjobs，返回纯文本块）
    from .sources import realjobs
    chs = channels or list(realjobs.FETCHERS)
    for ch in chs:
        progress(None, "采集", f"{realjobs.CHANNELS.get(ch, ch)}…")
        try:
            texts = realjobs.FETCHERS[ch]()
            got = _legacy_texts_to_items(texts, ch)
            items += got
            report[ch] = len(got)
        except Exception as e:  # noqa: BLE001
            report[ch] = f"失败: {type(e).__name__}"

    # 2) RSS 资讯
    progress(None, "采集", "RSS 资讯…")
    r = rss_source.fetch_rss_items(get_settings())
    items += r["items"]
    report["rss"] = len(r["items"])

    # 3) 官方直连板
    progress(None, "采集", "官方直连招聘板（Greenhouse/Lever）…")
    r = premium.collect()
    items += r["items"]
    report["premium"] = len(r["items"])

    # 4) 信息差组
    progress(None, "采集", "Telegram 公开频道…")
    r = tgchannels.collect()
    items += r["items"]
    report["tg"] = len(r["items"])
    progress(None, "采集", "GitHub 内推仓库…")
    r = ghreferral.collect()
    items += r["items"]
    report["gh_referral"] = len(r["items"])
    progress(None, "采集", "圈内垂直板块…")
    r = niche_boards.collect()
    items += r["items"]
    report["niche"] = len(r["items"])

    # 5) 前沿技术
    progress(None, "采集", "前沿技术源（arXiv/HF/Trending）…")
    r = techfront.collect()
    items += r["items"]
    report["techfront"] = len(r["items"])

    for it in items:
        it["body"] = normalize_text(it.get("body", ""))
    return {"items": items, "report": report}


def ingest_url_items(url: str) -> list[dict]:
    """URL 智能导入（不豁免质量门）。"""
    from .sources import urlingest
    items = urlingest.ingest_url(url)
    for it in items:
        it["body"] = normalize_text(it.get("body", ""))
    return items


# ---------------- 质量门 + 发布 ----------------

def _publish_curated_jobs(conn, gateway, raws: list[dict], progress: ProgressFn) -> dict:
    """精选岗位 → 规则+LLM 结构化 → 精确回写 raw_id/gold/published_at。"""
    import json as _json
    from .extract import _tokenize_for_fts, extract_llm, extract_rules
    from .salary import parse_salary
    from .taxonomy import skills_text as fmt_skills
    stats = {"added": 0, "dup": 0, "linked": 0}
    for source, group in _group_by_source(raws).items():
        texts = [g["body"] for g in group]
        llm_results: list[dict | None] = [None] * len(texts)
        use_llm = (gateway is not None and gateway.settings.llm_ready("extraction")
                   and _extraction_ready(gateway))
        if use_llm:
            progress(None, "结构化", f"岗位 LLM 抽取 {source}（{len(texts)} 条）…")
            try:
                llm_results = extract_llm(gateway, texts)
            except Exception:  # noqa: BLE001 规则兜底
                llm_results = [None] * len(texts)
        for g, text, llm in zip(group, texts, llm_results):
            job = extract_rules(text)
            job["title"] = clean_title(job.get("title", ""))
            if llm:
                for k in ("title", "company", "city", "salary_raw", "experience", "education"):
                    if llm.get(k):
                        job[k] = llm[k]
                if llm.get("title"):
                    job["title"] = clean_title(llm["title"])
                if llm.get("salary_raw"):
                    sal = parse_salary(llm["salary_raw"])
                    if sal:
                        job.update(salary_min_k=sal.min_k, salary_max_k=sal.max_k,
                                   salary_months=sal.months,
                                   annual_min_wan=sal.annual_min_wan,
                                   annual_max_wan=sal.annual_max_wan)
                for k in ("work_mode", "company_type"):
                    if llm.get(k):
                        job[k] = llm[k]
                if llm.get("skills"):
                    merged = list(dict.fromkeys(_json.loads(job["skills"]) + llm["skills"]))
                    job["skills"] = _json.dumps(merged, ensure_ascii=False)
                if llm.get("desc_zh"):
                    job["description_zh"] = llm["desc_zh"]
                job["extract_method"] = "hybrid"
                job["structured_ok"] = 1
            job["source"] = source
            job["description"] = text[:8000]
            job["skills_text"] = fmt_skills(_json.loads(job["skills"]))
            job["desc_tokens"] = _tokenize_for_fts(job["title"], job["company"], job["city"],
                                                   job["skills_text"], text)
            job["fetched_at"] = db.now()
            job["published_at"] = g.get("published_at", "")
            job["dedup_hash"] = db.dedup_hash(job["title"], job["company"], job["city"],
                                              job["salary_raw"], job["description"])
            jid, is_new = db.upsert_job(conn, job)
            stats["added" if is_new else "dup"] = stats.get("added" if is_new else "dup", 0) + 1
            if jid:
                conn.execute(
                    "UPDATE jobs SET raw_id=?, gold_score=?, gold_json=?, published_at=? "
                    "WHERE id=?", (g["id"], g["score"]["gold"], _score_json(g),
                                   g.get("published_at", ""), jid))
                db.decide_raw(conn, g["id"], "curated", g["score"]["gold"],
                              _score_json(g), curated_id=jid)
                stats["linked"] += 1
        conn.commit()
    return stats


def _group_by_source(raws: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in raws:
        out.setdefault(r["source"], []).append(r)
    return out


def _score_json(raw: dict) -> str:
    import json
    return json.dumps(raw.get("score") or {}, ensure_ascii=False)


def _publish_curated_news(conn, gateway, raws: list[dict], progress: ProgressFn) -> dict:
    """精选资讯/技术 → 正文补全 → 信号卡 → 落库。"""
    from .news_pipeline import (build_signal_cards, fetch_fulltext, needs_fulltext,
                                publish_news)
    stats = {"published": 0, "fulltext_fetched": 0}
    for i, r in enumerate(raws):
        if needs_fulltext(r):
            progress(None, "资讯", f"正文补全 {i + 1}/{len(raws)}：{r['title'][:30]}…")
            ft = fetch_fulltext(r.get("source_url", ""))
            if ft and len(ft) > len(r.get("body", "")):
                r["body"] = ft[:20000]
                stats["fulltext_fetched"] += 1
            time.sleep(1.0)   # 礼貌限速
    progress(None, "资讯", f"生成信号卡（{len(raws)} 条）…")
    cards = build_signal_cards(gateway, raws)
    for r in raws:
        card = cards.get(r["id"])
        if not card:
            continue
        publish_news(conn, r, card, r["score"]["gold"], _score_json(r), raw_id=r["id"],
                     item_kind="tech" if r["kind"] == "tech" else "news")
        db.decide_raw(conn, r["id"], "curated", r["score"]["gold"], _score_json(r))
        stats["published"] += 1
    return stats


# ---------------- 主流程 ----------------

def run(conn: sqlite3.Connection, gateway=None, progress: ProgressFn = _noop_progress,
        channels: list[str] | None = None, trigger: str = "manual",
        collect_enabled: bool = True, rescore_only: bool = False) -> dict:
    """全链路运行。返回汇总。collect_enabled=False 时只处理已落地的 pending。"""
    t0 = time.time()
    stages: list[dict] = []
    summary = {"trigger": trigger, "seconds": 0, "collected": 0, "raw_new": 0,
               "curated": 0, "quarantined": 0, "rejected": 0, "news": 0, "tech": 0,
               "jobs": 0, "errors": []}
    try:
        # ---- 1) 采集 → raw 落地
        if collect_enabled and not rescore_only:
            progress(5, "采集", "开始抓取全部渠道…")
            c = collect_all(progress, channels)
            for it in c["items"]:
                _id, new = db.upsert_raw(conn, it)
                if new:
                    summary["raw_new"] += 1
            summary["collected"] = len(c["items"])
            stages.append({"name": "collect", "ok": True, "items": len(c["items"]),
                           "report": c["report"]})
            progress(25, "采集", f"抓取 {len(c['items'])} 条，新落地 {summary['raw_new']} 条")

        # ---- 2) 质量门（逐 kind）
        settings = get_settings()
        qcfg = settings.quality
        use_judge = bool(qcfg.get("judge", True))
        all_curated: list[dict] = []
        for kind in ("job", "news", "tech"):
            pend = db.list_raw(conn, status="pending", kind=kind, limit=500)
            if rescore_only:
                # 重放质量门：未人工复核的隔离条目一并重过（人工 promote/discard 结论不被覆盖）
                pend += db.list_raw(conn, status="quarantined", kind=kind, limit=2000,
                                    unreviewed_only=True)
            if not pend:
                continue
            progress(40, "质量门", f"{kind} 过门（{len(pend)} 条）…")
            results = quality.gate_items(conn, gateway if use_judge else None, pend, kind,
                                         quality_cfg=qcfg, use_judge=use_judge)
            for r in results:
                sj = quality.score_to_json(r.score)
                db.decide_raw(conn, r.raw_id, r.status, r.gold, sj)
                if r.status == "curated":
                    row = db.get_raw(conn, r.raw_id)
                    row["score"] = json.loads(sj)
                    all_curated.append(row)
                    summary["curated"] += 1
                elif r.status == "quarantined":
                    summary["quarantined"] += 1
                else:
                    summary["rejected"] += 1
            stages.append({"name": f"gate:{kind}", "ok": True,
                           "curated": sum(1 for r in results if r.status == "curated"),
                           "quarantined": sum(1 for r in results if r.status == "quarantined"),
                           "rejected": sum(1 for r in results if r.status == "rejected")})
            progress(50, "质量门", f"{kind}：精选 {sum(1 for r in results if r.status == 'curated')}"
                                  f" / 隔离 {sum(1 for r in results if r.status == 'quarantined')}")

        # ---- 2.5) 隔离区自动过期（未人工复核的超期条目 → rejected，防积压）
        expire_days = int(qcfg.get("quarantine_expire_days", 14) or 0)
        if expire_days > 0:
            expired = db.expire_quarantined(conn, expire_days)
            if expired:
                stages.append({"name": "expire_quarantine", "ok": True, "expired": expired})
                summary["rejected"] += expired
                progress(55, "过期", f"隔离区自动清理 {expired} 条超期未复核条目")

        # ---- 3) 发布精选（含幂等补偿：历史运行中过门但未发布的悬空条目）
        orphans = db.list_raw(conn, status="curated", limit=500)
        for o in orphans:
            if o.get("curated_id"):
                continue
            if o["kind"] == "job":
                row = conn.execute("SELECT 1 FROM jobs WHERE raw_id=?", (o["id"],)).fetchone()
            else:
                row = conn.execute("SELECT 1 FROM news WHERE raw_id=?", (o["id"],)).fetchone()
            if not row:
                all_curated.append(o)
        _unique: dict[int, dict] = {}
        for r in all_curated:
            _unique.setdefault(r["id"], r)
        all_curated = list(_unique.values())
        jobs_raw = [r for r in all_curated if r["kind"] == "job"]
        news_raw = [r for r in all_curated if r["kind"] == "news"]
        tech_raw = [r for r in all_curated if r["kind"] == "tech"]
        if jobs_raw:
            js = _publish_curated_jobs(conn, gateway, jobs_raw, progress)
            summary["jobs"] = js["added"]
            stages.append({"name": "publish:jobs", "ok": True, **js})
        if news_raw or tech_raw:
            progress(60, "发布", f"资讯 {len(news_raw)} 条 / 技术 {len(tech_raw)} 条…")
            ns = _publish_curated_news(conn, gateway, news_raw + tech_raw, progress)
            summary["news"] = sum(1 for r in news_raw if r["id"] in
                                  {x["id"] for x in news_raw})
            summary["news"] = len(news_raw)
            summary["tech"] = len(tech_raw)
            stages.append({"name": "publish:news", "ok": True, **ns})

        # ---- 4) 整合（跨渠道归并 / 信息差度量）
        progress(75, "整合", "跨渠道归并与信息差度量…")
        try:
            from .consolidate import run_consolidate
            cons = run_consolidate(conn)
            stages.append({"name": "consolidate", "ok": True, **cons})
        except Exception as e:  # noqa: BLE001
            stages.append({"name": "consolidate", "ok": False, "error": str(e)})

        # ---- 5) 周快照（趋势）
        progress(85, "快照", "落周级统计快照…")
        try:
            from .snapshots import snapshot_weekly
            snapshot_weekly(conn)
            stages.append({"name": "snapshot", "ok": True})
        except Exception as e:  # noqa: BLE001
            stages.append({"name": "snapshot", "ok": False, "error": str(e)})

        # ---- 6) 向量化（混合检索；模型未配则跳过）
        progress(92, "向量化", "精选层增量向量化…")
        try:
            from .retrieval import ensure_vectors
            vec = ensure_vectors(conn, progress=progress)
            stages.append({"name": "vectorize", "ok": True, **vec})
        except ImportError:
            stages.append({"name": "vectorize", "ok": False, "error": "retrieval 未就绪"})
        except Exception as e:  # noqa: BLE001
            stages.append({"name": "vectorize", "ok": False, "error": str(e)})

        progress(100, "完成", _summary_line(summary))
        summary["seconds"] = round(time.time() - t0, 1)
        db.log_pipeline_run(conn, trigger, stages)
        return summary
    except Exception as e:  # noqa: BLE001
        summary["errors"].append(f"{type(e).__name__}: {e}")
        summary["seconds"] = round(time.time() - t0, 1)
        db.log_pipeline_run(conn, trigger, stages, error=str(e))
        raise


def _summary_line(s: dict) -> str:
    return (f"新落地 {s['raw_new']}，精选 {s['curated']}（岗位 {s['jobs']}/资讯 {s['news']}"
            f"/技术 {s['tech']}），隔离 {s['quarantined']}，拦截 {s['rejected']}，"
            f"耗时 {s['seconds'] or '?'}s")


# ---------------- 复核动作（隔离区） ----------------

def promote_raw(conn, raw_id: int, gateway=None) -> str:
    """隔离区恢复：直接过门发布。"""
    raw = db.get_raw(conn, raw_id)
    if not raw:
        return "条目不存在"
    score = raw.get("score") or {}
    score.setdefault("gold", raw.get("gold_score") or 0.0)   # 老/异常数据缺 gold 键时兜底
    raw["score"] = score
    db.review_raw(conn, raw_id, "promote")
    if raw["kind"] == "job":
        _publish_curated_jobs(conn, gateway, [raw], _noop_progress)
    else:
        _publish_curated_news(conn, gateway, [raw], _noop_progress)
    return "已恢复进精选"


def discard_raw(conn, raw_id: int) -> str:
    db.review_raw(conn, raw_id, "discard")
    db.decide_raw(conn, raw_id, "rejected", None, "{}")
    return "已永久丢弃"
