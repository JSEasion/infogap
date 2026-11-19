"""技能归一词典：把 JD 里的各种写法归一到规范技能 id。纯规则匹配。"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# (id, 分组, [别名])；匹配时小写子串（短别名用词边界）
DEFAULT_SKILLS: list[tuple[str, str, list[str]]] = [
    # 编排框架
    ("langchain", "编排框架", ["langchain"]),
    ("langgraph", "编排框架", ["langgraph"]),
    ("llamaindex", "编排框架", ["llamaindex", "llama index"]),
    ("autogen", "编排框架", ["autogen"]),
    ("crewai", "编排框架", ["crewai", "crew ai"]),
    ("dify", "编排框架", ["dify"]),
    ("coze", "编排框架", ["coze", "扣子"]),
    ("n8n", "编排框架", ["n8n"]),
    # RAG 体系
    ("rag", "RAG", ["rag", "检索增强", "知识库问答"]),
    ("vector_db", "RAG", ["向量数据库", "faiss", "milvus", "qdrant", "weaviate", "pgvector", "vespa", "chroma", "elasticsearch"]),
    ("embedding", "RAG", ["embedding", "向量化", "稠密检索"]),
    ("rerank", "RAG", ["rerank", "重排"]),
    ("graph_rag", "RAG", ["graphrag", "graph rag", "知识图谱"]),
    ("es_search", "RAG", ["elasticsearch", "opensearch", "es 检索", "全文检索"]),
    # 协议与工具
    ("mcp", "协议", ["mcp", "model context protocol"]),
    ("a2a", "协议", ["a2a", "agent2agent", "agent to agent"]),
    ("function_calling", "协议", ["function calling", "函数调用", "tool use", "工具调用", "tool call"]),
    ("openapi_tool", "协议", ["openapi", "swagger"]),
    # 提示与评测
    ("prompt_engineering", "提示工程", ["prompt", "提示词", "提示工程"]),
    ("agent_eval", "评测", ["评测", "evaluation", "eval ", "benchmark", "基线测试"]),
    ("multimodal", "多模态", ["多模态", "multimodal", "视觉模型", "ocr"]),
    ("multi_agent", "Agent能力", ["多智能体", "multi-agent", "multi agent", "智能体协作"]),
    # 模型与训练
    ("finetune", "模型训练", ["微调", "sft", "lora", "qlora", "指令微调"]),
    ("rlhf", "模型训练", ["rlhf", "dpo", "强化学习对齐", "grpo"]),
    ("pytorch", "模型训练", ["pytorch", "torch"]),
    ("inference_serving", "推理部署", ["vllm", "推理加速", "推理优化", "量化", "tensorrt", "ollama", "sglang", "triton"]),
    # 语言与后端
    ("python", "语言", ["python"]),
    ("go", "语言", ["golang", "go语言"]),
    ("java", "语言", ["java", "spring boot", "jvm"]),
    ("fastapi", "后端框架", ["fastapi"]),
    ("django", "后端框架", ["django"]),
    ("flask", "后端框架", ["flask"]),
    ("nodejs", "后端框架", ["node.js", "nodejs", "nestjs"]),
    # 工程与数据
    ("distributed", "工程能力", ["分布式", "高并发", "微服务", "高可用", "海量数据"]),
    ("mysql", "数据存储", ["mysql"]),
    ("postgresql", "数据存储", ["postgresql", "postgres", "pgsql"]),
    ("redis", "数据存储", ["redis"]),
    ("kafka", "数据存储", ["kafka", "消息队列", "rocketmq", "pulsar"]),
    ("mongodb", "数据存储", ["mongodb", "mongo"]),
    ("docker", "部署", ["docker", "容器化"]),
    ("k8s", "部署", ["k8s", "kubernetes", "容器编排"]),
    ("linux", "部署", ["linux", "shell"]),
    ("ci_cd", "部署", ["ci/cd", "devops", "jenkins", "github actions"]),
    ("observability", "部署", ["可观测", "prometheus", "grafana", "链路追踪"]),
    # 领域经验
    ("llm_app_exp", "领域经验", ["大模型应用", "llm应用", "ai应用", "aigc"]),
    ("agent_exp", "领域经验", ["agent开发", "智能体开发", "agent应用", "ai agent"]),
    ("to_b", "领域经验", ["to b", "tob", "企业级", "saas"]),
    ("english", "软技能", ["英语", "英文", "english"]),
]

_ID2GROUP = {sid: group for sid, group, _ in DEFAULT_SKILLS}
_ID_SET = set(_ID2GROUP)


def load_extra(path: Path | None = None) -> None:
    """从 config/skills.yaml 追加自定义技能（id: {group, aliases}）。"""
    from .config import ROOT
    path = path or ROOT / "config" / "skills.yaml"
    if not path.exists():
        return
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for sid, spec in data.items():
        group = (spec or {}).get("group", "自定义")
        aliases = (spec or {}).get("aliases", []) or []
        DEFAULT_SKILLS.append((sid, group, aliases))
        _ID2GROUP[sid] = group
        _ID_SET.add(sid)


def skill_group(sid: str) -> str:
    return _ID2GROUP.get(sid, "其他")


def valid_ids() -> set[str]:
    return set(_ID2GROUP)


def match_skills(text: str) -> list[str]:
    """文本 → 规范技能 id 列表（保持词典顺序，去重）。"""
    if not text:
        return []
    t = text.lower()
    hits: list[str] = []
    for sid, _group, aliases in DEFAULT_SKILLS:
        for alias in aliases:
            a = alias.lower()
            if len(a) <= 3 and re.fullmatch(r"[\w.+#-]+", a):
                if re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", t):
                    hits.append(sid)
                    break
            elif a in t:
                hits.append(sid)
                break
    return hits


def skills_text(ids: list[str]) -> str:
    """规范 id → 展示文本（含分组便于检索）。"""
    parts = [f"{sid}({skill_group(sid)})" if sid in _ID2GROUP else sid for sid in ids]
    return " ".join(parts)
