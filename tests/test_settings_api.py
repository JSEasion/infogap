"""settings 配置中心：双层配置保存 → 热重载 → 通道测试入口（不触网）。"""
import json

from fastapi.testclient import TestClient

from market_lens import db
from market_lens.config import get_settings, reset_live_config
from market_lens.web.app import app

client = TestClient(app, follow_redirects=False)


def test_settings_page_renders(tmp_db, no_llm):
    r = client.get("/settings")
    assert r.status_code == 200
    assert "LLM 通道" in r.text and "向量与精排" in r.text and "质量门" in r.text


def test_save_section_hot_reload(tmp_db, no_llm, monkeypatch):
    """保存 quality 段 → get_settings() 立即读到（热生效）。"""
    r = client.post("/api/settings/save", json={
        "section": "quality",
        "patch": {"gates": {"job": [60, 75]}, "judge": False}})
    assert r.status_code == 200 and r.json()["ok"]
    s = get_settings()
    assert s.quality["gates"]["job"] == [60, 75]
    assert s.quality["judge"] is False


def test_save_embedding_and_secret(tmp_db, no_llm):
    r = client.post("/api/settings/save", json={
        "section": "embedding",
        "patch": {"base_url": "http://mock:9999/v1", "model": "mock-m3"},
        "secret": {"env": "EMBEDDING_API_KEY", "value": "sk-test"}})
    assert r.status_code == 200
    import os
    assert os.environ.get("EMBEDDING_API_KEY") == "sk-test"
    assert get_settings().embedding_ready()


def test_save_rejects_bad_section(tmp_db, no_llm):
    r = client.post("/api/settings/save", json={"section": "evil", "patch": {}})
    assert r.status_code == 400


def test_llm_provider_add_validate(tmp_db, no_llm):
    ok = client.post("/api/settings/llm", json={
        "id": "my-vllm", "base_url": "http://127.0.0.1:8000/v1",
        "models": ["qwen3"]})
    assert ok.status_code == 200
    s = get_settings()
    assert "my-vllm" in s.providers
    assert s.providers["my-vllm"]["api_key_env"] == "KEY_MY_VLLM"
    bad = client.post("/api/settings/llm", json={"id": "X!", "base_url": "nope",
                                                 "models": []})
    assert bad.status_code == 400


def test_reset_live(tmp_db, no_llm):
    client.post("/api/settings/save", json={"section": "quality", "patch": {"judge": False}})
    r = client.post("/api/settings/reset")
    assert r.status_code == 200
    s = get_settings()
    assert s.quality.get("judge", True) is not False
