"""CLI 入口：xinxicha <command>"""
from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="信息差 xinxicha：本地优先的求职情报与决策服务")


def _conn():
    from . import db
    conn = db.connect()
    db.init_db(conn)
    return conn


def _profile(conn):
    import json
    from . import db
    from .models import DEFAULT_PROFILE
    raw = db.load_profile(conn)
    try:
        from .models import Profile
        return Profile.model_validate(json.loads(raw)) if raw else DEFAULT_PROFILE
    except Exception:  # noqa: BLE001
        return DEFAULT_PROFILE


@app.command("init-db")
def init_db_cmd():
    """初始化 SQLite 数据库。"""
    _conn().close()
    typer.echo("✔ 数据库已初始化：data/app.db")


@app.command("load-sample")
def load_sample():
    """载入 32 条示例 JD + 资讯（无 API key 也能完整体验）。"""
    from . import db
    from .extract import structure
    from .sample_data import SAMPLE_JOBS, SAMPLE_NEWS
    conn = _conn()
    s = structure(conn, SAMPLE_JOBS, "sample", None, use_llm=False)
    news = sum(db.upsert_news(conn, {**n, "fetched_at": db.now()}) for n in SAMPLE_NEWS)
    typer.echo(f"✔ 岗位新增 {s.added}（重复 {s.duplicates}），资讯 {news} 条")
    conn.close()


@app.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8000):
    """启动 Web 服务。"""
    import uvicorn
    typer.echo(f"🔭 信息差启动：http://{host}:{port}")
    uvicorn.run("market_lens.web.app:app", host=host, port=port)


@app.command("ingest-file")
def ingest_file(path: Path, llm: bool = typer.Option(True, "--llm/--no-llm")):
    """导入 JD 文件（.txt/.md 多条 --- 分隔；.jsonl 每行 {"text":…}）。"""
    import json as _json
    from .extract import split_paste, structure
    from .gateway import LLMGateway
    from .config import get_settings
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        texts = []
        for line in raw.splitlines():
            try:
                obj = _json.loads(line)
                if isinstance(obj, dict) and obj.get("text"):
                    texts.append(obj["text"])
            except _json.JSONDecodeError:
                continue
    else:
        texts = split_paste(raw)
    conn = _conn()
    gw = LLMGateway(get_settings(), conn) if llm else None
    s = structure(conn, texts, "manual", gw, use_llm=llm)
    typer.echo(f"✔ 共 {s.total} 条：新增 {s.added}，重复 {s.duplicates}"
               + ("，LLM增强✓" if s.llm_used else ""))
    for e in s.errors:
        typer.echo(f"  ⚠ {e}")
    conn.close()


@app.command("ingest-rss")
def ingest_rss():
    """抓取公开资讯 RSS。"""
    from .sources import rss
    conn = _conn()
    r = rss.fetch_rss(conn, __import__("market_lens.config", fromlist=["get_settings"]).get_settings())
    typer.echo(f"✔ RSS 新增 {r['added']} 条；来源：{r['sources']}")
    for e in r["errors"]:
        typer.echo(f"  ⚠ {e}")
    conn.close()


@app.command("report")
def report(kind: str = typer.Option("market", help="market|gap|digest")):
    """生成报告（输出到 data/reports/ 并存库）。"""
    from . import db, reports
    from .gateway import LLMGateway
    from .config import get_settings, DATA_DIR
    conn = _conn()
    profile = _profile(conn)
    gw = LLMGateway(get_settings(), conn)
    rid = {"market": reports.gen_market_report, "gap": reports.gen_gap_report,
           "digest": reports.gen_digest}[kind](conn, gw, profile) if kind != "digest" \
        else reports.gen_digest(conn, gw)
    r = db.get_report(conn, rid)
    out = DATA_DIR / "reports"
    out.mkdir(parents=True, exist_ok=True)
    fp = out / f"{kind}-{rid}.md"
    fp.write_text(r["content_md"], encoding="utf-8")
    typer.echo(f"✔ 报告 #{rid} 已生成：{fp}")
    conn.close()


@app.command("ask")
def ask(question: str):
    """命令行问答。"""
    from . import qa
    from .gateway import LLMGateway
    from .config import get_settings
    conn = _conn()
    r = qa.answer(conn, LLMGateway(get_settings(), conn), question)
    typer.echo(r["answer_md"])
    if r["cited"]:
        typer.echo(f"\n（引用 {len(r['cited'])} 条岗位，见 Web 端问答页）")
    conn.close()


@app.command("regovern")
def regovern(kind: str = typer.Option("all", help="all|news|tech"), limit: int = 200):
    """存量回填治理：把规则卡/英文直出的条目重新提取为中文信号卡。"""
    from .gateway import LLMGateway
    from .config import get_settings
    from .news_pipeline import regovern_news
    kinds = {"all": ("news", "tech"), "news": ("news",), "tech": ("tech",)}[kind]
    conn = _conn()
    gw = LLMGateway(get_settings(), conn)

    def prog(pct, stage, msg):
        typer.echo(f"  [{stage}] {msg}")
    r = regovern_news(conn, gw, limit=limit, kinds=kinds, progress=prog)
    typer.echo(f"✔ 重新治理 {r.get('regoverned', 0)}/{r.get('pending', 0)} 条为中文信号卡")
    conn.close()


