"""模型接入层：LLMGateway。

- 多 Provider（DeepSeek 官方 / vLLM-OpenAI / vLLM-GLM / 内网端点），全部走 OpenAI 兼容协议
- 任务级路由 + 降级链；JSON mode 能力协商（不支持时自动降级为提示词约束）
- 剥离推理模型的 <think> 块；容错 JSON 提取
- token 用量记账 + 月度预算熔断
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field

from openai import APIStatusError, OpenAI

from .config import Candidate, Settings, get_settings

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    json_mode_used: bool = False


class ThinkSplitter:
    """流式 <think>…</think> 分离器：把 token 增量切成 (think|answer, text) 事件。
    标签可能被切在任意 chunk 边界，用保留尾巴策略处理。"""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.buf = ""
        self.in_think = False

    def _emit_safe(self):
        """未找到完整标签时：滞留「可能是任一标签前缀」的尾巴，其余立即输出。"""
        hold = 0
        max_len = max(len(self.OPEN), len(self.CLOSE))
        for i in range(1, min(max_len, len(self.buf)) + 1):
            suffix = self.buf[-i:]
            if self.OPEN.startswith(suffix) or self.CLOSE.startswith(suffix):
                hold = i
        out_len = len(self.buf) - hold
        if out_len > 0:
            out, self.buf = self.buf[:out_len], self.buf[out_len:]
            return ("think" if self.in_think else "answer"), out
        return None

    def feed(self, delta: str):
        self.buf += delta
        while self.buf:
            tag = self.CLOSE if self.in_think else self.OPEN
            other = self.OPEN if self.in_think else self.CLOSE
            idx = self.buf.find(tag)
            oidx = self.buf.find(other)
            if oidx != -1 and (idx == -1 or oidx < idx):
                # 反常方向的标签（如 think 外的 </think>）：丢弃，防止泄漏进正文
                if oidx:
                    yield ("think" if self.in_think else "answer"), self.buf[:oidx]
                self.buf = self.buf[oidx + len(other):]
                continue
            if idx == -1:
                hit = self._emit_safe()
                if hit:
                    yield hit
                return
            if self.in_think:
                if idx:
                    yield ("think", self.buf[:idx])
                self.buf = self.buf[idx + len(tag):]
                self.in_think = False
            else:
                if idx:
                    yield ("answer", self.buf[:idx])
                self.buf = self.buf[idx + len(tag):]
                self.in_think = True

    def flush(self):
        if self.buf:
            yield ("think" if self.in_think else "answer"), self.buf
            self.buf = ""


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    pass


# ==================== Embedding / Rerank 客户端（OpenAI 兼容） ====================

class EmbeddingClient:
    """bge-m3 等向量端点（/v1/embeddings）。降级：不可用时检索回落纯 FTS。"""

    def __init__(self, settings: Settings | None = None,
                 conn: sqlite3.Connection | None = None) -> None:
        self.settings = settings or get_settings()
        self.conn = conn
        self.cand = self.settings.embedding_candidate()
        self.batch = int(self.settings.embedding.get("batch_size", 16))
        self.timeout = float(self.settings.embedding.get("timeout", 30))

    @property
    def ready(self) -> bool:
        return self.cand is not None

    def _client(self) -> OpenAI:
        return OpenAI(base_url=self.cand.base_url, api_key=self.cand.api_key,
                      timeout=self.timeout, max_retries=1)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入（自动分批）。失败抛 LLMError。"""
        if not self.cand:
            raise LLMError("embedding 端点未配置（设置页或 .env 配 EMBEDDING_BASE_URL）")
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = [t[:6000] for t in texts[i:i + self.batch]]
            try:
                resp = self._client().embeddings.create(model=self.cand.model, input=chunk)
                data = sorted(resp.data, key=lambda d: d.index)
                out.extend(d.embedding for d in data)
            except Exception as e:  # noqa: BLE001
                raise LLMError(f"embedding 调用失败：{type(e).__name__}: {e}") from e
        self._log(sum(len(t) for t in texts) // 2, 0, True)
        return out

    def _log(self, pt: int, ct: int, ok: bool) -> None:
        if not self.conn:
            return
        from .db import log_llm
        log_llm(self.conn, "embedding", self.cand.provider if self.cand else "-",
                self.cand.model if self.cand else "-", pt, ct, 0.0, ok)

    def test(self) -> tuple[bool, str, float]:
        """健康探测：返回 (ok, message, latency)；message 含维度信息。"""
        if not self.cand:
            return False, "未配置 base_url", 0.0
        t0 = time.time()
        try:
            resp = self._client().embeddings.create(model=self.cand.model, input=["测试"])
            dim = len(resp.data[0].embedding)
            return True, f"嵌入正常（dim={dim}）", round(time.time() - t0, 2)
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}", round(time.time() - t0, 2)


