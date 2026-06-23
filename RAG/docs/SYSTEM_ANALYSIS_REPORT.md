# RAG 系统全面分析报告

> 分析时间：2026-06-21
> 分析范围：Docker 运行状态、数据库数据、服务日志、代码架构、设计文档

---

## 一、Docker 服务运行状态

| 服务 | 镜像 | 状态 | 端口映射 | 运行时长 |
|-----|------|:----:|---------|---------|
| PostgreSQL | postgres:16 | healthy | 0.0.0.0:5432 | 41小时 |
| Redis | redis:7 | 正常 | 6379（未暴露到宿主机） | 41小时 |
| Qdrant | qdrant:v1.12.5 | 正常 | 6333-6334（未暴露到宿主机） | 33分钟（重启过） |
| MinIO | minio | 正常 | 0.0.0.0:9100/9101 | 3小时（重启过） |
| API | rag-api (FastAPI) | 正常 | 0.0.0.0:8010 | 1小时（重启过） |
| Worker | rag-worker (Celery) | 正常 | 无 | 33分钟（重启过） |
| Frontend | rag-frontend (Next.js) | 正常 | 0.0.0.0:4070 | 1小时（重启过） |

### 基础设施问题

1. **Qdrant 端口未暴露**：`docker-compose.yml` 没有把 6333 映射到宿主机，外部无法直接访问 Qdrant 面板。Docker 内部通信正常，但排障不方便。
2. **Redis 端口未暴露**：6379 未映射到宿主机。
3. **前端端口不一致**：`docker-compose.yml` 写的是 `3010:3000`，但实际运行在 **4070:3000**。可能是容器启动后修改了 compose 文件。
4. **服务重启过**：Qdrant、Worker、API、Frontend 都重启过，而 PostgreSQL 和 Redis 已稳定运行 41 小时。说明基础设施稳定，应用层有过重启。

---

## 二、数据库实际数据

### 核心数据

| 表 | 数量 | 说明 |
|---|:---:|------|
| collections | 3 | 实验室周会知识库、WordFile验收知识库、RAGFlow验收知识库 |
| documents | 7 | 5个有chunk，**2个为0 chunk但状态=ready** |
| chunks | 61 | 实际存储在 PostgreSQL |
| answers | 24 | 全部通过 `qwen3.7-plus via RAGFlow retrieval` 生成 |
| retrieval_traces | 27 | 比 answer 多 3 条（可能有查询未生成回答） |
| eval_cases | **0** | **从未创建过评估用例** |
| import_batches | **0** | **从未使用过 ZIP 批量导入** |
| audit_logs | 81 | 操作审计记录完整 |
| users | 4 | 1个admin + 3个member |
| model_configs | 4 | DeepSeek、Codex、Mock兜底、qwen3.7-plus（当前激活） |
| rag_strategy_presets | 1 | "Hybrid Baseline"（默认策略） |

### 文档详细状态

| 文档 | 集合 | 状态 | 实际 Chunk | 分析 Chunk | 质量告警 |
|-----|------|:----:|:---------:|:---------:|---------|
| 顾建伟6-17周 报.docx | 实验室周会知识库 | ready | 6 | 6 | many_tiny_chunks_possible |
| 1.txt | WordFile验收知识库 | ready | 14 | 14 | many_tiny_chunks_possible |
| 传感器平台 API 文档.docx | WordFile验收知识库 | ready | 8 | 8 | many_tiny_chunks_possible |
| 科技论文-212509020046-顾建伟.pdf | WordFile验收知识库 | ready | 13 | 13 | many_tiny_chunks_possible |
| 科技论文-212509020046-顾建伟.doc | WordFile验收知识库 | ready | 20 | 20 | 无 |
| Engineering_Ethics_Sensor_Fusion_Paper.docx | RAGFlow验收知识库 | ready | **0** | 11 | 无 |
| 顾建伟6-17周 报.docx | RAGFlow验收知识库 | ready | **0** | 12 | 无 |

### 严重数据问题

**RAGFlow 验收知识库中有 2 个文档：状态=ready 但实际 chunk=0。**

分析报告中说分别有 11 和 12 个 chunk，但 PostgreSQL 里一个都没有。这是一个 Pipeline 数据完整性 Bug：`IngestionPipeline` 在 `document.status = ready` 和 `db.commit()` 之前，如果 Qdrant upsert 失败或连接中断，会导致分析记录已写入但 chunk 丢失。

