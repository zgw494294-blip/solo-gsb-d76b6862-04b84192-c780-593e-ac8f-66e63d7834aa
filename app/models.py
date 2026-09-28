"""请求/响应模型。"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, field_validator


def _reject_bool(v):
    if isinstance(v, bool):
        raise ValueError("必须是有限数值，不能是布尔值")
    return v


# 有限浮点数：拒绝布尔、inf 与 NaN（布尔须在转 float 之前拦截）。
FiniteFloat = Annotated[float, BeforeValidator(_reject_bool), Field(allow_inf_nan=False)]


class Variable(BaseModel):
    value: FiniteFloat = Field(description="有限数值（不接受 inf/NaN/布尔）")
    unit: str = Field(min_length=1, description="受支持的单位符号，见 README 单位表")


class EvalRequest(BaseModel):
    expression: str = Field(
        min_length=1,
        description="算式：数字、变量、括号、一元正负与 + - * /",
        examples=["dist / time"],
    )
    variables: dict[str, Variable] = Field(
        default_factory=dict, description="变量名 -> {value, unit}"
    )
    target_unit: str = Field(
        min_length=1,
        description="目标单位：受支持单位组成的乘除式（可带整数幂）或 1",
        examples=["m/s"],
    )


class EvalResponse(BaseModel):
    value: float = Field(description="按目标单位换算后的数值")
    unit: str = Field(description="归一化后的目标单位")
    dimension: str = Field(description="结果量纲，如 [length] / [time]；无量纲为 dimensionless")


class UnknownVariable(BaseModel):
    name: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="待求变量名：合法标识符，须在算式中出现且不与已知变量重名",
    )
    unit: str = Field(min_length=1, description="待求变量的单位：受支持的单个单位或 1（无量纲）")


class InvertRequest(BaseModel):
    expression: str = Field(
        min_length=1,
        description="算式：数字、变量、括号、一元正负与 + - * /；待求变量须出现",
        examples=["dist / time"],
    )
    variables: dict[str, Variable] = Field(
        default_factory=dict, description="已知变量名 -> {value, unit}"
    )
    unknown: UnknownVariable = Field(description="待求变量的名称与单位")
    bounds: tuple[FiniteFloat, FiniteFloat] = Field(
        description="待求变量的严格递增有限数值区间 [下界, 上界]（数值按待求变量单位解释）"
    )
    target: Variable = Field(description="期望结果 {value, unit}")

    @field_validator("bounds")
    @classmethod
    def _bounds_strictly_increasing(cls, v: tuple[float, float]) -> tuple[float, float]:
        if not (v[0] < v[1]):
            raise ValueError("数值区间必须严格递增：下界必须严格小于上界")
        return v


class InvertResponse(BaseModel):
    name: str = Field(description="待求变量名")
    value: float = Field(description="反求出的待求变量数值（按其声明单位）")
    unit: str = Field(description="待求变量的单位")
    result: float = Field(description="代入待求值后，按目标单位表示的实际结果")
    result_unit: str = Field(description="归一化后的目标单位")
    dimension: str = Field(description="结果量纲，如 [length] / [time]；无量纲为 dimensionless")
