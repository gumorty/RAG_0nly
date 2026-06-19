# Python 与企业级 RAG 架构取舍

## 当前结论

当前阶段采用 Python/FastAPI 作为主后端更合适，原因是：

- RAG 算法生态更成熟：embedding、reranker、OCR、表格解析、评估框架、LangChain/LlamaIndex 等都更容易接入和迭代。
- 策略实验变化快：解析、清洗、分块、检索、重排和证据评分都需要快速调整，Python 更适合作为主实现语言。
- Docker 落地清晰：FastAPI + Celery + PostgreSQL + Redis + Qdrant + MinIO 可以在单机或企业服务器上稳定部署。
- 大模型路由独立：底层模型通过 OpenAI-compatible 接口动态配置，避免把某一家模型供应商写死。

Java/Spring Boot 仍然可以作为未来企业集成层选项，例如接入已有网关、权限系统、审计平台或内部微服务，但当前项目主线以 Python 为准。

## 当前架构

- Python/FastAPI API
- Celery worker
- PostgreSQL 存储集合、文档、chunk、回答、知识缺口、模型配置和审计日志
- Redis 作为任务队列和结果后端
- Qdrant 作为向量存储
- MinIO 作为原始文件存储
- Hash embedding + sparse keyword 混合检索作为本地可运行兜底
- OpenAI-compatible/百炼/DeepSeek 等第三方大模型动态路由
- JWT 双 token 登录验证

## 推荐演进

第一阶段，也就是当前版本：

- 稳定 Docker Compose 启动
- 保证登录、导入、索引、检索、问答、摘要、缺口沉淀可闭环
- 保留 mock 模型兜底，真实模型通过模型路由激活

第二阶段：

- 接入 bge-m3 或企业 embedding 服务
- 接入 bge-reranker 或第三方 rerank API
- 增加离线评估集和策略对比
- 加强文档摘要的来源约束和引用展示

第三阶段：

- 企业 SSO/OAuth
- 租户隔离
- 文档 ACL
- 敏感信息脱敏
- Notion、飞书、Git、网盘、数据库连接器
