"""POST /v1/invert 的行为测试。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def invert(body):
    return client.post("/v1/invert", json=body)


def invert_ok(expression, unknown, bounds, target, variables=None):
    resp = invert(
        {
            "expression": expression,
            "variables": variables or {},
            "unknown": unknown,
            "bounds": bounds,
            "target": target,
        }
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def invert_err(expression, unknown, bounds, target, variables=None):
    resp = invert(
        {
            "expression": expression,
            "variables": variables or {},
            "unknown": unknown,
            "bounds": bounds,
            "target": target,
        }
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, list) and detail, resp.text
    return detail[0]


# ---------------------------------------------------------------------------
# 成功反求
# ---------------------------------------------------------------------------

def test_invert_velocity_distance():
    # d / t，t = 2 h 已知，目标 5 m/s，反求 d（km）
    body = invert_ok(
        "d / t",
        {"name": "d", "unit": "km"},
        [0, 1000],
        {"value": 5, "unit": "m/s"},
        {"t": {"value": 2, "unit": "h"}},
    )
    assert body["name"] == "d"
    assert body["unit"] == "km"
    assert body["result_unit"] == "m/s"
    assert body["value"] == pytest.approx(36.0, abs=1e-5)
    assert body["result"] == pytest.approx(5.0, abs=5e-8)
    assert body["dimension"] == "[length] / [time]"


def test_invert_with_unit_conversion_on_both_sides():
    # d / t = 5 m/s，d = 10 m 已知，反求 t（s）=> 2 s（两端各做一次单位换算）
    body = invert_ok(
        "d / t",
        {"name": "t", "unit": "s"},
        [0.1, 100],
        {"value": 5, "unit": "m/s"},
        {"d": {"value": 10, "unit": "m"}},
    )
    assert body["value"] == pytest.approx(2.0, abs=1e-5)
    assert body["result"] == pytest.approx(5.0, abs=5e-8)


def test_invert_unknown_appears_multiple_times():
    # x*x - 3*x + 2 = 0 在 [0, 1.5] 内的根为 x = 1（待求变量出现 3 次）
    body = invert_ok(
        "x*x - 3*x + 2",
        {"name": "x", "unit": "1"},
        [0, 1.5],
        {"value": 0, "unit": "1"},
    )
    assert body["value"] == pytest.approx(1.0, abs=1e-6)
    assert body["result"] == pytest.approx(0.0, abs=1e-8)
    assert body["dimension"] == "dimensionless"


def test_tangent_double_root_is_not_bracketed():
    # (x-1)^2 = 0 是相切重根：函数在 x=1 处触零但不变号，
    # 端点残差同为正号，不构成异号括区 -> 422，不得猜解。
    e = invert_err(
        "x*x - 2*x + 1",
        {"name": "x", "unit": "1"},
        [0, 10],
        {"value": 0, "unit": "1"},
    )
    assert e["type"] == "no_bracket"
    assert e["loc"] == ["body", "bounds"]


def test_invert_square():
    body = invert_ok(
        "x*x",
        {"name": "x", "unit": "m"},
        [0, 10],
        {"value": 9, "unit": "m^2"},
    )
    assert body["value"] == pytest.approx(3.0, abs=1e-6)
    assert body["result"] == pytest.approx(9.0, abs=9e-8)


def test_invert_endpoint_hit_low_and_high():
    low = invert_ok("x", {"name": "x", "unit": "m"}, [1, 10], {"value": 1, "unit": "m"})
    assert low["value"] == 1.0
    high = invert_ok("x", {"name": "x", "unit": "m"}, [1, 10], {"value": 10, "unit": "m"})
    assert high["value"] == 10.0


def test_invert_endpoint_hit_within_tolerance():
    # 端点结果与目标相差极小（远小于 1e-8 * max(1, |目标|)）即视为命中
    body = invert_ok(
        "x + 1",
        {"name": "x", "unit": "1"},
        [1e-12, 10],
        {"value": 1.0, "unit": "1"},
    )
    assert body["value"] == 1e-12
    assert body["result"] == pytest.approx(1.0, abs=1e-8)


def test_invert_absolute_temperature_affine():
    # t(degC) - 10 delta_K = 68 degF(=20 degC)  =>  t = 30 degC
    body = invert_ok(
        "t - d",
        {"name": "t", "unit": "degC"},
        [-50, 100],
        {"value": 68, "unit": "degF"},
        {"d": {"value": 10, "unit": "delta_K"}},
    )
    assert body["value"] == pytest.approx(30.0, abs=1e-5)
    assert body["result_unit"] == "degF"
    assert body["dimension"] == "[temperature]"


def test_invert_delta_temperature_rate():
    # d / t，t = 2 s，目标 5 K/s，反求 d = 10 delta_K
    body = invert_ok(
        "d / t",
        {"name": "d", "unit": "delta_K"},
        [0, 100],
        {"value": 5, "unit": "K/s"},
        {"t": {"value": 2, "unit": "s"}},
    )
    assert body["value"] == pytest.approx(10.0, abs=1e-6)
    assert body["result"] == pytest.approx(5.0, abs=5e-8)


def test_invert_returns_target_unit_canonical():
    body = invert_ok(
        "m * d / (t * t)",
        {"name": "d", "unit": "m"},
        [0, 100],
        {"value": 1.5, "unit": "kg*m/s^2"},
        {"m": {"value": 2, "unit": "kg"}, "t": {"value": 2, "unit": "s"}},
    )
    assert body["value"] == pytest.approx(3.0, abs=1e-5)
    assert body["result_unit"] == "kg*m/s^2"


# ---------------------------------------------------------------------------
# 错误：括区与收敛
# ---------------------------------------------------------------------------

def test_no_bracket_when_endpoint_residuals_same_sign():
    e = invert_err(
        "x + 1",
        {"name": "x", "unit": "1"},
        [0, 10],
        {"value": 100, "unit": "1"},
    )
    assert e["type"] == "no_bracket"
    assert e["loc"] == ["body", "bounds"]


def test_not_converged_when_bracket_is_a_pole():
    # 1/x = 1 的真根 x=1 在区间外；[-1, 0.5] 端点异号只是因为夹住了极点 0
    e = invert_err(
        "1/x",
        {"name": "x", "unit": "s"},
        [-1, 0.5],
        {"value": 1, "unit": "1/s"},
    )
    assert e["type"] == "not_converged"
    assert e["loc"] == ["body", "bounds"]
    assert "100" in e["msg"]


def test_midpoint_division_by_zero_is_422_not_a_solution():
    # [-0.5, 0.5] 的第一个中点恰为极点 0
    e = invert_err(
        "1/x",
        {"name": "x", "unit": "s"},
        [-0.5, 0.5],
        {"value": 1, "unit": "1/s"},
    )
    assert e["type"] == "division_by_zero"
    assert e["loc"] == ["body", "expression", 1]


def test_endpoint_division_by_zero_is_422():
    # 下界 x=0 直接零除
    e = invert_err(
        "1/x",
        {"name": "x", "unit": "s"},
        [0, 1],
        {"value": 1, "unit": "1/s"},
    )
    assert e["type"] == "division_by_zero"
    assert e["loc"][:2] == ["body", "expression"]


def test_tolerance_is_relative_to_target():
    # 目标 1e9 时容差为 10；x + 1 在区间 [0, 10] 上残差始终小于 -容差但同号
    e = invert_err(
        "x + 1",
        {"name": "x", "unit": "1"},
        [0, 10],
        {"value": 1e9, "unit": "1"},
    )
    assert e["type"] == "no_bracket"


# ---------------------------------------------------------------------------
# 错误：输入定位
# ---------------------------------------------------------------------------

def test_unknown_name_conflicts_with_known_variable():
    e = invert_err(
        "x",
        {"name": "x", "unit": "m"},
        [0, 1],
        {"value": 1, "unit": "m"},
        {"x": {"value": 1, "unit": "m"}},
    )
    assert e["type"] == "unknown_variable_conflict"
    assert e["loc"] == ["body", "unknown", "name"]


def test_unknown_not_in_expression():
    e = invert_err(
        "1 + 2",
        {"name": "x", "unit": "m"},
        [0, 10],
        {"value": 3, "unit": "1"},
    )
    assert e["type"] == "unknown_not_in_expression"
    assert e["loc"] == ["body", "unknown", "name"]


def test_other_undefined_variable_points_to_expression():
    e = invert_err(
        "x + y",
        {"name": "x", "unit": "m"},
        [0, 1],
        {"value": 1, "unit": "m"},
    )
    assert e["type"] == "unknown_variable"
    assert e["loc"] == ["body", "expression", 4]


def test_invalid_unknown_unit():
    e = invert_err(
        "x",
        {"name": "x", "unit": "mile"},
        [0, 1],
        {"value": 1, "unit": "m"},
    )
    assert e["type"] == "unknown_unit"
    assert e["loc"] == ["body", "unknown", "unit"]


def test_unknown_unit_one_is_dimensionless():
    body = invert_ok(
        "2 * x",
        {"name": "x", "unit": "1"},
        [0, 10],
        {"value": 6, "unit": "1"},
    )
    assert body["value"] == pytest.approx(3.0, abs=1e-6)


def test_invalid_target_unit_locates_target_unit():
    e = invert_err(
        "x",
        {"name": "x", "unit": "m"},
        [0, 1],
        {"value": 1, "unit": "mile"},
    )
    assert e["type"] == "unknown_unit"
    assert e["loc"][:3] == ["body", "target", "unit"]


def test_dimension_mismatch_locates_target_unit():
    e = invert_err(
        "x",
        {"name": "x", "unit": "m"},
        [0, 10],
        {"value": 5, "unit": "s"},
    )
    assert e["type"] == "incompatible_units"
    assert e["loc"] == ["body", "target", "unit"]


def test_absolute_temperature_multiplication_rejected():
    e = invert_err(
        "t * 2",
        {"name": "t", "unit": "degC"},
        [0, 100],
        {"value": 10, "unit": "degC"},
    )
    assert e["type"] == "absolute_temperature"
    assert e["loc"] == ["body", "expression", 2]


def test_syntax_error_points_to_expression():
    e = invert_err(
        "x +",
        {"name": "x", "unit": "m"},
        [0, 1],
        {"value": 1, "unit": "m"},
    )
    assert e["type"] == "syntax_error"
    assert e["loc"][:2] == ["body", "expression"]


def test_known_variable_bad_unit_locates_variable():
    e = invert_err(
        "x + a",
        {"name": "x", "unit": "m"},
        [0, 1],
        {"value": 1, "unit": "m"},
        {"a": {"value": 1, "unit": "mile"}},
    )
    assert e["type"] == "unknown_unit"
    assert e["loc"] == ["body", "variables", "a", "unit"]


def test_illegal_unknown_name_shape_is_422():
    resp = invert(
        {
            "expression": "x",
            "variables": {},
            "unknown": {"name": "1x", "unit": "m"},
            "bounds": [0, 1],
            "target": {"value": 1, "unit": "m"},
        }
    )
    assert resp.status_code == 422


def test_bounds_must_be_strictly_increasing():
    for bounds in ([5, 5], [10, 0]):
        resp = invert(
            {
                "expression": "x",
                "variables": {},
                "unknown": {"name": "x", "unit": "m"},
                "bounds": bounds,
                "target": {"value": 1, "unit": "m"},
            }
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"][0]["loc"][:2] == ["body", "bounds"]


def test_bounds_must_be_finite_and_numeric():
    for raw_bounds in ("[0, 1e999]", '[0, "nan"]', "[true, 10]"):
        resp = client.post(
            "/v1/invert",
            content=(
                '{"expression":"x","variables":{},'
                '"unknown":{"name":"x","unit":"m"},"bounds":'
                + raw_bounds
                + ',"target":{"value":1,"unit":"m"}}'
            ),
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 422, raw_bounds
        assert resp.json()["detail"][0]["loc"][:2] == ["body", "bounds"]


def test_target_value_must_be_finite():
    resp = invert(
        {
            "expression": "x",
            "variables": {},
            "unknown": {"name": "x", "unit": "m"},
            "bounds": [0, 1],
            "target": {"value": True, "unit": "m"},
        }
    )
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"][:2] == ["body", "target"]


def test_missing_fields_are_422():
    assert invert({"expression": "x"}).status_code == 422


# ---------------------------------------------------------------------------
# /v1/evaluate 行为保持不变
# ---------------------------------------------------------------------------

def test_evaluate_still_works():
    resp = client.post(
        "/v1/evaluate",
        json={
            "expression": "dist / time",
            "variables": {
                "dist": {"value": 36, "unit": "km"},
                "time": {"value": 2, "unit": "h"},
            },
            "target_unit": "m/s",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == pytest.approx(5.0)


def test_healthz_still_works():
    assert client.get("/healthz").status_code == 200
