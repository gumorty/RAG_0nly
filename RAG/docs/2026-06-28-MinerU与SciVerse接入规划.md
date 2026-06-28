# 2026-06-28 MinerU 与 SciVerse 接入记录

## 当前结论

MinerU 和 SciVerse 的定位不同：

- MinerU 是入库前解析增强器，适合复杂 PDF、扫描件、图片、Office、表格、公式、多栏论文等资料。启用后，复杂文档先由 MinerU 解析为 Markdown，再交给 RAGFlow 做分块、Embedding、索引和检索。
- SciVerse 是科研文献证据源，适合论文检索、元数据搜索、可引用论文片段和外部文献补证。它不替代内部 Word/PDF/PPT 的解析，也不默认参与普通企业资料入库。

系统仍然以 RAGFlow 作为核心 RAG 引擎：RAGFlow 负责文档入库、切块、Embedding、索引、混合检索；管理系统负责用户隔离、模型配置、解析路由、检索增强、证据治理和前端体验。

## 后端配置

配置只在后端 `.env` 生效，不进入前端，也不要提交真实密钥。

```env
MINERU_ENABLED=true
MINERU_BASE_URL=https://mineru.net
MINERU_API_KEY=
MINERU_MODEL_VERSION=vlm
MINERU_LANGUAGE=ch
MINERU_TIMEOUT_SECONDS=600
MINERU_POLL_INTERVAL_SECONDS=5
MINERU_MAX_FILE_SIZE_MB=200

SCIVERSE_ENABLED=true
SCIVERSE_BASE_URL=https://api.sciverse.space
SCIVERSE_API_KEY=
```

## 已实现内容

1. `backend/app/services/mineru.py`
   实现 MinerU 精准解析 API：申请上传 URL、上传文件、轮询结果、下载结果 zip、提取 `full.md`。

2. `backend/app/services/parser_router.py`
   对 PDF、图片、扫描件候选、Office 文件启用 MinerU 预解析。MinerU 失败时回退到 RAGFlow 原链路，并在文档 metadata 中记录 `parser_warning`。

3. `backend/app/services/document_quality.py`
   增加解析质量评分，输出 `raw_quality`、`parsed_quality`、`parser_engine`、`parser_decision`。

4. `backend/app/services/sciverse.py`
   增加 SciVerse 后端 connector。当前验证通过的是 `POST /agentic-search`，用于后续论文/科研资料外部证据增强。

5. 用户体系
   登录注册改为用户名 + 密码；数据库仍兼容旧结构，使用原 `User.email` 字段存储登录名，同时 API 返回 `username`。

6. 用户隔离
   新建知识库会写入 `owner_id` 和 `acl`。普通成员只能看到、上传、删除、对话和查看自己的知识库；管理员和维护者仍能管理全局。

7. 检索增强
   RAGFlow retrieval 前增加多查询规划：原问题、改写问题、进展/风险/下一步等意图子查询会合并召回；结果按 chunk 去重并保留最高分，再交给证据约束回答。

## 实测结果

- MinerU 真实解析：使用 `docs/W020200908376466568529.pdf`，返回 `state=done`，生成 Markdown 约 11356 字符。
- SciVerse 真实调用：`POST https://api.sciverse.space/agentic-search` 返回成功，包含 `hits`。
- 后端测试：`27 passed`。
- 后端编译：通过。
- 前端构建：通过。
- Docker：`api`、`frontend`、`postgres`、`redis`、`minio` 正常运行。
- API health：`{"status":"ok"}`。
- 前端地址：`http://localhost:14070` 返回 200。

## 当前链路

```text
用户上传资料
-> 管理系统保存原文件到 MinIO
-> 解析路由判断
   -> 复杂 PDF/图片/扫描件/Office: MinerU -> Markdown
   -> 普通文件或 MinerU 失败: RAGFlow 原生解析
-> RAGFlow documents API
-> 按文件类型设置 chunk_method
-> RAGFlow parse/chunk/embedding/index
-> 管理系统同步解析状态、chunk 数、token 数、解析质量
-> 用户在知识库会话中提问
-> 多查询规划
-> RAGFlow 混合检索 + 可选 rerank
-> 证据去重、聚焦、引用展示
-> LLM 严格基于证据回答
```

## SciVerse 使用边界

适合加入的场景：

- 用户上传论文题目、DOI、关键词，需要补充外部论文证据。
- 内部知识库回答需要关联最新文献或论文元数据。
- 垂直科研领域需要“内部资料 + 外部论文”双证据。

不适合的场景：

- 替代内部 Word/PDF/PPT 解析。
- 处理公司私有资料全文。
- 替代 RAGFlow 或 MinerU 的文档结构化能力。

下一步建议把 SciVerse 做成“外部文献证据源”，只在问题意图涉及论文、研究进展、外部文献时触发，并在答案中明确区分内部资料证据和外部文献证据。
