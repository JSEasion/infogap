"""DSH（DeepSeek Harness）引擎适配器：研究型任务走 agent 目标模式。

- SDK 未安装/初始化失败 → 抛 DshUnavailable，由调用方降级为直连 chat
- DSH 内置 provider 走 Anthropic 协议（/v1/messages + x-api-key）；
  对 OpenAI 兼容端点（vLLM 等）通过 llm-pi-ai 插件注册 openai-completions provider
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from .config import DATA_DIR, get_settings

PROVIDER_ID = "ml-engine"          # 注册进 DSH 的自定义 provider id
_PATCH_PATH_TMPL = "{home}/profiles/{profile}/cordis.patch.yml"


class DshUnavailable(Exception):
    pass


def dsh_sdk_available() -> bool:
    try:
        import deepseek_harness  # noqa: F401
        return True
    except ImportError:
        return False


def _register_openai_provider(home: Path, profile: str, cand) -> str:
    """把 OpenAI 兼容端点写进 profile 补丁层，返回 provider id。"""
    patch_path = Path(_PATCH_PATH_TMPL.format(home=home, profile=profile))
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    if patch_path.exists():
        try:
            entries = yaml.safe_load(patch_path.read_text(encoding="utf-8")) or []
        except yaml.YAMLError:
            entries = []
    entry = {
        "id": "llm-pi-ai",
        "config": {
            "providers": {
                PROVIDER_ID: {
                    "displayName": "market-lens engine",
                    "api": "openai-completions",
                    "baseURL": cand.base_url,
                    "apiKeyEnv": "ML_ENGINE_API_KEY",
                    "models": [{
                        "id": cand.model,
                        "name": cand.model,
                        "contextWindow": 131072,
                        "maxTokens": 32768,
                    }],
                }
            }
        },
    }
    entries = [e for e in entries if not (isinstance(e, dict) and e.get("id") == "llm-pi-ai")]
    entries.append(entry)
    patch_path.write_text(yaml.safe_dump(entries, allow_unicode=True, sort_keys=False),
                          encoding="utf-8")
    return PROVIDER_ID


def run_research(objective: str, timeout_s: int = 600) -> str:
    """用 DSH 执行研究任务（如公司口碑调研），返回最终文本。"""
    settings = get_settings()
    if not settings.dsh_enabled:
        raise DshUnavailable("config.yaml 中 dsh_enabled=false")
    try:
        from deepseek_harness import DeepSeekHarness
    except ImportError as e:
        raise DshUnavailable("deepseek-harness-sdk 未安装（uv pip install 'market-lens[dsh]'）") from e

    cands = settings.candidates("research")
    if not cands:
        raise DshUnavailable("research 任务没有可用模型通道")
    cand = cands[0]
    home = DATA_DIR / "dsh_home"
    ws = DATA_DIR / "research"
    home.mkdir(parents=True, exist_ok=True)
    ws.mkdir(parents=True, exist_ok=True)

    # deepseek-official 用 DSH 内置 provider；其余（vLLM/内网）注册 OpenAI 型 provider
    provider = "deepseek-official" if cand.provider == "deepseek-official" else \
        _register_openai_provider(home, "sdk", cand)

    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(home)),
        "ML_ENGINE_API_KEY": cand.api_key,
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
        "DEEPSEEK_BASE_URL": cand.base_url if provider == "deepseek-official" else "",
    }
    with DeepSeekHarness(
        dsh_home=str(home), cwd=str(ws), env=env,
        provider=provider, model=cand.model,
    ) as harness:
        session = harness.start_session()
        result = session.run(f"/goal {objective}")
        if result.finish_reason != "completed":
            raise DshUnavailable(
                f"DSH 目标未完成：{result.finish_reason}: {result.final_response[:200]}")
        from .gateway import strip_think
        return strip_think(result.final_response)