---

## 三、用户使用情况分析

### 操作审计

| 操作 | 次数 | 说明 |
|-----|:---:|------|
| chat.query | 37 | 提问了 37 次（24 条生成了回答） |
| document.reindex | **17** | 重新索引 17 次（异常偏高，说明索引经常出问题） |
| document.upload | 12 | 上传了 12 个文档（现存 7 个，删除过 2 个） |
| collection.create | 7 | 创建过 7 个集合（现存 3 个） |
| model_config.create | 4 | 配置了 4 个模型 |
| model_config.activate | 2 | 切换了 2 次激活模型 |

### 回答质量

| 指标 | 数值 | 评估 |
|-----|------|------|
| 总回答数 | 24 | — |
| 证据分 = 0 | 4 条 | 严重问题，检索完全没找到相关内容 |
| 证据分 < 0.22 | 8 条（33%） | 按设计应该被拒绝回答 |
| 最高证据分 | 0.42 | 仅一篇周报的精准提问达到 |
| 平均证据分 | ~0.25 | 整体偏低 |
| 用户反馈 | 全部为空 | 从未使用过反馈功能 |

### 回答样例分析

最近的提问集中在论文实验问题：

| 问题 | 证据分 | 模型 |
|-----|:------:|------|
| 我这个知识库里面关于我的论文实验这一块遇到了什么问题？ | 0.289 | qwen3.7-plus |
| 对于这些问题我给出的解决方法是什么？ | 0.310 | qwen3.7-plus |
| 是我的论文实验中遇到的问题不是部署平台遇到的问题 | 0.329 | qwen3.7-plus |
| 在顾建伟6-17周 报中他主要完成了什么任务，下周的计划是什么？ | 0.422 | qwen3.7-plus |
| 这个知识库里最近的项目进展是什么？ | 0.000~0.210 | qwen3.7-plus |

证据分偏低的原因：当前使用 hash embedding（不是真实语义向量），语义匹配能力很弱，主要靠 sparse keyword 兜底。

---

## 四、系统架构全景

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Frontend (Next.js :4070)                      │
│  集合管理 · 文档上传 · 质量看板 · 问答对话 · 会议纪要 · 知识缺口 · 策略对比  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ REST API
┌───────────────────────────────┴──────────────────────────────────────┐
│                     API Server (FastAPI :8010)                       │
│  /api/collections  /api/documents  /api/chat  /api/eval  /api/admin  │
├──────────────────────────────────────────────────────────────────────┤
│  Auth (JWT + API Key)  ·  RBAC (admin/maintainer/member/viewer)     │
│  ACL 文档级权限  ·  AuditLog 操作审计  ·  ModelConfig 热切换模型     │
└──────┬──────────┬──────────┬──────────┬─────────────────────────────┘
       │          │          │          │
  ┌────┴───┐ ┌───┴────┐ ┌───┴────┐ ┌───┴────┐
  │PostgreSQL│ │ Redis  │ │ Qdrant │ │ MinIO  │
  │ 主数据库 │ │消息队列│ │向量存储│ │对象存储│
  └──────────┘ └───┬────┘ └────────┘ └────────┘
                   │
           ┌───────┴───────┐
           │ Worker (Celery)│
           │ 异步文档处理    │
           └────────────────┘
```

### 12 张数据表

| 表 | 职责 |
|---|------|
| users | 用户 + RBAC 角色 + JWT 认证 |
| collections | 知识集合 |
| documents | 文档元数据 + 状态机 |
| chunks | 文本块 + 向量 ID + 稀疏词 + 层级路径 |
| import_batches | ZIP 批量导入报告 |
| retrieval_traces | 检索过程完整追踪 |
| rag_strategy_presets | 可配置的检索策略预设 |
| model_configs | LLM 模型热切换配置 |
| answers | 回答 + 引用 + 证据分 + 用户反馈 |
| eval_cases | 检索评估基准用例 |
| audit_logs | 全操作审计日志 |

---

## 五、文档处理链（Ingestion Pipeline）

核心在 `pipeline.py` 的 `IngestionPipeline.run()`，是一个 6 步串行流水线。

### 状态机

```
Document.status:
  uploaded → parsing → indexing → ready
                                  ↘ failed (任何异常)
