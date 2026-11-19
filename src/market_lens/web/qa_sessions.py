"""数据问答流式会话：生成与 HTTP 连接解耦。

- 提问即建会话，后台线程生成（检索 → 思考流 → 回答流），与浏览器连接无关：
  页面切走继续跑、回来可续看、断线可从 seq 断点续传。
- 提问先在 qa_logs 落一条占位记录，结束时更新完整回答 —— 进程意外退出也能在
  历史里留下问题与已生成的部分内容。
- 每帧事件带自增 seq；订阅者（SSE）从 from_seq 回放再跟随，15s 心跳防空闲断连。
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import markdown as _md

from .. import db, qa
from ..config import get_settings
from ..gateway import LLMGateway

_KEEP_FINISHED_SEC = 1800  # 已完成会话保留 30 分钟，供迟到的订阅者回放


def render_md(text: str) -> str:
    return _md.markdown(text or "", extensions=["tables", "nl2br", "fenced_code"])


def _cite(h: dict) -> dict:
    base = {"id": h["id"], "kind": h.get("_kind", "job"),
            "title": h.get("title", ""), "source": h.get("source", ""),
            "gold": h.get("gold_score")}
    if h.get("_kind") == "job":
        base.update(company=h.get("company", ""), city=h.get("city", ""),
                    salary_raw=h.get("salary_raw", ""), wlb_level=h.get("wlb_level"))
    else:
        base.update(one_liner=h.get("one_liner", ""), signal=h.get("signal_type"))
    return base


class QASession:
    """单次问答的事件缓冲区。worker 线程写，任意多个 SSE 订阅者并发读。"""

    def __init__(self, sid: str, question: str) -> None:
        self.sid = sid
        self.question = question
        self.created_at = db.now()
        self.t0 = time.time()
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.finished = False   # worker 终态（正常 done 或异常 error）
        self.log_id: int | None = None

    # ---------- worker 侧 ----------

    def emit(self, ev: dict) -> None:
        with self.cond:
            ev = dict(ev)
            ev["seq"] = len(self.events) + 1
            self.events.append(ev)
            self.cond.notify_all()

    def finish(self) -> None:
        with self.cond:
            self.finished = True
            self.cond.notify_all()

    # ---------- 订阅侧（SSE 生成器） ----------

    def subscribe(self, from_seq: int = 0):
        """从 from_seq 起回放已有事件，再跟随新事件直到会话结束；空闲时产心跳。"""
        i = max(0, int(from_seq or 0))
        while True:
            with self.cond:
                while i >= len(self.events) and not self.finished:
                    self.cond.wait(timeout=15)
                batch = self.events[i:]
                i = len(self.events)
                over = self.finished
            if not batch:
                if over:
                    return
                yield {"type": "ping"}   # 心跳：防代理/浏览器空闲断连
                continue
            yield from batch
            if over:
                return

    def snapshot(self) -> dict:
        with self.cond:
            return {"sid": self.sid, "question": self.question,
                    "created_at": self.created_at, "finished": self.finished,
                    "n_events": len(self.events)}


class QASessionManager:
    """进程内会话表；生成线程与请求生命周期完全解耦。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, QASession] = {}

    def create(self, question: str) -> QASession:
        sid = uuid.uuid4().hex[:12]
        s = QASession(sid, question.strip())
        with self._lock:
            self._prune()
            self._sessions[sid] = s
        threading.Thread(target=_run, args=(s,), daemon=True, name=f"qa-{sid}").start()
        return s

    def get(self, sid: str) -> QASession | None:
        with self._lock:
            return self._sessions.get(sid)

    def active(self) -> list[dict]:
        with self._lock:
            return [s.snapshot() for s in self._sessions.values() if not s.finished]

    def _prune(self) -> None:   # 调用方持锁
        horizon = time.time() - _KEEP_FINISHED_SEC
        for sid in [k for k, s in self._sessions.items()
                    if s.finished and s.t0 < horizon]:
            del self._sessions[sid]


