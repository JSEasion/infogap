"""常驻调度：serve 启动后台线程，每日按 config.scheduler.hour 跑全链路。

零依赖（threading + 精确到分钟的轮询），状态暴露给设置页与底栏。
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timedelta

STATE = {"enabled": False, "next_run": "", "last_run": "", "last_status": "", "running": False}
_LOCK = threading.Lock()


def _next_run_time(hour: int) -> datetime:
    now = datetime.now()
    run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    return run


def _loop() -> None:
    from .config import get_settings
    from . import db as _db
    while True:
        try:
            s = get_settings()
            cfg = s.scheduler_cfg or {}
            if not cfg.get("enabled", True):
                STATE.update(enabled=False, next_run="")
                time.sleep(60)
                continue
            hour = int(cfg.get("hour", 8))
            nxt = _next_run_time(hour)
            with _LOCK:
                STATE.update(enabled=True, next_run=nxt.isoformat(timespec="minutes"))
            if datetime.now() >= nxt - timedelta(seconds=30):
                if not STATE["running"]:
                    STATE.update(running=True,
                                 last_run=datetime.now().isoformat(timespec="seconds"),
                                 last_status="运行中")
                    try:
                        from . import pipeline
                        from .gateway import LLMGateway
                        conn = _db.connect()
                        _db.init_db(conn)
                        gw = LLMGateway(get_settings(), conn)
                        summary = pipeline.run(conn, gw, trigger="scheduler")
                        STATE["last_status"] = (
                            f"完成：精选 {summary['curated']}，隔离 {summary['quarantined']}")
                        conn.close()
                    except Exception as e:  # noqa: BLE001
                        STATE["last_status"] = f"失败：{e}"
                        traceback.print_exc()
                    finally:
                        STATE["running"] = False
                time.sleep(120)
            else:
                time.sleep(60)
        except Exception:  # noqa: BLE001 调度循环永不死
            time.sleep(120)


def start() -> None:
    """启动调度线程（serve 启动时调用，幂等）。"""
    with _LOCK:
        if STATE.get("_thread"):
            return
        t = threading.Thread(target=_loop, daemon=True, name="xinxicha-scheduler")
        t.start()
        STATE["_thread"] = t


def status() -> dict:
    return {k: v for k, v in STATE.items() if not k.startswith("_")}