@app.command("repair-salary")
def repair_salary():
    """薪资数据修复：以 salary_raw 原文为唯一依据重算数值；无原文的残留数值清零（有数无据）。"""
    from .salary import parse_salary
    conn = _conn()
    rows = conn.execute(
        "SELECT id, salary_raw, salary_min_k, salary_max_k FROM jobs").fetchall()
    reparsed = cleared = unchanged = 0
    for r in rows:
        raw = (r["salary_raw"] or "").strip()
        sal = parse_salary(raw) if raw else None
        if sal:
            conn.execute(
                "UPDATE jobs SET salary_min_k=?, salary_max_k=?, salary_months=?,"
                " annual_min_wan=?, annual_max_wan=? WHERE id=?",
                (sal.min_k, sal.max_k, sal.months,
                 sal.annual_min_wan, sal.annual_max_wan, r["id"]))
            reparsed += 1
        elif r["salary_min_k"] is not None or r["salary_max_k"] is not None:
            conn.execute(
                "UPDATE jobs SET salary_min_k=NULL, salary_max_k=NULL, salary_months=12,"
                " annual_min_wan=NULL, annual_max_wan=NULL WHERE id=?", (r["id"],))
            cleared += 1
        else:
            unchanged += 1
    conn.commit()
    typer.echo(f"✔ 重算 {reparsed} 条；清除无原文依据的残留数值 {cleared} 条；无薪资 {unchanged} 条")
    conn.close()


@app.command("self-test")
def self_test():
    """规则层快速自检（薪资/WLB/技能/匹配/文本规范化/质量门）。"""
    from .salary import parse_salary
    from .wlb import analyze_wlb
    from .taxonomy import match_skills
    from . import textfmt, quality
    cases = [("15-25K·14薪", 15, 25, 14), ("2-4万", 20, 40, 12), ("20-40万/年", None, None, 12)]
    for raw, lo, hi, m in cases:
        r = parse_salary(raw)
        assert r is not None, raw
        if lo is not None:
            assert (r.min_k, r.max_k, r.months) == (lo, hi, m), (raw, r)
    assert parse_salary("面议") is None
    lvl = analyze_wlb("弹性工作 双休 不加班").level
    assert lvl == 0, lvl
    lvl996 = analyze_wlb("狼性文化 快节奏 能承受压力").level
    assert lvl996 == 2, lvl996
    assert "mcp" in match_skills("熟悉 MCP 协议与 function calling")
    n = textfmt.normalize_text("负责 <b>AI</b> 开发\n\n\n\n要求：\n  • Python\n  ●RAG")
    assert "· Python" in n and "· RAG" in n and "\n\n\n" not in n
    bad, _ = textfmt.is_bad_summary("点击查看原文>>", "标题")
    assert bad
    scams = quality.scam_check({"title": "付费内推", "body": "缴纳内推服务费"})
    assert scams
    typer.echo("✔ 规则层自检通过（薪资/WLB/技能/文本规范化/反诈骗）")


@app.command("run-once")
def run_once(collect: bool = typer.Option(True, "--collect/--no-collect"),
             channels: str = typer.Option("", help="逗号分隔渠道，空=全部")):
    """一键全链路：采集→质量门→精选发布→整合→快照→向量化。"""
    from . import db, pipeline
    from .config import get_settings
    from .gateway import LLMGateway

    def progress(pct, stage, msg):
        bar = f" {int(pct)}%" if pct is not None else ""
        typer.echo(f"  [{stage}{bar}] {msg}")
    conn = _conn()
    gw = LLMGateway(get_settings(), conn)
    chs = [c.strip() for c in channels.split(",") if c.strip()] or None
    try:
        s = pipeline.run(conn, gw, progress=progress, channels=chs, trigger="cli",
                         collect_enabled=collect)
        typer.echo(f"✔ 完成：新落地 {s['raw_new']}，精选 {s['curated']}"
                   f"（岗位 {s['jobs']}/资讯 {s['news']}/技术 {s['tech']}），"
                   f"隔离 {s['quarantined']}，拦截 {s['rejected']}，耗时 {s['seconds']}s")
    finally:
        conn.close()


