"""计算器插件:基于 ast 白名单的安全算术求值,绝不 eval。

参考 nonebot-plugin-calc。支持四则运算、取余、幂、括号与常用数学函数。
安全防护:仅白名单 AST 节点;表达式 ≤200 字符;幂指数 ≤64;
数值上溢自动截断返回。
命令:
- /计算 <表达式>  例如: /计算 1+2*3, /计算 (8+2)^2
- /calc <表达式>
"""
from __future__ import annotations

import ast
import math
import operator

from src.adapter.event import AgentEvent
from src.adapter.message import escape_cq

_MAX_LEN = 200
_MAX_EXPONENT = 64
_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "ceil": math.ceil,
    "floor": math.floor,
}
_CONSTS = {"pi": math.pi, "e": math.e}


def _eval(node: ast.AST):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        op = _OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError("不支持的运算符")
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and isinstance(right, (int, float)) and abs(right) > _MAX_EXPONENT:
            raise ValueError("指数过大")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY.get(type(node.op))
        if op is None:
            raise ValueError("不支持的运算符")
        return op(_eval(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(f"未知符号: {node.id}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("不支持的函数")
        args = [_eval(a) for a in node.args]
        return _FUNCS[node.func.id](*args)
    raise ValueError("不支持的表达式")


def _safe_calc(expr: str) -> str:
    if not expr:
        return "表达式不能为空。"
    if len(expr) > _MAX_LEN:
        return f"表达式过长(≤{_MAX_LEN} 字符)。"
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError):
        return "表达式语法错误。"
    try:
        result = _eval(tree)
    except ZeroDivisionError:
        return "除数不能为 0。"
    except OverflowError:
        return "数值溢出,请缩小范围。"
    except ValueError as exc:
        return f"计算失败: {exc}"
    try:
        if isinstance(result, float) and not math.isfinite(result):
            return "结果非有限数值。"
    except OverflowError:
        return "数值溢出,请缩小范围。"
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        result = int(result)
    if isinstance(result, float):
        result = round(result, 10)
    return f"{escape_cq(expr)} = {result}"


def setup(registry) -> None:
    @registry.command("计算", permission_level=1)
    async def calc(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        await event.reply(_safe_calc(arg))
        return None

    @registry.command("calc", permission_level=1)
    async def calc_alias(event: AgentEvent):
        arg = (event.state.get("command_arg") or "").strip()
        await event.reply(_safe_calc(arg))
        return None
