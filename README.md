# 🔭 信息差 xinxicha

本地优先的**求职情报与决策服务**：把散落在各平台的招聘 JD 与行业资讯统一采集 → **质量门筛选** → LLM 结构化 → 统计聚合 → 行情报告 / 差距分析 / 资讯周报 / 数据问答，并按你的**个人偏好**（薪资底线、目标城市、加班容忍度等）对每个岗位算可解释的匹配分。

> 打破信息差的思路：信息不是拿不到，而是**散落、非结构化、口径不一、无法比较**——而且**大家随便一搜就能搜到的不是信息差**。所以采集端做轻做合规、冷门渠道做深做真（官方直连 + TG 频道 + GitHub 内推 + 圈内板块），结构化与筛选做重（七维含金量评分 + LLM 灰区裁决），呈现只消费过了质量门的精选层。

## v0.2 核心架构：三层漏斗 + 证据链

```
采集（30+ 渠道）→ raw_items 原始层（全量落地 append-only，可重放）
  → 质量门 quality.py：七维含金量评分（可信/稀缺/完备/证据/时效/相关/交叉验证）
     + 反诈骗硬规则（付费内推/保证金/培训贷 → 直接拦截）
     + 灰区 LLM 裁决（judge 路由，短超时+熔断）
  → curated 精选层（jobs/news/tech）｜ quarantined 隔离区（UI 复核可逆）｜ rejected 留档
  → 整合：跨渠道同岗归并 → 独家判定 / 首发领先天数 / 薪资矛盾检测
  → 周快照（技能势能/薪资分位/渠道信息差贡献）→ 混合检索（FTS5+同义词 ∥ bge-m3 → RRF → bge-reranker 精排）
```

每条精选数据：**可解释**（gold 七维分解+理由）、**可追溯**（raw_id → 原文）、**可逆**（隔离区恢复）、**可重放**（阈值改版后对 raw 层重打分）。

## 功能总览

| 能力 | 说明 |
|---|---|
| 📊 仪表盘 | 机会流（gold×匹配 Top）、**信息差看板**（各渠道独家岗位数/首发领先天数）、技能势能、技能/薪资/城市/WLB 图表 |
| 💼 岗位库 | 多维筛选（含 min_gold 含金量线）+ 匹配徽章 + **gold 七维分解**；详情页 JD 段落化排版（无伪影空格/空行） |
| 🔌 数据接入中心 | 五分区：**URL 智能导入**（贴链接自动识别岗位/资讯/技术页，JS 页降级 Playwright）/ 渠道中心（卡片+健康灯+单采）/ 手动导入 / 历史（漏斗数）/ 高级；一切采集皆任务 |
| 📡 渠道矩阵 | **官方直连**（Greenhouse：anthropic/xai/scaleai/databricks…）·**信息差组**（TG 公开频道 t.me/s、GitHub 内推 Issues+README、Ruby China、阮一峰《谁在招人》）·社区（V2EX/电鸭/HN/Remotive/RemoteOK）·央国企（国聘/高校人才网/公告流）·**前沿技术**（arXiv/GitHub Trending）·RSS 资讯（量子位/InfoQ/TechCrunch AI/MIT TR…） |
| 📰 行业资讯 | **信号卡**：徽章（💰融资/📉裁员/🚀发布/📋政策/🔁招聘动向/🔬技术）+ 一句话 + 要点 + 影响；原始摘要永不直出，无效摘要自动抓原文补全 |
| 🔬 前沿技术 | arXiv/HF/Trending 技术流 + 技能势能（新技术从冒头到进 JD 的 6-18 个月窗口） |
| 🧪 隔离区 | 质量门拦下的低分数据复核（恢复/永弃），复核动作落库校准渠道权重 |
| 💬 数据问答 | **混合检索**（FTS5+同义词扩展 ∥ bge-m3 向量 → RRF → bge-reranker 精排 → gold×时效融合）+ LLM 带引用作答；全程降级链（rerank 挂→RRF；embedding 挂→FTS） |
| ⚙️ 模型配置中心 | 设置页即 LLM/Embedding/Rerank 唯一配置入口：**保存即测试、即热生效**（双层配置 config.yaml 基线 + config_live.yaml 运行层；密钥只落 .env 0600） |
| ⏰ 自动调度 | serve 常驻后台线程每日全链路（采集→门→整合→快照→向量化），设置页可开关 |
| 📋 任务中心 | 一切 >1s 操作任务化：触发即返回、进度条+阶段文案、完成 toast、刷新安全、协作取消——页面永不转圈 |
| 📑 报告 | 行情/差距/周报三类（SSE 流式 + 任务化）；周报吃信号卡而非原始摘要 |
| 🎯 偏好画像 | 硬门槛 + 软偏好加权；匹配分解可视化 |
| 🌗 企业级 UI | 设计系统 ds.css（tokens+组件：徽章/进度条/骨架屏/空态/Modal/toast）+ ui.js 交互层；深浅色主题；键盘可达 |

