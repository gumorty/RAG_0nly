# Enterprise RAG Knowledge Base

这是一个面向企业资料、项目文档、会议纪要、制度文件和网页资料的 RAG 知识库管理系统。当前主链路是“管理平台 + RAGFlow 引擎”：

- 管理平台负责账号、权限、知识库、文档状态、会话、模型路由、审计和前端交互。
- RAGFlow 负责 DeepDoc 解析、chunk、Embedding、索引和混合检索。
- 最终回答由管理平台基于 RAGFlow 返回的证据调用当前启用的 Chat 模型生成。

## 架构

```text
Browser
-> Frontend / Next.js
-> Management API / FastAPI
-> RAGFlow API
-> DeepDoc or optional parser / Embedding / Elasticsearch
-> Chat model
-> Answer with citations and retrieval trace
```

核心组件：

- Frontend: Next.js
- Backend: Python + FastAPI + SQLAlchemy
- Auth: JWT access token + refresh token
- Management DB: PostgreSQL
- Cache: Redis
- Object Storage: MinIO
- RAG Engine: RAGFlow
- RAGFlow Storage: Elasticsearch / MySQL / MinIO / Redis

## 快速启动

推荐使用脚本启动两套 compose：

```powershell
cd D:\Researching\LLMStart\RAG
powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1
```

查看状态：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\status-all.ps1
```

停止服务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop-all.ps1
```

## 访问地址

- 管理系统前端: http://localhost:14070
- 管理系统 API: http://localhost:8010/api/health
- 管理系统 MinIO API: http://localhost:19100
- 管理系统 MinIO Console: http://localhost:19101
- RAGFlow Web: http://localhost:18080
- RAGFlow API: http://localhost:19380/api/v1

默认本地管理员：

- Email: `admin@example.com`
- Password: `Admin@123456`

生产环境部署前必须替换 `.env` 中的密钥、管理员密码、MinIO 密码、RAGFlow API Key 和模型 API Key。

## Docker 说明

Docker Desktop 中会看到两组容器：

- `rag-*`: 本管理系统，包括 `frontend`、`api`、`postgres`、`redis`、`minio`。
- `docker-*`: RAGFlow 官方 compose 栈，包括 `ragflow-gpu`、`mysql`、`es01`、`minio`、`redis`。

两组容器通过 Docker 网络 `docker_ragflow` 互通。管理平台容器内访问 RAGFlow 使用：

```text
http://ragflow-gpu:9380/api/v1
```

旧的 Qdrant + worker 自研 RAG 链路已放入 `legacy-local-rag` profile，不是默认主链路。

## 当前验证

- API health: `{"status":"ok"}`
- 前端 `http://localhost:14070` 返回 200
- CORS 已允许 `http://localhost:14070`
- 后端测试：`20 passed`
- 前端构建：`npm run build` 通过

更多细节见：

- [Docker 启动与解析检索规划](docs/2026-06-28-Docker启动与解析检索规划.md)
- [RAGFlow 集成部署说明](docs/RAGFlow集成部署说明.md)
- [RAGFlow 链路与企业级评估](docs/RAGFlow链路与企业级评估.md)
