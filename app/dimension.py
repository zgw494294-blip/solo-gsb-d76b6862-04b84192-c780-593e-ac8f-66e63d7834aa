"""静态量纲推断：算式中恰有一个变量缺少单位时，由量纲方程求其单位。

不代入任何数值，只在四个**基础量纲**上做整数线性代数：
长度 m、时间 s、质量 kg、温差 delta_K。

每个子式的量纲表示为关于待推断变量指数向量 x 的仿射单项式

    k + e * x

其中 k 是四个已知整数指数组成的向量，e 是待推断变量在该子式中的
净重数（整数，高次时 |e| 可为 2 以上）：

- 数字：无量纲（k=0, e=0）；
- 已知变量：其单位的量纲（k=单位指数, e=0）；
- 待推断变量：k=0, e=1；
- 乘除：k 与 e 分别相加 / 相减（如 x*x 的 e=2，x/x 消成 k=0, e=0）；
- 加减：两侧量纲必须相同，产生一条线性等式（约束）；相容时合并结果
  与两侧等量纲，任取一侧的仿射式向上传播即可。

最终算式的量纲须等于目标量纲，再增加一条等式。所有等式构成关于 x
的整数线性方程组：每个待推断指数可能被多条等式约束，必须被钉成
同一个整数且相容（非整数报 422）；完全没有被钉住的指数属于欠定，
同样报 422。
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import EvalError
from .expression import Bin, Num, Unary, Var
from .units import ureg

# 基础量纲名 -> 分量下标；规范单位按此顺序输出。
BASE_DIMENSIONS: tuple[str, ...] = ("length", "time", "mass", "delta_K")
# Pint 量纲字典中的键
_PINT_DIM_KEY = ("[length]", "[time]", "[mass]", "[temperature]")
CANONICAL_BASE_UNITS: dict[str, str] = {
    "length": "m",
    "time": "s",
    "mass": "kg",
    "delta_K": "delta_K",
}

_VEC = tuple[int, int, int, int]
_ZERO: _VEC = (0, 0, 0, 0)


@dataclass(frozen=True)
class AffineDim:
    """子式量纲 = known + mult * unknown，均为四维整数向量/整数。"""

    known: _VEC
    mult: int

    @classmethod
    def const(cls, known: _VEC) -> "AffineDim":
        return cls(known, 0)

    @classmethod
    def unknown_dim(cls) -> "AffineDim":
        return cls(_ZERO, 1)

    @classmethod
    def zero(cls) -> "AffineDim":
        return cls(_ZERO, 0)

    def _combine(self, other: "AffineDim", sign: int) -> "AffineDim":
        return AffineDim(
            tuple(self.known[i] + sign * other.known[i] for i in range(4)),  # type: ignore[index]
            self.mult + sign * other.mult,
        )

    def mul(self, other: "AffineDim") -> "AffineDim":
        return self._combine(other, 1)

    def div(self, other: "AffineDim") -> "AffineDim":
        return self._combine(other, -1)


def _unit_exponents(unit: str) -> _VEC:
    """受支持的单个单位（含 1）-> 四个基础量纲的指数。"""
    if unit == "1":
        return _ZERO
    dims = ureg.Unit(unit).dimensionality
    return tuple(int(dims.get(key, 0)) for key in _PINT_DIM_KEY)  # type: ignore[return-value]


def _target_exponents(target) -> _VEC:
    dims = target.pint_unit.dimensionality
    return tuple(int(dims.get(key, 0)) for key in _PINT_DIM_KEY)  # type: ignore[return-value]


@dataclass
class _Constraint:
    """一条等式 mult * x = rhs；pos 为产生该等式的算式位置（目标等式为 None）。"""

    mult: int
    rhs: _VEC
    pos: int | None
    kind: str  # addsub | target


def _collect(node, known_dims: dict[str, _VEC], unknown_name: str):
    """递归计算子式量纲并收集加减相容性等式。"""
    constraints: list[_Constraint] = []

    def walk(n) -> AffineDim:
        if isinstance(n, Num):
            # 数字视为无量纲
            return AffineDim.zero()
        if isinstance(n, Var):
            if n.name == unknown_name:
                return AffineDim.unknown_dim()
            return AffineDim.const(known_dims[n.name])
        if isinstance(n, Unary):
            # 一元正负不改变量纲
            return walk(n.operand)
        if isinstance(n, Bin):
            left = walk(n.left)
            right = walk(n.right)
            if n.op in "+-":
                # 两侧量纲必须相同：
                #   left.known + left.mult*x = right.known + right.mult*x
                #   => (left.mult - right.mult) * x = right.known - left.known
                constraints.append(
                    _Constraint(
                        left.mult - right.mult,
                        tuple(right.known[i] - left.known[i] for i in range(4)),  # type: ignore[index]
                        n.pos,
                        "addsub",
                    )
                )
                # 相容时两侧量纲相同；任选一侧的仿射式向上传播，优先标准的 x 形。
                return _addsub_repr(left, right)
            if n.op == "*":
                return left.mul(right)
            return left.div(right)
        raise AssertionError(f"未知 AST 节点: {n!r}")

    result = walk(node)
    return result, constraints


def _addsub_repr(left: AffineDim, right: AffineDim) -> AffineDim:
    """两侧量纲相容时，为加减结果选一个与两侧都相等的标准代表。

    优先取恰为 x（known=0、mult=1）的一侧，否则取左侧。例如
    ``(x + a) - (x + a)`` 两侧都是 x 的仿射式，选 x 后外层相减才能
    正确消成无量纲，而不是残留 x 系数。
    """
    std = AffineDim.unknown_dim()
    if left != std and right == std:
        return right
    return left


def _solve(constraints: list[_Constraint]):
    """解 mult*x=rhs 方程组，返回四个整数指数。

    每个分量独立处理（mult 是标量，对四个分量相同）：
    - mult=0 的等式要求 rhs 对应分量为 0，否则无解（量纲不兼容）；
    - mult≠0 的等式给出一个候选整数 rhs/mult（非整数报 422），
      所有候选必须相同，否则约束不相容；
    - 没有任何 mult≠0 等式覆盖的分量属于欠定。
    """
    pinned: list[int | None] = [None] * 4

    def fail(c: _Constraint, type_: str, msg: str) -> None:
        if c.kind == "target":
            raise EvalError.at_target(type_, msg)
        raise EvalError.at_expression(type_, c.pos, msg)

    for c in constraints:
        if c.mult == 0:
            if any(c.rhs[i] != 0 for i in range(4)):
                if c.kind == "target":
                    msg = (
                        "算式量纲（待推断变量未出现或已完全消去）与目标单位量纲 "
                        f"{tuple(c.rhs)} 不兼容"
                    )
                else:
                    msg = (
                        f"位置 {c.pos}：加减两侧量纲不兼容，固定量纲指数相差 "
                        f"{tuple(c.rhs)}（[长度, 时间, 质量, 温差]），"
                        "无法通过任何待推断变量单位弥合"
                    )
                fail(c, "incompatible_units", msg)
            continue

        for i in range(4):
            if c.rhs[i] % c.mult != 0:
                value = c.rhs[i] / c.mult
                if c.kind == "target":
                    msg = (
                        f"待推断变量的{BASE_DIMENSIONS[i]}指数须为 {value:g}，"
                        "不是整数；单位量纲只允许整数指数"
                    )
                else:
                    msg = (
                        f"位置 {c.pos}：加减量纲相容性要求待推断变量的"
                        f"{BASE_DIMENSIONS[i]}指数为 {value:g}，不是整数"
                    )
                fail(c, "non_integer_dimension", msg)

            value = c.rhs[i] // c.mult
            if pinned[i] is not None and pinned[i] != value:
                if c.kind == "target":
                    msg = (
                        f"量纲约束不相容：{BASE_DIMENSIONS[i]}指数同时被要求为 "
                        f"{pinned[i]} 与 {value}"
                    )
                else:
                    msg = (
                        f"位置 {c.pos}：量纲约束不相容，{BASE_DIMENSIONS[i]}指数同时被"
                        f"要求为 {pinned[i]} 与 {value}"
                    )
                fail(c, "incompatible_units", msg)
            pinned[i] = value

    free = [name for name, v in zip(BASE_DIMENSIONS, pinned) if v is None]
    if free:
        raise EvalError(
            "underdetermined_dimension",
            ["body", "unknown", "name"],
            "待推断变量的量纲在算式中没有足够约束，以下基础量纲无法唯一推断："
            + ", ".join(free),
        )
    return tuple(pinned)  # type: ignore[return-value]


def canonical_unit(exponents) -> str:
    """四个基础量纲指数 -> m、s、kg、delta_K 组成的规范单位式；全零为 1。"""
    pos_factors = [
        (CANONICAL_BASE_UNITS[name], e)
        for name, e in zip(BASE_DIMENSIONS, exponents) if e > 0
    ]
    neg = [
        (CANONICAL_BASE_UNITS[name], -e)
        for name, e in zip(BASE_DIMENSIONS, exponents) if e < 0
    ]

    numerator = "*".join(u if p == 1 else f"{u}^{p}" for u, p in pos_factors) or "1"
    if not neg:
        return numerator
    denominator = "*".join(u if p == 1 else f"{u}^{p}" for u, p in neg)
    if len(neg) > 1:
        denominator = f"({denominator})"
    return f"{numerator}/{denominator}"


def infer_dimensions(ast, known_units: dict[str, str], unknown_name: str, target):
    """静态推断 unknown_name 的量纲，返回 (规范单位式, 四指数元组)。"""
    known_dims = {name: _unit_exponents(unit) for name, unit in known_units.items()}

    result, constraints = _collect(ast, known_dims, unknown_name)
    target_exp = _target_exponents(target)
    # 最终结果量纲 = 目标量纲：result.mult * x = target - result.known
    constraints.append(
        _Constraint(
            result.mult,
            tuple(target_exp[i] - result.known[i] for i in range(4)),
            None,
            "target",
        )
    )

    exponents = _solve(constraints)
    return canonical_unit(exponents), exponents
