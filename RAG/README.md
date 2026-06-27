# Enterprise RAG Knowledge Base

这是一个面向企业资料、项目文档、会议纪要、制度文件和网页资料的 RAG 知识库管理系统。当前主链路已经切换为“管理平台 + RAGFlow 引擎”：管理平台负责账号、权限、知识库、模型路由、审计和前端交互；RAGFlow 负责 DeepDOC 解析、切块、Embedding、索引和检索。

## 架构

- Frontend: Next.js，对话优先的知识库工作台
- Backend: Python + FastAPI + SQLAlchemy
- Auth: JWT access token + refresh token
- Management DB: PostgreSQL
- Cache: Redis
- Object Storage: MinIO
- RAG Engine: RAGFlow + DeepDOC
- RAGFlow Storage: Elasticsearch / MinIO / MySQL / Redis

主链路：

```text
浏览器前端 -> 管理平台 API -> RAGFlow API -> DeepDOC / Embedding / Elasticsearch -> 管理平台调用 Chat 模型生成回答
```

## 快速启动

先启动 RAGFlow，再启动本管理系统。

```powershell
cd D:\Researching\LLMStart\RAG\external-repos\ragflow\docker
docker compose --profile elasticsearch --profile gpu up -d

cd D:\Researching\LLMStart\RAG
docker compose up --build -d
```

访问地址：

- 管理系统前端: http://localhost:4070
- 管理系统 API: http://localhost:8010/api/health
- RAGFlow Web: http://localhost:18080
- RAGFlow API: http://localhost:19380
- 管理系统 MinIO API: http://localhost:9100
- 管理系统 MinIO Console: http://localhost:9101

默认本地管理员：

- Email: `admin@example.com`
- Password: `Admin@123456`

生产环境部署前必须替换 `APP_SECRET`、`BOOTSTRAP_ADMIN_API_KEY`、`BOOTSTRAP_ADMIN_PASSWORD`、MinIO 密码、RAGFlow API Key 和模型 API Key。

## 模型配置

前端左侧“模型路由”用于管理 Chat 生成模型。当前推荐的阿里云百炼 OpenAI-compatible 配置：

- Provider: `openai_compatible`
- Chat model: `qwen3.7-plus`
- Base URL: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- RAGFlow 内部 Embedding model: `text-embedding-v4`

浏览器前端只使用 JWT，不再暴露 `NEXT_PUBLIC_API_KEY`。Admin API Key 只能用于服务间调用或运维脚本。

## RAGFlow 处理链路

1. 在管理平台创建知识库。
2. 后端在 RAGFlow 中创建或复用 dataset，并把 `ragflow_dataset_id` 写入本地知识库元数据。
3. 本机文件、网页、ZIP 批量资料进入统一导入入口。
4. 必要时把 Excel / CSV / HTML / PPTX 等格式标准化为 RAGFlow 友好的内容。
5. RAGFlow 执行 DeepDOC 解析、分块、Embedding 和索引写入。
6. 后端同步 RAGFlow 文档状态和解析进度，并在前端展示“已解析完成、生成 N 个分块、写入索引”等状态。
7. 问答时优先调用 RAGFlow retrieval 获取证据。
8. 后端按本地文档 ACL 过滤 RAGFlow 返回的 chunks，再调用当前启用的 Chat 模型。
9. 回答、引用、证据分、反馈和 retrieval trace 会落库，供后续复盘和评估使用。

## Docker 项目说明

Docker Desktop 中会看到两组容器：

- `rag-*`: 本管理系统，包括 `frontend`、`api`、`postgres`、`redis`、`minio`。
- `docker-*`: RAGFlow 官方 compose 栈，包括 `ragflow-gpu`、`mysql`、`es01`、`minio`、`redis`。

两组容器通过 Docker 网络 `docker_ragflow` 互通。管理平台容器内访问 RAGFlow 使用：

```text
http://ragflow-gpu:9380/api/v1
```

旧的 Qdrant + worker 自研 RAG 链路已经放入 `legacy-local-rag` profile，不是默认主链路。

## 验证

当前已完成的验证：

- `python -m compileall backend\app`
- `npm run build`
- `docker compose config`
- API health: `http://localhost:8010/api/health`
- RAGFlow health: database / doc_engine / redis / storage 均为 green
- 容器内测试: `15 passed`
- 浏览器烟测: `http://localhost:4070` 可打开，中文无乱码，控制台无 error/warn

## 已知限制

- 表格型 PDF 的部分引用片段仍可能显示为 HTML table 片段，后续需要做表格引用的结构化可读渲染。
- 当前已经做了文档 ACL 的后置过滤，但更理想的企业级方案是在 RAGFlow 检索层支持 metadata filter 或按用户权限拆分 dataset。
- 长期对话记忆仍需引入 `chat_sessions`、`chat_messages`、session summary 和 confirmed memory store。
- 生产环境建议关闭公开注册，并接入企业 SSO / OAuth / 邀请码注册。

更多细节见 [docs/RAGFlow集成部署说明.md](docs/RAGFlow集成部署说明.md) 和 [docs/当前问题修复与启动记录.md](docs/当前问题修复与启动记录.md)。