class RerankClient:
    """bge-reranker-v2-m3 精排端点（/v1/rerank，兼容 /rerank 路径）。降级：回落 RRF 融合。"""

    def __init__(self, settings: Settings | None = None,
                 conn: sqlite3.Connection | None = None) -> None:
        self.settings = settings or get_settings()
        self.conn = conn
        self.cand = self.settings.rerank_candidate()
        self.top_k = int(self.settings.rerank.get("top_k", 24))
        self.timeout = float(self.settings.rerank.get("timeout", 30))

    @property
    def ready(self) -> bool:
        return self.cand is not None

    def rerank(self, query: str, docs: list[str],
               top_k: int | None = None) -> list[tuple[int, float]]:
        """返回 [(原索引, 相关分)]，按分降序。失败抛 LLMError（调用方降级）。"""
        if not self.cand:
            raise LLMError("rerank 端点未配置")
        top_k = top_k or self.top_k
        payload = {"model": self.cand.model, "query": query,
                   "documents": [d[:4000] for d in docs], "top_n": min(top_k, len(docs))}
        import httpx
        t0 = time.time()
        last_err: Exception | None = None
        for path in ("/rerank", "/v1/rerank"):
            try:
                with httpx.Client(timeout=self.timeout) as c:
                    r = c.post(f"{self.cand.base_url.rstrip('/')}{path}", json=payload,
                               headers={"Authorization": f"Bearer {self.cand.api_key}"})
                r.raise_for_status()
                data = r.json()
                results = data.get("results") or []
                out = [(int(x["index"]), float(x.get("relevance_score", x.get("score", 0))))
                       for x in results]
                self._log(len(query) // 2 + sum(len(d) for d in docs) // 4,
                          len(str(results)) // 4, True)
                return sorted(out, key=lambda x: -x[1])
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        self._log(0, 0, False)
        raise LLMError(f"rerank 调用失败：{type(last_err).__name__}: {last_err}")

    def _log(self, pt: int, ct: int, ok: bool) -> None:
        if not self.conn:
            return
        from .db import log_llm
        log_llm(self.conn, "rerank", self.cand.provider if self.cand else "-",
                self.cand.model if self.cand else "-", pt, ct, 0.0, ok)

    def test(self) -> tuple[bool, str, float]:
        if not self.cand:
            return False, "未配置 base_url", 0.0
        t0 = time.time()
        try:
            out = self.rerank("测试", ["文档A", "文档B"], top_k=2)
            return True, f"精排正常（{len(out)} 结果）", round(time.time() - t0, 2)
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}", round(time.time() - t0, 2)


def strip_think(text: str) -> str:
    """剥离推理模型输出中的 <think>…</think> 块（未闭合时丢弃全部）。"""
    if "<think>" not in text:
        return text
    out = _THINK_RE.sub("", text)
    if "<think>" in out:  # 未闭合的思考块
        out = out.split("<think>")[0]
    return out.strip()


