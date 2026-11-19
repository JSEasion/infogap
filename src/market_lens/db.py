"""数据层：SQLite + FTS5 全文索引（jieba 预分词）。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import DATA_DIR, ROOT

DB_PATH = DATA_DIR / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL DEFAULT 'manual',
    source_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    company TEXT DEFAULT '',
    city TEXT DEFAULT '',
    salary_raw TEXT DEFAULT '',
    salary_min_k REAL, salary_max_k REAL, salary_months INTEGER DEFAULT 12,
    annual_min_wan REAL, annual_max_wan REAL,
    experience TEXT DEFAULT '', education TEXT DEFAULT '',
    description_zh TEXT DEFAULT '',   -- 外文JD中文摘要（LLM增强时生成）
    work_mode TEXT DEFAULT 'unknown',
    company_type TEXT DEFAULT 'unknown',
    wlb_level INTEGER, wlb_signals TEXT DEFAULT '[]', wlb_basis TEXT DEFAULT '',
    skills TEXT DEFAULT '[]', skills_raw TEXT DEFAULT '[]',
    extract_method TEXT DEFAULT 'rules', structured_ok INTEGER DEFAULT 0,
    description TEXT DEFAULT '',
    skills_text TEXT DEFAULT '',      -- FTS 冗余列
    desc_tokens TEXT DEFAULT '',      -- FTS 冗余列（jieba 分词）
    fetched_at TEXT DEFAULT '', created_at TEXT DEFAULT '',
    dedup_hash TEXT UNIQUE,
    published_at TEXT DEFAULT '',     -- 来源发布时间（时效评分依据）
    gold_score REAL, gold_json TEXT DEFAULT '{}',   -- 含金量分与七维分解
    raw_id INTEGER,                   -- 原始层回链（raw_items.id，可溯源）
    referral_code TEXT DEFAULT '',    -- 内推码（须原文可溯）
    deadline TEXT DEFAULT '',         -- 投递截止（须原文可溯）
    contact TEXT DEFAULT ''           -- 投递邮箱/链接（须原文可溯）
);
CREATE INDEX IF NOT EXISTS idx_jobs_city ON jobs(city);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);

CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
    title, company, city, skills_text, desc_tokens,
    content='jobs', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS jobs_ai AFTER INSERT ON jobs BEGIN
  INSERT INTO jobs_fts(rowid, title, company, city, skills_text, desc_tokens)
  VALUES (new.id, new.title, new.company, new.city, new.skills_text, new.desc_tokens);
END;
CREATE TRIGGER IF NOT EXISTS jobs_ad AFTER DELETE ON jobs BEGIN
  INSERT INTO jobs_fts(jobs_fts, rowid, title, company, city, skills_text, desc_tokens)
  VALUES ('delete', old.id, old.title, old.company, old.city, old.skills_text, old.desc_tokens);
END;
CREATE TRIGGER IF NOT EXISTS jobs_au AFTER UPDATE ON jobs BEGIN
  INSERT INTO jobs_fts(jobs_fts, rowid, title, company, city, skills_text, desc_tokens)
  VALUES ('delete', old.id, old.title, old.company, old.city, old.skills_text, old.desc_tokens);
  INSERT INTO jobs_fts(rowid, title, company, city, skills_text, desc_tokens)
  VALUES (new.id, new.title, new.company, new.city, new.skills_text, new.desc_tokens);
END;

CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT DEFAULT '', title TEXT DEFAULT '', url TEXT UNIQUE DEFAULT '',
    summary TEXT DEFAULT '', published_at TEXT DEFAULT '', fetched_at TEXT DEFAULT '',
    gold_score REAL, gold_json TEXT DEFAULT '{}', raw_id INTEGER,
    one_liner TEXT DEFAULT '',        -- 信号卡：≤40字说清事件
    key_points TEXT DEFAULT '[]',     -- 信号卡：2-4条要点
    entities_json TEXT DEFAULT '[]',  -- 信号卡：公司/技术/人物
    impact TEXT DEFAULT '',           -- 信号卡：对求职者/行业的含义
    signal_type TEXT DEFAULT 'none',  -- funding|layoff|release|policy|hiring|tech|none
    fulltext TEXT DEFAULT '',         -- 正文补全（无效摘要时抓取原文）
    fts_tokens TEXT DEFAULT '',       -- FTS 冗余列（jieba 分词）
    kind TEXT DEFAULT 'news',         -- news | tech（前沿技术流）
    card_method TEXT DEFAULT 'rule',  -- 治理方式：llm（中文提取卡）/ rule（规则降级）
    heat INTEGER DEFAULT 0            -- 热度：GitHub ⭐ / HF upvotes
);

-- 注意：news_fts 虚表与触发器不在本脚本里批量建（executescript 批量建 + 后续
-- drop/rebuild 会触发 SQLite 语句缓存问题 → "malformed"），由 init_db 逐条 execute 创建。

-- ============ 三层漏斗：原始层 / 归并 / 信号 / 快照 / 向量 / 流水线 / 任务 ============

CREATE TABLE IF NOT EXISTS raw_items (           -- 原始层：全量落地 append-only
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'job',            -- job | news | tech
    source TEXT NOT NULL DEFAULT '',
    source_url TEXT DEFAULT '',
    title TEXT DEFAULT '',
    body TEXT DEFAULT '',                        -- normalize_text 后的原文
    published_at TEXT DEFAULT '',
    fetched_at TEXT DEFAULT '',
    channel_meta TEXT DEFAULT '{}',              -- 渠道元数据 tier/scarcity/group
    norm_hash TEXT UNIQUE,                       -- 跨渠道去重指纹
    status TEXT DEFAULT 'pending',               -- pending|curated|quarantined|rejected
    gold_score REAL, score_json TEXT DEFAULT '{}',
    curated_id INTEGER,                          -- 精选层回链
    review_action TEXT DEFAULT '',               -- 用户复核动作（校准权重）
    created_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_raw_status ON raw_items(status);
CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_items(source);
CREATE INDEX IF NOT EXISTS idx_raw_kind ON raw_items(kind);

CREATE TABLE IF NOT EXISTS job_entities (        -- 跨渠道同岗归并（信息差度量）
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_key TEXT UNIQUE,                       -- norm(title)|company|city
    job_ids TEXT DEFAULT '[]',
    sources TEXT DEFAULT '[]',
    first_seen_at TEXT DEFAULT '',
    first_seen_source TEXT DEFAULT '',
    lead_days REAL DEFAULT 0,                    -- 冷门渠道早于主流平台的天数
    salary_conflict TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS news_signals (
    news_id INTEGER PRIMARY KEY,
    signal_type TEXT DEFAULT 'none',
    entities TEXT DEFAULT '[]',
    importance REAL DEFAULT 0,
    cluster_id INTEGER,
    created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS snapshots (           -- 周级统计快照（趋势）
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period TEXT NOT NULL,                        -- 如 2026-W46
    kind TEXT NOT NULL,                          -- skills|salary|volume|channels
    stats_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT '',
    UNIQUE(period, kind)
);

CREATE TABLE IF NOT EXISTS vectors (             -- 混合检索向量层（bge-m3）
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,                     -- job|news|tech
    item_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER DEFAULT 0,
    vec BLOB NOT NULL,
    text_hash TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    UNIQUE(item_type, item_id)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (       -- 流水线可观测
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT DEFAULT 'manual',               -- manual|scheduler|cli
    stages_json TEXT DEFAULT '[]',
    started_at TEXT DEFAULT '', finished_at TEXT DEFAULT '',
    error TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (               -- 异步任务中心
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, title TEXT DEFAULT '',
    params_json TEXT DEFAULT '{}',
    status TEXT DEFAULT 'queued',                -- queued|running|done|failed|cancelled|orphaned
    progress REAL, stage TEXT DEFAULT '', message TEXT DEFAULT '',
    result_json TEXT DEFAULT '{}', error TEXT DEFAULT '',
    created_at TEXT DEFAULT '', started_at TEXT DEFAULT '', finished_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,               -- market | gap | digest
    title TEXT DEFAULT '',
    content_md TEXT DEFAULT '',
    stats_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT, provider TEXT, model TEXT,
    prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
    cost_cny REAL DEFAULT 0, ok INTEGER DEFAULT 1, created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS qa_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT, answer_md TEXT DEFAULT '', cited_ids TEXT DEFAULT '[]',
    created_at TEXT DEFAULT ''
);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    # 顺序很重要：先给老库补列（触发器/FTS 在 CREATE 时校验列引用），再跑全量 DDL；
    # 全新库 table_info 查不到表 → _add_columns 自动跳过
    _add_columns(conn, "jobs", {
        "description_zh": "TEXT DEFAULT ''",
        "published_at": "TEXT DEFAULT ''",
        "gold_score": "REAL",
        "gold_json": "TEXT DEFAULT '{}'",
        "raw_id": "INTEGER",
        "referral_code": "TEXT DEFAULT ''",
        "deadline": "TEXT DEFAULT ''",
        "contact": "TEXT DEFAULT ''",
    })
    _add_columns(conn, "news", {
        "gold_score": "REAL",
        "gold_json": "TEXT DEFAULT '{}'",
        "raw_id": "INTEGER",
        "one_liner": "TEXT DEFAULT ''",
        "key_points": "TEXT DEFAULT '[]'",
        "entities_json": "TEXT DEFAULT '[]'",
        "impact": "TEXT DEFAULT ''",
        "signal_type": "TEXT DEFAULT 'none'",
        "fulltext": "TEXT DEFAULT ''",
        "fts_tokens": "TEXT DEFAULT ''",
        "kind": "TEXT DEFAULT 'news'",
        "card_method": "TEXT DEFAULT 'rule'",   # 治理方式：llm（中文提取卡）/ rule（规则降级）
        "heat": "INTEGER DEFAULT 0",            # 热度：GitHub ⭐ / HF upvotes
    })
    conn.executescript(SCHEMA)
    # 外部内容 FTS 引导：老库已有数据但索引为空时彻底重建（否则触发器 delete → malformed）
    _fts_bootstrap(conn)
    conn.commit()
    if own:
        conn.close()


def _fts_bootstrap(conn: sqlite3.Connection) -> None:
    """news FTS：快速路径（表/触发器/行数一致 → 直接返回）；
    否则 drop → 逐条 execute 创建虚表+触发器 → rebuild（老库列已补齐）。
    jobs_fts 在 SCHEMA 内（老库列本就齐全，无迁移问题）。"""
    try:
        n_tbl = conn.execute("SELECT COUNT(*) c FROM news").fetchone()["c"]
        n_fts = conn.execute("SELECT COUNT(*) c FROM news_fts").fetchone()["c"]
        trig = conn.execute("SELECT COUNT(*) c FROM sqlite_master WHERE type='trigger' "
                            "AND name IN ('news_ai','news_ad','news_au')").fetchone()["c"]
        if n_fts == n_tbl and trig == 3:
            return
    except sqlite3.DatabaseError:
        conn.rollback()
    conn.execute("DROP TABLE IF EXISTS news_fts")
    conn.execute("""CREATE VIRTUAL TABLE news_fts USING fts5(
        title, fts_tokens, content='news', content_rowid='id', tokenize='unicode61')""")
    conn.execute("CREATE TRIGGER IF NOT EXISTS news_ai AFTER INSERT ON news BEGIN "
                 "INSERT INTO news_fts(rowid, title, fts_tokens) "
                 "VALUES (new.id, new.title, new.fts_tokens); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS news_ad AFTER DELETE ON news BEGIN "
                 "INSERT INTO news_fts(news_fts, rowid, title, fts_tokens) "
                 "VALUES ('delete', old.id, old.title, old.fts_tokens); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS news_au AFTER UPDATE ON news BEGIN "
                 "INSERT INTO news_fts(news_fts, rowid, title, fts_tokens) "
                 "VALUES ('delete', old.id, old.title, old.fts_tokens); "
                 "INSERT INTO news_fts(rowid, title, fts_tokens) "
                 "VALUES (new.id, new.title, new.fts_tokens); END")
    try:
        if n_tbl:
            conn.execute("INSERT INTO news_fts(news_fts) VALUES('rebuild')")
    except sqlite3.DatabaseError:
        conn.rollback()   # 不阻塞启动；可手动 rebuild 修复


def _add_columns(conn: sqlite3.Connection, table: str, cols: dict[str, str]) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if not existing:   # 全新库：表还没建，交给 executescript
        return
    for col, ddl in cols.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


# ---------- 岗位 ----------

JOB_FIELDS = ["id", "source", "source_url", "title", "company", "city", "salary_raw",
              "salary_min_k", "salary_max_k", "salary_months", "annual_min_wan", "annual_max_wan",
              "experience", "education", "work_mode", "company_type", "wlb_level",
              "wlb_signals", "wlb_basis", "skills", "skills_raw", "extract_method",
              "structured_ok", "description", "skills_text", "desc_tokens",
              "fetched_at", "created_at", "dedup_hash"]


def dedup_hash(title: str, company: str, city: str, salary_raw: str, description: str) -> str:
    basis = f"{title.strip()}|{company.strip()}|{city.strip()}|{salary_raw.strip()}|{description.strip()[:200]}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def upsert_job(conn: sqlite3.Connection, job: dict[str, Any]) -> tuple[int | None, bool]:
    """按 dedup_hash 去重。返回 (id, 是否新插入)。"""
    h = job.get("dedup_hash") or dedup_hash(
        job.get("title", ""), job.get("company", ""), job.get("city", ""),
        job.get("salary_raw", ""), job.get("description", ""))
    row = conn.execute("SELECT id FROM jobs WHERE dedup_hash=?", (h,)).fetchone()
    if row:
        return row["id"], False
    fields = [f for f in JOB_FIELDS if f in job and f != "id"]
    cols = ",".join(fields)
    ph = ",".join("?" for _ in fields)
    vals = [job[f] for f in fields]
    cur = conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({ph})", vals)
    conn.commit()
    return cur.lastrowid, True


def get_job(conn, job_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


_EDU_PATTERNS = {
    "大专": ["%大专%", "%专科%"],
    "本科": ["%本科%"],
    "硕士": ["%硕士%", "%研究生%"],
    "博士": ["%博士%"],
}


def list_jobs(conn, q: str = "", city: str = "", source: str = "", skill: str = "",
              salary_min: float | None = None, education: str = "",
              min_gold: float | None = None,
              limit: int = 100, offset: int = 0) -> list[dict]:
    where, vals = ["1=1"], []
    if q:
        where.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
        vals += [f"%{q}%"] * 3
    if city:
        where.append("city LIKE ?")
        vals.append(f"%{city}%")
    if source:
        where.append("source=?")
        vals.append(source)
    if skill:
        where.append("skills LIKE ?")
        vals.append(f'%"{skill}"%')
    if salary_min is not None:
        where.append("(salary_min_k IS NOT NULL AND salary_min_k >= ?)")
        vals.append(salary_min)
    if min_gold is not None:
        where.append("(gold_score IS NOT NULL AND gold_score >= ?)")
        vals.append(min_gold)
    if education in _EDU_PATTERNS:
        pats = _EDU_PATTERNS[education]
        where.append("(" + " OR ".join("education LIKE ?" for _ in pats) + ")")
        vals += pats
    rows = conn.execute(
        f"SELECT * FROM jobs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ? OFFSET ?",
        vals + [limit, offset]).fetchall()
    return [dict(r) for r in rows]


def count_jobs(conn) -> int:
    return conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]


def delete_job(conn, job_id: int) -> None:
    conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    conn.commit()


# ---------- 画像 ----------

def save_profile(conn, data_json: str) -> None:
    conn.execute("INSERT INTO profiles(id, data_json) VALUES (1, ?) "
                 "ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json", (data_json,))
    conn.commit()


def load_profile(conn) -> str | None:
    row = conn.execute("SELECT data_json FROM profiles WHERE id=1").fetchone()
    return row["data_json"] if row else None


# ---------- 资讯 / 报告 / 问答 ----------

NEWS_FIELDS = ["source", "title", "url", "summary", "published_at", "gold_score",
               "gold_json", "raw_id", "one_liner", "key_points", "entities_json",
               "impact", "signal_type", "card_method", "heat", "fulltext",
               "fts_tokens", "kind"]


def upsert_news(conn, item: dict) -> bool:
    """按 url 去重入库；支持信号卡字段。新插入返回 True。"""
    if not item.get("url"):
        return False
    row = conn.execute("SELECT id FROM news WHERE url=?", (item["url"],)).fetchone()
    if row:
        # 已存在：仅回填可更新的信号卡/正文字段（不覆盖既有非空值）
        updates = {k: item[k] for k in ("one_liner", "key_points", "entities_json",
                                        "impact", "signal_type", "fulltext",
                                        "gold_score", "gold_json", "fts_tokens")
                   if item.get(k) not in (None, "", "[]")}
        if updates:
            old = conn.execute(
                f"SELECT {', '.join(updates)} FROM news WHERE id=?", (row["id"],)).fetchone()
            keep = {k: v for k, v in updates.items()
                    if not (old and old[k] not in (None, "", "[]"))}
            if keep:
                sets = ", ".join(f"{k}=?" for k in keep)
                conn.execute(f"UPDATE news SET {sets} WHERE id=?",
                             (*keep.values(), row["id"]))
                conn.commit()
        return False
    fields = [f for f in NEWS_FIELDS if f in item]
    vals = [item[f] for f in fields]
    cols = ",".join(fields)
    ph = ",".join("?" for _ in fields)
    try:
        conn.execute(f"INSERT INTO news ({cols}) VALUES ({ph})", vals)
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def query_news(conn, kind: str = "", q: str = "", signal: str = "", source: str = "",
               min_gold: float | None = None, sort: str = "hot", page: int = 1,
               per: int = 20) -> dict:
    """资讯/技术列表统一查询：关键字搜索 + 多维排序 + 分页。
    sort: hot=热度(heat×含金×重要性) | new=最新 | gold=含金量。返回 {items, total, page, pages}。"""
    where, vals = ["1=1"], []
    if kind:
        where.append("n.kind=?")
        vals.append(kind)
    if signal:
        where.append("n.signal_type=?")
        vals.append(signal)
    if source:
        where.append("n.source=?")
        vals.append(source)
    if min_gold is not None:
        where.append("(gold_score IS NOT NULL AND gold_score >= ?)")
        vals.append(min_gold)
    if q:
        like = f"%{q.strip()}%"
        where.append("(title LIKE ? OR one_liner LIKE ? OR key_points LIKE ? "
                     "OR entities_json LIKE ? OR fulltext LIKE ?)")
        vals += [like] * 5
    wsql = " AND ".join(where)
    order = {
        "new": "COALESCE(NULLIF(n.published_at,''), n.fetched_at) DESC, n.id DESC",
        "gold": "COALESCE(n.gold_score,0) DESC, n.id DESC",
    }.get(sort, "(COALESCE(n.heat,0)*100 + COALESCE(n.gold_score,0)"
                "*COALESCE(s.importance,0.3)) DESC, n.id DESC")
    total = conn.execute(
        f"SELECT COUNT(*) c FROM news n LEFT JOIN news_signals s ON s.news_id=n.id "
        f"WHERE {wsql}", vals).fetchone()["c"]
    per = max(1, min(per, 100))
    page = max(1, page)
    rows = conn.execute(
        f"SELECT n.* FROM news n LEFT JOIN news_signals s ON s.news_id=n.id "
        f"WHERE {wsql} ORDER BY {order} LIMIT ? OFFSET ?",
        vals + [per, (page - 1) * per]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["key_points"] = json.loads(d.get("key_points") or "[]")
        d["entities"] = json.loads(d.get("entities_json") or "[]")
        d["score"] = json.loads(d.get("gold_json") or "{}")
        out.append(d)
    return {"items": out, "total": total, "page": page,
            "pages": max(1, (total + per - 1) // per)}


def list_news(conn, limit: int = 100, signal: str = "", min_gold: float | None = None) -> list[dict]:
    """资讯列表（旧接口，报告/周报用）：默认按 含金量×信号重要性×时间 排序。"""
    return query_news(conn, signal=signal, min_gold=min_gold,
                      sort="hot", page=1, per=limit)["items"]


def save_report(conn, kind: str, title: str, content_md: str, stats: dict) -> int:
    cur = conn.execute(
        "INSERT INTO reports(kind,title,content_md,stats_json,created_at) VALUES (?,?,?,?,?)",
        (kind, title, content_md, json.dumps(stats, ensure_ascii=False), now()))
    conn.commit()
    return cur.lastrowid


def get_report(conn, rid: int) -> dict | None:
    row = conn.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
    if row:
        d = dict(row)
        d["stats"] = json.loads(d.pop("stats_json") or "{}")
        return d
    return None


def list_reports(conn, kind: str = "", limit: int = 50) -> list[dict]:
    if kind:
        rows = conn.execute("SELECT id,kind,title,created_at FROM reports WHERE kind=? "
                            "ORDER BY id DESC LIMIT ?", (kind, limit)).fetchall()
    else:
        rows = conn.execute("SELECT id,kind,title,created_at FROM reports "
                            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def log_llm(conn, task: str, provider: str, model: str,
            prompt_tokens: int, completion_tokens: int, cost_cny: float, ok: bool) -> None:
    conn.execute(
        "INSERT INTO llm_usage(task,provider,model,prompt_tokens,completion_tokens,cost_cny,ok,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (task, provider, model, prompt_tokens, completion_tokens, cost_cny, int(ok), now()))
    conn.commit()


def usage_summary(conn) -> dict:
    rows = conn.execute(
        "SELECT provider, model, COUNT(*) n, SUM(prompt_tokens) pt, SUM(completion_tokens) ct, "
        "SUM(cost_cny) cost FROM llm_usage GROUP BY provider, model ORDER BY cost DESC").fetchall()
    month = datetime.now().strftime("%Y-%m")
    month_cost = conn.execute(
        "SELECT COALESCE(SUM(cost_cny),0) c FROM llm_usage WHERE created_at LIKE ? AND ok=1",
        (f"{month}%",)).fetchone()["c"]
    return {"rows": [dict(r) for r in rows], "month_cost_cny": round(month_cost or 0, 4)}


# ==================== 原始层（三层漏斗） ====================

def norm_hash(source_url: str, title: str, body: str) -> str:
    """跨渠道去重指纹：URL 精确优先，无 URL 用标题+正文指纹。"""
    if source_url.strip():
        return "u:" + hashlib.sha1(source_url.strip().encode("utf-8")).hexdigest()
    basis = f"{title.strip()}|{body.strip()[:300]}"
    return "t:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()


def upsert_raw(conn: sqlite3.Connection, item: dict) -> tuple[int | None, bool]:
    """原始层落地（append-only，norm_hash 去重）。返回 (id, 是否新插入)。"""
    h = item.get("norm_hash") or norm_hash(
        item.get("source_url", ""), item.get("title", ""), item.get("body", ""))
    row = conn.execute("SELECT id FROM raw_items WHERE norm_hash=?", (h,)).fetchone()
    if row:
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO raw_items(kind,source,source_url,title,body,published_at,fetched_at,"
        "channel_meta,norm_hash,status,created_at) VALUES (?,?,?,?,?,?,?,?,?, 'pending', ?)",
        (item.get("kind", "job"), item.get("source", ""), item.get("source_url", ""),
         item.get("title", ""), item.get("body", ""), item.get("published_at", ""),
         item.get("fetched_at") or now(), json.dumps(item.get("channel_meta") or {},
                                                     ensure_ascii=False),
         h, now()))
    conn.commit()
    return cur.lastrowid, True


def get_raw(conn, rid: int) -> dict | None:
    row = conn.execute("SELECT * FROM raw_items WHERE id=?", (rid,)).fetchone()
    if row:
        d = dict(row)
        d["channel_meta"] = json.loads(d.get("channel_meta") or "{}")
        d["score"] = json.loads(d.get("score_json") or "{}")
        return d
    return None


_RAW_ORDERS = {"": "id DESC", "id": "id DESC", "gold": "gold_score DESC, id DESC"}


def list_raw(conn, status: str = "", kind: str = "", source: str = "",
             limit: int = 100, offset: int = 0, unreviewed_only: bool = False,
             order: str = "") -> list[dict]:
    where, vals = ["1=1"], []
    if status:
        where.append("status=?"); vals.append(status)
    if kind:
        where.append("kind=?"); vals.append(kind)
    if source:
        where.append("source=?"); vals.append(source)
    if unreviewed_only:
        where.append("(review_action IS NULL OR review_action='')")
    rows = conn.execute(
        f"SELECT * FROM raw_items WHERE {' AND '.join(where)} "
        f"ORDER BY {_RAW_ORDERS.get(order, 'id DESC')} LIMIT ? OFFSET ?",
        vals + [limit, offset]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["channel_meta"] = json.loads(d.get("channel_meta") or "{}")
        d["score"] = json.loads(d.get("score_json") or "{}")
        out.append(d)
    return out


def count_raw(conn, status: str = "", kind: str = "") -> int:
    where, vals = ["1=1"], []
    if status:
        where.append("status=?"); vals.append(status)
    if kind:
        where.append("kind=?"); vals.append(kind)
    return conn.execute(
        f"SELECT COUNT(*) c FROM raw_items WHERE {' AND '.join(where)}", vals).fetchone()["c"]


def decide_raw(conn, rid: int, status: str, gold: float | None = None,
               score_json: str = "{}", curated_id: int | None = None) -> None:
    """质量门裁决回写。"""
    conn.execute(
        "UPDATE raw_items SET status=?, gold_score=?, score_json=?, curated_id=? WHERE id=?",
        (status, gold, score_json, curated_id, rid))
    conn.commit()


def review_raw(conn, rid: int, action: str) -> None:
    """用户复核动作（promote 恢复进精选 / discard 永久丢弃），落库用于校准渠道权重。"""
    conn.execute("UPDATE raw_items SET review_action=? WHERE id=?",
                 (f"{action}@{now()}", rid))
    conn.commit()


def expire_quarantined(conn, days: int) -> int:
    """隔离区自动过期：未人工复核且 fetched_at 超期的条目转 rejected。返回过期条数。"""
    if days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc).astimezone() - timedelta(days=days)
              ).isoformat(timespec="seconds")
    cur = conn.execute(
        "UPDATE raw_items SET status='rejected', review_action=? "
        "WHERE status='quarantined' AND (review_action IS NULL OR review_action='') "
        "AND fetched_at < ?",
        (f"expire@{now()}", cutoff))
    conn.commit()
    return cur.rowcount


def funnel_stats(conn) -> dict:
    """漏斗统计：各状态数量（仪表盘/信息差看板用）。"""
    rows = conn.execute(
        "SELECT status, kind, COUNT(*) n FROM raw_items GROUP BY status, kind").fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["status"], {})[r["kind"]] = r["n"]
    return out


# ==================== 归并 / 信号 / 快照 ====================

def upsert_entity(conn, match_key: str, job_id: int, source: str, seen_at: str) -> dict:
    """跨渠道同岗归并：同 key 的岗位聚合，记录首发渠道与时间。"""
    row = conn.execute("SELECT * FROM job_entities WHERE match_key=?", (match_key,)).fetchone()
    if row:
        ids, sources = json.loads(row["job_ids"]), json.loads(row["sources"])
        if job_id not in ids:
            ids.append(job_id)
        if source not in sources:
            sources.append(source)
        conn.execute("UPDATE job_entities SET job_ids=?, sources=?, updated_at=? WHERE id=?",
                     (json.dumps(ids), json.dumps(sources), now(), row["id"]))
        conn.commit()
        return dict(row) | {"job_ids": ids, "sources": sources}
    cur = conn.execute(
        "INSERT INTO job_entities(match_key,job_ids,sources,first_seen_at,first_seen_source,"
        "updated_at) VALUES (?,?,?,?,?,?)",
        (match_key, json.dumps([job_id]), json.dumps([source]), seen_at, source, now()))
    conn.commit()
    return {"id": cur.lastrowid, "match_key": match_key, "job_ids": [job_id],
            "sources": [source], "first_seen_at": seen_at, "first_seen_source": source}


def update_entity_conflict(conn, entity_id: int, lead_days: float, conflict: str) -> None:
    conn.execute("UPDATE job_entities SET lead_days=?, salary_conflict=? WHERE id=?",
                 (lead_days, conflict, entity_id))
    conn.commit()


def save_signal(conn, news_id: int, signal_type: str, entities: list, importance: float) -> None:
    conn.execute(
        "INSERT INTO news_signals(news_id,signal_type,entities,importance,created_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(news_id) DO UPDATE SET "
        "signal_type=excluded.signal_type, entities=excluded.entities, "
        "importance=excluded.importance",
        (news_id, signal_type, json.dumps(entities, ensure_ascii=False), importance, now()))
    conn.commit()


def save_snapshot(conn, period: str, kind: str, stats: dict) -> None:
    conn.execute(
        "INSERT INTO snapshots(period,kind,stats_json,created_at) VALUES (?,?,?,?) "
        "ON CONFLICT(period, kind) DO UPDATE SET stats_json=excluded.stats_json",
        (period, kind, json.dumps(stats, ensure_ascii=False), now()))
    conn.commit()


def get_snapshot(conn, period: str, kind: str) -> dict | None:
    row = conn.execute("SELECT stats_json FROM snapshots WHERE period=? AND kind=?",
                       (period, kind)).fetchone()
    return json.loads(row["stats_json"]) if row else None


def list_snapshots(conn, kind: str, limit: int = 12) -> list[dict]:
    rows = conn.execute("SELECT period, stats_json, created_at FROM snapshots WHERE kind=? "
                        "ORDER BY period DESC LIMIT ?", (kind, limit)).fetchall()
    return [{"period": r["period"], "stats": json.loads(r["stats_json"]),
             "created_at": r["created_at"]} for r in rows]


# ==================== 向量层 ====================

def upsert_vector(conn, item_type: str, item_id: int, model: str, dim: int,
                  vec: bytes, text_hash: str) -> None:
    conn.execute(
        "INSERT INTO vectors(item_type,item_id,model,dim,vec,text_hash,created_at) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT(item_type, item_id) DO UPDATE SET "
        "model=excluded.model, dim=excluded.dim, vec=excluded.vec, text_hash=excluded.text_hash",
        (item_type, item_id, model, dim, vec, text_hash, now()))
    conn.commit()


def load_vectors(conn, model: str) -> list[dict]:
    rows = conn.execute(
        "SELECT item_type, item_id, dim, vec, text_hash FROM vectors WHERE model=?",
        (model,)).fetchall()
    return [dict(r) for r in rows]


def vector_needs_update(conn, item_type: str, item_id: int, model: str, text_hash: str) -> bool:
    row = conn.execute(
        "SELECT text_hash FROM vectors WHERE item_type=? AND item_id=? AND model=?",
        (item_type, item_id, model)).fetchone()
    return not row or row["text_hash"] != text_hash


# ==================== 流水线可观测 ====================

def log_pipeline_run(conn, trigger: str, stages: list[dict], error: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO pipeline_runs(trigger,stages_json,started_at,finished_at,error) "
        "VALUES (?,?,?,?,?)",
        (trigger, json.dumps(stages, ensure_ascii=False), stages[0].get("at", "") if stages else now(),
         now(), error))
    conn.commit()
    return cur.lastrowid


def last_pipeline_run(conn) -> dict | None:
    row = conn.execute("SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        d = dict(row)
        d["stages"] = json.loads(d.pop("stages_json") or "[]")
        return d
    return None


# ==================== 异步任务 ====================

def create_task(conn, kind: str, title: str, params: dict | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO tasks(kind,title,params_json,status,created_at) VALUES (?,?,?,?,?)",
        (kind, title, json.dumps(params or {}, ensure_ascii=False), "queued", now()))
    conn.commit()
    return cur.lastrowid


def update_task(conn, task_id: int, **fields) -> None:
    allowed = {"status", "progress", "stage", "message", "result_json", "error",
               "started_at", "finished_at"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if sets:
        vals.append(task_id)
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", vals)
        conn.commit()


def get_task(conn, task_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _task_row(row)


def _task_row(row) -> dict | None:
    if not row:
        return None
    d = dict(row)
    d["params"] = json.loads(d.pop("params_json") or "{}")
    d["result"] = json.loads(d.pop("result_json") or "{}")
    return d


def list_tasks(conn, active_only: bool = False, limit: int = 50) -> list[dict]:
    if active_only:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('queued','running') ORDER BY id DESC "
            "LIMIT ?", (limit,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [r for r in (_task_row(x) for x in rows) if r]
