"""POST /v1/dimension 的行为测试：缺失变量单位的静态量纲推断。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def dim(body):
    return client.post("/v1/dimension", json=body)


def dim_ok(expression, unknown, target_unit, variables=None):
    resp = dim(
        {
            "expression": expression,
            "variables": variables or {},
            "unknown": unknown,
            "target_unit": target_unit,
        }
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def dim_err(expression, unknown, target_unit, variables=None):
    resp = dim(
        {
            "expression": expression,
            "variables": variables or {},
            "unknown": unknown,
            "target_unit": target_unit,
        }
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, list) and detail, resp.text
    return detail[0]


# ---------------------------------------------------------------------------
# 成功推断
# ---------------------------------------------------------------------------

def test_velocity_infer_distance():
    # d / t = 5 m/s，t 以 h 已知 -> d 的量纲为长度
    body = dim_ok("d / t", "d", "m/s", {"t": {"unit": "h"}})
    assert body["name"] == "d"
    assert body["unit"] == "m"
    assert body["exponents"] == {"length": 1, "time": 0, "mass": 0, "delta_temperature": 0}


def test_force_infer_mass():
    # M * d / (t * t) = kg*m/s^2，d 以 m、t 以 s 已知 -> M 的量纲为质量
    body = dim_ok("M * d / (t * t)", "M", "kg*m/s^2", {"d": {"unit": "m"}, "t": {"unit": "s"}})
    assert body["unit"] == "kg"
    assert body["exponents"] == {"length": 0, "time": 0, "mass": 1, "delta_temperature": 0}


def test_infer_time_with_unit_conversions():
    # d / t = m/s，d 以 km 已知 -> t 为时间（单位换算不影响量纲）
    body = dim_ok("d / t", "t", "m/s", {"d": {"unit": "km"}})
    assert body["unit"] == "s"
    assert body["exponents"]["time"] == 1


def test_square_root_is_integer_when_even():
    body = dim_ok("x * x", "x", "m^2")
    assert body["unit"] == "m"
    assert body["exponents"]["length"] == 1


def test_unknown_repeated_with_dimensionless_solution():
    # x*x - 3*x + 2 = 1（无量纲）-> x 无量纲
    body = dim_ok("x*x - 3*x + 2", "x", "1")
    assert body["unit"] == "1"
    assert body["exponents"] == {"length": 0, "time": 0, "mass": 0, "delta_temperature": 0}


def test_unary_minus_does_not_change_dimension():
    body = dim_ok("-x + a", "x", "m", {"a": {"unit": "m"}})
    assert body["unit"] == "m"
    body = dim_ok("a - -x", "x", "m", {"a": {"unit": "m"}})
    assert body["unit"] == "m"


def test_addition_unknown_matches_known_dimension():
    body = dim_ok("a + b + x", "x", "m", {"a": {"unit": "m"}, "b": {"unit": "cm"}})
    assert body["unit"] == "m"


def test_delta_temperature_dimension():
    # dK / t = delta_K/s -> dK 为温差
    body = dim_ok("dK / t", "dK", "delta_K/s", {"t": {"unit": "s"}})
    assert body["unit"] == "delta_K"
    assert body["exponents"]["delta_temperature"] == 1
    # 目标用 K/s（K 是无偏移绝对温度单位）-> 本接口拒绝绝对温度单位
    e = dim_err("dK / t", "dK", "K/s", {"t": {"unit": "s"}})
    assert e["type"] == "absolute_temperature"
    assert e["loc"][:2] == ["body", "target_unit"]


def test_canonical_units_for_all_bases():
    # x * M * d / (t*t) = 1（无量纲），M=kg、d=m、t=s -> x = s^2/(m*kg)
    body = dim_ok(
        "x * M * d / (t * t)",
        "x",
        "1",
        {"M": {"unit": "kg"}, "d": {"unit": "m"}, "t": {"unit": "s"}},
    )
    assert body["unit"] == "s^2/(m*kg)"
    assert body["exponents"] == {"length": -1, "time": 2, "mass": -1, "delta_temperature": 0}


def test_dimensionless_factor_two():
    body = dim_ok("2 * x", "x", "1")
    assert body["unit"] == "1"


def test_self_cancellation_underdetermined_vs_determined():
    # 乘除抵消（x/x 恒为无量纲 1）会彻底抹掉落入变量：目标为 1 也欠定。
    e = dim_err("x / x", "x", "1")
    assert e["type"] == "underdetermined_dimension"
    assert e["loc"] == ["body", "target_unit"]
    # 加减“抵消”（x - x = 0）仍带有 x 的量纲：目标为 1 可确定 x 无量纲。
    body = dim_ok("x - x", "x", "1")
    assert body["unit"] == "1"


# ---------------------------------------------------------------------------
# 422：无解 / 非整数 / 欠定 / 不相容
# ---------------------------------------------------------------------------

def test_non_integer_square_root_rejected():
    # x^2 = m -> x = m^(1/2)，非整数解
    e = dim_err("x * x", "x", "m")
    assert e["type"] == "non_integer_dimension"
    assert e["loc"] == ["body", "target_unit"]


def test_non_integer_from_add_constraint_points_to_expression():
    # x*x + t = s^2：加法要求 x^2 与 s 同量纲 -> n = 1/2
    e = dim_err("x*x + t", "x", "s^2", {"t": {"unit": "s"}})
    assert e["type"] == "non_integer_dimension"
    assert e["loc"] == ["body", "expression", 4]


def test_incompatible_addition_points_to_operator():
    # x + m + s 中 m 与 s 不可能同量纲：定位到第二个 '+'（位置 6）
    e = dim_err("x + m + s", "x", "m", {"m": {"unit": "m"}, "s": {"unit": "s"}})
    assert e["type"] == "incompatible_units"
    assert e["loc"] == ["body", "expression", 6]


def test_expression_target_dimension_conflict_locates_target():
    # x + m 迫使 x 为长度，但目标是时间
    e = dim_err("x + m", "x", "s", {"m": {"unit": "m"}})
    assert e["type"] == "incompatible_units"
    assert e["loc"] == ["body", "target_unit"]


def test_cancelled_result_targeting_dimensionful_unit_rejected():
    # x/x 恒为无量纲，却要求结果为 m
    e = dim_err("x / x", "x", "m")
    assert e["type"] == "incompatible_units"
    assert e["loc"] == ["body", "target_unit"]


def test_underdetermined_when_unknown_cancels():
    e = dim_err("x * m / x", "x", "m", {"m": {"unit": "m"}})
    assert e["type"] == "underdetermined_dimension"
    assert e["loc"] == ["body", "target_unit"]


def test_underdetermined_when_added_to_cancelled_term():
    e = dim_err("x / x + t", "x", "s", {"t": {"unit": "s"}})
    assert e["type"] == "underdetermined_dimension"


# ---------------------------------------------------------------------------
# 422：输入校验与定位
# ---------------------------------------------------------------------------

def test_unknown_not_in_expression():
    e = dim_err("a + b", "x", "m", {"a": {"unit": "m"}, "b": {"unit": "m"}})
    assert e["type"] == "unknown_not_in_expression"
    assert e["loc"] == ["body", "unknown"]


def test_unknown_conflicts_with_known_variable():
    e = dim_err("x", "x", "m", {"x": {"unit": "m"}})
    assert e["type"] == "unknown_variable_conflict"
    assert e["loc"] == ["body", "unknown"]


def test_other_undefined_variable_points_to_expression():
    e = dim_err("x + y", "x", "m")
    assert e["type"] == "unknown_variable"
    assert e["loc"] == ["body", "expression", 4]


def test_unknown_known_variable_unit():
    e = dim_err("x", "x", "m", {"a": {"unit": "mile"}})
    assert e["type"] == "unknown_unit"
    assert e["loc"] == ["body", "variables", "a", "unit"]


def test_absolute_temperature_known_unit_rejected():
    for unit in ("K", "degC", "degF"):
        e = dim_err("x + t", "x", "m", {"t": {"unit": unit}})
        assert e["type"] == "absolute_temperature", unit
        assert e["loc"] == ["body", "variables", "t", "unit"], unit


def test_absolute_temperature_target_rejected():
    for unit in ("K", "degC", "degF", "m/degF", "K/s"):
        e = dim_err("x", "x", unit)
        assert e["type"] == "absolute_temperature", unit
        assert e["loc"][:2] == ["body", "target_unit"], unit


def test_delta_temperature_units_allowed():
    body = dim_ok("x", "x", "delta_K")
    assert body["unit"] == "delta_K"
    body = dim_ok("x", "x", "delta_degC/s")
    assert body["unit"] == "delta_K/s"


def test_invalid_target_syntax():
    e = dim_err("x", "x", "m/")
    assert e["type"] == "invalid_target_unit"
    assert e["loc"][:2] == ["body", "target_unit"]


def test_unknown_target_unit():
    e = dim_err("x", "x", "mile")
    assert e["type"] == "unknown_unit"
    assert e["loc"][:2] == ["body", "target_unit"]


def test_syntax_error_points_to_expression():
    e = dim_err("x +", "x", "m")
    assert e["type"] == "syntax_error"
    assert e["loc"][:2] == ["body", "expression"]


def test_bad_unknown_name_shape_is_422():
    assert dim(
        {"expression": "x", "variables": {}, "unknown": "1x", "target_unit": "m"}
    ).status_code == 422


def test_missing_fields_are_422():
    assert dim({"expression": "x"}).status_code == 422
    assert dim({"expression": "", "unknown": "x", "target_unit": "m"}).status_code == 422


def test_numbers_are_dimensionless():
    # 1 + 2 = 3 无量纲；待推断变量未出现 -> unknown_not_in_expression
    e = dim_err("1 + 2", "x", "1")
    assert e["type"] == "unknown_not_in_expression"


# ---------------------------------------------------------------------------
# 既有接口保持不变
# ---------------------------------------------------------------------------

def test_evaluate_still_works():
    resp = client.post(
        "/v1/evaluate",
        json={
            "expression": "dist / time",
            "variables": {"dist": {"value": 36, "unit": "km"}, "time": {"value": 2, "unit": "h"}},
            "target_unit": "m/s",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == 5.0


def test_invert_still_works():
    resp = client.post(
        "/v1/invert",
        json={
            "expression": "d / t",
            "variables": {"t": {"value": 2, "unit": "h"}},
            "unknown": {"name": "d", "unit": "km"},
            "bounds": [0, 1000],
            "target": {"value": 5, "unit": "m/s"},
        },
    )
    assert resp.status_code == 200, resp.text


def test_healthz_still_works():
    assert client.get("/healthz").status_code == 200
