# RAG 知识库管理系统 — 深度分析与 Docker 部署指南

> 分析时间: 2026-06-28 17:11 | 系统版本: 0.2.0

---

## 一、系统架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          用户浏览器                                      │
│                     http://localhost:14070                              │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────────────┐
│                        管理系统 (rag_default 网络)                       │
│                                                                         │
│  ┌──────────────┐    ┌──────────────────┐    ┌──────────────────────┐   │
│  │  rag-frontend │───▶│    rag-api       │───▶│   rag-postgres       │   │
│  │  Next.js 15   │    │  FastAPI 0.115   │    │  PostgreSQL 16       │   │
│  │  port 14070   │    │  port 8010       │    │  用户/知识库/文档    │   │
│  └──────────────┘    └───────┬──────────┘    └──────────────────────┘   │
│                              │                                          │
│              ┌───────────────┼───────────────┐                          │
│              ▼               ▼               ▼                          │
│  ┌─────────────────┐ ┌────────────┐ ┌──────────────────┐               │
│  │  rag-redis       │ │ rag-minio  │ │  LLM (外部)      │               │
│  │  Redis 7         │ │ MinIO      │ │  阿里云百炼      │               │
│  │  消息/缓存       │ │ 对象存储   │ │  qwen3.7-plus    │               │
│  └─────────────────┘ └────────────┘ └──────────────────┘               │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  rag-api 同时连接 ragflow 网络 → 直接访问 RAGFlow 服务           │   │
│  │  不再需要 host.docker.internal 中转                             │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────┬──────────────────────────────────────┘
                                  │
      ┌───────────────────────────┼───────────────────────────┐
      │    docker_ragflow 网络    │   (外部网络)               │
      │                           │                            │
      ▼                           ▼                            ▼
┌──────────────┐     ┌──────────────────┐     ┌────────────────────┐
│ ragflow-gpu  │────▶│   docker-es01    │     │  docker-mysql      │
│ RAGFlow      │     │  Elasticsearch   │     │  MySQL 8.0         │
│ v0.26.1      │     │  8.11.3          │     │  RAGFlow 元数据    │
│ port 19380   │     │  port 1200       │     │  port 13306        │
└──────┬───────┘     └──────────────────┘     └────────────────────┘
       │
       ▼
