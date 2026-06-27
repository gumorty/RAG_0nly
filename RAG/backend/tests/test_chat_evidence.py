from app.services.chat import _focus_evidence_for_question, _readable_evidence_text


def test_html_table_evidence_is_rendered_as_readable_rows():
    html = """
    <table>
      <tr><th>地区</th><th>部级</th><th>司局级</th><th>其他人员</th><th>旺季期间</th></tr>
      <tr><td>北京市</td><td>1100</td><td>650</td><td>500</td><td></td></tr>
    </table>
    """

    text = _readable_evidence_text(html)

    assert "<table" not in text
    assert "地区 | 部级 | 司局级 | 其他人员 | 旺季期间" in text
    assert "北京市 | 1100 | 650 | 500" in text


def test_query_focus_keeps_xinjiang_row_without_neighboring_seasonal_rows():
    rows = "\n".join(
        [
            "青海 | 西宁市 | 800 | 500 | 350 | 6-9月 | 1200 | 750 | 530",
            "新疆 | 乌鲁木齐市 | 800 | 480 | 350",
            "新疆 | 石河子市、克拉玛依市、昌吉州、伊犁州、阿克苏地区、喀什地区 | 800 | 480 | 340",
            "海南 | 三亚市 | 1000 | 600 | 400 | 10-4月 | 1200 | 720 | 480",
        ]
    )

    focused = _focus_evidence_for_question("新疆有哪些地区在旺季期间有浮动吗？浮动了多少？", rows)

    assert "新疆" in focused
    assert "青海" not in focused
    assert "海南" not in focused
    assert "6-9月 | 1200" not in focused
    assert "10-4月 | 1200" not in focused
    assert "必须说明证据未提供具体浮动信息" in focused


def test_query_focus_does_not_select_table_header_by_generic_words():
    long_header = (
        "序号 | 地区 (城市) | 旺季浮动标准 住宿费标准 旺季地区 旺季上浮价 "
        + "司局其他 旺季期间 司局其他 部级 人员 " * 30
    )
    long_row = (
        "其他省份数据 " * 40
        + "36 新疆 | 乌鲁木齐市 石河子市、克拉玛依市、昌吉州、伊犁州、阿克苏地区、喀什地区 | 800 | 480 | 350"
        + " 其他省份数据 " * 40
    )
    rows = "\n".join([long_header, long_row])

    focused = _focus_evidence_for_question("新疆有哪些地区在旺季期间有浮动吗？浮动了多少？", rows)

    assert "36 新疆" in focused
    assert "旺季浮动标准 住宿费标准" not in focused
    assert len(focused) < 1200