**WLB 隐性信号抽取**：从 JD 话术挖加班文化（"弹性/双休" vs "抗压/狼性/快节奏"），L0-L4 刻度 + 原文引用，规则优先 LLM 兜底。

## 快速开始

```bash
# 1) 环境（需要 Python 3.10+，推荐 uv）
uv venv .venv --python 3.12

# 2) 安装
uv pip install -e ".[dev]"

# 3) 配置模型（可选——不配也能用示例数据体验全部界面与规则统计）
cp .env.example .env   # 填 DEEPSEEK_API_KEY，或指向你的 vLLM/内网端点

# 4) 初始化 + 示例数据 + 启动
.venv/bin/xinxicha init-db
.venv/bin/xinxicha load-sample
.venv/bin/xinxicha serve          # http://127.0.0.1:8000
```

打开 <http://127.0.0.1:8000>，点「数据导入 → 载入示例数据」即可开箱体验；真实使用从「粘贴导入」你关注的 JD 开始。

### CLI 一览

```bash
xinxicha init-db          # 初始化 SQLite
xinxicha load-sample      # 32 条示例 JD + 资讯
xinxicha serve            # 启动 Web（--host/--port 可调；自动拉起任务框架+每日调度）
xinxicha run-once         # v0.2 一键全链路：采集→质量门→精选发布→整合→快照→向量化
                              #   --no-collect 只处理已落地数据；--channels v2ex,iguopin 指定渠道
xinxicha migrate-gold     # v0.2 老库升级：存量入 raw 层 + 重过质量门 + JD 排版修复 + 资讯补信号卡
                              #   （只降级不删除；--no-rescore 跳过重打分）
xinxicha ingest-file a.txt --no-llm   # 导入 JD 文件（--no-llm 纯规则）
xinxicha ingest-rss       # 抓取公开资讯
xinxicha report --kind market|gap|digest   # 生成报告到 data/reports/
xinxicha ask "北京的 Agent 岗普遍要求什么"  # 命令行问答（混合检索）
xinxicha repair-salary    # 薪资以 salary_raw 原文为唯一依据重算
xinxicha self-test        # 规则层快速自检（薪资/WLB/技能/文本规范化/反诈骗）
```

## 模型接入配置（推荐：设置页一键配置，保存即测试、即热生效）

v0.2 起设置页是模型接入的唯一入口（LLM 通道增删改/启停/测试、Embedding、Rerank、预算、质量门阈值、调度）。
底层为**双层配置**：`config.yaml` 手工基线（注释保留）+ `data/config_live.yaml` 设置页运行层（live 优先合并，热重载）；密钥只落 `.env`（0600 权限、UI 脱敏）。

混合检索端点（OpenAI 兼容）：

```yaml
embedding: {base_url: ${EMBEDDING_BASE_URL:}, model: ${EMBEDDING_MODEL:bge-m3}, batch_size: 16}
rerank:    {base_url: ${RERANK_BASE_URL:}, model: ${RERANK_MODEL:bge-reranker-v2-m3}, top_k: 24}
retrieval: {vector: true, rerank: true}   # 双开关即降级链开关
```

LLM 通道全部 OpenAI 兼容协议，任务路由按序降级（`base_url` 为空自动跳过；judge/summarize 走廉价通道且带短超时+熔断）：

```yaml
llm:
  providers:
    deepseek-official: {base_url: https://api.deepseek.com/v1, api_key_env: DEEPSEEK_API_KEY, models: [deepseek-chat]}
    vllm-deepseek:     {base_url: ${VLLM_DSK_BASE_URL:...}, models: [deepseek-v3]}   # vLLM OpenAI 格式
    vllm-glm:          {base_url: ${VLLM_GLM_BASE_URL:...}, models: [glm-4.6]}      # vLLM GLM 格式
    internal:          {base_url: ${AGENT_BASE_URL:}, models: ["${AGENT_MODEL_ID:}"]}  # 内网端点
  routing:
    extraction: [internal, vllm-glm/glm-4-flash, deepseek-official/deepseek-chat]  # 批量抽取走便宜模型
    judge:      [internal, vllm-glm/glm-4-flash, deepseek-official/deepseek-chat]  # 质量门灰区裁决
    summarize:  [internal, vllm-glm/glm-4-flash, deepseek-official/deepseek-chat]  # 资讯信号卡
```

- **推理模型适配**：自动剥离 `<think>` 块；`max_tokens` 耗尽于推理阶段时判定候选失败并走降级链
- **JSON mode 协商**：端点不支持 `response_format` 时自动降级为提示词约束 + 容错解析（尾逗号/单引号修复）
- **成本**：用量按通道记账（本地端点免费），可设 `budget_monthly_cny` 月度熔断（设置页可改）