┌──────────────────┐     ┌────────────────────┐
│  docker-minio    │     │  docker-redis      │
│  MinIO           │     │  Valkey 8          │
│  文档缓存        │     │  RAGFlow 缓存      │
│  port 19000      │     │  port 6379         │
└──────────────────┘     └────────────────────┘
```

---

## 二、项目源码结构

```
RAG/
├── .env                          # 环境变量配置（核心）
├── docker-compose.yml            # Docker Compose 编排
├── backend/                      # Python FastAPI 后端
│   ├── Dockerfile                # 后端容器构建
│   ├── requirements.txt          # Python 依赖
│   ├── app/
│   │   ├── main.py               # 应用入口（FastAPI lifespan + CORS）
│   │   ├── core/
│   │   │   ├── config.py         # 配置中心（所有环境变量定义）
│   │   │   ├── db.py             # 数据库初始化
│   │   │   └── security.py       # JWT 认证
│   │   ├── api/
│   │   │   ├── routes.py         # 所有 REST API 路由
│   │   │   ├── schemas.py        # Pydantic 请求/响应模型
│   │   │   └── deps.py           # 依赖注入
│   │   ├── models/
│   │   │   └── entities.py       # SQLAlchemy 实体模型
│   │   ├── rag/                  # 自研 RAG 管线（legacy）
│   │   │   ├── pipeline.py       # 文档处理管线
│   │   │   ├── chunking.py       # 分块策略
│   │   │   ├── embeddings.py     # Embedding 客户端
│   │   │   ├── retrieval.py      # 混合检索
│   │   │   ├── reranker.py       # 重排序
│   │   │   ├── llm.py            # LLM 客户端
│   │   │   ├── evaluation.py     # RAG 评估
│   │   │   ├── parsers.py        # 文档解析器
│   │   │   ├── vector_store.py   # 向量存储（Qdrant）
│   │   │   ├── normalize.py      # 文本规范化
│   │   │   ├── analysis.py       # 文档分析
│   │   │   └── schemas.py        # 数据模型
│   │   ├── ragflow/              # RAGFlow 集成层（新增）
│   │   │   ├── client.py         # RAGFlow API 客户端
│   │   │   ├── agent.py          # RAGFlow Agent (Canvas DAG)
│   │   │   └── chunk_method.py   # 分块方法映射
│   │   ├── services/             # 业务服务层
│   │   │   ├── chat.py           # 问答服务
│   │   │   ├── storage.py        # 对象存储服务
│   │   │   ├── batch_ingest.py   # 批量导入
│   │   │   ├── web_ingest.py     # 网页导入
│   │   │   ├── parser_router.py  # 解析器路由（新增）
│   │   │   ├── document_normalize.py  # 文档规范化（新增）
│   │   │   ├── document_quality.py    # 文档质量评分（新增）
│   │   │   ├── mineru.py         # MinerU 解析集成（新增）
│   │   │   ├── sciverse.py       # SciVerse 学术搜索（新增）
│   │   │   └── ragflow_session.py     # RAGFlow 会话管理（新增）
│   │   └── workers/              # 后台工作器
│   │       ├── celery_app.py     # Celery 配置（legacy）
│   │       ├── tasks.py          # Celery 任务（legacy）
│   │       ├── ragflow_ingestion.py   # RAGFlow 摄入监控（新增）
│   │       └── migrate_ragflow_ids.py # 数据迁移脚本（新增）
│   └── tests/                    # 测试用例
├── frontend/                     # Next.js 前端
│   ├── Dockerfile                # 前端容器构建
│   ├── package.json              # Node 依赖
│   ├── app/
│   │   ├── page.tsx              # 主页面（知识库对话）
│   │   ├── layout.tsx            # 根布局
│   │   ├── api.ts                # API 客户端
│   │   ├── types.ts              # TypeScript 类型
│   │   └── styles.css            # 全局样式
│   └── public/
│       └── rag.png               # Logo
├── docs/                         # 设计文档
│   ├── RAGFlow集成部署说明.md
│   ├── RAGFlow链路与企业级评估.md
│   ├── RAG智能体上下文记忆方案.md
│   ├── ARCHITECTURE_DECISION.md
│   ├── DEPLOYMENT.md
│   └── RAG_SYSTEM_DESIGN.md
├── scripts/                      # 运维脚本
│   ├── start-all.ps1             # 一键启动
│   ├── stop-all.ps1              # 一键停止
│   └── status-all.ps1            # 状态检查
└── evalsets/                     # 评估数据集
    └── lab_weekly_rag_eval.json
```

---

## 三、数据流详解

### 3.1 文档上传 → 解析 → 索引

```
前端上传文件
    │
    ▼
POST /api/upload  (routes.py)
    │
    ▼
_ingest_with_ragflow_or_local()
    │
    ├── [RAGFlow 路径] ──────────────────────────────────────
    │   │
    │   ▼
    │   prepare_for_ragflow()  ← parser_router.py
    │   │   │
    │   │   ├── score_ingest_candidate()  ← 质量评分
    │   │   ├── [MinerU 可选] 调用 MinerU API 精解
    │   │   │   │  失败时降级到 normalize
    │   │   └── normalize_for_ragflow()   ← 格式转换
    │   │       .xlsx/.xls → Markdown 表格
    │   │       .html     → Markdown 正文
    │   │       .pptx     → 逐页文本
    │   │       .csv      → Markdown 表格
    │   │
    │   ▼
    │   RagFlowClient.upload_document(dataset_id, file)
    │   RagFlowClient.parse_documents(dataset_id, [doc_id])
    │       ▶ RAGFlow DeepDOC 解析
    │       ▶ text-embedding-v4 Embedding
    │       ▶ Elasticsearch 索引
    │   start_background_monitor()  ← 后台轮询解析进度
    │
    └── [本地路径] ──────────────────────────────────────────
        (legacy-local-rag profile, 默认不启用)
        _schedule_ingestion()
        ▶ IngestionPipeline.run()
        ▶ Qdrant 存储
```

### 3.2 问答流程

```
前端发起问答 (POST /api/chat)
    │
    ▼
