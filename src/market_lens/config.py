"""配置加载：config.yaml + .env + 环境变量展开。"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

FROZEN = bool(getattr(sys, "frozen", False))  # PyInstaller 打包后的桌面版


def _resource_dir() -> Path:
    """只读资源目录（源码仓库根 / 打包后的内置资源）。"""
    if FROZEN:
        meipass = getattr(sys, "_MEIPASS", None)
        return Path(meipass) if meipass else Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


def _writable_root() -> Path:
    """可写根目录：开发态=仓库根；打包态=用户数据目录（.app/.exe 内部不可写）。"""
    if not FROZEN:
        return _resource_dir()
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    else:
        base = home / ".local" / "share"
    return base / "market-lens"


RESOURCE_DIR = _resource_dir()
ROOT = _writable_root()
DATA_DIR = Path(os.environ.get("MARKET_LENS_DATA", ROOT / "data"))
CONFIG_PATH = Path(os.environ.get("MARKET_LENS_CONFIG", ROOT / "config.yaml"))


def _bootstrap_frozen() -> None:
    """打包版首启：把内置 config.yaml 复制到用户目录（此后用户可自由修改）。"""
    if not FROZEN:
        return
    try:
        bundled = RESOURCE_DIR / "config.yaml"
        if bundled.exists() and not CONFIG_PATH.exists():
            ROOT.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass


_bootstrap_frozen()

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)(?::([^}]*))?\}")


def _expand(value: Any) -> Any:
    """递归展开字符串中的 ${VAR:默认值}。"""
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            var, default = m.group(1), m.group(2)
            return os.environ.get(var, default if default is not None else m.group(0))
        return _ENV_PATTERN.sub(sub, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_dotenv(path: Path | None = None) -> None:
    """极简 .env 加载（不覆盖已有环境变量）。"""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


@dataclass
class Candidate:
    """一个可用的「provider + 模型」组合。"""
    provider: str
    model: str
    base_url: str
    api_key: str
    json_mode: str = "auto"  # auto | off


class Settings:
    def __init__(self) -> None:
        load_dotenv()
        self.reload()

    def reload(self) -> None:
        """双层配置：config.yaml（手工基线，注释保留）+ data/config_live.yaml（设置页运行层）。
        live 优先合并——设置页保存后热生效，不破坏手工文件。"""
        raw: dict[str, Any] = {}
        if CONFIG_PATH.exists():
            raw = _expand(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {})
        live_path = DATA_DIR / "config_live.yaml"
        if live_path.exists():
            live = _expand(yaml.safe_load(live_path.read_text(encoding="utf-8")) or {})
            raw = _deep_merge(raw, live)
        self.raw = raw
        llm = raw.get("llm") or {}
        self.providers: dict[str, dict] = llm.get("providers") or {}
        self.routing: dict[str, list[str]] = llm.get("routing") or {}
        self.budget_monthly_cny: float = float(llm.get("budget_monthly_cny", 0) or 0)
        self.dsh_enabled: bool = bool(llm.get("dsh_enabled", True))
        self.search = raw.get("search") or {}
        self.rss_sources: list[dict] = (raw.get("rss") or {}).get("sources") or []
        self.prefs_default: dict = raw.get("prefs_default") or {}
        self.quality: dict = raw.get("quality") or {}
        self.embedding: dict = raw.get("embedding") or {}
        self.rerank: dict = raw.get("rerank") or {}
        self.channels_cfg: dict = raw.get("channels") or {}
        self.scheduler_cfg: dict = raw.get("scheduler") or {}
        self.retrieval_cfg: dict = raw.get("retrieval") or {}

    # ---------- embedding / rerank ----------

    def embedding_ready(self) -> bool:
        return bool((self.embedding.get("base_url") or "").strip())

    def rerank_ready(self) -> bool:
        return bool((self.rerank.get("base_url") or "").strip())

    def embedding_candidate(self) -> Candidate | None:
        if not self.embedding_ready():
            return None
        return Candidate(provider="embedding", model=self.embedding.get("model", "bge-m3"),
                         base_url=str(self.embedding["base_url"]),
                         api_key=self.api_key(self.embedding.get("api_key_env")))

    def rerank_candidate(self) -> Candidate | None:
        if not self.rerank_ready():
            return None
        return Candidate(provider="rerank", model=self.rerank.get("model", "bge-reranker-v2-m3"),
                         base_url=str(self.rerank["base_url"]),
                         api_key=self.api_key(self.rerank.get("api_key_env")))

    # ---------- provider / 路由 ----------

    def api_key(self, env_name: str | None) -> str:
        if not env_name:
            return "EMPTY"
        return os.environ.get(env_name, "").strip() or "EMPTY"

    def provider_defined(self, pid: str) -> bool:
        p = self.providers.get(pid)
        return bool(p and p.get("base_url"))

    def candidates(self, task: str) -> list[Candidate]:
        """任务路由 → 有序候选列表（含降级链）。"""
        out: list[Candidate] = []
        for spec in self.routing.get(task) or []:
            pid, _, model = spec.partition("/")
            p = self.providers.get(pid)
            if not p or not p.get("base_url"):
                continue
            model = model or (p.get("models") or [""])[0]
            out.append(Candidate(
                provider=pid, model=model, base_url=str(p["base_url"]),
                api_key=self.api_key(p.get("api_key_env")),
                json_mode=str(p.get("json_mode", "auto")),
            ))
        return out

    def llm_ready(self, task: str = "analysis") -> bool:
        """该任务是否有已配置 key 的官方候选，或本地端点候选。
        （本地 vLLM 无需真实 key，无法静态判断可达性——以 candidates 非空为准）"""
        return bool(self.candidates(task))

    def all_providers(self) -> list[dict]:
        rows = []
        for pid, p in self.providers.items():
            models = p.get("models") or []
            key_env = p.get("api_key_env")
            has_key = bool(key_env and os.environ.get(key_env, "").strip())
            is_local = "127.0.0.1" in str(p.get("base_url", "")) or "localhost" in str(p.get("base_url", ""))
            rows.append({
                "id": pid, "base_url": p.get("base_url"), "models": models,
                "key_env": key_env, "has_key": has_key, "local": is_local,
                "ready": has_key or is_local,
            })
        return rows


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _deep_merge(base: dict, overlay: dict) -> dict:
    """递归合并：overlay 覆盖 base（dict 深合并，其余直接替换）。"""
    out = dict(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------- 运行时配置层（设置页写入口，热生效） ----------------

def save_live_config(patch: dict) -> None:
    """原子写 data/config_live.yaml 并热重载。密钥不落 yaml（走 .env）。"""
    import tempfile
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    live_path = DATA_DIR / "config_live.yaml"
    current: dict[str, Any] = {}
    if live_path.exists():
        current = yaml.safe_load(live_path.read_text(encoding="utf-8")) or {}
    merged = _deep_merge(current, patch or {})
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), suffix=".yaml.tmp")
    import os as _os
    with _os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# 运行时配置层：由「设置」页管理，覆盖 config.yaml（密钥在 .env）\n")
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    _os.replace(tmp, live_path)
    _os.chmod(live_path, 0o600)
    get_settings().reload()


def reset_live_config() -> None:
    """重置为 config.yaml 基线。"""
    live_path = DATA_DIR / "config_live.yaml"
    if live_path.exists():
        live_path.unlink()
    get_settings().reload()


def save_secret(env_name: str, value: str) -> None:
    """密钥写入 .env（原子替换/追加）并同步 os.environ。"""
    import os as _os
    import tempfile
    env_path = ROOT / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines
            if not ln.strip().startswith(f"{env_name}=")]
    if value.strip():
        kept.append(f"{env_name}={value.strip()}")
    fd, tmp = tempfile.mkstemp(dir=str(ROOT), suffix=".env.tmp")
    with _os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")
    _os.replace(tmp, env_path)
    _os.chmod(env_path, 0o600)
    if value.strip():
        _os.environ[env_name] = value.strip()
    elif env_name in _os.environ:
        del _os.environ[env_name]
