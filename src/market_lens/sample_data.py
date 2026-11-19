"""内置示例数据：32 条合成 JD + 14 条资讯（source=sample，仅用于开箱演示；
接入真实数据后可在岗位库按来源过滤掉它们）。"""
from __future__ import annotations

SAMPLE_JOBS: list[str] = [
"""AI Agent 平台开发工程师
城市：北京
薪资：35-60K·16薪
经验：3-5年  学历：本科及以上
公司：字节跳动
岗位职责：负责豆包业务线 Agent 平台的编排引擎与工具调用链路；基于自研+LangGraph 混合架构构建多智能体协作；建设 MCP 工具生态打通内部数据服务；优化大规模 RAG 检索的召回与 rerank 效果。
任职要求：本科及以上，3 年以上后端经验，Python/Go 扎实；熟悉 LangChain/LangGraph、function calling；有向量数据库（Milvus/FAISS）落地经验；具备分布式、高并发工程能力；有 agent 评测体系经验优先。
福利：弹性工作、三餐免费、健身房""",
"""大模型应用开发工程师（Agent 方向）
城市：北京
薪资：30-55K·15薪
经验：3-5年  学历：硕士及以上
公司：月之暗面
岗位职责：负责 Kimi 场景化的 Agent 编排与深度研究（Deep Research）能力；设计 multi-agent 任务规划与反思机制；建设 agent 评测基准。
任职要求：熟悉 PyTorch 与主流模型推理框架（vLLM）；有 RAG/微调（LoRA/SFT）实战；Python 工程能力强；硕士优先，优秀本科可放宽。
节奏：技术驱动团队，节奏快，能承受压力""",
"""AI 应用开发工程师
城市：杭州
薪资：25-45K·15薪
经验：3-5年  学历：本科及以上
公司：阿里巴巴
岗位职责：负责通义千问在电商场景的 Agent 应用落地；构建智能客服/导购多智能体系统；使用 LlamaIndex 与内部向量库建设知识库问答。
任职要求：Java/Python 双栈优先；熟悉 RAG 全链路（embedding、rerank）；有大规模分布式系统经验；了解 prompt 工程与 agent 编排。
福利：965 工作制，双休""",
"""Agent 后端开发工程师
城市：上海
薪资：28-50K·14薪
经验：1-3年  学历：本科及以上
公司：智谱AI
岗位职责：参与 GLM 生态 Agent 平台（AutoGLM）后端建设；开发 MCP server 与工具注册中心；构建 agent 记忆与上下文管理服务。
任职要求：Python 熟练，FastAPI/Flask 有生产经验；熟悉 MCP、A2A 协议者优先；了解 LangChain/Dify 等框架；有开源贡献优先。
福利：外企风格管理，弹性工作不打卡，20天年假""",
"""Senior Agent Engineer
城市：远程
薪资：40-70K·14薪
经验：5-10年  学历：本科及以上
公司：Shopify（外企远程）
岗位职责：Build agentic workflows for merchant automation; design tool-calling pipelines with strict evals; own RAG infrastructure for product docs.
任职要求：5+ years backend (Python/Go); production experience with LangGraph or similar; strong distributed systems background; fluent English.
福利：全远程办公，work-life balance，每年两次线下团建""",
"""AI Agent 工程师（RAG 方向）
城市：北京
薪资：25-50K·14薪
经验：1-3年  学历：本科及以上
公司：百度
岗位职责：负责文心智能体平台 RAG 链路：文档解析、切分、embedding、混合检索、rerank；优化知识库问答的准确率与延迟。
任职要求：熟悉 Elasticsearch/向量数据库混合检索；有 NLP 项目经验；Python 扎实；了解 prompt 工程与评测。
福利：双休，加班有加班费""",
"""大模型 Agent 应用开发
城市：深圳
薪资：30-55K·15薪
经验：3-5年  学历：本科及以上
公司：腾讯
岗位职责：负责混元大模型在微信场景的 agent 应用开发；构建插件/工具调用体系；优化多轮对话的记忆与规划能力。
任职要求：后端开发经验 3 年+（Go/Python）；熟悉 function calling、MCP；有高并发服务经验；了解多模态优先。
氛围：核心业务线，成长快，节奏快""",
"""AI 应用工程师（金融场景）
城市：上海
薪资：20-35K·13薪
经验：1-3年  学历：本科及以上
公司：招商银行信用卡中心
岗位职责：负责零售金融场景的大模型应用：智能客服、营销文案生成、RAG 知识库；参与提示词工程与效果评测。
任职要求：Python/Java；了解 RAG 基本链路；有金融IT背景加分；责任心强，能适应合规要求。
福利：国企风格，稳定，双休不加班，补充公积金""",
"""Agent 编排开发工程师
城市：北京
薪资：35-65K·15薪
经验：5-10年  学历：本科及以上
公司：DeepSeek
岗位职责：参与 DeepSeek 生态 agent 编排层设计；构建高性能工具调度与沙箱执行环境；优化流式多轮交互延迟。
任职要求：5 年+系统级后端经验（C++/Python）；深入理解推理服务（vLLM/TensorRT）；有编译器或沙箱经验优先；热爱硬核技术。
福利：薪酬顶级，扁平化管理，弹性工作""",
"""AI Agent 开发工程师（创业公司）
城市：北京
薪资：20-40K·14薪 + 期权
经验：1-3年  学历：本科及以上
公司：某AI原生创业公司（天使轮）
岗位职责：从零构建面向跨境电商的自动化 Agent 产品；全栈参与 Dify/LangChain 编排、前端对话界面、数据后台。
任职要求：Python 熟练；动手能力强，能在模糊需求下推进；有 Dify/Coze 低代码平台经验优先。
氛围：创业氛围浓厚，需要自驱与抗压，大小周""",
"""大模型平台开发工程师
城市：成都
薪资：20-35K·14薪
经验：3-5年  学历：本科及以上
公司：快手
岗位职责：负责快手大模型平台推理服务建设：模型网关、弹性伸缩、推理加速（量化/蒸馏）；支撑上层 agent 业务。
任职要求：熟悉 vLLM/TGI 推理框架；有 K8s 大规模部署经验；C++/Python；了解 CUDA 编程加分。
福利：成都研发中心，965，双休，通勤友好""",
"""AI 工程师（智能体平台）
城市：上海
薪资：25-45K·14薪
经验：1-3年  学历：硕士及以上
公司：MiniMax
岗位职责：负责 MiniMax 开放平台 agent 能力建设：workflow 编排、插件生态、多模态工具调用；撰写技术文档与示例。
任职要求：硕士优先；熟悉 Python 与 FastAPI；了解 LangChain/LangGraph；有良好的英文文档能力。
福利：免费三餐、零食下午茶、双休""",
"""Senior LLM Application Engineer
城市：北京
薪资：45-70K·15薪
经验：5-10年  学历：本科及以上
公司：微软（外企）
岗位职责：Develop copilot-class agent features for enterprise customers; integrate MCP tool ecosystem; build evaluation pipelines for agent quality.
任职要求：8 years software engineering; deep experience with LLM APIs, RAG architecture; excellent system design; English working proficiency.
福利：外企文化，弹性办公，每周2天居家，年假15天起""",
"""AI Agent 开发（企业知识库方向）
城市：深圳
薪资：22-38K·13薪
经验：1-3年  学历：本科及以上
公司：金蝶软件
岗位职责：负责苍穹平台企业知识库 Agent 产品：文档解析、GraphRAG 构建行业知识图谱、多轮问答。
任职要求：Python/Java；了解知识图谱与 RAG 融合方案；有 To B 产品经验优先。
福利：园区办公，五天工作制""",
"""Agent 全栈工程师
城市：远程
薪资：25-45K
经验：3-5年  学历：不限
公司：Remote AI Native Team（海外）
岗位职责：独立负责一款 AI 客服 SaaS 的 agent 闭环：意图识别、工具编排（function calling）、人工接管；参与 prompt 调优与 evals。
任职要求：全栈能力（Python 后端 + React 基础）；英文读写流利；自驱，异步协作。
福利：全远程，不打卡，结果导向""",
"""大模型应用开发工程师（搜推方向）
城市：北京
薪资：30-55K·15薪
经验：3-5年  学历：硕士及以上
公司：美团
岗位职责：负责到店搜索的大模型改写与 agent 化改造：查询理解、多轮澄清、结果聚合；建设离线评测体系。
任职要求：搜索/推荐背景；熟悉 LLM+RAG 在搜推的应用；Python/Java；有大规模 A/B 实验经验。
福利：弹性工作，部分团队大小周""",
"""AI Agent 工程师（法务科技）
城市：上海
薪资：18-30K·13薪
经验：1-3年  学历：本科及以上
公司：某法律科技公司
岗位职责：负责合同审查 Agent 的规则引擎+大模型混合链路；构建法条向量库与检索；输出可解释的审查报告。
任职要求：Python；了解 RAG；对法律行业有兴趣；细致耐心。
福利：朝九晚六，双休，很少加班""",
"""Agent Infra 工程师
城市：杭州
薪资：35-60K·15薪
经验：5-10年  学历：本科及以上
公司：蚂蚁集团
岗位职责：负责金融级 Agent 运行时基础设施建设：沙箱隔离、权限体系、审计追踪、灰度评测；保障 agent 生产可观测。
任职要求：5 年+基础设施经验；熟悉 K8s/服务网格；有安全或风控背景；Python/Go。
福利：杭州总部，双休""",
"""AI 应用开发（制造行业）
城市：苏州
薪资：15-25K·13薪
经验：1-3年  学历：本科及以上
公司：汇川技术
岗位职责：负责工业质检报告生成 Agent 与设备知识库问答；使用 Dify 搭建内部工作流；对接产线数据。
任职要求：Python；了解 Dify/Coze；能接受偶尔出差到产线。
福利：制造业节奏，双休，加班少""",
"""Prompt & Agent 优化工程师
城市：北京
薪资：25-40K·14薪
经验：1-3年  学历：本科及以上
公司：理想汽车
岗位职责：负责理想同学车载助手的 prompt 工程与 agent 工具编排；构建多模态（语音+视觉）交互链路；持续做评测与回归。
任职要求：对 prompt 工程有系统方法论；Python；了解多模态模型；有评测经验。
福利：弹性工作，免费班车""",
"""大模型算法工程师（Agent 训练）
城市：北京
薪资：40-70K·16薪
经验：3-5年  学历：硕士及以上
公司：快手
岗位职责：负责 agent 模型的后训练：SFT/RLHF/DPO 数据构建与训练；研究工具使用的强化学习（GRPO）；上线 A/B 验证。
任职要求：深度学习实战（PyTorch）；有后训练项目经验；论文阅读与复现能力强。
节奏：算法核心团队，强度大，狼性文化""",
"""AI Agent 开发工程师（游戏NPC）
城市：上海
薪资：25-45K·14薪
经验：1-3年  学历：本科及以上
公司：米哈游
岗位职责：负责游戏智能 NPC 的 agent 行为树+大模型混合决策；构建世界观知识库 RAG；调试对话人设一致性。
任职要求：Python/Lua；热爱游戏；了解行为树与状态机；有 LLM 应用经验。
福利：弹性打卡，不强制加班""",
"""大模型应用工程师（政务）
城市：成都
薪资：15-28K·12薪
经验：1-3年  学历：本科及以上
公司：中移信息（国企背景）
岗位职责：负责政务热线智能工单 Agent；构建政策文件知识库；保障内容安全与合规审计。
任职要求：Python/Java；有国企项目经验优先；政治素养过硬，抗压能力强。
福利：五险一金足额，稳定""",
"""Agent 平台高级工程师
城市：深圳
薪资：40-65K·15薪
经验：5-10年  学历：本科及以上
公司：华为
岗位职责：负责盘古大模型 agent 使能平台：编排引擎、插件市场、企业级权限；支撑内部多个业务域 agent 落地。
任职要求：5 年+后端（Java/Python）；分布式架构能力；有大模型平台建设经验；英语可作为工作语言。
氛围：奋斗者文化，项目忙时需要加班""",
"""AI 工程师（NLP→Agent转型）
城市：广州
薪资：20-35K·13薪
经验：1-3年  学历：本科及以上
公司：唯品会
岗位职责：负责客服域从传统 NLP 管道向 LLM Agent 的迁移改造；维护意图识别与知识库；逐步引入 function calling。
任职要求：NLP 工程经验（分类/NER）；Python；愿意转型大模型技术栈。
福利：双休，每年两次调薪窗口""",
"""LLM Ops 工程师
城市：北京
薪资：30-50K·14薪
经验：3-5年  学历：本科及以上
公司：B站
岗位职责：负责 LLM 网关与 agent 线上稳定性：限流、降级、成本核算、可观测（trace 全链路）；建设灰度评测流水线。
任职要求：SRE/后端背景；熟悉 K8s、Prometheus；了解 LLM API 生态；有成本优化案例。
福利：B站氛围轻松，弹性工作""",
"""Agent 产品工程师
城市：杭州
薪资：20-35K·14薪
经验：1-3年  学历：本科及以上
公司：钉钉（阿里）
岗位职责：负责钉钉 AI 助理的低代码 agent 搭建能力：工作流模板、连接器生态、多 agent 协作模板；沉淀最佳实践。
任职要求：Python；有低代码/iPaaS 经验优先；沟通表达好；有耐心做用户支持。
福利：杭州滨江区，双休""",
"""AI Agent 工程师（医疗方向）
城市：北京
薪资：25-45K·14薪
经验：3-5年  学历：硕士及以上
公司：医渡科技
岗位职责：负责医疗病历结构化与临床辅助决策 Agent：医学知识库 RAG、多轮追问、结论可解释性。
任职要求：医学信息学/计算机硕士+；有医疗 NLP 经验；严谨细致，能承受高责任场景压力。
福利：双休""",
"""独立 Agent 开发 contractor
城市：远程
薪资：2000-3500元/天
经验：3-5年  学历：不限
公司：海外Web3团队
岗位职责：按项目交付 telegram bot agent / 数据分析 agent；技术栈不限，重结果。
任职要求：独立交付能力强；英文沟通；熟悉至少一种 agent 框架。
说明：远程异步，自备开发环境""",
"""大模型应用开发（保险）
城市：上海
薪资：18-32K·13薪
经验：1-3年  学历：本科及以上
公司：平安产险
岗位职责：负责理赔智能化 Agent：单证 OCR+多模态理解、条款知识库问答、自动化审核流。
任职要求：Python；有 OCR/多模态项目经验；了解保险业务加分。
福利：传统金融节奏，加班少，双休""",
"""Agent 评测工程师
城市：北京
薪资：25-40K·14薪
经验：1-3年  学历：本科及以上
公司：蔚来
岗位职责：负责 NOMI 车载 agent 的评测体系：构建场景库、自动化回归、badcase 归因分析；与算法团队协作闭环。
任职要求：Python 自动化测试功底；了解 LLM 评测方法（LLM-as-judge）；细心，有质量意识。
福利：965，双休，免费充电""",
"""AI Agent 架构师
城市：北京
薪资：50-80K·16薪
经验：10年以上  学历：本科及以上
公司：京东
岗位职责：负责京东言犀 agent 中台的架构演进：编排引擎性能优化、跨域 agent 治理、成本与质量平衡的顶层设计。
任职要求：10 年+后端/架构经验；主导过大型平台建设；对 agent 技术栈有深度实践与独立判断；带过团队。
节奏：核心部门，业绩压力大，需要高强度投入""",
]

