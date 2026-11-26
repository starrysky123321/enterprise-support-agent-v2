# Enterprise Support Agent v2

[![Java](https://img.shields.io/badge/Java-17-orange.svg)](https://openjdk.org/)
[![Spring Boot](https://img.shields.io/badge/Spring%20Boot-3.5-brightgreen.svg)](https://spring.io/projects/spring-boot)
[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![Vue 3](https://img.shields.io/badge/Vue-3.5-42b883.svg)](https://vuejs.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

面向企业技术支持团队的**双平面分布式知识库与故障诊断平台**。工程师可在权限与元数据范围内联合检索产品手册、运维 SOP、版本说明和历史工单，获得带页码/分块引用的 **SSE 逐字流式诊断**，并在证据不足时触发结构化拒答。

---

## 核心特性

- **双平面解耦与可靠异步发布**
  - Spring Boot 承担账号会话（Argon2 + `HttpOnly` Cookie）、工作区 RBAC、API Key 与单调文档版本状态机；Python 退居内网专注索引、检索与生成。
  - 采用 **Transactional Outbox + Kafka（`acks=all` / DLT）** 保证写库与派发最终一致；固定加锁顺序（`documents → document_versions → workspaces`）防死锁，`attempt` 乐观锁与 `retired_versions` 墓碑表（`410 Gone`）阻断慢 Worker 覆盖与删后迟到写入。
  - PDF 上传通过 64KB 缓冲流式校验魔数、边写外部存储卷边算 SHA-256，避免大对象写入 PostgreSQL 放大 WAL。
- **权限前置混合检索、pgvector HNSW 与证据拒答**
  - Dense（pgvector）与 BM25（PostgreSQL FTS）在召回前应用同一工作区权限与元数据（`document_type` / `product_name` / `product_version`）过滤，RRF 融合候选。
  - 按 `(model, dim)` 动态构建局部表达式 HNSW 索引：小范围（`< 32` 篇）走精确扫描，大工作区自动切换 HNSW 并启用 `hnsw.iterative_scan = relaxed_order`。
  - LangGraph 编排有界重检与引用 ID 完整性校验（`verify_groundedness`），校验通过后再经 SSE 逐字推送（`answer_delta`）与可点击内联引用，证据不足时结构化拒答。
- **分布式限流、版本感知缓存与集群可观测性**
  - Redis Lua 令牌桶按工作区跨副本限流（使用 Redis `TIME` 消除时钟漂移）；Redis 不可用时搜索降级本地 `Semaphore` 护栏（Fail-Open），问答与登录返回 `503`（Fail-Closed）。
  - 工作区 `catalog_version` 实现 `O(1)` 缓存键与发布即失效，ZSET 有界淘汰防止挤占限流键；内置跨副本聚合指标面板与 `/actuator/prometheus`。
- **现代 Vue 3 极简工作台**
  - 支持账号 / 访问密钥登录、实时 SSE 流式故障诊断、诊断历史与追问、标签筛选、PDF 拖拽上传、版本轮询重试、成员 RBAC、API Key 签发撤销与实时运行指标查看。

---

## 核心实测数据

| 维度 | 测试场景 | 关键结果 |
| --- | --- | --- |
| **混合检索 & 拒答** | 357 题合成评测 + 48 题验证集 | Hybrid Recall@5 **89.0%**（较 BM25 **+9.25 pp**）；本地抽取正确拒答 **20.3% → 100%（69/69）**，误拒 **11.4% → 5.7%**；验证集 **30/30** 正确拒答、**0/18** 误拒，**0 次越权** |
| **高并发压测** | 48 并发混合负载（Kafka + Redis） | 吞吐 **590.6 req/s**（17,762 次全成功，搜索 P95 **262 ms**）；开启版本感知缓存后搜索 P95 **37.19 ms → 19.18 ms** |
| **真实模型压测** | 512 篇文档 · RTX 5060 Laptop（Qwen2.5-1.5B）+ BGE-small | 48 并发吞吐 **81.5 req/s**，**2,126 次搜索 100% 成功**，超容问答由 `Semaphore` 返回 `503` 保护下游不雪崩 |
| **高可用停机演练** | PG（Patroni+etcd+HAProxy）· 3 节点 Kafka · Redis Sentinel | PG 主库强停 **30.3s** 恢复搜索 / **30.8s** 完成发布；Kafka 停 1 Broker **13.5s**；Redis 切主搜索全程成功、问答 **4.1s** 恢复 |

---

## Quick Start

### 1. 一键启动全栈服务（Docker Compose）

环境要求：Docker & Docker Compose。

```bash
git clone https://github.com/starrysky123321/enterprise-support-agent-v2.git
cd enterprise-support-agent-v2

export KNOWLEDGE_DATABASE_PASSWORD="ChangeMe_DB_Password_123!"
export KNOWLEDGE_API_TOKEN="$(openssl rand -hex 32)"
export KNOWLEDGE_INDEX_TOKEN="$(openssl rand -hex 32)"
export KNOWLEDGE_SEARCH_DATABASE_PASSWORD="$(openssl rand -hex 32)"

docker compose -f knowledge-compose.yml up -d --build --wait
curl -fsS http://127.0.0.1:8081/health/ready
```

启动完成后，浏览器访问 **http://127.0.0.1:8081/**，切到「访问密钥」粘贴 `KNOWLEDGE_API_TOKEN` 即可进入工作台（也可直接注册普通账号并由管理员分配工作区）。

### 2. 接入真实大模型（可选：本机 Ollama / 通义千问）

默认使用无密钥本地模式（方便开箱验通链路）。如需接入真实大模型（如本地 Ollama 的 `qwen3:1.7b` 或 DashScope Qwen API），复制并修改配置后重启 `index` 容器即可：

```bash
cp .env.knowledge.ollama.example .env.knowledge
# 按需修改 DASHSCOPE_BASE_URL / DASHSCOPE_MODEL_NAME / DASHSCOPE_API_KEY
KNOWLEDGE_PYTHON_ENV_FILE=.env.knowledge \
  docker compose -f knowledge-compose.yml up -d --no-deps --force-recreate index
```

### 3. 启动高可用集群拓扑（可选）

叠加 Patroni + etcd + 双 HAProxy + 3 节点 Kafka KRaft + Redis Sentinel：

```bash
export KNOWLEDGE_HA_REPLICATION_PASSWORD="ChangeMe_Repl_Password_123!"
docker compose -f knowledge-compose.yml -f knowledge-ha-compose.yml \
  -p enterprise-knowledge-ha up -d --build --wait
```

---

## 项目结构

```text
enterprise-support-agent-v2/
├── knowledge-backend/          # Spring Boot 3 控制面（Flyway V1-V8 / JUnit 5）
│   ├── src/main/java/com/enterprisesupport/knowledge/
│   │   ├── controller/         # REST API 与 SSE 流式控制器
│   │   ├── service/            # Outbox/Kafka、Redis Lua、PDF 外置存储、并发护栏
│   │   ├── repository/         # PostgreSQL JdbcClient 数据访问与事务状态机
│   │   ├── security/           # Bearer / HttpOnly Cookie 认证过滤器与 RBAC
│   │   └── config/             # 强类型配置与启动期校验
│   └── ha/                     # Patroni / HAProxy / Redis Sentinel 高可用配置
├── knowledge-frontend/         # Vue 3 + Element Plus + Vite 6 极简故障诊断工作台
├── src/                        # Python AI 数据面（内网检索、索引与 Agentic RAG 引擎）
│   ├── api/                    # 内网 FastAPI 入口（knowledge_index.py，承接索引/检索/SSE 流式接口）
│   ├── agents/                 # LangGraph Agentic RAG 状态机（分类、改写、重检、引用校验与拒答）
│   ├── rag/                    # PDF 解析、语义切分、BM25+Dense 混合检索、RRF 融合与逐分块证据检查
│   ├── infrastructure/         # pgvector HNSW 迭代扫描、PG FTS、墓碑表、Qwen/Ollama LLM 与 Embedding 适配
│   └── settings/               # 检索、模型与运行时强类型配置
├── eval/                       # 357 题拒答对比评测集与 48 并发压测脚本
├── tests/                      # 单元、集群、Kafka、Redis、生命周期与 HA 停机演练测试
├── knowledge-compose.yml       # 标准多副本部署编排
└── knowledge-ha-compose.yml    # 全栈高可用集群叠加编排
```

---

## 验证与测试

```bash
# Python 单元测试与静态检查
uv run pytest -q
uv run ruff check src main.py scripts eval
uv run mypy src main.py

# Java 控制面 JUnit 5 单元测试
mvn -f knowledge-backend/pom.xml test

# 357 题本地评测
uv run python eval/run_local_evaluation.py
```

---

## 详细文档与许可

- [系统架构说明](docs/architecture.md) · [故障诊断演示](docs/support-demo.md) · [评测与复现](docs/evaluation.md) · [故障排查](docs/troubleshooting.md) · [安全策略](SECURITY.md)
- 本项目采用 [MIT License](LICENSE)，上游来源与第三方许可声明见 [NOTICE](NOTICE)。
