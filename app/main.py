"""FastAPI 应用入口：POST /v1/evaluate、POST /v1/invert。"""
from __future__ import annotations

import math

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .errors import EvalError
from .evaluator import convert_result, evaluate, value_from_unit
from .expression import parse_expression, variable_names
from .invert import invert
from .models import EvalRequest, EvalResponse, InvertRequest, InvertResponse
from .units import ALLOWED_UNITS, VARIABLE_UNITS, parse_target

app = FastAPI(
    title="量纲公式求值 API",
    version="1.1.0",
    description="离线量纲公式求值：解析算术算式，按单位代数求值并换算到目标单位；支持目标值反求。",
)


def _json_safe(obj):
    """把错误详情中的非有限浮点数替换为字符串，避免 inf/NaN 破坏 JSON 响应。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else repr(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


@app.exception_handler(EvalError)
async def eval_error_handler(_: Request, exc: EvalError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": [{"type": exc.type, "loc": exc.loc, "msg": exc.msg}]},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # 默认处理器会回显输入；其中的 inf/NaN 无法序列化为合法 JSON（会变成 500），
    # 这里保持同样的 422 结构（jsonable_encoder），仅把非有限浮点替换为字符串。
    detail = jsonable_encoder(exc.errors())
    return JSONResponse(status_code=422, content={"detail": _json_safe(detail)})


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


def _build_env(variables, allowed_units: set[str]) -> dict:
    """把请求中的变量构造为求值环境；未知单位抛出指向该字段的 422。"""
    env: dict = {}
    for name, var in variables.items():
        if var.unit not in allowed_units:
            raise EvalError.at_variable(
                "unknown_unit",
                name,
                "unit",
                f"变量 {name!r} 的单位 {var.unit!r} 不受支持，"
                f"可用单位：{', '.join(sorted(allowed_units))}",
            )
        env[name] = value_from_unit(var.value, var.unit)
    return env


@app.post("/v1/evaluate", response_model=EvalResponse)
def evaluate_endpoint(req: EvalRequest) -> EvalResponse:
    ast = parse_expression(req.expression)
    env = _build_env(req.variables, ALLOWED_UNITS)
    result = evaluate(ast, env)

    target = parse_target(req.target_unit)
    value, dimension = convert_result(result, target)
    return EvalResponse(value=value, unit=target.canonical, dimension=dimension)


@app.post("/v1/invert", response_model=InvertResponse)
def invert_endpoint(req: InvertRequest) -> InvertResponse:
    ast = parse_expression(req.expression)

    # 待求变量不能与已知变量重名；其单位须受支持（允许 1 表示无量纲）。
    if req.unknown.name in req.variables:
        raise EvalError(
            "unknown_variable_conflict",
            ["body", "unknown", "name"],
            f"待求变量 {req.unknown.name!r} 与已知变量重名",
        )
    if req.unknown.unit not in VARIABLE_UNITS:
        raise EvalError(
            "unknown_unit",
            ["body", "unknown", "unit"],
            f"待求变量的单位 {req.unknown.unit!r} 不受支持，"
            f"可用单位：{', '.join(sorted(VARIABLE_UNITS))}",
        )

    known_env = _build_env(req.variables, ALLOWED_UNITS)

    # 待求变量必须在算式中出现；算式也不得引用其它未定义变量。
    names = variable_names(ast)
    if req.unknown.name not in names:
        raise EvalError(
            "unknown_not_in_expression",
            ["body", "unknown", "name"],
            f"待求变量 {req.unknown.name!r} 未在算式中出现",
        )
    for name, pos in names.items():
        if name != req.unknown.name and name not in known_env:
            raise EvalError.at_expression(
                "unknown_variable",
                pos,
                f"位置 {pos}：未知变量 {name!r}，已定义的变量："
                f"{', '.join(sorted(set(known_env) | {req.unknown.name}))}",
            )

    solution = invert(
        ast,
        known_env,
        req.unknown.name,
        req.unknown.unit,
        req.bounds[0],
        req.bounds[1],
        req.target.value,
        req.target.unit,
    )
    return InvertResponse(
        name=req.unknown.name,
        value=solution.value,
        unit=req.unknown.unit,
        result=solution.result,
        result_unit=solution.result_unit,
        dimension=solution.dimension,
    )
