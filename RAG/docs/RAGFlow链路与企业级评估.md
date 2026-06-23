# RAGFlow 链路与企业级评估

更新时间：2026-06-21

## 当前结论

当前系统已经把核心 RAG 链路切换到 RAGFlow：知识库创建、文档上传、DeepDOC 解析、分块、Embedding、Elasticsearch 索引和检索都由 RAGFlow 执行。本项目保留管理层能力，包括登录鉴权、知识库管理、文档状态展示、模型配置、对话界面、上下文组织和最终回答格式控制。

这套系统可以作为后续垂直领域 RAG 产品的基础底座继续建设，但还不能直接定义为完整企业级成品。它已经具备可落地雏形，仍需要补齐权限隔离、评测体系、任务队列治理、可观测性、审计、批量失败恢复、知识版本管理和精排策略等生产能力。

## 真实文档链路

1. 用户在前端创建知识库。
2. 后端调用 RAGFlow `/api/v1/datasets` 创建 dataset，并记录 `ragflow_dataset_id`。
3. 用户上传 Word、PDF、Markdown 等文档。
4. 后端把原始文件上传到 RAGFlow dataset documents API。
5. 后端触发 RAGFlow parsing/chunks API。
6. RAGFlow 使用 DeepDOC 做版面解析、正文抽取和结构化处理。
7. RAGFlow 按 dataset/parser 配置执行分块。
8. RAGFlow 调用 `text-embedding-v4` 生成向量。
9. RAGFlow 将 chunk、文档元数据和向量写入 Elasticsearch。
10. 本项目轮询 RAGFlow 文档状态，并向前端展示“解析中、已生成分块、已写入索引”等状态。
11. 用户提问时，后端调用 RAGFlow retrieval API 做混合检索。
12. 后端过滤明显低价值证据块，例如短参考文献条目。
13. 后端把检索证据、对话历史和用户问题交给当前启用的 Chat 模型生成回答。
14. 前端展示回答、引用、证据分数和相关文档。

## 已验证结果

测试文档：`D:\WordFile\Engineering_Ethics_Sensor_Fusion_Paper.docx`

RAGFlow 日志显示：

- DeepDOC 解析完成。
- 生成 14 个 chunk。
- 使用 `text-embedding-v4` 完成 Embedding。
- 写入 Elasticsearch 索引完成。
- 问答阶段通过 RAGFlow retrieval 返回 8 条引用。

测试问题：

`这篇文档提出了哪些主要工程伦理风险？请给出引用。`

返回结果能基于文档归纳出安全与生命风险、隐私与数据权利风险、算法偏差与公平性风险、责任归属风险、双重使用与公众信任风险，并给出引用编号。

## 当前特点

- RAGFlow 负责底层 RAG 工程链路，本项目不再自己实现主链路分块、向量写入和检索。
- 管理系统负责业务层 UI、认证、知识库管理、模型路由和问答体验。
- 模型配置可以从前端管理，后端根据当前启用配置调用 OpenAI-compatible 模型。
- 文档状态已经能表达 RAGFlow 的解析、分块和索引阶段。
- 问答接口已经支持带历史上下文的问题改写与回答。

## 本次修复

### Dataset creation failed

报错：

`RAGFlow dataset creation failed: The dataset ... doesn't own parsed file`

实际原因不是 dataset 创建失败，而是创建 dataset 后，系统额外尝试调用 RAGFlow Chat/Dialog 创建接口。RAGFlow 在没有可用解析文件归属关系时拒绝创建 Chat/Dialog，导致本项目把这个可选步骤误包装成 dataset 创建失败。

处理方式：

- 默认关闭 `RAGFLOW_ENABLE_CHAT_COMPLETIONS`。
- Chat/Dialog 创建失败不再阻断知识库创建。
- dataset 创建、文档上传、解析、索引和检索继续稳定走 RAGFlow。

### 检索噪声

真实测试中发现参考文献短块可能获得较高关键词分数，影响最终回答证据质量。

处理方式：

- 在 RAGFlow 返回结果后保留原排名。
- 仅当存在替代正文证据时，过滤明显参考文献条目。
- 不改变 RAGFlow 的索引和检索实现，只做回答前证据清洗。

## 复杂多章节与跨文档链路

当前多章节文档主要依赖 DeepDOC 抽取结构、RAGFlow parser 分块、标题路径和 chunk 元数据来保留局部上下文。对于跨文档问题，RAGFlow retrieval 会在同一 dataset 内跨文档召回相关 chunk，后端再把多个证据片段合并给 Chat 模型。

适合的问题类型：

- 单文档摘要、风险归纳、进展提取。
- 多章节事实查询。
- 多文档同主题对比。
- 带引用的项目资料问答。

当前不足：

- 还没有完整的 query decomposition，多跳问题可能只召回局部证据。
- 还没有专门的 cross-document reranker。
- 还没有文档级摘要索引、章节级摘要索引和 chunk 级索引的多层检索融合。
- 还没有把 GraphRAG 作为默认可控能力启用。
- 对表格、扫描件、图片文字和长 PDF 的评测样本仍不足。

## 企业级建设建议

后续进入垂直领域前，建议按以下顺序增强：

1. 建立评测集：问题、标准答案、必须引用、禁止引用、拒答样例。
2. 增加检索评估：Recall@K、MRR、答案引用命中率、低证据拒答率。
3. 引入 reranker：优先用于高价值知识库和跨文档问题。
4. 做多层索引：文档摘要、章节摘要、chunk 证据三层召回。
5. 增加权限模型：租户、角色、知识库 ACL、文档级权限。
6. 增加审计：上传、删除、问答、模型调用和配置变更留痕。
7. 增加任务治理：解析失败重试、批量任务状态、死信队列、人工重跑。
8. 增加版本管理：文档版本、知识库快照、答案基于哪个版本生成。
9. 加强可观测性：RAGFlow 调用耗时、解析耗时、Embedding token、模型 token、错误率。
10. 针对垂直领域建立术语词典、同义词、实体抽取和领域专用提示词。

## 已落地的第一步：评测集

已经新增固定评测集与一键评测脚本：

- `evalsets/lab_weekly_rag_eval.json`
- `scripts/run-rag-eval.py`

运行方式：

```powershell
cd D:\Researching\LLMStart\RAG
python scripts\run-rag-eval.py
```

当前基线覆盖 3 类真实问题：

1. 哪些文档提到了论文实验方面。
2. 当前实验风险、阻塞和下一步计划。
3. 最近项目进展摘要。

评测脚本会检查：

- 回答必须包含的关键词。
- 回答禁止出现的兜底/不可读文本。
- 最低引用数量。
- 最低证据分。
- 引用片段是否可读。
- 回答是否存在重复行。

最新一次运行结果：

- `lab-experiment-documents`：通过。
- `lab-risks-next-steps`：通过。
- `lab-progress-summary`：通过。

评测报告输出到：

`data/eval/latest-rag-eval-report.json`

后续每进入一个垂直领域，都应该先复制一份领域评测集，明确问题、必须命中的结论、必须引用的文档、禁止出现的幻觉表达，再开展模型、分块、检索和重排策略优化。
