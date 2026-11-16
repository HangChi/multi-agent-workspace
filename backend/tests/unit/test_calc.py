"""calculate 工具单元测试：真实求值 + 表达式注入防护。"""

import math

import pytest

from app.executors.calc import CalculateExecutor, ExpressionError, safe_eval


class TestSafeEval:
    @pytest.mark.parametrize(
        ("expr", "expected"),
        [
            ("2+3*4", 14),
            ("(1+2)**3", 27),
            ("10/4", 2.5),
            ("10//3", 3),
            ("10%3", 1),
            ("-5+2", -3),
            ("abs(-7)", 7),
            ("round(3.14159, 2)", 3.14),
            ("max(1, 5, 3)", 5),
            ("sqrt(16)", 4.0),
            ("pi", math.pi),
        ],
    )
    def test_valid_expressions(self, expr: str, expected: float):
        assert safe_eval(expr) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "expr",
        [
            "__import__('os').system('rm -rf /')",
            "open('/etc/passwd').read()",
            "os.system('ls')",
            "eval('1+1')",
            "exec('x=1')",
            "(1).__class__.__bases__",
            "[1,2,3]",
            "lambda: 1",
            "x + 1",
            "1 if True else 2",
            "'a' * 3",
        ],
    )
    def test_rejects_dangerous_or_unsupported(self, expr: str):
        with pytest.raises(ExpressionError):
            safe_eval(expr)

    @pytest.mark.parametrize(
        ("expr", "reason"),
        [
            ("", "为空"),
            (" " * 10, "为空"),
            ("9**9**9", "指数过大"),
            ("1" * 201, "过长"),
            ("1/0", "除数"),
            ("5%0", "除数"),
            # 每层指数都在上限内，但中间结果位宽爆炸（幂塔）
            ("(9**1000)**1000", "过大"),
            ("((9**1000)**1000)**1000", "过大"),
            # math.exp 抛的是 OverflowError，不能漏成工具崩溃
            ("exp(1000000)", "可计算范围"),
        ],
    )
    def test_rejects_abuse(self, expr: str, reason: str):
        with pytest.raises(ExpressionError, match=reason):
            safe_eval(expr)

    def test_syntax_error_is_readable(self):
        with pytest.raises(ExpressionError, match="语法错误"):
            safe_eval("2 + * 3")


class TestCalculateExecutor:
    async def test_returns_result(self):
        result = await CalculateExecutor().execute({"expression": "(1250*0.8+300)/3"})
        assert result["result"] == pytest.approx(433.3333333, rel=1e-6)
        assert result["expression"] == "(1250*0.8+300)/3"

    async def test_error_is_returned_not_raised(self):
        """非法表达式返回 error 字段（LLM 能读到并自我纠正），不中断执行。"""
        result = await CalculateExecutor().execute({"expression": "__import__('os')"})
        assert "error" in result
        assert "result" not in result

    async def test_schema_is_openai_tool_format(self):
        schema = CalculateExecutor().schema()
        assert schema["function"]["name"] == "calculate"
        assert "expression" in schema["function"]["parameters"]["properties"]
