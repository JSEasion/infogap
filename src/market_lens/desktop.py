"""桌面版启动器：后台起 uvicorn，原生窗口（pywebview）打开 Web 界面。

PyInstaller 打包后的流程：
1. config 已在导入时定位到用户数据目录（mac: ~/Library/Application Support/infogap）
2. 初始化数据库；首启自动载入示例数据（无 API key 也能完整体验）
3. 随机空闲端口起本地服务，仅 127.0.0.1 可访问
4. 打开原生窗口；关窗即退出
"""
from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser

APP_TITLE = "信息差"


def _pick_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _bootstrap() -> None:
    """建库 + 首启载入示例数据。"""
    from . import db, sample_data
    from .extract import structure

    conn = db.connect()
    try:
        db.init_db(conn)
        if db.count_jobs(conn) == 0:
            structure(conn, sample_data.SAMPLE_JOBS, "sample", None, use_llm=False)
            for n in sample_data.SAMPLE_NEWS:
                db.upsert_news(conn, {**n, "fetched_at": db.now()})
    finally:
        conn.close()


def _wait_up(port: int, timeout: float = 30.0) -> bool:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/", timeout=1.0)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    return False


def main() -> None:
    port = int(os.environ.get("MARKET_LENS_PORT", "0")) or _pick_port()
    _bootstrap()

    import uvicorn

    from .web.app import app

    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning",
    ))
    threading.Thread(target=server.run, daemon=True, name="uvicorn").start()
    _wait_up(port)

    url = f"http://127.0.0.1:{port}/"
    try:
        import webview

        webview.create_window(APP_TITLE, url, width=1360, height=860, min_size=(1024, 640))
        webview.start()
    except Exception:  # noqa: BLE001 —— 无 GUI 环境（如无 WebView2/远程会话）退回浏览器
        webbrowser.open(url)
        server.wait()  # 前台驻留，Ctrl-C 退出
    os._exit(0)  # 关窗即退出；uvicorn 为守护线程随进程结束
