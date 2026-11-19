"""画像与偏好模型。岗位记录用 dict（SQLite 行），画像走 pydantic（表单校验）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

OvertimeLevel = Literal[0, 1, 2, 3, 4]


class HardPrefs(BaseModel):
    """硬性门槛：一票否决。"""
    salary_floor_k: float | None = Field(default=25.0, description="月薪下限（K）；空=不限")
    cities: list[str] = Field(default_factory=lambda: ["北京", "上海", "深圳", "杭州", "远程"],
                              description='目标城市，"远程"为虚拟城市')
    overtime_max: int | None = Field(default=2, ge=0, le=4, description="加班容忍上限 L0-L4；空=不限")


class SoftPrefs(BaseModel):
    """软性偏好：加权打分（1-5）。"""
    work_mode: Literal["any", "remote", "hybrid", "onsite"] = "any"
    company_types: list[str] = Field(default_factory=lambda: ["foreign", "unicorn"],
                                     description="偏好公司类型 bigtech/unicorn/startup/foreign/soe/traditional")
    tech_like: list[str] = Field(default_factory=lambda: ["langgraph", "mcp", "rag", "multi_agent"],
                                 description="想深耕的技能 id/关键词")
    tech_avoid: list[str] = Field(default_factory=list, description="想避开的技能/关键词")
    weights: dict[str, int] = Field(
        default_factory=lambda: {"wlb": 5, "work_mode": 3, "company_type": 2, "tech_fit": 4, "salary_margin": 3},
        description="维度权重：wlb/work_mode/company_type/tech_fit/salary_margin")


class Profile(BaseModel):
    name: str = "我"
    years_exp: int = 6
    background: str = (
        "6 年后端研发经验，主导过分布式/高并发服务与中间件建设；"
        "近一年转型 AI Agent 应用研发，熟悉 LLM API、RAG 与 Agent 编排，"
        "工程能力强，算法背景较薄弱。")
    skills: list[str] = Field(default_factory=lambda: [
        "python", "go", "java", "fastapi", "distributed", "mysql", "redis", "kafka",
        "docker", "k8s", "rag", "mcp", "function_calling", "prompt_engineering", "llm_app_exp"])
    target_role: str = "AI Agent 应用开发工程师 / 大模型应用工程师"
    prefs: SoftPrefs = Field(default_factory=SoftPrefs)
    hard: HardPrefs = Field(default_factory=HardPrefs)


DEFAULT_PROFILE = Profile()

COMPANY_TYPE_DESC = {
    "bigtech": "大厂", "unicorn": "独角兽/中厂", "startup": "创业公司",
    "foreign": "外企", "soe": "国企/央企", "traditional": "传统企业", "unknown": "未知",
}
WORK_MODE_DESC = {"remote": "远程", "hybrid": "混合", "onsite": "坐班", "unknown": "未知"}
SOURCE_DESC = {
    "manual": "手动导入", "rss": "RSS资讯", "search": "搜索采集",
    "browser": "浏览器采集", "sample": "示例数据",
}


# ==================== 数据契约（三层漏斗） ====================

ItemKind = Literal["job", "news", "tech"]
ItemStatus = Literal["pending", "curated", "quarantined", "rejected"]


class RawItem(BaseModel):
    """原始层条目：采集忠实搬运，不做价值判断。append-only。"""
    kind: ItemKind = "job"
    source: str = ""                       # 渠道 id（v2ex/tg:xxx/gh:repo/…）
    source_url: str = ""
    title: str = ""
    body: str = ""                         # 规范化后的原文（normalize_text）
    published_at: str = ""                 # 来源发布时间（ISO 或原文格式）
    fetched_at: str = ""
    channel_meta: dict = Field(default_factory=dict)  # 渠道元数据：tier/scarcity/group 等
    norm_hash: str = ""                    # 跨渠道去重指纹


class ScoreDimension(BaseModel):
    """单维评分：分数 + 理由（可解释）。"""
    key: str
    score: float                          # 0-100
    weight: float                         # 权重（合计 1）
    reason: str = ""


class ScoreResult(BaseModel):
    """七维含金量评分结果。"""
    gold: float = 0.0                      # 加权总分 0-100
    dims: list[ScoreDimension] = Field(default_factory=list)
    judge_used: bool = False               # 是否经过 LLM 灰区裁决
    fallback_used: bool = False            # judge 不可用，灰区按门中值自动分流
    judge_note: str = ""
    scam_risk: bool = False                # 反诈骗命中
    scam_rules: list[str] = Field(default_factory=list)


class NewsSignalCard(BaseModel):
    """资讯信号卡：UI 展示的唯一形态（原始摘要永不直出）。"""
    one_liner: str = ""                    # ≤40 字，说清发生了什么（中文）
    key_points: list[str] = Field(default_factory=list)   # 2-4 条要点（含数字/主体，中文）
    signal_type: Literal["funding", "layoff", "release", "policy",
                         "hiring", "tech", "none"] = "none"
    entities: list[str] = Field(default_factory=list)     # 公司/技术/人物
    impact: str = ""                       # 对求职者/行业的一句话含义（中文）
    method: Literal["llm", "rule"] = "rule"               # 治理方式：LLM 提取翻译 / 规则降级


class ModelProviderConfig(BaseModel):
    """模型接入契约（LLM / Embedding / Rerank 通用）。"""
    kind: Literal["llm", "embedding", "rerank"] = "llm"
    provider_id: str = ""
    base_url: str = ""
    api_key_env: str = ""
    models: list[str] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)  # json_mode/batch_size/dims/top_k…


class TaskRecord(BaseModel):
    """异步任务契约（任务中心）。"""
    kind: str = ""                         # collect/url_import/rss/report/…
    title: str = ""
    params: dict = Field(default_factory=dict)
    status: Literal["queued", "running", "done", "failed", "cancelled", "orphaned"] = "queued"
    progress: float | None = None          # 0-100；None=不可计数（阶段步进）
    stage: str = ""
    message: str = ""