ChatService.ask()
    │
    ├── [RAGFlow 路径] ─────────────────────────────────────
    │   │
    │   ├── [Agent 模式] ragflow_enable_agent=True
    │   │   RagflowAgentClient.execute_agent()
    │   │   ▶ RAGFlow Canvas DAG 执行
    │   │
    │   ├── [Chat Completions 模式] enable_chat_completions=True
    │   │   RagFlowClient.create_session(chat_id)
    │   │   RagFlowClient.chat_completions(session_id, messages)
    │   │   ▶ RAGFlow 内部调用 LLM + 引用
    │   │
    │   └── [Retrieval 模式] (默认)
    │       RagFlowClient.retrieve(dataset_id, query)
    │       ▶ ES 混合检索
    │       ▶ 返回证据 chunks
    │       LLMClient.answer(context, query)
    │       ▶ 阿里云百炼 qwen3.7-plus
    │       ▶ 基于证据生成回答
    │
    └── [本地路径] ──────────────────────────────────────────
        Qdrant 检索 + BM25 关键词 + Reranker
```

---

## 四、Docker 部署架构

### 4.1 当前运行容器（10 个容器）

| 容器名 | 镜像 | 端口映射 | 状态 |
|--------|------|---------|------|
| **管理系统** | | | |
| `rag-api-1` | rag-api (自建) | 8010:8000 | Up 1h |
| `rag-frontend-1` | rag-frontend (自建) | 14070:3000 | Up 2h |
| `rag-postgres-1` | postgres:16 | 5432:5432 | healthy |
| `rag-redis-1` | redis:7 | - | Up 4h |
| `rag-minio-1` | minio/minio | 19100:9000, 19101:9001 | Up 4h |
| **RAGFlow** | | | |
| `docker-ragflow-gpu-1` | infiniflow/ragflow:v0.26.1 | 19380:9380 | Up 4h |
| `docker-es01-1` | elasticsearch:8.11.3 | 1200:9200 | healthy |
| `docker-mysql-1` | mysql:8.0.39 | 13306:3306 | healthy |
| `docker-minio-1` | pgsty/minio | 19000:9000, 19001:9001 | healthy |
| `docker-redis-1` | valkey/valkey:8 | 6379:6379 | healthy |

### 4.2 网络拓扑

```
rag_default 网络 (172.21.0.0/16)
├── rag-api-1 *
├── rag-frontend-1
├── rag-postgres-1
├── rag-redis-1
└── rag-minio-1

docker_ragflow 网络 (172.20.0.0/16)
├── rag-api-1 *  ──── 桥接节点
├── docker-ragflow-gpu-1
├── docker-es01-1
├── docker-mysql-1
├── docker-minio-1
└── docker-redis-1

* rag-api-1 同时连接两个网络
```

### 4.3 端口映射

| 外部端口 | 内部端口 | 服务 | 说明 |
|---------|---------|------|------|
| 8010 | 8000 | 管理 API | FastAPI 后端 |
| 14070 | 3000 | 管理前端 | Next.js 控制台 |
| 5432 | 5432 | 管理 PostgreSQL | 元数据存储 |
| 19100 | 9000 | 管理 MinIO API | 对象存储 |
| 19101 | 9001 | 管理 MinIO Console | 管理界面 |
| 19380 | 9380 | RAGFlow API | RAG 引擎 API |
| 18080 | 80 | RAGFlow Web | RAGFlow 管理界面 |
| 1200 | 9200 | Elasticsearch | 向量+全文检索 |
| 13306 | 3306 | RAGFlow MySQL | 内部状态 |
| 19000 | 9000 | RAGFlow MinIO | 文件缓存 |
| 6379 | 6379 | RAGFlow Valkey | 缓存 |

---

## 五、如何更新代码并推送到 Docker

### 完整工作流（三步）

#### 步骤 1：修改代码

在项目目录下修改源码：
```powershell
cd D:\Researching\LLMStart\RAG
# 修改 backend/app/ 下的 Python 文件
# 或修改 frontend/app/ 下的前端代码
```

#### 步骤 2：重建并重启具体容器

**选项 A：仅重启 API（最快，推荐开发阶段）**
```powershell
docker compose up -d --build api
```
- 重新构建 `rag-api` 镜像
- 重启 API 容器
- frontend、postgres、redis、minio 不受影响

**选项 B：重启前端**
```powershell
docker compose up -d --build frontend
```
- 重新构建 `rag-frontend` 镜像
- 重启前端容器

**选项 C：重启所有管理系统**
```powershell
.\scripts\start-all.ps1
```
- 执行 `start-all.ps1` 脚本
- 先启动 RAGFlow（如果未运行）
- 移除 legacy 容器
- 重建并启动所有管理服务

**选项 D：逐容器单独重建（最精确）**
```powershell
docker compose build api                # 仅构建（不重启）
docker compose up -d api                # 仅重启（用已有镜像）
docker compose up -d --build api        # 构建+重启一步完成
```

#### 步骤 3：验证运行状态

```powershell
# 检查容器状态
docker compose ps

