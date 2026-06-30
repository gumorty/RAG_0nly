from app.services.chat import _focus_evidence_for_question


def test_non_travel_table_focus_does_not_inject_accommodation_guardrail():
    content = "\n".join(
        [
            "表格1 第1行: 公司=Meta AI | 模型=Llama 2 | 上下文长度=8k",
            "表格1 第2行: 公司=OpenAI | 模型=GPT-4 | 上下文长度=32k",
            "图 6 基于百个优秀案例统计的 AI 应用产业链分布",
        ]
    )

    focused = _focus_evidence_for_question("图6说明了人工智能产业链的什么分布？", content)

    assert "旺季" not in focused
    assert "上浮" not in focused
    assert "相邻地区" not in focused
    assert "图 6" in focused


def test_travel_policy_table_focus_keeps_conservative_guardrail():
    content = "\n".join(
        [
            "广西 | 南宁市 | 住宿费标准 | 部级800 | 司局级470 | 其他人员350",
            "广西 | 桂林市 | 旺季期间 1-2月、7-9月 | 上浮 1040 610 430",
        ]
    )

    focused = _focus_evidence_for_question("广西住宿费旺季上浮标准是多少？", content)

    assert "不得从相邻地区推断" in focused
    assert "桂林市" in focused
