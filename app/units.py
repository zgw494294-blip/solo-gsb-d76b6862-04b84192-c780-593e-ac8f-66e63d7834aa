"""单位注册表、受支持单位集合、绝对温度仿射换算与目标单位解析。

温度说明：Pint 对偏移单位（degC/degF）的运算报错不带算式位置，
因此绝对温度（K/degC/degF）的换算由本模块用仿射公式手工完成；
温差（delta_*）是普通的倍率单位，与其余单位一样交给 Pint 处理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pint

from .errors import EvalError

ureg = pint.UnitRegistry()

# Pint 默认注册表自带 delta_degC / delta_degF，但没有 delta_K，这里补齐。
try:
    ureg.parse_units("delta_K")
except pint.UndefinedUnitError:
    ureg.define("delta_K = kelvin")

LENGTH_UNITS = {"m", "cm", "km"}
TIME_UNITS = {"s", "min", "h"}
MASS_UNITS = {"kg", "g"}
ABSOLUTE_TEMP_UNITS = {"K", "degC", "degF"}
DELTA_TEMP_UNITS = {"delta_K", "delta_degC", "delta_degF"}
ALLOWED_UNITS = (
    LENGTH_UNITS | TIME_UNITS | MASS_UNITS | ABSOLUTE_TEMP_UNITS | DELTA_TEMP_UNITS
)
# 变量单位：除单位表外，额外允许 1 表示无量纲数值（主要供反求接口使用）。
VARIABLE_UNITS = ALLOWED_UNITS | {"1"}

# 绝对温度单位 -> 同刻度的温差单位
DELTA_OF_ABSOLUTE = {"K": "delta_K", "degC": "delta_degC", "degF": "delta_degF"}

TEMPERATURE_DIM = ureg.Unit("K").dimensionality


# ---------------------------------------------------------------------------
# 绝对温度仿射换算（以开尔文为中转）
# ---------------------------------------------------------------------------

def absolute_to_kelvin(magnitude: float, unit: str) -> float:
    if unit == "K":
        return magnitude
    if unit == "degC":
        return magnitude + 273.15
    if unit == "degF":
        return (magnitude - 32.0) * 5.0 / 9.0 + 273.15
    raise ValueError(f"不是绝对温度单位: {unit}")


def kelvin_to_absolute(kelvin: float, unit: str) -> float:
    if unit == "K":
        return kelvin
    if unit == "degC":
        return kelvin - 273.15
    if unit == "degF":
        return (kelvin - 273.15) * 9.0 / 5.0 + 32.0
    raise ValueError(f"不是绝对温度单位: {unit}")


# ---------------------------------------------------------------------------
# 目标单位解析：单位、1、括号、*、/ 以及整数幂（^ 或 **）
# ---------------------------------------------------------------------------

@dataclass
class TargetUnit:
    raw: str                      # 用户原始输入
    canonical: str                # 归一化后的单位式（保留用户单位符号）
    pint_unit: pint.Unit          # 对应的 Pint 单位
    absolute_unit: str | None     # 目标恰为单个绝对温度单位时为该单位名，否则 None
    tree: object = None           # 解析后的单位式 AST（供静态推断遍历检查）


@dataclass
class _UToken:
    kind: str  # ident | number | pow | op | eof
    text: str
    pos: int


_UTOKEN_RE = re.compile(
    r"(?P<number>\d+(?:\.\d*)?|\.\d+)"
    r"|(?P<ident>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<pow>\*\*|\^)"
    r"|(?P<op>[*/()+\-])"
    r"|(?P<space>\s+)"
    r"|(?P<bad>.)"
)


@dataclass
class _UIdent:
    name: str
    pos: int


@dataclass
class _UOne:
    pos: int


@dataclass
class _UMul:
    left: object
    right: object
    pos: int


@dataclass
class _UDiv:
    left: object
    right: object
    pos: int


@dataclass
class _UPow:
    base: object
    exponent: int
    pos: int


class _TargetParser:
    """u_expr := u_term (('*'|'/') u_term)* ; u_term := u_factor (POW 整数)?"""

    def __init__(self, tokens: list[_UToken], loc_prefix: tuple[str, ...]) -> None:
        self._tokens = tokens
        self._i = 0
        self._loc_prefix = loc_prefix

    def _err(self, msg: str, pos: int) -> EvalError:
        return EvalError.at_target("invalid_target_unit", msg, pos, self._loc_prefix)

    def _peek(self) -> _UToken:
        return self._tokens[self._i]

    def _next(self) -> _UToken:
        tok = self._tokens[self._i]
        self._i += 1
        return tok

    def parse(self):
        node = self._expr()
        tok = self._peek()
        if tok.kind != "eof":
            raise self._err(
                f"目标单位在位置 {tok.pos} 附近有多余内容 {tok.text!r}", tok.pos
            )
        return node

    def _expr(self):
        node = self._term()
        while (tok := self._peek()).kind == "op" and tok.text in "*/":
            self._next()
            right = self._term()
            node = (_UMul if tok.text == "*" else _UDiv)(node, right, tok.pos)
        return node

    def _term(self):
        node = self._factor()
        tok = self._peek()
        if tok.kind == "pow":
            self._next()
            exponent = self._signed_int()
            node = _UPow(node, exponent, tok.pos)
        return node

    def _signed_int(self) -> int:
        sign = 1
        tok = self._peek()
        if tok.kind == "op" and tok.text in "+-":
            self._next()
            sign = -1 if tok.text == "-" else 1
            tok = self._peek()
        if tok.kind != "number" or not tok.text.isdigit():
            raise self._err(
                f"目标单位在位置 {tok.pos} 处的指数必须是整数", tok.pos
            )
        self._next()
        return sign * int(tok.text)

    def _factor(self):
        tok = self._peek()
        if tok.kind == "ident":
            self._next()
            return _UIdent(tok.text, tok.pos)
        if tok.kind == "number":
            if tok.text == "1":
                self._next()
                return _UOne(tok.pos)
            raise self._err(
                f"目标单位只允许数字 1（无量纲），位置 {tok.pos} 处的 {tok.text!r} 不允许",
                tok.pos,
            )
        if tok.kind == "op" and tok.text == "(":
            self._next()
            node = self._expr()
            closing = self._peek()
            if closing.kind == "op" and closing.text == ")":
                self._next()
                return node
            raise self._err(
                f"目标单位在位置 {closing.pos} 处缺少右括号", closing.pos
            )
        raise self._err(
            f"目标单位在位置 {tok.pos} 处应为单位符号、1 或括号", tok.pos
        )


def _walk(node):
    yield node
    if isinstance(node, (_UMul, _UDiv)):
        yield from _walk(node.left)
        yield from _walk(node.right)
    elif isinstance(node, _UPow):
        yield from _walk(node.base)


def target_identifiers(tree) -> list[tuple[str, int]]:
    """遍历目标单位式 AST，返回其中所有单位标识符 (名称, 位置)。"""
    return [(n.name, n.pos) for n in _walk(tree) if isinstance(n, _UIdent)]


def _build_unit(node) -> pint.Unit:
    if isinstance(node, _UIdent):
        return ureg.Unit(node.name)
    if isinstance(node, _UOne):
        return ureg.dimensionless
    if isinstance(node, _UMul):
        return _build_unit(node.left) * _build_unit(node.right)
    if isinstance(node, _UDiv):
        return _build_unit(node.left) / _build_unit(node.right)
    if isinstance(node, _UPow):
        return _build_unit(node.base) ** node.exponent
    raise AssertionError(f"未知目标单位节点: {node!r}")


def _format_unit(node) -> str:
    if isinstance(node, _UIdent):
        return node.name
    if isinstance(node, _UOne):
        return "1"
    if isinstance(node, _UPow):
        base = node.base
        text = (
            _format_unit(base)
            if isinstance(base, (_UIdent, _UOne))
            else f"({_format_unit(base)})"
        )
        return f"{text}^{node.exponent}"
    if isinstance(node, _UMul):
        left = _format_unit(node.left)
        right = _format_unit(node.right)
        if isinstance(node.left, _UDiv):
            left = f"({left})"
        if isinstance(node.right, _UDiv):
            right = f"({right})"
        return f"{left}*{right}"
    if isinstance(node, _UDiv):
        left = _format_unit(node.left)
        right = _format_unit(node.right)
        if isinstance(node.right, (_UMul, _UDiv)):
            right = f"({right})"
        return f"{left}/{right}"
    raise AssertionError(f"未知目标单位节点: {node!r}")


def parse_target(
    raw: str, loc_prefix: tuple[str, ...] = ("body", "target_unit")
) -> TargetUnit:
    """解析目标单位：受支持单位、1、括号、*、/ 与整数幂组成的式子。

    ``loc_prefix`` 决定错误 loc 的字段前缀（反求接口的目标单位位于 target.unit）。
    """
    tokens: list[_UToken] = []
    for m in _UTOKEN_RE.finditer(raw):
        kind, text, pos = m.lastgroup, m.group(), m.start()
        if kind == "space":
            continue
        if kind == "bad":
            raise EvalError.at_target(
                "invalid_target_unit",
                f"目标单位在位置 {pos} 处有非法字符 {text!r}",
                pos,
                loc_prefix,
            )
        tokens.append(_UToken(kind, text, pos))
    tokens.append(_UToken("eof", "", len(raw)))
    if tokens[0].kind == "eof":
        raise EvalError.at_target("invalid_target_unit", "目标单位为空", 0, loc_prefix)

    tree = _TargetParser(tokens, loc_prefix).parse()

    for node in _walk(tree):
        if isinstance(node, _UIdent) and node.name not in ALLOWED_UNITS:
            raise EvalError.at_target(
                "unknown_unit",
                f"目标单位中的单位 {node.name!r} 不受支持（位置 {node.pos}）",
                node.pos,
                loc_prefix,
            )

    absolute_unit = None
    if isinstance(tree, _UIdent) and tree.name in ABSOLUTE_TEMP_UNITS:
        absolute_unit = tree.name
    else:
        # K 是无偏移单位，可出现在复合单位中；degC/degF 有偏移，只允许单独作为目标。
        for node in _walk(tree):
            if isinstance(node, _UIdent) and node.name in {"degC", "degF"}:
                raise EvalError.at_target(
                    "invalid_target_unit",
                    f"{node.name} 是偏移温度单位，只能单独作为目标单位，"
                    f"不能出现在复合单位或幂次中（位置 {node.pos}）",
                    node.pos,
                    loc_prefix,
                )

    try:
        pint_unit = _build_unit(tree)
    except EvalError:
        raise
    except Exception as exc:  # 兜底：Pint 组合单位时的意外错误
        raise EvalError.at_target(
            "invalid_target_unit", f"目标单位无效：{exc}", None, loc_prefix
        )

    return TargetUnit(
        raw=raw,
        canonical=_format_unit(tree),
        pint_unit=pint_unit,
        absolute_unit=absolute_unit,
        tree=tree,
    )