```

### Step 1 — 接入

三种入口统一汇入 `routes.py`：

| 入口 | 接口 | 特殊处理 |
|-----|------|---------|
| 文件上传 | POST /collections/{id}/documents | SHA-256 去重 |
| URL 导入 | POST /collections/{id}/url-documents | httpx 抓取 + HTML 标题提取 |
| ZIP 批量 | POST /collections/{id}/batch-zip | 过滤 __MACOSX、.DS_Store、隐藏文件 |

所有文件存入 MinIO 对象存储，元数据写入 PostgreSQL。

### Step 2 — 解析

`parsers.py` 按后缀分发解析器：

| 类型 | 解析器 | 提取策略 | 图片处理 |
|-----|-------|---------|:------:|
| PDF | pypdf | 逐页抽取 + [Page N] 标记 | 忽略 |
| Word | python-docx | 段落 + 表格（管道分隔） | 忽略 |
| PPT | python-pptx | 逐 Slide 抽取 shape.text | 忽略 |
| Excel | openpyxl | 按 Sheet 逐行（管道分隔） | N/A |
| HTML | BeautifulSoup + markdownify | 去 script/style → 转 Markdown | 保留 alt |
| 文本类 | chardet 编码探测 | UTF-8 → UTF-8-sig → 自动探测 | N/A |

解析后通过 `_extract_sections()` 按 `#` 标题和冒号结尾标题做章节识别。

### Step 3 — 清洗

`normalize.py` 做 5 项统一处理：

1. CRLF/CR → LF
2. Tab + NBSP → 普通空格
3. 行首行尾空格清理
4. 连续空行压缩为双换行
5. 连续空格压缩为单空格

### Step 4 — 分块

`chunking.py` 的 `HierarchicalChunker`，三级切分策略：

```
Level 1: 按章节（title_path）遍历
Level 2: 按段落（\n\n 分隔）累积 token，超过 600 切块
Level 3: 超长段落按句（。！？.!?）切分
```

- 重叠窗口：overlap_tokens=100
- token 估算：中文 × 0.8 + 英文词 × 1.2 + 标点 × 0.2
- 每个 chunk 携带：原始内容、归一化内容、title_path、token_count、section 元数据

### Step 5 — 质量分析

`analysis.py` 生成分析报告：

- 字符数、token 数、chunk 统计（min/max/avg）
- Top 30 稀疏词
- 6 种质量告警
- 实验周报信号抽取（进展、风险、下一步、决策、负责人）

### Step 6 — 向量化 + 索引

- 稠密向量：通过 `embeddings.py` 生成，写入 `vector_store.py` 的 Qdrant（余弦相似度）
- 稀疏词索引：`tokenize_for_sparse()` 生成中英文 n-gram，存入 PostgreSQL 的 chunks.sparse_terms
- content_hash：每个 chunk 的 SHA-256，用于去重

---

## 六、检索与回答链

### 检索全流程

```
用户提问
  │
  ▼
① 查询改写（LLM rewrite_query）
  │
  ▼
② 稠密向量检索（Qdrant 余弦相似度）→ top-30 + ACL 过滤
  │
  ▼
③ 关键词检索（PostgreSQL sparse_terms 匹配）→ top-30
  │
  ▼
④ RRF 融合（Reciprocal Rank Fusion）→ 1/(60+rank)
  │
  ▼
⑤ 重排（ScoreFusionReranker: 0.65×dense + 0.35×keyword）
  │
  ▼
⑥ 取 final_top_k=8 + 上下文扩展（前后各 1 chunk 拼接）
  │
  ▼
⑦ 证据门槛判断 → max(score) < 0.22 则拒绝回答
  │
  ▼
⑧ LLM 带引用回答 → 存 RetrievalTrace + Answer
```

### 当前 Embedding 配置

| 配置项 | 当前值 | 说明 |
|-------|-------|------|
| EMBEDDING_PROVIDER | hash | 哈希伪向量，非真实语义 |
| EMBEDDING_MODEL | hash-384 | 384维 hash embedding |
| ENABLE_RERANKER | false | CrossEncoder 重排未开启 |

---

## 七、安全与权限模型

### 认证方式