# 检查日志
docker compose logs api --tail 30
docker compose logs frontend --tail 30

# 检查 API 健康
curl http://localhost:8010/api/health

# 检查前端
curl -o /dev/null -s -w "%{http_code}" http://localhost:14070

# 检查 RAGFlow 连通性
curl http://localhost:19380/api/v1/system/status
```

### 后端代码变更后的典型操作

```
修改了 Python 代码 (routes.py, services/chat.py 等)
    │
    ▼
docker compose up -d --build api
    │
    ▼
等待 API 启动（约 5-10 秒）
    │
    ▼
curl http://localhost:8010/api/health
    → {"status":"ok"}
    │
    ▼
打开浏览器 http://localhost:14070 验证功能
```

### 前端代码变更后的典型操作

```
修改了前端代码 (page.tsx, styles.css 等)
    │
    ▼
docker compose up -d --build frontend
    │
    ▼
等待前端编译完成（约 10-20 秒）
    │
    ▼
刷新浏览器 http://localhost:14070 验证
```

### 依赖变更（requirements.txt 更新后）

```
更新了 requirements.txt (新增/升级 Python 包)
    │
    ▼
docker compose build api --no-cache     # 强制无缓存构建
docker compose up -d api                # 启动新容器
```

### 环境变量变更（.env 更新后）

```
修改了 .env 文件
    │
    ▼
docker compose down api                 # 停止旧容器
docker compose up -d api                # 自动加载新 .env
# 注: docker compose down 会删除容器但保留卷
```

---

## 六、完整启动流程（从零开始）

### 首次部署

```powershell
# 1. 启动 RAGFlow 引擎
cd D:\Researching\LLMStart\RAG\external-repos\ragflow\docker
docker compose --profile elasticsearch --profile gpu up -d

# 2. 等待 RAGFlow 就绪（约 1-2 分钟，视硬件而定）
#    确保 MySQL/ES/Redis/MinIO 全部 healthy

# 3. 启动管理系统
cd D:\Researching\LLMStart\RAG
docker compose up -d --build postgres redis minio api frontend

# 4. 验证所有服务
docker compose ps
curl http://localhost:8010/api/health
```

### 日常启动（已部署过）

```powershell
cd D:\Researching\LLMStart\RAG
.\scripts\start-all.ps1
```

### 日常停止

```powershell
cd D:\Researching\LLMStart\RAG
.\scripts\stop-all.ps1
```

---

## 七、关键配置说明

`.env` 文件中最重要的配置项：

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `RAGFLOW_ENABLED` | `true` | 启用 RAGFlow 集成 |
| `RAGFLOW_BASE_URL` | `http://ragflow-gpu:9380/api/v1` | RAGFlow API 地址 |
| `RAGFLOW_ENABLE_CHAT_COMPLETIONS` | `true` | 使用 RAGFlow chat 接口 |
| `MINERU_ENABLED` | `true` | 启用 MinerU 精解 |
| `SCIVERSE_ENABLED` | `true` | 启用 SciVerse 学术搜索 |
| `LLM_BASE_URL` | `https://dashscope.aliyuncs.com/...` | 阿里云百炼 API |
| `LLM_MODEL` | `qwen3.7-plus` | 通义千问大模型 |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:14070,...` | 前端跨域来源 |

---

## 八、常见问题

### Q: API 容器启动后立即退出？
A: 查看日志定位问题：`docker compose logs api`
常见原因：PostgreSQL 未就绪、.env 配置错误、依赖缺失

### Q: 前端白屏或 API 请求失败？
A: 确认 CORS 配置包含前端地址：`.env` 中 `CORS_ALLOWED_ORIGINS`
确认前端 `NEXT_PUBLIC_API_BASE_URL` 指向正确的 API 地址

### Q: RAGFlow 文档解析一直 pending？
A: 检查 RAGFlow 日志：`docker logs docker-ragflow-gpu-1 --tail 30`
首次解析可能需要等待 Tika/torch 初始化

### Q: 如何彻底清理重建？
A: 
```powershell
docker compose down -v     # 删除容器+卷（数据会丢失！）
docker compose up -d --build
```