### DSH 引擎（可选，研究型任务）

```bash
uv pip install "xinxicha[dsh]"    # deepseek-harness-sdk + 内置运行时
```

安装后「设置」页显示引擎可用，research 类任务自动走 DSH agent（多轮自主执行、工具调用）。DSH 内置 provider 走 Anthropic 协议；对 vLLM/内网端点，适配器会自动注册 `openai-completions` 型 provider（写 `data/dsh_home/profiles/sdk/cordis.patch.yml`），无需改 DSH 原生代码。DSH 是 preview 版有 breaking 风险，出问题可 `dsh_enabled: false` 一键退回直连。

## 数据源与合规边界

### 🌍 渠道矩阵（v0.2：三组新源 + 原有渠道，公开接口·逐个实测·无需付费 key）

「数据接入中心 → 一键全链路」抓取，全部过质量门（tier × scarcity 决定起点分）：

**A. 官方直连组（tier=1.0，含金量最高）**

| 渠道 | 内容 | 实测 |
|---|---|---|
| `greenhouse:*` Greenhouse 官方板 | anthropic / xai / scaleai / databricks / gleanwork / imbue / figma / vercel（404 自动跳过，config 可加） | ✅ ~280 条/次 |
| `iguopin` 国聘 | 央企国企官方 JSON API 直连（无鉴权） | ✅ ~150 条/次 |
| `gaoxiaojob` 高校人才网 | 央国企/事业单位/高校科研/军队文职/军工/银行/证券，18 类目 | ✅ ~200 条/次 |

**B. 信息差组（稀缺度高、低 tier 起步 + 反诈骗 + 交叉验证三重兜底）**

| 渠道 | 内容 | 实测 |
|---|---|---|
| `tg:*` Telegram 公开频道 | t.me/s 网页预览解析（无需账号）；默认 DeJob（Web3+AI，64K 订阅），config 追加 | ✅ 17 条/次 |
| `gh:*` GitHub 内推仓库 | Search API 按相关度发现活跃仓库 + Issues/README 内推提取（如 shfshanyue/Daily-Question 5.1k★ 的阿里/字节/腾讯内推） | ✅ 12 条/次 |
| `niche:ruby-china` Ruby China /jobs | Ruby 圈垂直板块（[远程]/[广州] 标注的圈内岗） | ✅ 25 条/次 |
| `ruanyf` 《谁在招人》 | 阮一峰周刊的招聘栏目（ruanyf/weekly Issue 评论，企业免费发布+人工审核） | ✅ 10 条/期 |

**C. 前沿技术组（kind=tech，对上技术迭代信息差）**：arXiv cs.AI/CL/CL ✅ 20 条/次 · GitHub Trending ✅ 15 条/次（HF Papers 端点 2026-11 起需登录、ThoughtWorks radar.json 已下线，均已记录）

**D. 社区与资讯**：v2ex ✅ / 电鸭 ✅ / HN Who is Hiring ✅ / Remotive ✅ / RemoteOK ✅ / 公告流 ✅；RSS：量子位 / InfoQ中国 / 阮一峰周刊 / 少数派 / HackerNews-AI / TechCrunch-AI ✅ / MITTR-AI ✅（已移除实测不可用源：机器之心 RSS 下线、36氪/Solidot 反爬、rust-jobs 域名失效、elixirforum RSS 404）

**全中文化**：外文 JD 在 LLM 增强时自动翻译；**学历筛选**；**URL 智能导入**：任何网站贴链接即抓（岗位列表/详情/资讯/技术自动识别，JS 页降级 Playwright，不豁免质量门）。

### 其他通道

| 通道 | 用法 | 合规性 |
|---|---|---|
| URL 导入 | 贴链接自动识别抓取（数据接入中心首屏） | ✅ 单页限速，个人研究 |
| 粘贴/文件导入 | 多条 `---` 分隔；jsonl | ✅ 一手信息，国内 JD 主力通道 |
| 浏览器采集 | `scripts/capture_zhipin.py`（Playwright 半自动）或「另存为 HTML」上传 | ⚠️ 仅限你本人登录态的搜索结果，供个人分析；勿高频翻页/规模化/转售 |
| 搜索 API | Tavily / 博查（.env 配 key） | ✅ 搜索即服务 |

> **国内大厂 JD 的现实路径**：Boss直聘等无公开 API 且反爬严格，规模化爬取违规。正确姿势 = 粘贴/URL 导入你真实关注的 JD（样本即你的市场切片）+ 官方直连与信息差渠道做行情面。

## 技术栈与架构

