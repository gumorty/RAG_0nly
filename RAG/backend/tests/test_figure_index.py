from app.services.figure_index import augment_markdown_with_figure_index


def test_figure_index_extracts_catalog_page_and_context():
    markdown = """
# 报告

图目录
图 6 基于百个优秀案例统计的 AI 应用产业链分布 ...... 35

一些正文。
图 7 大模型不同场景落地流程
该图说明数据、模型与业务流程之间的关系。
"""

    result = augment_markdown_with_figure_index(markdown)

    assert result.metadata["figure_count"] >= 2
    assert "图表检索索引" in result.markdown
    assert "图6" in result.markdown
    assert "第 35 页" in result.markdown
    assert "大模型不同场景落地流程" in result.markdown
