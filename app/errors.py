"""领域错误类型：携带指向请求字段或算式位置的 loc，统一映射为 422。"""
from __future__ import annotations


class EvalError(Exception):
    """可定位的求值错误。

    ``loc`` 沿用 FastAPI 校验错误的结构，例如：

    - ``["body", "expression", 4]``       算式中偏移量 4（0 起）的字符附近
    - ``["body", "variables", "t", "unit"]``  某个变量字段
    - ``["body", "target_unit", 2]``      目标单位中偏移量 2 的字符附近
    """

    def __init__(self, type_: str, loc: list, msg: str) -> None:
        super().__init__(msg)
        self.type = type_
        self.loc = loc
        self.msg = msg

    @classmethod
    def at_expression(cls, type_: str, pos: int | None, msg: str) -> "EvalError":
        loc = ["body", "expression"] + ([] if pos is None else [pos])
        return cls(type_, loc, msg)

    @classmethod
    def at_target(
        cls,
        type_: str,
        msg: str,
        pos: int | None = None,
        prefix: tuple[str, ...] = ("body", "target_unit"),
    ) -> "EvalError":
        loc = list(prefix) + ([] if pos is None else [pos])
        return cls(type_, loc, msg)

    @classmethod
    def at_variable(cls, type_: str, name: str, field: str, msg: str) -> "EvalError":
        return cls(type_, ["body", "variables", name, field], msg)