- JWT Bearer Token：access_token（30分钟）+ refresh_token（7天），支持 token_version 强制下线
- API Key：X-API-Key header，SHA-256 存储

### RBAC 角色（4 级）

| 角色 | 权限 |
|-----|------|
| admin | 全部操作 |
| maintainer | 管理文档/策略/评估 |
| member | 上传文档、提问 |
| viewer | 只读 |

### ACL 文档级权限

每个 document 有 acl 列表，检索时按 `_document_allowed()` 过滤。

---

## 八、当前系统优势

1. **完整的治理闭环**：接入→解析→清洗→分块→检索→回答→评估→缺口→再优化
2. **可追溯性强**：每个 chunk 有 title_path、content_hash、ordinal；每次回答有 RetrievalTrace + citations
3. **策略可配置**：RagStrategyPreset 支持多套检索策略预设
4. **混合检索 + RRF 融合**：dense + keyword 互补
5. **模型热切换**：ModelConfig 可以不重启切换 LLM
6. **ACL + RBAC 双层权限**
7. **质量看板**：collection_quality 聚合分析报告

---

## 九、当前系统局限与改进方向

| 局限 | 影响 | 建议改进 |
|-----|------|---------|
| 无 OCR/图表理解 | PDF 扫描件、嵌入图片、图表全部丢失 | 接入 PaddleOCR / 多模态模型 |
| Embedding 精度 | hash embedding 是伪语义 | 切换 BAAI/bge-m3 或 OpenAI embedding |
| 关键词检索线性扫描 | O(n) 遍历全 collection | 接入 BM25 / OpenSearch / pgvector sparse |
| 章节识别过于简单 | 只识别 # 和冒号标题 | 接入 unstructured 库 |
| 无增量索引 | 文档更新需全量重新解析 | 添加文档版本 + diff-based reindex |
| 无敏感数据检测 | PII 直接进入索引 | 添加 PII 检测 + 脱敏 pipeline |
| token 估算是启发式 | 中文×0.8 粗略估算 | 接入 tiktoken |
| Reranker 默认关闭 | 线性加权不如 CrossEncoder | 开启 BAAI/bge-reranker-v2-m3 |
| Pipeline chunk 丢失 | 2 个文档 0 chunk 但 ready | 修复 IngestionPipeline 回滚逻辑 |

---

## 十、项目阶段判定

根据 ARCHITECTURE_DECISION.md 的三阶段规划：

| 阶段 | 目标 | 完成度 |
|-----|------|:------:|
| 第一阶段 | Docker 稳定启动 + 登录/导入/索引/检索/问答/摘要/缺口闭环 | 85% |
| 第二阶段 | 接入 bge-m3 embedding + reranker + 离线评估 + 策略对比 | 0% |
| 第三阶段 | 企业 SSO + 租户隔离 + 连接器 | 0% |

### 第一阶段完成项

| 功能 | 状态 | 备注 |
|-----|:----:|------|
| Docker Compose 启动 | 通过 | 7 个容器全部运行 |
| JWT 登录/认证 | 通过 | 日志中有 401 + refresh_token 记录 |
| 文档上传 | 通过 | 支持 Word/PDF/TXT/DOC |
| 解析 + 分块 | 通过 | 5/7 文档正常分块 |
| 混合检索 + 问答 | 通过 | 已接入 qwen3.7-plus 真实模型 |
| 知识缺口沉淀 | 通过 | 代码已就绪，有低证据回答 |
| 模型热切换 | 通过 | 从 Mock 切换到 qwen3.7-plus |
| 质量分析 | 通过 | 每个文档有分析报告 |
| ZIP 批量导入 | 未使用 | 代码就绪但从未调用 |
| 评估用例 | 未使用 | 代码就绪但从未创建 |
| 用户反馈 | 未使用 | 代码就绪但从未触发 |

---

## 十一、最紧迫的 3 件事

1. **修复 Pipeline chunk 丢失 Bug**：检查 `IngestionPipeline.run()` 中 Qdrant upsert 失败时的回滚逻辑，确保 status=ready 一定有对应 chunk
2. **替换 hash embedding**：切换到 local 模式使用 BAAI/bge-m3，这是提升检索质量最直接的手段
3. **建立评估基线**：创建 10-20 个 EvalCase，跑一次 retrieval_eval，得到 Hit Rate/MRR/Recall 基线数据