qa_manager = QASessionManager()


def _save(conn, s: QASession, answer_md: str, cited_json: str) -> None:
    if not s.log_id:
        return
    try:
        conn.execute("UPDATE qa_logs SET answer_md=?, cited_ids=? WHERE id=?",
                     (answer_md, cited_json, s.log_id))
        conn.commit()
    except Exception:  # noqa: BLE001 落库失败不致命：事件流照常结束
        pass


def _run(s: QASession) -> None:
    """后台生成线程：任何异常都转成 error 事件 + 部分结果落库，绝不硬断连接。"""
    conn = db.connect()
    db.init_db(conn)
    parts: list[str] = []
    cited_json = "[]"
    errored = False
    last_save = time.time()
    try:
        # 先落库占位：即使中途崩溃，历史里也留下问题
        cur = conn.execute(
            "INSERT INTO qa_logs(question,answer_md,cited_ids,created_at) VALUES (?,?,?,?)",
            (s.question, "", "[]", db.now()))
        conn.commit()
        s.log_id = cur.lastrowid

        s.emit({"type": "step", "text": "混合检索（FTS5+同义词 ∥ 向量 → RRF → 精排）…"})
        from ..retrieval import search as hybrid_search
        hits = hybrid_search(conn, s.question, kinds=("job", "news"), limit=10)
        s.emit({"type": "step", "text": f"命中 {len(hits)} 条（岗位+资讯），组装上下文…"})
        cited_json = json.dumps([h["id"] for h in hits])
        cited = [_cite(h) for h in hits]

        if not hits:
            text = "库里暂时没有能匹配这个问题的数据。先去「数据接入中心」采集真实数据。"
            s.emit({"type": "answer", "text": text})
            parts.append(text)
        else:
            gw = LLMGateway(get_settings(), conn)
            if not gw.settings.llm_ready("qa"):
                rule = qa.rule_answer(s.question, [h for h in hits if h["_kind"] == "job"])
                s.emit({"type": "answer", "text": rule})
                parts.append(rule)
            else:
                saw_think = False
                for ev in gw.chat_stream("qa", qa._QA_SYSTEM,
                                         qa.build_prompt(s.question, hits), max_tokens=24000):
                    if ev["type"] in ("think", "answer"):
                        saw_think = saw_think or ev["type"] == "think"
                        if ev["type"] == "answer":
                            parts.append(ev["text"])
                        s.emit(ev)
                        # 周期性落库部分回答（约 2s 一次），进程崩溃也不丢已生成内容
                        if time.time() - last_save > 2:
                            _save(conn, s, "".join(parts), cited_json)
                            last_save = time.time()
                    elif ev["type"] == "error":
                        errored = True
                        s.emit(ev)
                        break
                if errored:
                    s.emit({"type": "step", "text": "模型通道异常，回落规则召回"})
                elif not parts and saw_think:
                    # 推理模型把输出上限全耗在思维链上、正文为空 → 明确告知而非静默回落
                    s.emit({"type": "step",
                            "text": "模型推理耗尽输出上限仍未产出正文（推理型模型思考过长），"
                                    "已回落规则召回；可在「系统设置」为问答换非推理模型"})
                if not parts:
                    rule = qa.rule_answer(s.question, [h for h in hits if h["_kind"] == "job"])
                    s.emit({"type": "answer", "text": rule})
                    parts.append(rule)
        s.emit({"type": "done", "answer_html": render_md("".join(parts)), "cited": cited})
    except Exception as e:  # noqa: BLE001 兜底：转成 error 事件，连接不硬断
        errored = True
        s.emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
    finally:
        final_md = "".join(parts)
        if errored:
            note = (f"\n\n> ⚠️ 生成中断（已保留 {len(final_md)} 字）："
                    f"可稍后在设置页检查模型通道后重试。")
            final_md = (final_md + note) if final_md.strip() else note.strip()
        _save(conn, s, final_md, cited_json)
        s.finish()
        conn.close()
