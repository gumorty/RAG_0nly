# PDF OCR 与表格行级索引修改说明

本包已按以下目标修改：

1. `backend/app/services/pdf_probe.py`：新增 PDF 文本层预检，用于识别扫描版 PDF。
2. `backend/app/services/mineru.py`：PDF OCR 改为 `auto|always|never` 可配置；默认 `auto`。
3. `backend/app/services/mineru.py`：补全 MinerU 上传、结果 zip 下载、zip 解压异常包装，失败后可进入 fallback。
4. `backend/app/services/table_index.py`：新增 Markdown/HTML 表格行级事实索引生成器。
5. `backend/app/services/parser_router.py`：MinerU Markdown 解析结果进入 RAGFlow 前追加表格行级索引。
6. `backend/app/services/chat.py`：表格类问题对行级索引 chunk 进行轻量加权，并在证据聚焦时优先保留最相关行。
7. `backend/tests/`：新增扫描 PDF、表格 PDF、表格行级索引、MinerU OCR auto、证据聚焦测试。

新增配置项：

```env
MINERU_PDF_OCR_MODE=auto
TABLE_ROW_INDEX_ENABLED=true
TABLE_ROW_INDEX_MAX_ROWS=5000
```

本地已验证可运行的测试：

```bash
DATABASE_URL=sqlite:////tmp/rag_test.db PYTHONPATH=backend pytest -q \
  backend/tests/test_pdf_probe.py \
  backend/tests/test_table_index.py \
  backend/tests/test_parser_router.py
```

结果：`12 passed`。

说明：当前执行环境缺少完整后端依赖中的 `qdrant_client` 等包，因此没有在当前沙箱完整运行全部后端测试；在 Docker 后端镜像中会按 `backend/requirements.txt` 安装完整依赖。
