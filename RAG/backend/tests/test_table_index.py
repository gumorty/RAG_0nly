from app.services.table_index import augment_markdown_with_table_index


def test_table_index_generates_row_facts():
    markdown = """
| 地区 | 城市 | 普通_部级 | 普通_司局级 | 普通_其他人员 | 旺季期间 | 旺季_部级 | 旺季_司局级 | 旺季_其他人员 |
|---|---|---:|---:|---:|---|---:|---:|---:|
| 河北 | 秦皇岛市 | 800 | 450 | 350 | 7-8月 | 1200 | 680 | 500 |
"""

    result = augment_markdown_with_table_index(markdown)

    assert "表格行级检索索引" in result.markdown
    assert "城市=秦皇岛市" in result.markdown
    assert "旺季_司局级=680" in result.markdown
    assert result.metadata["table_row_count"] == 1


def test_table_index_forward_fills_context_columns():
    markdown = """
| 省份 | 城市 | 普通_部级 | 普通_司局级 | 旺季期间 | 旺季_司局级 |
|---|---|---:|---:|---|---:|
| 河北 | 张家口市 | 800 | 450 | 7-9月、11-3月 | 675 |
|  | 秦皇岛市 | 800 | 450 | 7-8月 | 680 |
"""

    result = augment_markdown_with_table_index(markdown)

    assert "省份=河北；城市=秦皇岛市" in result.markdown
    assert "旺季_司局级=680" in result.markdown
    assert result.metadata["table_row_count"] == 2


def test_table_index_expands_html_rowspan_and_colspan():
    markdown = """
<table>
  <tr><th rowspan="2">Region</th><th colspan="2">Cost</th></tr>
  <tr><th>City</th><th>Amount</th></tr>
  <tr><td rowspan="2">Guangxi</td><td>Guilin</td><td>1040</td></tr>
  <tr><td>Beihai</td><td>1040</td></tr>
</table>
"""

    result = augment_markdown_with_table_index(markdown)

    assert "Region=Guangxi" in result.markdown
    assert "Cost_City=Guilin" in result.markdown
    assert "Cost_City=Beihai" in result.markdown
    assert "Cost_Amount=1040" in result.markdown
    assert result.metadata["table_row_count"] == 2
