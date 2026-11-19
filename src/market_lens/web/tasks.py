"""统一异步任务框架：一切 >1s 操作的唯一入口（采集/URL导入/报告/测试/向量化）。

交互铁律：按钮点击 → POST /api/tasks 立即返回 task_id → 任务中心进度条。
- tasks 表持久化（刷新/重启可见；重启后 running → orphaned）
- 进度回调协议：progress(pct|None, stage, message)；pct=None 表示阶段步进
- 协作式取消：进度回调检测取消标记
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import traceback
from typing import Callable

from .. import db
from ..config import get_settings

_PROGRESS = Callable[[float | None, str, str], None]


class TaskCancelled(Exception):
    pass


# ---------------- 任务处理器注册表 ----------------

HANDLERS: dict[str, Callable] = {}


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def _gw(conn: sqlite3.Connection):
    from ..gateway import LLMGateway
    return LLMGateway(get_settings(), conn)


@handler("pipeline")
def _h_pipeline(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    """全链路 / 渠道采集。params: {channels?: [...], collect?: bool}"""
    from .. import pipeline
    channels = params.get("channels") or None
    s = pipeline.run(conn, gateway, progress=progress, channels=channels,
                     collect_enabled=params.get("collect", True),
                     trigger=params.get("trigger", "manual"))
    return s


@handler("url_import")
def _h_url_import(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    """URL 智能导入：抓取 → raw → 过门发布（不豁免质量门）。"""
    from .. import pipeline
    url = params.get("url", "")
    progress(10, "抓取", f"抓取并识别页面：{url[:60]}…")
    items = pipeline.ingest_url_items(url)
    if not items:
        raise ValueError("页面未解析出内容（可能需登录，请用浏览器半自动采集）")
    progress(30, "抓取", f"识别出 {len(items)} 条内容，进入质量门…")
    n_new = sum(db.upsert_raw(conn, it)[1] for it in items)
    s = pipeline.run(conn, gateway, progress=progress, collect_enabled=False,
                     trigger="url_import")
    return {"url": url, "recognized": len(items), "raw_new": n_new, **{
        k: s.get(k) for k in ("curated", "quarantined", "rejected")}}
    # noqa: E501


@handler("import_text")
def _h_import_text(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    """粘贴/文件/HTML 导入 → raw → 过门。params: {texts: [...], source: str}"""
    from .. import pipeline
    texts = params.get("texts") or []
    source = params.get("source", "manual")
    items = []
    for t in texts:
        t = (t or "").strip()
        if not t:
            continue
        items.append({"kind": "job", "source": source, "source_url": "",
                      "title": t.split("\n", 1)[0][:80], "body": t[:8000],
                      "published_at": "", "channel_meta": {}})
    n_new = sum(db.upsert_raw(conn, it)[1] for it in items)
    progress(40, "质量门", f"导入 {len(items)} 条（新 {n_new}），过门…")
    s = pipeline.run(conn, gateway, progress=progress, collect_enabled=False,
                     trigger="import")
    return {"total": len(items), "raw_new": n_new,
            **{k: s.get(k) for k in ("curated", "quarantined", "rejected", "jobs")}}


@handler("import_html")
def _h_import_html(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    """浏览器另存 HTML 导入：服务端选择器解析（Boss/猎聘卡片 + 内嵌 JSON 兜底）。"""
    from ..sources.browser import parse_html
    from .. import pipeline
    texts: list[str] = []
    for html in params.get("htmls") or []:
        texts += parse_html(html)
    if not texts:
        raise ValueError("HTML 里没解析出岗位卡片（站点改版？选择器需更新）")
    items = [{"kind": "job", "source": "browser", "source_url": "",
              "title": t.split("\n", 1)[0][:80], "body": t[:8000],
              "published_at": "", "channel_meta": {}} for t in texts]
    n_new = sum(db.upsert_raw(conn, it)[1] for it in items)
    progress(40, "质量门", f"解析出 {len(texts)} 条（新 {n_new}），过门…")
    s = pipeline.run(conn, gateway, progress=progress, collect_enabled=False, trigger="import")
    return {"total": len(texts), "raw_new": n_new,
            **{k: s.get(k) for k in ("curated", "quarantined", "rejected", "jobs")}}


@handler("report")
def _h_report(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    """报告生成（任务化；SSE 流式阅读仍走独立端点）。"""
    from .. import reports
    from ..models import Profile
    kind = params.get("kind", "market")
    raw = db.load_profile(conn)
    try:
        profile = Profile.model_validate(json.loads(raw)) if raw else None
    except Exception:  # noqa: BLE001
        profile = None
    progress(20, "报告", "汇总统计…")
    fn = {"market": reports.gen_market_report, "gap": reports.gen_gap_report,
          "digest": reports.gen_digest}.get(kind)
    if not fn:
        raise ValueError(f"未知报告类型 {kind}")
    progress(50, "报告", "LLM 叙事生成中…")

    def cb(_k, _t):
        progress(75, "报告", "生成中…")
    if kind == "digest":
        rid = fn(conn, gateway, progress=cb)
    else:
        rid = fn(conn, gateway, profile, cb)
    return {"report_id": rid, "kind": kind}


@handler("test_provider")
def _h_test_provider(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    ok, message, latency = gateway.test_provider(params.get("provider", ""))
    return {"ok": ok, "message": message, "latency": latency}


@handler("test_embedding")
def _h_test_embedding(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    from ..gateway import EmbeddingClient
    ok, message, latency = EmbeddingClient(get_settings(), conn).test()
    return {"ok": ok, "message": message, "latency": latency}


@handler("test_rerank")
def _h_test_rerank(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    from ..gateway import RerankClient
    ok, message, latency = RerankClient(get_settings(), conn).test()
    return {"ok": ok, "message": message, "latency": latency}


@handler("reindex_vectors")
def _h_reindex(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    from ..retrieval import ensure_vectors
    return ensure_vectors(conn, progress=progress)


@handler("rescore")
def _h_rescore(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    """重放质量门（规则/阈值调整后）：未人工复核的隔离条目一并重过，清积压。"""
    from .. import pipeline
    return pipeline.run(conn, gateway, progress=progress, collect_enabled=False,
                        rescore_only=True, trigger="rescore")


@handler("quarantine_batch")
def _h_quarantine_batch(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    """隔离区批量复核。选源：显式 ids，或分数区间（score_min/score_max/kind），
    或 score_mode=above_mid/below_lo 按各 kind 的门中值/低线圈选。"""
    from .. import pipeline
    from ..quality import DEFAULT_GATES
    action = "promote" if params.get("action") == "promote" else "discard"
    raw_ids = []
    for i in params.get("ids") or []:
        try:
            raw_ids.append(int(i))
        except (TypeError, ValueError):
            continue
    if not raw_ids:
        where, vals = ["status='quarantined'"], []
        cfg_gates = get_settings().quality.get("gates") or {}
        gates = {**DEFAULT_GATES, **{k: tuple(v) for k, v in cfg_gates.items()}}
        mode = params.get("score_mode")
        if mode == "above_mid":
            cases = " ".join(f"WHEN '{k}' THEN {round((float(v[0]) + float(v[1])) / 2, 1)}"
                             for k, v in gates.items())
            where.append(f"gold_score >= CASE kind {cases} ELSE 60.0 END")
        elif mode == "below_lo":
            cases = " ".join(f"WHEN '{k}' THEN {float(v[0])}" for k, v in gates.items())
            where.append(f"gold_score < CASE kind {cases} ELSE 55.0 END")
        if params.get("score_min") is not None:
            where.append("gold_score >= ?"); vals.append(float(params["score_min"]))
        if params.get("score_max") is not None:
            where.append("gold_score <= ?"); vals.append(float(params["score_max"]))
        if params.get("kind"):
            where.append("kind=?"); vals.append(str(params["kind"]))
        raw_ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM raw_items WHERE {' AND '.join(where)} ORDER BY gold_score DESC",
            vals).fetchall()]
    done = {"promoted": 0, "discarded": 0, "failed": 0}
    for n, rid in enumerate(raw_ids, 1):
        progress(int(n * 90 / max(1, len(raw_ids))), "批量复核", f"{action} {n}/{len(raw_ids)}")
        try:
            if action == "promote":
                msg = pipeline.promote_raw(conn, rid, gateway)
            else:
                msg = pipeline.discard_raw(conn, rid)
            if msg == "条目不存在":
                done["failed"] += 1
            else:
                done["promoted" if action == "promote" else "discarded"] += 1
        except Exception:  # noqa: BLE001 单条失败不阻断整批
            done["failed"] += 1
    return {**done, "total": len(raw_ids)}


@handler("regovern")
def _h_regovern(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    """存量回填治理：把规则卡/英文直出的资讯与技术条目重新提取为中文信号卡。"""
    from ..news_pipeline import regovern_news
    kinds = tuple(params.get("kinds") or ("news", "tech"))
    return regovern_news(conn, gateway, kinds=kinds, progress=progress)


@handler("sample")
def _h_sample(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    from ..extract import structure
    from ..sample_data import SAMPLE_JOBS
    s = structure(conn, SAMPLE_JOBS, "sample", None, use_llm=False)
    return {"added": s.added, "dup": s.duplicates}


@handler("clear_sample")
def _h_clear_sample(conn, gateway, params: dict, progress: _PROGRESS) -> dict:
    j = conn.execute("DELETE FROM jobs WHERE source='sample'").rowcount
    n = conn.execute("DELETE FROM news WHERE source LIKE '示例%'").rowcount
    conn.commit()
    return {"jobs": j, "news": n}


# ---------------- TaskManager ----------------

class TaskManager:
    def __init__(self, db_path=None) -> None:
        self._db_path_override = str(db_path) if db_path else None
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._last_flush = 0.0

    def _path(self) -> str:
        """动态解析库路径（跟随测试 monkeypatch / 配置切换）。"""
        from .. import db as _db
        return self._db_path_override or str(_db.DB_PATH)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path(), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def submit(self, kind: str, title: str, params: dict | None = None) -> int:
        if kind not in HANDLERS:
            raise ValueError(f"未知任务类型 {kind}")
        if not self._threads:      # 惰性自启（测试/CLI 场景；serve 由 lifespan 显式启动）
            self.start(workers=1)
        conn = self._connect()
        db.init_db(conn)
        tid = db.create_task(conn, kind, title, params)
        conn.close()
        self._wake.set()
        return tid

    def cancel(self, task_id: int) -> bool:
        conn = self._connect()
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row or row["status"] not in ("queued", "running"):
            conn.close()
            return False
        db.update_task(conn, task_id, status="cancelled",
                       stage="已请求取消（协作式，当前阶段完成后停止）")
        conn.close()
        return True

    def _progress_of(self, task_id: int):
        def cb(pct, stage, message):
            now = time.time()
            if now - self._last_flush < 0.4 and pct is not None and pct < 100:
                return
            self._last_flush = now
            conn = self._connect()
            row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row and row["status"] == "cancelled":
                conn.close()
                raise TaskCancelled()
            db.update_task(conn, task_id, status="running", progress=pct,
                           stage=stage, message=message)
            conn.close()
        return cb

    def _worker(self) -> None:
        inited_paths: set[str] = set()
        while not self._stop.is_set():
            try:
                path = self._path()
                conn = self._connect()
                if path not in inited_paths:
                    db.init_db(conn)
                    inited_paths.add(path)
                row = conn.execute(
                    "SELECT * FROM tasks WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
                if not row:
                    conn.close()
                    self._wake.wait(timeout=2.0)
                    self._wake.clear()
                    continue
                tid, kind = row["id"], row["kind"]
                params = json.loads(row["params_json"] or "{}")
                # 二次确认未被取消
                cur = conn.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
                if cur["status"] == "cancelled":
                    conn.close()
                    continue
                db.update_task(conn, tid, status="running", started_at=db.now(),
                               progress=None, stage="启动中", message="")
                conn.close()
                wconn = self._connect()
                db.init_db(wconn)
                try:
                    fn = HANDLERS[kind]
                    gateway = _gw(wconn)
                    result = fn(wconn, gateway, params, self._progress_of(tid))
                    c2 = self._connect()
                    db.update_task(c2, tid, status="done", progress=100, stage="完成",
                                   result_json=json.dumps(result or {}, ensure_ascii=False,
                                                           default=str),
                                   finished_at=db.now())
                    c2.close()
                except TaskCancelled:
                    c2 = self._connect()
                    db.update_task(c2, tid, status="cancelled", stage="已取消",
                                   finished_at=db.now())
                    c2.close()
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    c2 = self._connect()
                    db.update_task(c2, tid, status="failed", stage="失败",
                                   error=f"{type(e).__name__}: {e}"[:500],
                                   finished_at=db.now())
                    c2.close()
                finally:
                    wconn.close()
            except Exception:  # noqa: BLE001 工作线程永不死
                traceback.print_exc()
                time.sleep(2.0)

    def start(self, workers: int = 2) -> None:
        for _ in range(workers):
            t = threading.Thread(target=self._worker, daemon=True,
                                 name=f"infogap-task-{len(self._threads)}")
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def recover_orphans(self) -> int:
        """服务重启后：把遗留 running 任务标记 orphaned。"""
        conn = self._connect()
        db.init_db(conn)
        n = conn.execute(
            "UPDATE tasks SET status='orphaned', stage='服务重启中断', finished_at=? "
            "WHERE status IN ('queued','running')", (db.now(),)).rowcount
        conn.commit()
        conn.close()
        return n


manager = TaskManager()