```
前端 Jinja2+ECharts+ds.css/ui.js 设计系统（无前端工程链）
  └─ FastAPI 应用层（质量门/匹配/统计 全为纯 Python 规则，零 LLM 成本）
       ├─ SQLite + 双 FTS5（jobs/news，jieba 预分词）+ vectors 表（bge-m3，numpy 余弦）
       ├─ 三层漏斗：raw_items → quality.gate_items → jobs/news/tech 精选层
       ├─ pipeline（编排）+ scheduler（每日调度）+ web/tasks（异步任务中心）
       ├─ 采集层插件式（sources/：urlingest/tgchannels/ghreferral/niche_boards/
       │              premium/techfront/realjobs/soe/rss/searchapi/browser）
       └─ LLMGateway（路由降级+熔断）─┬─ 直连适配器 ── Embedding/RerankClient（混合检索）
                                    └─ DSH 引擎适配器
```

- 匹配引擎：硬门槛一票否决 + 软偏好加权，分维度可解释分解
- 目录：`src/market_lens/` 核心包，`tests/` 129 个单测+接口测试，`scripts/` 采集脚本，`data/` 全部本地数据

## 桌面版（macOS / Windows 安装包）

零 Python 环境即可安装使用：PyInstaller 打包后端 + pywebview 原生窗口（mac 用 WKWebView，Windows 用 WebView2），
数据存用户目录，与开发态互不干扰。

### 构建

```bash
# macOS（本仓库在 Mac 上）：产出 dist/信息差.app + dist/信息差.dmg
bash packaging/build_mac.sh

# Windows（需在 Windows 机器上，装好 Python 3.10+ 与 Inno Setup 6）：
#   产出 dist\Xinxicha-Setup-0.1.0.exe（未装 Inno Setup 则产出 zip 免安装包）
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

CI 自动双平台构建：`.github/workflows/release.yml`——推 git 标签 `v*` 即在 GitHub Actions
构建 macOS（arm64/x64 dmg）+ Windows 安装包并附到 Release；也可手动触发后从 Artifacts 下载。

### 桌面版行为说明

- 首次启动自动建库并载入 32 条示例数据（无 API key 也能完整体验）；界面即「设置」页配置模型
- 用户数据目录：mac `~/Library/Application Support/xinxicha/`，Win `%APPDATA%\xinxicha\`
  （含 `config.yaml`、`.env`、`data/app.db`）——卸载不删数据；开发态仍用仓库内 `data/`
- 固定端口可设环境变量 `MARKET_LENS_PORT`；默认每次启动随机空闲端口，仅本机可访问

### 分发提示（macOS Gatekeeper）

未做开发者证书签名/公证时，他人首次打开需：**右键 .app → 打开 → 再点「打开」**，
或终端执行 `xattr -cr /Applications/信息差.app` 清除隔离标记。Windows 版由 Inno Setup
安装器签名位处理，SmartScreen 提示同理点「仍要运行」。

## 测试

```bash
.venv/bin/python -m pytest tests/ -q    # 129 passed：薪资/WLB/技能/匹配/网关/抽取/统计/问答/Web
                                        # + textfmt/质量门/资讯闭环/任务框架/混合检索/流水线e2e/配置中心
```

## 路线图

- [ ] 公司口碑卡：搜索 API 聚合「公司名+加班/裁员/融资」信号（DSH research 主战场）
- [ ] DSH TS 薄插件（dsh-plugin-xinxicha）：把岗位库查询暴露为 agent 工具
- [x] ~~向量召回~~ v0.2 已完成（bge-m3 + RRF + bge-reranker 混合检索）
- [x] ~~趋势追踪~~ v0.2 已完成（周快照 + 技能势能 rising/falling）
- [x] ~~定时任务~~ v0.2 已完成（serve 常驻每日调度 + 任务中心）
- [ ] 一亩三分地/牛客公开页尝试接入（campus.py 实验槽位已留）
- [ ] 复核反馈驱动渠道 tier 自动校准

## 已知限制

- **薪资样本覆盖率有限**：多数渠道帖子不带薪资，分位数只基于「有薪资原文」的子集（仪表盘已标注 n）；公告类聚合页与多岗位列表页不参与薪资统计；外币按汇率折算
- 冷门渠道（TG/GitHub）单条质量方差大：三重兜底（反诈骗硬规则 + 灰区 LLM + 隔离区复核）保证主库干净，但隔离区需要偶尔人工复核
- LLM 端点不可达时灰区全部进隔离区（保守策略）；judge/summarize 有 10 分钟熔断，端点恢复后自动重试
- 招聘站 DOM 会改版，浏览器采集/URL 导入选择器做多策略兜底但仍需偶发维护
- ECharts 走 CDN，离线时图表区降级提示（统计卡片不受影响）
