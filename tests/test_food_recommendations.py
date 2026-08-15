from fastapi.testclient import TestClient

from app.main import app
from app.services.food_recommendations import build_demo


def _department(result: dict, name: str) -> dict:
    return next(item for item in result["regional_comparison"] if item["department"] == name)


def _ingredient(region: dict, name: str) -> dict:
    return next(
        item for item in region["ingredient_price_context"] if item["recipe_ingredient"] == name
    )


def test_demo_uses_official_recipe_and_real_regional_prices_without_inventing_cost():
    result = build_demo()

    assert result["scenario"]["age_months"] == 7
    assert result["eligibility"]["eligible_official_recipes"] == 10
    assert result["official_recipe"]["recipe_id"] == "REC_0002"
    assert result["official_recipe"]["age_min_months"] == 6
    assert result["official_recipe"]["age_max_months"] == 8

    lima_potato = _ingredient(_department(result, "Lima"), "papa amarilla")
    tumbes_potato = _ingredient(_department(result, "Tumbes"), "papa amarilla")
    assert lima_potato["market_price"]["normalized_price_pen_per_kg_or_l"] == 2.2
    assert tumbes_potato["market_price"]["normalized_price_pen_per_kg_or_l"] == 3.47
    assert lima_potato["market_price"]["price_kind"] == "wholesale_reference"

    for region in result["regional_comparison"]:
        assert region["estimated_recipe_cost_pen"] is None
        assert region["validated_price_coverage_by_ingredient_count_pct"] == 20.0
    assert result["ranking"]["status"] == "cost_ranking_disabled_for_this_demo"


def test_demo_records_three_month_fallback_without_using_future_prices():
    result = build_demo(requested_year=2026, requested_month=9)
    potato = _ingredient(_department(result, "Lima"), "papa amarilla")["market_price"]

    assert potato["requested_period"] == "2026-09"
    assert potato["actual_period"] == "2026-08"
    assert potato["months_old"] == 1
    assert potato["fallback_used"] is True


def test_food_demo_endpoint_is_available_in_openapi():
    with TestClient(app) as client:
        response = client.get("/nutrition/recommendations/demo")
        schema = client.get("/openapi.json").json()

    assert response.status_code == 200
    assert response.json()["official_recipe"]["name"] == "Zapallito feliz"
    operation = schema["paths"]["/nutrition/recommendations/demo"]["get"]
    assert operation["tags"] == ["Alimentación"]