SAMPLE_NEWS: list[dict] = [
    {"source": "示例·机器之心", "title": "MiniMax 发布 M2.7：推理成本再降 40%，Agent 工具调用准确率提升至 92%",
     "url": "https://example.com/sample/minimax-m27", "published_at": "2026-11-10",
     "summary": "M2.7 强化了长程任务规划与多工具编排能力，官方基准显示在 SWE-bench 与 agent 评测集上大幅领先同价位模型。"},
    {"source": "示例·机器之心", "title": "MCP 生态一周年：注册服务器突破 1.2 万，成 Agent 工具事实标准",
     "url": "https://example.com/sample/mcp-1y", "published_at": "2026-11-08",
     "summary": "主流云厂商与数据库均已提供官方 MCP server，招聘要求中 MCP 出现率半年翻倍。"},
    {"source": "示例·36氪", "title": "大模型应用层融资回暖：Agent 赛道 Q3 融资额同比增 65%",
     "url": "https://example.com/sample/agent-funding", "published_at": "2026-11-05",
     "summary": "资本从基础模型转向垂直 Agent 应用，企业服务与跨境电商自动化是最热两条线。"},
    {"source": "示例·36氪", "title": "多家大厂组建 Agent 平台部，后端工程师转岗窗口开启",
     "url": "https://example.com/sample/transfer-window", "published_at": "2026-11-03",
     "summary": "内部转岗要求：3 年+后端经验、有 LLM 兴趣项目即可申请，转岗培训期 3 个月。"},
    {"source": "示例·HackerNews-AI", "title": "LangGraph 2.0 released: durable execution and human-in-the-loop primitives",
     "url": "https://example.com/sample/langgraph2", "published_at": "2026-11-12",
     "summary": "Major release adds checkpointing, time-travel debugging and first-class interrupt/resume for long-running agents."},
    {"source": "示例·HackerNews-AI", "title": "We benchmarked 14 agent frameworks: orchestration overhead matters",
     "url": "https://example.com/sample/agent-fw-bench", "published_at": "2026-11-06",
     "summary": "Framework overhead varies 3x under high concurrency; bare SDK + thin orchestration layer often wins in production."},
    {"source": "示例·阮一峰科技周刊", "title": "第 300 期：Agent 工程师的技能清单（译文）",
     "url": "https://example.com/sample/weekly-300", "published_at": "2026-11-07",
     "summary": "总结了 2026 年 Agent 岗位 JD 高频技能：编排框架、RAG、评测、协议（MCP/A2A）、工程化。"},
    {"source": "示例·机器之心", "title": "Agent 评测新基准 AgentBench-v2 发布：强调长程任务与成本约束",
     "url": "https://example.com/sample/agentbench-v2", "published_at": "2026-11-01",
     "summary": "新基准要求 agent 在预算内完成 20 步以上任务，国内模型成绩亮眼。"},
    {"source": "示例·36氪", "title": "外企 AI 团队在华扩招：远程+双休成招聘主打卖点",
     "url": "https://example.com/sample/foreign-hiring", "published_at": "2026-10-28",
     "summary": "多家外企中国区 AI 团队开放远程岗，WLB 成其为与大厂竞争人才的核心差异点。"},
    {"source": "示例·机器之心", "title": "开源 GLM-5 系列发布：Agent 能力对标闭源第一梯队",
     "url": "https://example.com/sample/glm5-oss", "published_at": "2026-10-25",
     "summary": "GLM-5 开权重版本在工具调用与多轮规划上表现强劲，本地部署生态（vLLM 第一时间支持）完善。"},
    {"source": "示例·HackerNews-AI", "title": "Show HN: I built a job-market intelligence tool with local LLMs",
     "url": "https://example.com/sample/showhn-jdtool", "published_at": "2026-11-11",
     "summary": "开发者用本地 vLLM + 规则统计做 JD 结构化与薪资分布分析，全套成本趋近于零。"},
    {"source": "示例·36氪", "title": "脉脉数据：Agent 岗位供需比 1:7.2，平均在职周期缩短",
     "url": "https://example.com/sample/maimai-supply", "published_at": "2026-10-20",
     "summary": "Agent 方向人才缺口扩大，企业抬薪抢人；候选人平均面到 offer 周期缩至 2.4 周。"},
    {"source": "示例·机器之心", "title": "A2A 协议进入 v1.0：跨厂商 agent 互操作落地案例增多",
     "url": "https://example.com/sample/a2a-v1", "published_at": "2026-10-18",
     "summary": "多家云厂商宣布支持 A2A v1.0，跨组织 multi-agent 协作开始出现在企业采购清单。"},
    {"source": "示例·36氪", "title": "行业观察：Agent 岗位『去算法化』，工程背景候选人更受青睐",
     "url": "https://example.com/sample/agent-engineering-trend", "published_at": "2026-11-15",
     "summary": "JD 中『后端工程能力』权重上升，纯算法要求下降——对转型工程师是明确利好。"},
]
