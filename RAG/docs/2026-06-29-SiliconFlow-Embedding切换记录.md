# 2026-06-29 SiliconFlow Embedding 切换记录

## 新模型

- Provider instance: `OpenAI-API-Compatible / siliconflow`
- Base URL: `https://api.siliconflow.cn/v1`
- Embedding model: `Qwen/Qwen3-Embedding-8B`
- RAGFlow embedding id: `Qwen/Qwen3-Embedding-8B@siliconflow@OpenAI-API-Compatible`
- 维度：4096

## 已完成配置

1. 直接调用 SiliconFlow `/v1/embeddings` 验证成功。
2. 在 RAGFlow MySQL 中注册：
   - `tenant_llm`
   - `llm`
   - `tenant_model_instance`
   - `tenant_model`
3. 将 RAGFlow tenant 默认 `embd_id` 切换到 SiliconFlow。
4. 将管理系统 `.env` / `.env.example` 的 `RAGFLOW_EMBEDDING_MODEL` 切换为：

```env
RAGFLOW_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-8B@siliconflow@OpenAI-API-Compatible
```

5. 重启管理系统 API。

## 验证结果

管理系统端到端烟测通过：

1. 使用新 embedding 创建临时 RAGFlow dataset。
2. 上传一个小文本。
3. 触发解析、切分和向量入库。
4. 检索问题命中 chunk。
5. 删除临时 dataset。

## 注意事项

已有知识库仍然绑定旧的 `text-embedding-v4@default@OpenAI-API-Compatible`。由于 SiliconFlow 新模型返回 4096 维向量，而旧 embedding 的向量维度可能不同，不能只改已有知识库的 `embd_id`。正确做法是：

1. 对已有知识库执行重新解析/重建向量索引。
2. 或新建知识库后重新上传文档。
3. 在重建完成前，系统会继续依赖本地 `retrieval_chunk_cache` 的 BM25 兜底检索，避免问答完全不可用。
