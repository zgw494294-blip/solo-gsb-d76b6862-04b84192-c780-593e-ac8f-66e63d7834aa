"""POST /v1/infer-unit 的行为测试：缺失单位的静态量纲推断。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

ZERO = {"length": 0, "time": 0, "mass": 0, "delta_K": 0}


def infer(body):
    return client.post("/v1/infer-unit", json=body)


def infer_ok(expression, unknown, target_unit, variables=None):
    resp = infer(
        {
            "expression": expression,
            "variables": variables or {},
            "unknown": {"name": unknown},
            "target_unit": target_unit,
        }
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def infer_err(expression, unknown, target_unit, variables=None):
    resp = infer(
        {
            "expression": expression,
            "variables": variables or {},
            "unknown": {"name": unknown},
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

def test_infer_distance_from_velocity():
    # d / t，t 以 h 已知，目标 m/s => d 的量纲是长度，规范单位 m
    body = infer_ok("d / t", "d", "m/s", {"t": {"unit": "h"}})
    assert body["name"] == "d"
    assert body["unit"] == "m"
    assert body["exponents"] == {"length": 1, "time": 0, "mass": 0, "delta_K": 0}


def test_infer_force_formula():
    # M * d / t^2 = kg*m/s^2 => d 为长度
    body = infer_ok(
        "M * d / (t * t)", "d", "kg*m/s^2",
        {"M": {"unit": "kg"}, "t": {"unit": "s"}},
    )
    assert body["unit"] == "m"
    assert body["exponents"]["length"] == 1
    assert body["exponents"]["mass"] == 0


def test_infer_via_square():
    body = infer_ok("x*x", "x", "m^2")
    assert body["unit"] == "m"
    assert body["exponents"] == {"length": 1, "time": 0, "mass": 0, "delta_K": 0}


def test_infer_unknown_repeated_linear():
    # x*x - 3*x + 2 的目标无量纲 => x 无量纲（出现 3 次）
    body = infer_ok("x*x - 3*x + 2", "x", "1")
    assert body["unit"] == "1"
    assert body["exponents"] == ZERO


def test_infer_through_addition_constraint():
    # x + a 且 a 为 m => x 必须为 m，即使目标也是 m
    body = infer_ok("x + a", "x", "m", {"a": {"unit": "m"}})
    assert body["unit"] == "m"
    assert body["exponents"]["length"] == 1


def test_infer_addition_unknown_on_right():
    body = infer_ok("a + x", "x", "m", {"a": {"unit": "cm"}})
    assert body["unit"] == "m"


def test_infer_addition_with_dimensionless_number():
    body = infer_ok("x + 1", "x", "1")
    assert body["unit"] == "1"
    assert body["exponents"] == ZERO


def test_infer_dimensionless_quotient_of_knowns():
    # x 单独出现，两侧都是 m => x 无量纲
    body = infer_ok("a / b * x", "x", "1", {"a": {"unit": "m"}, "b": {"unit": "km"}})
    assert body["unit"] == "1"


def test_infer_reciprocal():
    body = infer_ok("1 / x", "x", "1/s")
    assert body["unit"] == "s"
    assert body["exponents"]["time"] == 1


def test_infer_energy_unit_canonical_form():
    body = infer_ok("x", "x", "kg*m^2/s^2")
    assert body["unit"] == "m^2*kg/s^2"
    assert body["exponents"] == {"length": 2, "time": -2, "mass": 1, "delta_K": 0}


def test_infer_only_denominator_and_negative_mass():
    body = infer_ok("x", "x", "1/(s^2)*kg")
    assert body["unit"] == "kg/s^2"
    assert body["exponents"] == {"length": 0, "time": -2, "mass": 1, "delta_K": 0}


def test_infer_multiple_negative_powers_parenthesized():
    body = infer_ok("x", "x", "1/(m*s)")
    assert body["unit"] == "1/(m*s)"
    assert body["exponents"] == {"length": -1, "time": -1, "mass": 0, "delta_K": 0}


def test_infer_temperature_delta_rate():
    # d / t，d 为 delta_K，目标 delta_K/s => t 为 s
    body = infer_ok("d / t", "t", "delta_K/s", {"d": {"unit": "delta_degF"}})
    assert body["unit"] == "s"


def test_infer_delta_temperature_dimension():
    body = infer_ok("d / t", "d", "delta_K/s", {"t": {"unit": "min"}})
    assert body["unit"] == "delta_K"
    assert body["exponents"] == {"length": 0, "time": 0, "mass": 0, "delta_K": 1}


def test_infer_unary_minus_does_not_change_dimension():
    body = infer_ok("-x + 2", "x", "1")
    assert body["unit"] == "1"


def test_known_dimensionless_unit_one():
    body = infer_ok("x * k", "x", "m", {"k": {"unit": "1"}})
    assert body["unit"] == "m"


def test_numbers_are_dimensionless():
    body = infer_ok("2 * 3 * x", "x", "kg")
    assert body["unit"] == "kg"


def test_infer_unit_prefix_units_map_to_base_dimensions():
    # cm、km 只是长度的不同倍率，推断出的规范单位恒为 m
    body = infer_ok("a * x", "x", "m^2", {"a": {"unit": "cm"}})
    assert body["unit"] == "m"


# ---------------------------------------------------------------------------
# 422：无解 / 非整数 / 欠定
# ---------------------------------------------------------------------------

def test_non_integer_solution_from_square_rejected():
    e = infer_err("x*x", "x", "m")
    assert e["type"] == "non_integer_dimension"
    assert e["loc"][:2] == ["body", "target_unit"]


def test_underdetermined_when_unknown_cancels_completely():
    e = infer_err("x / x", "x", "1")
    assert e["type"] == "underdetermined_dimension"
    assert e["loc"] == ["body", "unknown", "name"]


def test_underdetermined_via_pure_muldiv_cancellation():
    # (x*a)/(x*b)：x 经乘除完全消去，且没有任何加减约束钉住 x；a、b 同量纲，
    # 比值恒为无量纲，x 任意 => 欠定
    e = infer_err(
        "(x * a) / (x * b)", "x", "1",
        {"a": {"unit": "m"}, "b": {"unit": "km"}},
    )
    assert e["type"] == "underdetermined_dimension"


def test_addsub_inside_cancelled_subexpression_still_pins():
    # (x+a)-(x+a) 数值恒为 0，但量纲仍是长度（长度-长度=长度），
    # 内部 x+a 的加减相容性要求 x 与 a 同量纲；目标 m => x 为 m。
    body = infer_ok("(x + a) - (x + a)", "x", "m", {"a": {"unit": "m"}})
    assert body["unit"] == "m"


def test_length_minus_length_is_not_dimensionless():
    # 长度减长度仍是长度：目标 1（无量纲）不兼容，报 422
    e = infer_err("(x + a) - (x + a)", "x", "1", {"a": {"unit": "m"}})
    assert e["type"] == "incompatible_units"
    assert e["loc"][:2] == ["body", "target_unit"]


def test_cancelled_unknown_with_incompatible_target_is_incompatible():
    e = infer_err("x / x", "x", "m")
    assert e["type"] == "incompatible_units"
    assert e["loc"][:2] == ["body", "target_unit"]


def test_addition_with_known_incompatible_target():
    # x - a，a 为 m：x 被迫是 m，但目标为 s => 约束不相容
    e = infer_err("x - a", "x", "s", {"a": {"unit": "m"}})
    assert e["type"] == "incompatible_units"
    # 不相容首先在目标等式处暴露（addsub 与目标两条等式冲突）
    assert e["loc"][:2] == ["body", "target_unit"]


def test_addsub_fixed_dimensions_mismatch_locates_operator():
    # 已知量之间的加减量纲不兼容，与待推断变量无关
    e = infer_err(
        "x * (a + b)", "x", "m*s",
        {"a": {"unit": "m"}, "b": {"unit": "s"}},
    )
    assert e["type"] == "incompatible_units"
    assert e["loc"] == ["body", "expression", 7]


def test_addsub_non_integer_constraint_locates_operator():
    # 2x = m（指数层面）：x 的长度指数须为 1/2
    e = infer_err("x * x + a", "x", "m^2", {"a": {"unit": "m"}})
    assert e["type"] == "non_integer_dimension"
    assert e["loc"][:2] == ["body", "expression"]


def test_conflicting_addsub_constraints_locate_operator():
    # x+a 要求 x=m，x+b 要求 x=s，两者相减必须同量纲 -> 在第二个 +/- 处不相容
    e = infer_err(
        "(x + a) - (x + b)", "x", "m",
        {"a": {"unit": "m"}, "b": {"unit": "s"}},
    )
    assert e["type"] == "incompatible_units"
    assert e["loc"][:2] == ["body", "expression"]


def test_dimensional_plus_dimensionless_rejected_even_when_target_pins():
    # 1.5*x（目标 kg，故 x 为 kg）与无量纲数字 2 相加：量纲不兼容。
    # 即使目标等式先把 x 钉成 kg，加减等式仍须成立 -> 422，不得放过。
    e = infer_err("1.5*x + 2", "x", "kg")
    assert e["type"] == "incompatible_units"


# ---------------------------------------------------------------------------
# 422：绝对温度输入
# ---------------------------------------------------------------------------

def test_absolute_temperature_known_variable_rejected():
    for unit in ("K", "degC", "degF"):
        e = infer_err("t + x", "x", "delta_K", {"t": {"unit": unit}})
        assert e["type"] == "absolute_temperature", unit
        assert e["loc"] == ["body", "variables", "t", "unit"], unit


def test_absolute_temperature_target_rejected_even_in_compound():
    e = infer_err("d / t", "d", "K/s", {"t": {"unit": "s"}})
    assert e["type"] == "absolute_temperature"
    assert e["loc"][:2] == ["body", "target_unit"]


def test_absolute_degC_standalone_target_rejected():
    e = infer_err("x", "x", "degC")
    assert e["type"] == "absolute_temperature"
    assert e["loc"] == ["body", "target_unit", 0]


# ---------------------------------------------------------------------------
# 422：输入定位
# ---------------------------------------------------------------------------

def test_unknown_name_conflicts_with_known_variable():
    e = infer_err("x", "x", "m", {"x": {"unit": "m"}})
    assert e["type"] == "unknown_variable_conflict"
    assert e["loc"] == ["body", "unknown", "name"]


def test_unknown_not_in_expression():
    e = infer_err("1 + 2", "x", "1")
    assert e["type"] == "unknown_not_in_expression"
    assert e["loc"] == ["body", "unknown", "name"]


def test_other_undefined_variable_points_to_expression():
    e = infer_err("x + y", "x", "1")
    assert e["type"] == "unknown_variable"
    assert e["loc"] == ["body", "expression", 4]


def test_unknown_known_unit_points_to_field():
    e = infer_err("x + a", "x", "1", {"a": {"unit": "mile"}})
    assert e["type"] == "unknown_unit"
    assert e["loc"] == ["body", "variables", "a", "unit"]


def test_invalid_target_unit_syntax_locates_target():
    e = infer_err("x", "x", "m/")
    assert e["type"] == "invalid_target_unit"
    assert e["loc"][:2] == ["body", "target_unit"]


def test_unknown_unit_symbol_in_target_locates_target():
    e = infer_err("x", "x", "mile")
    assert e["type"] == "unknown_unit"
    assert e["loc"][:2] == ["body", "target_unit"]


def test_syntax_error_points_to_expression():
    e = infer_err("x +", "x", "m")
    assert e["type"] == "syntax_error"
    assert e["loc"][:2] == ["body", "expression"]


def test_missing_fields_are_422():
    assert infer({"expression": "x"}).status_code == 422
    assert infer({}).status_code == 422


def test_illegal_unknown_name_shape_is_422():
    resp = infer({"expression": "x", "variables": {},
                  "unknown": {"name": "1x"}, "target_unit": "m"})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"][:3] == ["body", "unknown", "name"]


def test_unknown_appears_more_than_once_is_allowed():
    # 待推断变量可重复出现：x + x = 2x，目标 m => x 为 m
    body = infer_ok("x + x", "x", "m")
    assert body["unit"] == "m"


def test_request_carries_no_values():
    # variables 只需 unit；误传 value 也被忽略（额外字段），不需要数值
    resp = infer({
        "expression": "d / t",
        "variables": {"t": {"unit": "h", "value": 2}},
        "unknown": {"name": "d"},
        "target_unit": "m/s",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["unit"] == "m"


# ---------------------------------------------------------------------------
# 既有接口保持不变
# ---------------------------------------------------------------------------

def test_evaluate_still_works():
    resp = client.post(
        "/v1/evaluate",
        json={
            "expression": "dist / time",
            "variables": {"dist": {"value": 36, "unit": "km"},
                          "time": {"value": 2, "unit": "h"}},
            "target_unit": "m/s",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == pytest.approx(5.0)


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
    assert resp.status_code == 200
    assert resp.json()["value"] == pytest.approx(36.0, abs=1e-5)


def test_healthz_still_works():
    assert client.get("/healthz").status_code == 200
