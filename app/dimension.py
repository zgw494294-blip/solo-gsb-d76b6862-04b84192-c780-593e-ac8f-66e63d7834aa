"""缺失变量单位的静态量纲推断（不代入任何数值）。

把每个子式的量纲写成仿射向量 ``known + unknown * n``：

- ``known``：四个基本量纲（长度、时间、质量、温差）上的已知指数；
- ``unknown``：待推断变量在四个基本量纲上的指数系数；
- ``n``：待推断变量的（未知）量纲指数向量。

规则：

- 数字无量纲（零向量）；
- 已知变量取其单位的量纲向量；待推断变量取单位系数向量；
- 一元正负号不改变量纲；
- 乘除合并两侧指数（系数相加/相减）；
- 加减两侧必须同量纲：逐维记录线性约束 ``a*n_d + b = 0``；
- 根节点的量纲必须与目标单位相同，同样记为四个线性约束。

仅当四个维度都由相容约束给出唯一整数解时才成功；
无解、非整数解或欠定均抛出带定位的 :class:`EvalError`（由路由映射为 422）。

本模块只依赖标准库与同包的 ``errors`` / ``expression``，不导入 Pint，
量纲向量（单位 -> 四个整数指数）由调用方经 ``app.units`` 换算后传入。
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import EvalError
from .expression import Bin, Num, Unary, Var

# 四个基本量纲的顺序；规范单位式与响应中的指数均按此顺序给出。
BASE_NAMES = ("长度", "时间", "质量", "温差")
BASE_UNITS = ("m", "s", "kg", "delta_K")
DIMENSIONS = 4

_ZERO = (0, 0, 0, 0)
# 待推断变量：每个维度 d 的系数为 1（其自身量纲尚待求解）。
_SELF = (1, 1, 1, 1)


@dataclass(frozen=True)
class AffineDim:
    """量纲仿射式：known[d] + unknown[d] * n_d 为第 d 维的指数。"""

    known: tuple[int, int, int, int]
    unknown: tuple[int, int, int, int]


def _add_vec(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(x + y for x, y in zip(a, b))


def _sub_vec(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(x - y for x, y in zip(a, b))


@dataclass(frozen=True)
class _Constraint:
    """一维线性约束：coef * n_dim + const = 0；pos 为 None 表示来自根节点（目标单位）。"""

    dim: int
    coef: int
    const: int
    pos: int | None


def _loc(pos: int | None) -> list:
    return ["body", "expression", pos] if pos is not None else ["body", "target_unit"]


def _walk(node, known_dims: dict[str, tuple[int, int, int, int]], unknown_name: str,
          constraints: list[_Constraint]) -> AffineDim:
    if isinstance(node, Num):
        return AffineDim(_ZERO, _ZERO)
    if isinstance(node, Var):
        if node.name == unknown_name:
            return AffineDim(_ZERO, _SELF)
        return AffineDim(known_dims[node.name], _ZERO)
    if isinstance(node, Unary):
        # 一元正负号只是乘以无量纲数 ±1，不改变量纲。
        return _walk(node.operand, known_dims, unknown_name, constraints)
    if isinstance(node, Bin):
        left = _walk(node.left, known_dims, unknown_name, constraints)
        right = _walk(node.right, known_dims, unknown_name, constraints)
        if node.op in "+-":
            # 加减两侧须同量纲：(left - right) = 0，逐维记为约束。
            diff_known = _sub_vec(left.known, right.known)
            diff_unknown = _sub_vec(left.unknown, right.unknown)
            for d in range(DIMENSIONS):
                coef, const = diff_unknown[d], diff_known[d]
                if coef != 0 or const != 0:
                    constraints.append(_Constraint(d, coef, const, node.pos))
            # 两侧同量纲，取左侧仿射式作为本子式的量纲即可。
            return left
        if node.op == "*":
            return AffineDim(
                _add_vec(left.known, right.known), _add_vec(left.unknown, right.unknown)
            )
        return AffineDim(
            _sub_vec(left.known, right.known), _sub_vec(left.unknown, right.unknown)
        )
    raise AssertionError(f"未知 AST 节点: {node!r}")


def solve_dimensions(
    ast,
    known_dims: dict[str, tuple[int, int, int, int]],
    unknown_name: str,
    target_dim: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """静态推断 unknown_name 的量纲指数；失败抛 EvalError（映射为 422）。"""
    constraints: list[_Constraint] = []
    root = _walk(ast, known_dims, unknown_name, constraints)

    # 根节点量纲须等于目标：root.unknown[d] * n_d + (root.known[d] - target[d]) = 0。
    for d in range(DIMENSIONS):
        constraints.append(
            _Constraint(d, root.unknown[d], root.known[d] - target_dim[d], None)
        )

    solution: list[int | None] = [None] * DIMENSIONS
    for d in range(DIMENSIONS):
        # 欠定判定：该维度在加减约束与根节点（目标单位）中都没有被涉及。
        if not any(c.dim == d for c in constraints):
            raise EvalError(
                "underdetermined_dimension",
                ["body", "target_unit"],
                f"目标单位与算式都没有约束{BASE_NAMES[d]}指数，待推断变量 "
                f"{unknown_name!r} 的量纲欠定（须四个指数均有唯一整数解）",
            )
        for c in constraints:
            if c.dim != d:
                continue
            if c.coef == 0:
                if c.const != 0:
                    where = f"位置 {c.pos}" if c.pos is not None else "目标单位"
                    raise EvalError(
                        "incompatible_units",
                        _loc(c.pos),
                        f"{where}：量纲约束不相容（{BASE_NAMES[d]}指数无法满足），"
                        f"待推断变量 {unknown_name!r} 的量纲无解",
                    )
                continue
            # coef * n_d + const = 0：要求整数解。
            if (-c.const) % c.coef != 0:
                where = f"位置 {c.pos}" if c.pos is not None else "结果与目标单位"
                raise EvalError(
                    "non_integer_dimension",
                    _loc(c.pos),
                    f"{where}：{BASE_NAMES[d]}指数解为 {-c.const}/{c.coef}，"
                    f"不是整数，待推断变量 {unknown_name!r} 的量纲不存在整数解",
                )
            candidate = (-c.const) // c.coef
            if solution[d] is None:
                solution[d] = candidate
            elif solution[d] != candidate:
                where = f"位置 {c.pos}" if c.pos is not None else "目标单位"
                raise EvalError(
                    "incompatible_units",
                    _loc(c.pos),
                    f"{where}：{BASE_NAMES[d]}指数的约束互相冲突"
                    f"（{solution[d]} 与 {candidate}），待推断变量 {unknown_name!r} 的量纲无解",
                )
        if solution[d] is None:
            raise EvalError(
                "underdetermined_dimension",
                ["body", "target_unit"],
                f"算式与目标单位对{BASE_NAMES[d]}指数只有恒等约束，待推断变量 "
                f"{unknown_name!r} 的量纲在该维度上欠定（须四个指数均有唯一整数解）",
            )

    return tuple(solution)  # type: ignore[return-value]


def canonical_unit(exponents: tuple[int, int, int, int]) -> str:
    """把四个指数渲染为以 m、s、kg、delta_K 组成的规范单位式；全零为 1。"""
    if not any(exponents):
        return "1"

    def factor(base: str, exponent: int) -> str:
        return base if abs(exponent) == 1 else f"{base}^{abs(exponent)}"

    numerator = [
        factor(base, exp)
        for base, exp in zip(BASE_UNITS, exponents)
        if exp > 0
    ]
    denominator = [
        factor(base, exp)
        for base, exp in zip(BASE_UNITS, exponents)
        if exp < 0
    ]
    if numerator and denominator:
        den = "*".join(denominator)
        if len(denominator) > 1:
            den = f"({den})"
        return f"{'*'.join(numerator)}/{den}"
    if numerator:
        return "*".join(numerator)
    den = "*".join(denominator)
    if len(denominator) > 1:
        den = f"({den})"
    return f"1/{den}"