@app.command("migrate-gold")
def migrate_gold(rescore: bool = typer.Option(True, "--rescore/--no-rescore",
                                              help="存量数据重过质量门（低分降隔离）")):
    """存量数据迁移：jobs/news 反向生成 raw_items + 重打分 + JD 排版修复。"""
    import json as _json
    from . import db, quality
    from .textfmt import normalize_text
    conn = _conn()
    created = promoted = quarantined = repaired = 0
    # 1) 存量 jobs → raw_items
    for r in conn.execute("SELECT id, source, source_url, title, company, city, description,"
                          " fetched_at, gold_score FROM jobs").fetchall():
        raw = {"kind": "job", "source": r["source"], "source_url": r["source_url"] or "",
               "title": r["title"], "body": r["description"] or "",
               "published_at": "", "fetched_at": r["fetched_at"]}
        h = db.norm_hash(raw["source_url"], raw["title"], raw["body"])
        exists = conn.execute("SELECT id FROM raw_items WHERE norm_hash=?", (h,)).fetchone()
        if exists:
            continue
        cur = conn.execute(
            "INSERT INTO raw_items(kind,source,source_url,title,body,published_at,fetched_at,"
            "norm_hash,status,curated_id,created_at) VALUES (?,?,?,?,?,?,?,?,'curated',?,?)",
            (raw["kind"], raw["source"], raw["source_url"], raw["title"], raw["body"],
             raw["published_at"], raw["fetched_at"], h, r["id"], db.now()))
        conn.execute("UPDATE jobs SET raw_id=? WHERE id=?", (cur.lastrowid, r["id"]))
        created += 1
    conn.commit()
    # 2) 存量 news → raw_items
    for r in conn.execute("SELECT id, source, title, url, fulltext, summary, published_at,"
                          " fetched_at, raw_id FROM news").fetchall():
        if r["raw_id"]:
            continue
        raw = {"kind": "tech" if (r["source"] in ("arxiv", "hfpapers", "ghtrend", "radar"))
               else "news",
               "source": r["source"], "source_url": r["url"], "title": r["title"],
               "body": r["fulltext"] or r["summary"] or "", "published_at": r["published_at"]}
        h = db.norm_hash(raw["source_url"], raw["title"], raw["body"])
        exists = conn.execute("SELECT id FROM raw_items WHERE norm_hash=?", (h,)).fetchone()
        if exists:
            continue
        cur = conn.execute(
            "INSERT INTO raw_items(kind,source,source_url,title,body,published_at,fetched_at,"
            "norm_hash,status,curated_id,created_at) VALUES (?,?,?,?,?,?,?,?,'curated',?,?)",
            (raw["kind"], raw["source"], raw["source_url"], raw["title"], raw["body"],
             raw["published_at"], r["fetched_at"], h, r["id"], db.now()))
        conn.execute("UPDATE news SET raw_id=? WHERE id=?", (cur.lastrowid, r["id"]))
        created += 1
    conn.commit()
    # 3) 存量重打分（低分降隔离——只降级不删除）
    if rescore:
        for kind, table in (("job", "jobs"), ("news", "news"), ("tech", "news")):
            rows = [dict(x) for x in conn.execute(
                f"SELECT r.id, r.source, r.title, r.body, r.published_at, r.fetched_at,"
                f" r.channel_meta FROM raw_items r JOIN {table} t ON t.raw_id=r.id"
                f" WHERE r.kind=? AND r.status='curated'", (kind,))]
            results = quality.gate_items(conn, None, rows, kind, use_judge=False)
            for res in results:
                sj = quality.score_to_json(res.score)
                if res.status == "curated":
                    db.decide_raw(conn, res.raw_id, "curated", res.gold, sj)
                    promoted += 1
                else:
                    db.decide_raw(conn, res.raw_id, "quarantined", res.gold, sj)
                    conn.execute(f"UPDATE {table} SET gold_score=? WHERE raw_id=?",
                                 (res.gold, res.raw_id))
                    quarantined += 1
                conn.execute(f"UPDATE {table} SET gold_score=?, gold_json=? WHERE raw_id=?",
                             (res.gold, sj, res.raw_id))
        conn.commit()
    # 4) JD 排版修复（存量 description 重跑规范化）+ 标题清洗 + 存量资讯补规则信号卡
    from .textfmt import clean_title
    from .news_pipeline import fallback_card
    from . import db as _db
    for r in conn.execute("SELECT id, description, title FROM jobs WHERE description != ''").fetchall():
        fixed = normalize_text(r["description"])
        titled = clean_title(r["title"] or "")
        if fixed != r["description"] or titled != r["title"]:
            conn.execute("UPDATE jobs SET description=?, title=? WHERE id=?",
                         (fixed, titled, r["id"]))
            repaired += 1
    import json as _json2
    for r in conn.execute("SELECT id, title, fulltext, summary FROM news "
                          "WHERE one_liner = ''").fetchall():
        card = fallback_card(r["title"] or "", r["fulltext"] or r["summary"] or "")
        conn.execute("UPDATE news SET one_liner=?, signal_type=?, key_points=? WHERE id=?",
                     (card.one_liner, card.signal_type,
                      _json2.dumps(card.key_points, ensure_ascii=False), r["id"]))
    conn.commit()
    typer.echo(f"✔ 迁移完成：生成 raw {created} 条；重打分精选 {promoted}/隔离 {quarantined}；"
               f"排版修复 {repaired} 条")
    conn.close()


def main():
    app()


if __name__ == "__main__":
    main()
