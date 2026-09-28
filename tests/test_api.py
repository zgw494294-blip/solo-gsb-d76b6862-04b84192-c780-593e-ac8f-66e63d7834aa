"""POST /v1/evaluate 的行为测试。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def evaluate(expression, variables=None, target_unit="1"):
    return client.post(
        "/v1/evaluate",
        json={
            "expression": expression,
            "variables": variables or {},
            "target_unit": target_unit,
        },
    )


def ok(expression, variables=None, target_unit="1"):
    resp = evaluate(expression, variables, target_unit)
    assert resp.status_code == 200, resp.text
    return resp.json()


def err(expression, variables=None, target_unit="1"):
    resp = evaluate(expression, variables, target_unit)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, list) and detail, resp.text
    return detail[0]


# ---------------------------------------------------------------------------
# 基本算术与单位换算
# ---------------------------------------------------------------------------

def test_addition_converts_to_common_dimension():
    body = ok("a + b", {"a": {"value": 1, "unit": "m"}, "b": {"value": 50, "unit": "cm"}}, "m")
    assert body["value"] == pytest.approx(1.5)
    assert body["unit"] == "m"
    assert body["dimension"] == "[length]"


def test_precedence_and_parentheses():
    assert ok("1 + 2 * 3")["value"] == 7.0
    assert ok("(1 + 2) * 3")["value"] == 9.0


def test_unary_minus_and_plus():
    body = ok("-a + b", {"a": {"value": 3, "unit": "m"}, "b": {"value": 5, "unit": "m"}}, "m")
    assert body["value"] == pytest.approx(2.0)
    assert ok("+a", {"a": {"value": 3, "unit": "m"}}, "m")["value"] == 3.0
    assert ok("--a", {"a": {"value": 3, "unit": "m"}}, "m")["value"] == 3.0
    assert ok("2 - -3")["value"] == 5.0


def test_scientific_notation_and_decimals():
    assert ok("1.5e3 + 0.5")["value"] == 1500.5
    assert ok(".25 * 4")["value"] == 1.0


def test_velocity_km_per_h_to_m_per_s():
    body = ok(
        "dist / time",
        {"dist": {"value": 36, "unit": "km"}, "time": {"value": 2, "unit": "h"}},
        "m/s",
    )
    assert body["value"] == pytest.approx(5.0)
    assert body["dimension"] == "[length] / [time]"


def test_compound_target_force():
    body = ok(
        "m * d / (t * t)",
        {"m": {"value": 2, "unit": "kg"}, "d": {"value": 3, "unit": "m"}, "t": {"value": 2, "unit": "s"}},
        "kg*m/s^2",
    )
    assert body["value"] == pytest.approx(1.5)
    assert body["unit"] == "kg*m/s^2"
    assert body["dimension"] == "[mass] * [length] / [time] ** 2"


def test_time_and_mass_units():
    assert ok("t", {"t": {"value": 90, "unit": "min"}}, "h")["value"] == pytest.approx(1.5)
    body = ok("a + b", {"a": {"value": 500, "unit": "g"}, "b": {"value": 1, "unit": "kg"}}, "g")
    assert body["value"] == pytest.approx(1500.0)


def test_dimensionless_target_one():
    body = ok("2 * 3", target_unit="1")
    assert body["value"] == 6.0
    assert body["unit"] == "1"
    assert body["dimension"] == "dimensionless"


def test_target_with_parentheses_and_pow():
    assert ok("a * b", {"a": {"value": 2, "unit": "m"}, "b": {"value": 3, "unit": "m"}}, "m^2")["value"] == 6.0
    assert ok("a * b", {"a": {"value": 2, "unit": "m"}, "b": {"value": 3, "unit": "m"}}, "m**2")["value"] == 6.0
    assert ok("a", {"a": {"value": 1, "unit": "m"}}, "(m)")["value"] == 1.0
    assert ok("a", {"a": {"value": 1, "unit": "m"}}, "1/(s) * s * m / s * s")["value"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 温度规则
# ---------------------------------------------------------------------------

def test_absolute_minus_absolute_gives_delta():
    body = ok(
        "t1 - t2",
        {"t1": {"value": 10, "unit": "degC"}, "t2": {"value": 50, "unit": "degF"}},
        "delta_K",
    )
    assert body["value"] == pytest.approx(0.0)
    assert body["dimension"] == "[temperature]"


def test_absolute_plus_delta():
    body = ok(
        "t + d",
        {"t": {"value": 10, "unit": "degC"}, "d": {"value": 18, "unit": "delta_degF"}},
        "degC",
    )
    assert body["value"] == pytest.approx(20.0)


def test_absolute_minus_delta():
    body = ok(
        "t - d",
        {"t": {"value": 10, "unit": "degC"}, "d": {"value": 5, "unit": "delta_K"}},
        "degC",
    )
    assert body["value"] == pytest.approx(5.0)


def test_delta_plus_delta_and_conversion():
    body = ok(
        "a + b",
        {"a": {"value": 5, "unit": "delta_K"}, "b": {"value": 9, "unit": "delta_degF"}},
        "delta_degC",
    )
    assert body["value"] == pytest.approx(10.0)
    assert ok("d", {"d": {"value": 18, "unit": "delta_degF"}}, "delta_K")["value"] == pytest.approx(10.0)


def test_absolute_target_conversion():
    assert ok("t", {"t": {"value": 10, "unit": "degC"}}, "degF")["value"] == pytest.approx(50.0)
    assert ok("t", {"t": {"value": 10, "unit": "degC"}}, "K")["value"] == pytest.approx(283.15)
    assert ok("t", {"t": {"value": 32, "unit": "degF"}}, "degC")["value"] == pytest.approx(0.0)


def test_delta_rate_to_compound_target():
    body = ok("d / t", {"d": {"value": 10, "unit": "delta_K"}, "t": {"value": 2, "unit": "s"}}, "delta_K/s")
    assert body["value"] == pytest.approx(5.0)
    # K 是无偏移单位，可用于复合目标
    assert ok("d / t", {"d": {"value": 10, "unit": "delta_K"}, "t": {"value": 2, "unit": "s"}}, "K/s")["value"] == pytest.approx(5.0)


def test_absolute_plus_absolute_rejected():
    e = err("t1 + t2", {"t1": {"value": 1, "unit": "degC"}, "t2": {"value": 2, "unit": "K"}}, "K")
    assert e["type"] == "absolute_temperature"
    assert e["loc"][:2] == ["body", "expression"]


def test_absolute_mul_div_rejected():
    for expr in ("t * 2", "2 * t", "t / 2", "2 / t"):
        e = err(expr, {"t": {"value": 10, "unit": "degC"}}, "1")
        assert e["type"] == "absolute_temperature", expr


def test_absolute_negation_rejected():
    e = err("-t", {"t": {"value": 10, "unit": "degC"}}, "degC")
    assert e["type"] == "absolute_temperature"
    assert e["loc"] == ["body", "expression", 0]


def test_delta_minus_absolute_rejected():
    e = err("d - t", {"d": {"value": 5, "unit": "delta_K"}, "t": {"value": 10, "unit": "degC"}}, "K")
    assert e["type"] == "absolute_temperature"


def test_absolute_plus_non_temperature_rejected():
    e = err("t + x", {"t": {"value": 10, "unit": "degC"}, "x": {"value": 1, "unit": "m"}}, "degC")
    assert e["type"] == "incompatible_units"


def test_absolute_result_requires_absolute_target():
    e = err("t", {"t": {"value": 10, "unit": "degC"}}, "delta_K")
    assert e["type"] == "incompatible_units"
    assert e["loc"] == ["body", "target_unit"]


def test_delta_result_cannot_target_absolute():
    e = err("t1 - t2", {"t1": {"value": 10, "unit": "degC"}, "t2": {"value": 5, "unit": "degC"}}, "degC")
    assert e["type"] == "incompatible_units"
    assert e["loc"] == ["body", "target_unit"]


# ---------------------------------------------------------------------------
# 错误：零除、未知变量、非法算式、单位不兼容
# ---------------------------------------------------------------------------

def test_division_by_zero():
    e = err("1 / (x - x)", {"x": {"value": 5, "unit": "m"}}, "1")
    assert e["type"] == "division_by_zero"
    assert e["loc"][:2] == ["body", "expression"]
    assert e["loc"][2] == 2  # '/' 的位置


def test_unknown_variable_points_to_expression_position():
    e = err("a + u", {"a": {"value": 1, "unit": "m"}}, "m")
    assert e["type"] == "unknown_variable"
    assert e["loc"] == ["body", "expression", 4]


@pytest.mark.parametrize(
    "expression,pos",
    [
        ("1 +", 3),          # 意外结束
        ("2(3)", 1),         # 隐式乘法
        ("2 3", 2),          # 并列运算数
        ("f(1)", 1),         # 函数调用
        ("1 & 2", 2),        # 非法字符
        ("()", 1),           # 空括号
        ("* 2", 0),          # 缺少左运算数
        ("(1 + 2", 6),       # 缺少右括号
        ("1)", 1),           # 多余右括号
        ("2 ** 3", 3),       # 算式不支持幂运算
    ],
)
def test_syntax_errors_carry_position(expression, pos):
    e = err(expression)
    assert e["type"] == "syntax_error"
    assert e["loc"] == ["body", "expression", pos]


def test_unknown_variable_unit_points_to_field():
    resp = evaluate("x", {"x": {"value": 1, "unit": "mile"}}, "1")
    assert resp.status_code == 422
    e = resp.json()["detail"][0]
    assert e["type"] == "unknown_unit"
    assert e["loc"] == ["body", "variables", "x", "unit"]


def test_incompatible_addition():
    e = err("a + b", {"a": {"value": 1, "unit": "m"}, "b": {"value": 1, "unit": "s"}}, "m")
    assert e["type"] == "incompatible_units"
    assert e["loc"] == ["body", "expression", 2]


def test_incompatible_target_unit():
    e = err("a", {"a": {"value": 1, "unit": "m"}}, "s")
    assert e["type"] == "incompatible_units"
    assert e["loc"] == ["body", "target_unit"]


def test_dimensionless_vs_unit_incompatible():
    e = err("1 + 2", target_unit="m")
    assert e["type"] == "incompatible_units"


def test_unknown_target_unit():
    e = err("a", {"a": {"value": 1, "unit": "m"}}, "mile")
    assert e["type"] == "unknown_unit"
    assert e["loc"][:2] == ["body", "target_unit"]


def test_offset_unit_in_compound_target_rejected():
    e = err("d / t", {"d": {"value": 1, "unit": "delta_K"}, "t": {"value": 1, "unit": "s"}}, "degC/s")
    assert e["type"] == "invalid_target_unit"


def test_invalid_target_syntax():
    assert err("a", {"a": {"value": 1, "unit": "m"}}, "m/")["type"] == "invalid_target_unit"
    assert err("a", {"a": {"value": 1, "unit": "m"}}, "2*m")["type"] == "invalid_target_unit"
    assert evaluate("a", {"a": {"value": 1, "unit": "m"}}, "").status_code == 422


def test_result_overflow_rejected():
    e = err("a * a", {"a": {"value": 1e308, "unit": "m"}}, "m^2")
    assert e["type"] == "non_finite_result"


def test_non_finite_variable_value_rejected():
    resp = client.post(
        "/v1/evaluate",
        json={"expression": "x", "variables": {"x": {"value": "nan", "unit": "m"}}, "target_unit": "m"},
    )
    assert resp.status_code == 422


def test_bool_variable_value_rejected():
    resp = evaluate("x", {"x": {"value": True, "unit": "m"}}, "m")
    assert resp.status_code == 422


def test_missing_fields_and_empty_expression_are_422():
    assert client.post("/v1/evaluate", json={}).status_code == 422
    assert evaluate("", target_unit="1").status_code == 422
    assert evaluate("   ", target_unit="1").status_code == 422


def test_healthz():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
