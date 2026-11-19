"""测试公共夹具：临时数据库 + 规则模式（隔离真实 LLM 网络）。"""
from __future__ import annotations

import pytest

import market_lens.config as cfg
from market_lens import db


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """每个测试一个独立 SQLite。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.DATA_DIR = tmp_path
    conn = db.connect()
    db.init_db(conn)
    yield conn
    conn.close()


@pytest.fixture()
def no_llm(monkeypatch):
    """清空模型通道 → 全链路规则模式（不触网）。"""
    s = cfg.Settings()
    s.providers = {}
    s.routing = {}
    monkeypatch.setattr(cfg, "_settings", s)
    return s


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """默认隔离：测试不读项目 .env、不读项目 config.yaml（个别测试显式覆盖）。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_BASE_URL", raising=False)
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
    monkeypatch.setattr(cfg, "_settings", None)
