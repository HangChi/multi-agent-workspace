"""calculate 工具（真实实现）：安全四则运算求值。

此前是 NoopExecutor 占位——LLM 调 calculate 拿到 {"note": "占位"} 却当成
计算结果写进交付物，产生看起来正常实则错误的答案。

安全约束（不使用 eval）：
- AST 白名单：只允许数字、二元/一元运算符、少量数学函数
- 表达式长度与幂指数上限：防 9**9**9 这类计算爆炸
- 结果必须是有限实数
"""

import ast
import math
import operator
from typing import Any

from app.executors.base import ToolExecutor
from app.executors.catalog import to_openai_schema

_MAX_EXPR_LEN = 200
_MAX_POW_EXPONENT = 1000
_MAX_ABS_OPERAND = 1e12
# 指数上限挡不住「幂塔」：每层指数都 <= 1000 时，(9**1000)**1000 已近百万位，
# 再套一层就是十亿位。限制每一步的整数位宽，运算一产生就拒绝，代价才有界。
_MAX_INT_BITS = 4096

_BIN_OPS: dict[type[Any], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[Any], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
}
_CONSTS: dict[str, float] = {"pi": math.pi, "e": math.e}


class ExpressionError(ValueError):
    """表达式非法或不可计算。"""


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ExpressionError(f"不支持的常量类型: {type(node.value).__name__}")
        if abs(node.value) > _MAX_ABS_OPERAND:
            raise ExpressionError("数值过大")
        return node.value

    if isinstance(node, ast.BinOp):
        op_type: type[Any] = type(node.op)
        if op_type not in _BIN_OPS:
            raise ExpressionError(f"不支持的运算符: {op_type.__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if op_type is ast.Pow and abs(right) > _MAX_POW_EXPONENT:
            raise ExpressionError(f"指数过大（上限 {_MAX_POW_EXPONENT}）")
        if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            raise ExpressionError("除数不能为 0")
        try:
            value = _BIN_OPS[op_type](left, right)
        except (OverflowError, ZeroDivisionError) as exc:
            raise ExpressionError("结果超出可计算范围") from exc
        if isinstance(value, int) and value.bit_length() > _MAX_INT_BITS:
            raise ExpressionError(f"计算结果过大（上限 {_MAX_INT_BITS} 位）")
        return value

    if isinstance(node, ast.UnaryOp):
        unary_type: type[Any] = type(node.op)
        if unary_type not in _UNARY_OPS:
            # 2026-11-19：这里原本写的是 `op_type.__name__`（从上面的二元分支
            # 复制过来的），于是 safe_eval("~5") 抛的是 UnboundLocalError 而不是
            # ExpressionError——工具调用会变成 500，而 LLM 收到的是堆栈而不是
            # 「这个运算符不支持」。补测时暴露。
            raise ExpressionError(f"不支持的一元运算符: {unary_type.__name__}")
        return _UNARY_OPS[unary_type](_eval_node(node.operand))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ExpressionError("只支持直接调用内置数学函数")
        func = _FUNCS.get(node.func.id)
        if func is None:
            raise ExpressionError(f"不支持的函数: {node.func.id}")
        if node.keywords:
            raise ExpressionError("不支持关键字参数")
        try:
            return func(*(_eval_node(arg) for arg in node.args))
        except (OverflowError, ZeroDivisionError, ValueError) as exc:
            # math.exp(1e12) 之类会抛 OverflowError，不是 ExpressionError
            raise ExpressionError("函数参数超出可计算范围") from exc

    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ExpressionError(f"未知标识符: {node.id}")

    raise ExpressionError(f"不支持的表达式片段: {type(node).__name__}")


def safe_eval(expression: str) -> float:
    """求解算术表达式，非法输入抛 ExpressionError。"""
    expr = (expression or "").strip()
    if not expr:
        raise ExpressionError("表达式为空")
    if len(expr) > _MAX_EXPR_LEN:
        raise ExpressionError(f"表达式过长（上限 {_MAX_EXPR_LEN} 字符）")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"表达式语法错误: {exc.msg}") from exc

    value = _eval_node(tree.body)
    if isinstance(value, int) and value.bit_length() > _MAX_INT_BITS:
        raise ExpressionError(f"计算结果过大（上限 {_MAX_INT_BITS} 位）")
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError):
        # 超大 int 无法转 float：比例有限性更严格，直接拒绝
        raise ExpressionError("结果不是有限实数") from None
    if isinstance(value, complex) or not finite:
        raise ExpressionError("结果不是有限实数")
    return value


class CalculateExecutor(ToolExecutor):
    """calculate：执行数值计算。"""

    name = "calculate"

    def schema(self) -> dict[str, Any]:
        """工具 schema 从 catalog 派生（R5），理由同 `read_url.schema()`。"""
        return to_openai_schema(self.name) or {}

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        expression = str(arguments.get("expression") or arguments.get("input") or "")
        try:
            value = safe_eval(expression)
        except ExpressionError as exc:
            return {"error": str(exc), "expression": expression}
        return {"expression": expression, "result": value}