def extract_json(text: str):
    """从模型输出中鲁棒地提取 JSON 对象或数组；失败返回 None。"""
    t = strip_think(text).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip(), flags=re.S)
    starts = [(t.find(o), o, c) for o, c in (("[", "]"), ("{", "}")) if t.find(o) != -1]
    for start, opener, closer in sorted(starts, key=lambda x: x[0]):
        end = t.rfind(closer)
        if end > start:
            frag = t[start:end + 1]
            try:
                return json.loads(frag)
            except json.JSONDecodeError:
                for fix in (lambda s: re.sub(r",\s*([}\]])", r"\1", s),      # 尾逗号
                            lambda s: re.sub(r"(?<!\\)'", '"', s),            # 单引号
                            lambda s: re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", s)):
                    try:
                        return json.loads(fix(frag))
                    except json.JSONDecodeError:
                        continue
    return None


# 官方 API 千 token 价格（人民币），用于记账；本地端点视为免费
_PRICE_PER_1K = {("deepseek-official", "deepseek-chat"): 0.00228}


class LLMGateway:
    def __init__(self, settings: Settings | None = None, conn: sqlite3.Connection | None = None) -> None:
        self.settings = settings or get_settings()
        self.conn = conn
        self._json_mode_cache: dict[str, bool] = {}

    # ---------- 记账 ----------

    def _cost(self, cand: Candidate, pt: int, ct: int) -> float:
        price = _PRICE_PER_1K.get((cand.provider, cand.model), 0.0)
        return round((pt + ct) / 1000 * price, 6)

    def _log(self, task: str, cand: Candidate, pt: int, ct: int, ok: bool) -> None:
        if not self.conn:
            return
        from .db import log_llm
        log_llm(self.conn, task, cand.provider, cand.model, pt, ct, self._cost(cand, pt, ct), ok)

    def _check_budget(self) -> None:
        if not self.conn or not self.settings.budget_monthly_cny:
            return
        from datetime import datetime
        from .db import usage_summary
        s = usage_summary(self.conn)
        if s["month_cost_cny"] >= self.settings.budget_monthly_cny:
            raise BudgetExceeded(
                f"本月 LLM 花费 {s['month_cost_cny']} 元已达预算上限 {self.settings.budget_monthly_cny} 元，"
                f"可在 config.yaml 调整 budget_monthly_cny")

    # ---------- 核心 ----------

    def _json_mode_ok(self, cand: Candidate, client: OpenAI) -> bool:
        if cand.json_mode == "off":
            return False
        if cand.provider in self._json_mode_cache:
            return self._json_mode_cache[cand.provider]
        ok = True
        try:
            client.chat.completions.create(
                model=cand.model,
                messages=[{"role": "user", "content": "输出JSON: {\"ok\":true}"}],
                max_tokens=2048, temperature=0,
                response_format={"type": "json_object"},
            )
        except (APIStatusError, Exception):
            ok = False
        self._json_mode_cache[cand.provider] = ok
        return ok

    def chat(self, task: str, system: str, user: str, *,
             json_mode: bool = False, max_tokens: int = 4000,
             temperature: float = 0.2, timeout: float = 120.0) -> LLMResult:
        """按任务路由调用模型，逐候选降级。全部失败抛 LLMError。"""
        self._check_budget()
        cands = self.settings.candidates(task)
        if not cands:
            raise LLMError(f"任务「{task}」没有可用的模型通道：请在 .env 配置任一 provider 的 key/端点")

        errors: list[str] = []
        for cand in cands:
            try:
                client = OpenAI(base_url=cand.base_url, api_key=cand.api_key, timeout=timeout,
                                max_retries=0)
                use_json = json_mode and self._json_mode_ok(cand, client)
                resp = client.chat.completions.create(
                    model=cand.model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    max_tokens=max_tokens, temperature=temperature,
                    **({"response_format": {"type": "json_object"}} if use_json else {}),
                )
                content = resp.choices[0].message.content or ""
                finish = resp.choices[0].finish_reason
                if finish == "length" and not strip_think(content).strip():
                    raise RuntimeError(
                        f"{cand.provider}/{cand.model}: max_tokens={max_tokens} 全部耗于推理阶段，无正文输出")
                usage = getattr(resp, "usage", None)
                pt = getattr(usage, "prompt_tokens", 0) or 0
                ct = getattr(usage, "completion_tokens", 0) or 0
                self._log(task, cand, pt, ct, True)
                return LLMResult(strip_think(content), cand.provider, cand.model,
                                 pt, ct, use_json)
            except BudgetExceeded:
                raise
            except Exception as e:  # noqa: BLE001 逐候选降级
                errors.append(f"{cand.provider}/{cand.model}: {type(e).__name__}: {e}")
                try:
                    self._log(task, cand, 0, 0, False)
                except Exception:
                    pass
        raise LLMError("；".join(errors))

    def chat_stream(self, task: str, system: str, user: str, *,
                    max_tokens: int = 8000, temperature: float = 0.2):
        """流式生成（DSH 风格事件流）：yield {"type": "step"|"think"|"answer"|"meta"|"error", ...}。
        事件：step=阶段提示，think=思考链增量，answer=正文增量，meta=完成信息，error=失败。"""
        events: list[dict] = []
        cands = self.settings.candidates(task)
        if not cands:
            yield {"type": "error", "message": f"任务「{task}」没有可用的模型通道"}
            return
        for cand in cands:
            splitter = ThinkSplitter()
            started = False
            try:
                client = OpenAI(base_url=cand.base_url, api_key=cand.api_key, timeout=180.0,
                                max_retries=1)
                try:
                    stream = client.chat.completions.create(
                        model=cand.model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": user}],
                        max_tokens=max_tokens, temperature=temperature, stream=True,
                        stream_options={"include_usage": True})
                except APIStatusError:
                    stream = client.chat.completions.create(
                        model=cand.model,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": user}],
                        max_tokens=max_tokens, temperature=temperature, stream=True)
                pt = ct = 0
                for chunk in stream:
                    usage = getattr(chunk, "usage", None)
                    if usage:
                        pt, ct = getattr(usage, "prompt_tokens", 0) or 0, \
                            getattr(usage, "completion_tokens", 0) or 0
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content
                    if not delta:
                        continue
                    started = True
                    for kind, text in splitter.feed(delta):
                        yield {"type": kind, "text": text}
                for kind, text in splitter.flush():
                    yield {"type": kind, "text": text}
                if not started:
                    raise RuntimeError("空响应")
                self._log(task, cand, pt, ct, True)
                yield {"type": "meta", "provider": cand.provider, "model": cand.model,
                       "prompt_tokens": pt, "completion_tokens": ct}
                return
            except BudgetExceeded:
                yield {"type": "error", "message": "月度预算已用尽"}
                return
            except Exception as e:  # noqa: BLE001 未产出任何 token → 降级下一候选
                if not started:
                    events.append({"type": "step",
                                   "text": f"通道 {cand.provider} 不可用（{type(e).__name__}），切换下一通道…"})
                    for ev in events:
                        yield ev
                    events = []
                    continue
                yield {"type": "error", "message": f"{cand.provider}: {e}"}
                return
        yield {"type": "error", "message": "所有通道均不可用"}

    def quick_check(self, task: str = "extraction", timeout: float = 8.0) -> bool:
        """快速探测任务通道可达性（8s 小请求）。流水线增强前的探活，避免逐批长超时。"""
        try:
            self.chat(task, "你是连通性探测器。", "回复两个字：正常", max_tokens=16,
                      temperature=0.0, timeout=timeout)
            return True
        except LLMError:
            return False

    def chat_json(self, task: str, system: str, user: str, *, max_tokens: int = 6000,
                  timeout: float = 120.0) -> tuple[object, LLMResult]:
        """chat + JSON 提取；解析失败抛 LLMError。"""
        res = self.chat(task, system, user, json_mode=True, max_tokens=max_tokens,
                        timeout=timeout)
        data = extract_json(res.text)
        if data is None:
            raise LLMError(f"模型输出无法解析为 JSON（provider={res.provider}，前200字：{res.text[:200]!r}）")
        return data, res

    # ---------- 连接测试 ----------

    def test_provider(self, pid: str) -> tuple[bool, str, float]:
        p = self.settings.providers.get(pid)
        if not p or not p.get("base_url"):
            return False, "provider 未配置（base_url 为空）", 0.0
        cand = Candidate(pid, (p.get("models") or [""])[0], str(p["base_url"]),
                         self.settings.api_key(p.get("api_key_env")))
        t0 = time.time()
        try:
            client = OpenAI(base_url=cand.base_url, api_key=cand.api_key, timeout=15.0)
            client.models.list()
            return True, f"models.list 正常（{cand.model}）", round(time.time() - t0, 2)
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}", round(time.time() - t0, 2)
