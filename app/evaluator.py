"""量纲求值语义。

- 绝对温度（K/degC/degF）单独表示，不进入 Pint 的 Quantity，
  以便按规则给出带算式位置的错误：可加减温差；两绝对温度只许相减且
  结果为温差；不得取负；不得参与乘除。
- 其余量（含温差 delta_*、复合量纲、无量纲数）都是 Pint Quantity，
  加减由 Pint 先换算到相同量纲再合并，乘除由 Pint 组合量纲。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pint

from .errors import EvalError
from .expression import Bin, Num, Unary, Var
from .units import (
    ABSOLUTE_TEMP_UNITS,
    DELTA_OF_ABSOLUTE,
    TEMPERATURE_DIM,
    TargetUnit,
    absolute_to_kelvin,
    kelvin_to_absolute,
    ureg,
)


@dataclass
class Value:
    """求值中间量：要么是绝对温度，要么是一个 Pint Quantity。"""

    quantity: pint.Quantity | None = None
    abs_magnitude: float | None = None
    abs_unit: str | None = None

    @classmethod
    def absolute(cls, magnitude: float, unit: str) -> "Value":
        return cls(abs_magnitude=magnitude, abs_unit=unit)

    @classmethod
    def general(cls, quantity: pint.Quantity) -> "Value":
        return cls(quantity=quantity)

    @property
    def is_absolute(self) -> bool:
        return self.abs_unit is not None


def value_from_unit(magnitude: float, unit: str) -> Value:
    """按声明单位构造变量值；unit 必须已在 ALLOWED_UNITS 中校验过。"""
    if unit in ABSOLUTE_TEMP_UNITS:
        return Value.absolute(magnitude, unit)
    return Value.general(ureg.Quantity(magnitude, unit))


def evaluate(node, env: dict[str, Value]) -> Value:
    if isinstance(node, Num):
        return Value.general(ureg.Quantity(node.value))
    if isinstance(node, Var):
        try:
            return env[node.name]
        except KeyError:
            known = f"，已定义的变量：{', '.join(sorted(env))}" if env else ""
            raise EvalError.at_expression(
                "unknown_variable", node.pos, f"位置 {node.pos}：未知变量 {node.name!r}{known}"
            )
    if isinstance(node, Unary):
        value = evaluate(node.operand, env)
        if node.op == "+":
            return value
        if value.is_absolute:
            raise EvalError.at_expression(
                "absolute_temperature", node.pos, f"位置 {node.pos}：绝对温度不能取负"
            )
        return Value.general(-value.quantity)
    if isinstance(node, Bin):
        left = evaluate(node.left, env)
        right = evaluate(node.right, env)
        if node.op in "+-":
            return _addsub(left, right, node.op, node.pos)
        return _muldiv(left, right, node.op, node.pos)
    raise AssertionError(f"未知 AST 节点: {node!r}")


def _addsub(left: Value, right: Value, op: str, pos: int) -> Value:
    if left.is_absolute or right.is_absolute:
        return _addsub_absolute(left, right, op, pos)
    try:
        quantity = left.quantity + right.quantity if op == "+" else left.quantity - right.quantity
    except pint.DimensionalityError:
        raise EvalError.at_expression(
            "incompatible_units",
            pos,
            f"位置 {pos}：加减两侧量纲不兼容：{left.quantity.units} 与 {right.quantity.units}",
        )
    return Value.general(quantity)


def _addsub_absolute(left: Value, right: Value, op: str, pos: int) -> Value:
    if left.is_absolute and right.is_absolute:
        if op == "+":
            raise EvalError.at_expression(
                "absolute_temperature",
                pos,
                f"位置 {pos}：两个绝对温度不能相加（只允许相减，结果为温差）",
            )
        # 两绝对温度相减 -> 温差（统一以 delta_K 表示）
        delta = absolute_to_kelvin(left.abs_magnitude, left.abs_unit) - absolute_to_kelvin(
            right.abs_magnitude, right.abs_unit
        )
        return Value.general(ureg.Quantity(delta, "delta_K"))

    abs_value, other = (left, right) if left.is_absolute else (right, left)
    if other.quantity.dimensionality != TEMPERATURE_DIM:
        raise EvalError.at_expression(
            "incompatible_units",
            pos,
            f"位置 {pos}：绝对温度只能与温差相加减，不能加减 {other.quantity.units}",
        )
    if not left.is_absolute and op == "-":
        raise EvalError.at_expression(
            "absolute_temperature", pos, f"位置 {pos}：不能从温差中减去绝对温度"
        )
    # 把温差换算到绝对温度同刻度的温差单位后再合并
    delta = other.quantity.to(DELTA_OF_ABSOLUTE[abs_value.abs_unit]).magnitude
    magnitude = abs_value.abs_magnitude + delta if op == "+" else abs_value.abs_magnitude - delta
    return Value.absolute(magnitude, abs_value.abs_unit)


def _muldiv(left: Value, right: Value, op: str, pos: int) -> Value:
    if left.is_absolute or right.is_absolute:
        raise EvalError.at_expression(
            "absolute_temperature", pos, f"位置 {pos}：绝对温度不能参与乘除运算"
        )
    if op == "/":
        if right.quantity.magnitude == 0:
            raise EvalError.at_expression(
                "division_by_zero", pos, f"位置 {pos}：除数为零"
            )
        return Value.general(left.quantity / right.quantity)
    return Value.general(left.quantity * right.quantity)


def convert_result(
    value: Value,
    target: TargetUnit,
    loc_prefix: tuple[str, ...] = ("body", "target_unit"),
) -> tuple[float, str]:
    """把结果换算到目标单位，返回 (数值, 量纲字符串)。"""
    if value.is_absolute:
        if target.absolute_unit is None:
            raise EvalError.at_target(
                "incompatible_units",
                f"结果是绝对温度，只能换算到 K/degC/degF，不能换算为 {target.raw!r}",
                None,
                loc_prefix,
            )
        kelvin = absolute_to_kelvin(value.abs_magnitude, value.abs_unit)
        magnitude = kelvin_to_absolute(kelvin, target.absolute_unit)
        dimension = "[temperature]"
    else:
        if target.absolute_unit is not None:
            raise EvalError.at_target(
                "incompatible_units",
                f"结果不是绝对温度，不能换算为绝对温度单位 {target.absolute_unit!r}",
                None,
                loc_prefix,
            )
        try:
            converted = value.quantity.to(target.pint_unit)
        except (pint.DimensionalityError, pint.OffsetUnitCalculusError):
            raise EvalError.at_target(
                "incompatible_units",
                f"结果量纲 {value.quantity.dimensionality} 与目标单位 {target.raw!r} 不兼容",
                None,
                loc_prefix,
            )
        magnitude = float(converted.magnitude)
        dimension = str(converted.dimensionality)

    if not math.isfinite(magnitude):
        raise EvalError.at_expression(
            "non_finite_result", None, "求值结果溢出，不是有限数值"
        )
    return magnitude, dimension
