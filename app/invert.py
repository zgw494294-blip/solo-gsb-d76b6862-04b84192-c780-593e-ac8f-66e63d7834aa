"""目标值反求：先查端点，未命中时仅在端点残差异号的区间内二分。

语义与 ``/v1/evaluate`` 完全一致（同样的算式语法、单位换算与绝对温度规则），
因此零除、量纲错误、非有限结果等都会带着算式位置以 422 抛出——
间断点（例如 1/x 在 0 附近）绝不会被当作解返回。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import EvalError
from .evaluator import convert_result, evaluate, value_from_unit
from .units import parse_target

MAX_ITERATIONS = 100
TOLERANCE_FACTOR = 1e-8
TARGET_LOC_PREFIX = ("body", "target", "unit")


@dataclass
class InvertSolution:
    value: float          # 待求变量数值（按其声明单位）
    result: float         # 实际结果（按目标单位）
    result_unit: str      # 归一化目标单位
    dimension: str        # 结果量纲


def invert(
    ast,
    known_env: dict,
    unknown_name: str,
    unknown_unit: str,
    low: float,
    high: float,
    target_value: float,
    raw_target_unit: str,
) -> InvertSolution:
    target = parse_target(raw_target_unit, TARGET_LOC_PREFIX)
    tolerance = TOLERANCE_FACTOR * max(1.0, abs(target_value))

    def probe(x: float) -> float:
        """求 x（按待求变量单位）代入后的结果残差（结果数值 - 目标值）。"""
        env = dict(known_env)
        env[unknown_name] = value_from_unit(x, unknown_unit)
        magnitude, _ = convert_result(evaluate(ast, env), target, TARGET_LOC_PREFIX)
        return magnitude - target_value

    def solve_at(x: float) -> InvertSolution:
        env = dict(known_env)
        env[unknown_name] = value_from_unit(x, unknown_unit)
        magnitude, dimension = convert_result(
            evaluate(ast, env), target, TARGET_LOC_PREFIX
        )
        return InvertSolution(x, magnitude, target.canonical, dimension)

    # 先检查区间端点
    f_low = probe(low)
    if math.isfinite(f_low) and abs(f_low) <= tolerance:
        return solve_at(low)
    f_high = probe(high)
    if math.isfinite(f_high) and abs(f_high) <= tolerance:
        return solve_at(high)

    # 端点未命中：只有残差异号（括住一个穿越点）才允许二分。
    # 非有限残差无法确定符号；端点恰好为 0 已在上面的容差判断中处理。
    if (
        not math.isfinite(f_low)
        or not math.isfinite(f_high)
        or (f_low > 0) == (f_high > 0)
    ):
        raise EvalError(
            "no_bracket",
            ["body", "bounds"],
            f"区间端点 [{low:g}, {high:g}] 处的结果残差为 {f_low:g} 与 {f_high:g}，"
            f"未异号括住目标值（容差 {tolerance:g}），无法二分反求",
        )

    lo, hi, f_lo = low, high, f_low
    mid = 0.5 * (lo + hi)
    for _ in range(MAX_ITERATIONS):
        mid = 0.5 * (lo + hi)
        f_mid = probe(mid)
        if math.isfinite(f_mid) and abs(f_mid) <= tolerance:
            return solve_at(mid)
        if (f_lo > 0) == (f_mid > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid

    raise EvalError(
        "not_converged",
        ["body", "bounds"],
        f"二分 {MAX_ITERATIONS} 次后仍未收敛到容差 {tolerance:g} 以内，"
        f"最后中点为 {mid:g}",
    )
