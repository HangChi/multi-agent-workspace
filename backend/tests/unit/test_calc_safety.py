"""calculate 的安全边界单测（015 覆盖率补齐）。

calculate 是 LLM 可以任意调用、且**结果会被直接写进交付物**的工具，所以
它的价值不在「算得对」，而在「算不了的时候必须明确报错」。下面每条对应一种
真实滥用：布尔当数字、指数爆炸、除零、非白名单函数、关键字参数、lambda 调用。
"""

import pytest

from app.executors.calc import ExpressionError, safe_eval


def test_basic_arithmetic():
    assert safe_eval("1 + 2 * 3") == 7
    assert safe_eval("(1 + 2) * 3") == 9
    assert safe_eval("-4 + 1") == -3


def test_constants_and_functions():
    assert safe_eval("pi") == pytest.approx(3.141592653589793)
    assert safe_eval("round(2.567, 1)") == pytest.approx(2.6)
    assert safe_eval("max(1, 5, 3)") == 5


def test_boolean_constant_rejected():
    """bool 是 int 的子类，不显式拦就会算出 True + 1 == 2。"""
    with pytest.raises(ExpressionError):
        safe_eval("True + 1")


def test_non_numeric_constant_rejected():
    with pytest.raises(ExpressionError):
        safe_eval("'abc'")


def test_oversized_operand_rejected():
    with pytest.raises(ExpressionError):
        safe_eval("1e18 + 1")


def test_power_exponent_capped():
    """9**9**9 这类幂塔必须在指数这一步就拦下，不能等算完。"""
    with pytest.raises(ExpressionError):
        safe_eval("9 ** 100000")


def test_power_result_bit_width_capped():
    """指数在限内、结果却超宽（(9**1000)**1000）：按结果位宽拒绝。"""
    with pytest.raises(ExpressionError):
        safe_eval("(10 ** 1000) * (10 ** 1000) * (10 ** 1000)")


def test_division_by_zero_rejected():
    with pytest.raises(ExpressionError):
        safe_eval("1 / 0")
    with pytest.raises(ExpressionError):
        safe_eval("5 % 0")


def test_unsupported_unary_operator_rejected():
    with pytest.raises(ExpressionError):
        safe_eval("~5")


def test_unsupported_binary_operator_rejected():
    with pytest.raises(ExpressionError):
        safe_eval("5 | 3")


def test_unknown_function_rejected():
    with pytest.raises(ExpressionError):
        safe_eval("system('ls')")


def test_keyword_arguments_rejected():
    with pytest.raises(ExpressionError):
        safe_eval("round(2.5, ndigits=1)")


def test_non_name_callee_rejected():
    """(lambda: 1)() 之类：只允许直接调用内置函数名。"""
    with pytest.raises(ExpressionError):
        safe_eval("(lambda: 1)()")


def test_overlong_expression_rejected():
    with pytest.raises(ExpressionError):
        safe_eval("1+" * 120 + "1")


def test_div_result_out_of_range_is_wrapped():
    """浮点除极小数会 OverflowError，必须转成 ExpressionError 而不是裸抛。"""
    with pytest.raises(ExpressionError):
        safe_eval("1e300 / 1e-300")
