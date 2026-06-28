# 2026-06-28 MinerU 与 SciVerse 接入规划

## 本阶段结论

MinerU 和 SciVerse 的定位不同：

- MinerU 是解析增强器，适合复杂 PDF、图片、扫描件、Office 文件、表格、公式、多栏论文等“入库前处理”。
- SciVerse 是科研文献证据源，适合论文检索、元数据搜索、可引用文献片段和全文证据补充，不适合替代内部 Word/PDF/PPT 的通用解析。

因此本阶段采用：

- MinerU：作为后台可选预解析器，默认关闭。启用后复杂文件会先解析成 Markdown，再交给 RAGFlow 做 chunk、Embedding、索引与检索。
- SciVerse：增加后端配置和客户端骨架，默认关闭。后续用于“论文/文献型知识库”的外部证据增强。

## 依据

MinerU 文档说明精准解析 API 支持 PDF、图片、Doc/Docx、PPT/PPTx、Xls/Xlsx，输出 zip，其中包含 Markdown、JSON，并可额外导出 docx/html/latex；它适合复杂版式、多模态内容、表格、公式、扫描件。官方接口是异步提交、轮询结果。来源：https://mineru.net/apiManage/docs

SciVerse 文档说明它主要面向科学 AI workflow，提供文献检索与元数据搜索，`agentic-search` 返回可引用 paper chunks，`meta-search` 支持字段过滤、排序、分面和新鲜度加权，content/resource API 可读取全文和附件。来源：https://sciverse.space/docs

RAGFlow AI Search 本身是关键词相似度与向量相似度加权的 hybrid search。当前系统继续保留 RAGFlow 作为核心 RAG 引擎。来源：https://ragflow.io/docs/ai_search

## 已实现内容

### 1. 后台配置

新增后端环境变量：

```env
MINERU_ENABLED=false
MINERU_BASE_URL=https://mineru.net
MINERU_API_KEY=
MINERU_MODEL_VERSION=vlm
MINERU_LANGUAGE=ch
MINERU_TIMEOUT_SECONDS=600
MINERU_POLL_INTERVAL_SECONDS=5
MINERU_MAX_FILE_SIZE_MB=200

SCIVERSE_ENABLED=false
SCIVERSE_BASE_URL=https://sciverse.space
SCIVERSE_API_KEY=
```

这些配置只在后端读取，不暴露给前端用户。

### 2. MinerU 客户端

新增：

```text
backend/app/services/mineru.py
```

当前使用 MinerU 精准解析 API 的本地文件批量上传流程：

1. 调用 `/api/v4/file-urls/batch` 获取 `batch_id` 和签名上传 URL。
2. 后端用 `PUT` 上传文件 bytes。
3. 轮询 `/api/v4/extract-results/batch/{batch_id}`。
4. 下载结果 zip。
5. 提取 `full.md`。
6. 把 Markdown 交给 RAGFlow。

安全处理：

- 不把 MinerU token 暴露给前端。
- 不把 MinerU result zip URL 写入数据库，避免暴露全文解析包。

### 3. 解析引擎路由

新增：

```text
backend/app/services/parser_router.py
```

路由规则：

- 默认：继续 RAGFlow DeepDoc/当前规范化链路。
- `MINERU_ENABLED=false` 或未配置 token：不改变现有行为。
- 配置 MinerU 后，PDF、图片、扫描件候选、Office 文件进入 MinerU 精准解析。
- MinerU 失败时回退当前 RAGFlow 链路，并在 metadata 中记录 `parser_warning`。

### 4. 解析质量评分

新增：

```text
backend/app/services/document_quality.py
```

当前评分维度：

- 文件大小
- 文件类型
- 是否图片/扫描件候选
- 是否 PDF 版式风险
- 是否表格结构风险
- 文本乱码占比
- 解析文本长度
- 表格行完整性

输出写入 `Document.metadata`：

- `raw_quality`
- `parsed_quality`
- `parser_engine`
- `parser_decision`

前端/接口现在能拿到：

- `parser_engine`
- `parse_quality_score`
- `parse_quality_warnings`

集合质量接口新增：

- `avg_parse_quality_score`
- `parser_engine_counts`

## 当前链路

```text
上传文件
-> 保存原文到管理系统 MinIO
-> 解析引擎路由
   -> MinerU 精准解析，输出 Markdown
   -> 或 RAGFlow 原生 DeepDoc / 本地 Markdown shadow 规范化
-> RAGFlow documents API
-> 按文件类型设置 chunk_method
-> RAGFlow parse/chunk/embedding/index
-> 同步 RAGFlow 状态
-> 文档状态展示解析质量、chunk 数、token 数、进度
```

## SciVerse 是否应该加入

应该加入，但不要作为通用文件解析器。

适合加入的场景：

- 用户上传论文题目、DOI、关键词，需要补充外部论文证据。
- 内部知识库回答需要关联最新文献或论文元数据。
- 垂直领域研究型知识库需要“内部资料 + 外部文献”双证据。

不适合的场景：

- 内部 Word/PDF/PPT 的入库解析。
- 公司私有资料的全文处理。
- 替代 RAGFlow/MinerU 的文档结构化能力。

建议下一步把 SciVerse 接入为“外部文献证据源”：

```text
用户问题
-> 意图识别：是否需要外部论文证据
-> 内部 RAGFlow retrieval
-> SciVerse literature retrieval
-> 证据归并与 rerank
-> 答案生成，明确区分“内部资料证据”和“外部论文证据”
```

## 下一步增强召回与检索

1. Query decomposition：把复杂问题拆成多个子查询。
2. 多路召回：原问题、改写问题、关键词查询、文档标题/摘要索引并行召回。
3. Reranker：配置 RAGFlow reranker model 后，对候选 chunk 做二阶段排序。
4. 多层索引：文档摘要、章节摘要、chunk 证据三层。
5. 证据充足性判断：无明确证据时拒答，不允许模型补常识。
6. 评测闭环：每个领域建立标准问题、标准答案、必须引用、禁止幻觉项。

## 验证

- 后端编译：通过。
- 容器内测试：`24 passed`。
- 前端构建：通过。
- Docker API/Frontend 重建并启动：通过。
- API health：`{"status":"ok"}`。
- 前端：`http://localhost:14070` 返回 200。
