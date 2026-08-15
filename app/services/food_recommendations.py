"""Caso demostrativo, determinista y trazable del recomendador alimentario."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_RECIPES = _ROOT / "data" / "processed" / "ins" / "recipes.csv"
_INGREDIENTS = _ROOT / "data" / "processed" / "ins" / "recipe_ingredients.csv"
_PRICES = _ROOT / "data" / "processed" / "midagri" / "midagri_prices.csv"

_DEMO_RECIPE_ID = "REC_0002"
_DEMO_AGE_MONTHS = 7
_DEMO_DEPARTMENTS = ("Lima", "Tumbes")
_DEMO_MAPPINGS = (
    {
        "ingredient_contains": "papa amarilla",
        "product_code": "010401",
        "status": "validated_for_demo",
        "note": "Coincidencia específica con Papa amarilla; limitada a esta demostración.",
    },
    {
        "ingredient_contains": "zapallo",
        "product_code": "023103",
        "status": "candidate_not_validated",
        "note": (
            "La receta dice zapallo y MIDAGRI especifica Zapallo macre. "
            "Se muestra como candidato, pero no se usa para calcular costo."
        ),
    },
)


class FoodDemoDataError(RuntimeError):
    """El conjunto local no contiene las filas necesarias para la demostración."""


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FoodDemoDataError(f"No se encontró el archivo requerido: {path.name}")
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def _period_index(year: int, month: int) -> int:
    return year * 12 + month


def _price_for(
    prices: list[dict[str, str]],
    *,
    department: str,
    product_code: str,
    requested_year: int,
    requested_month: int,
    fallback_months: int,
) -> dict | None:
    requested_index = _period_index(requested_year, requested_month)
    candidates: list[tuple[int, dict]] = []
    for row in prices:
        if row.get("department") != department or row.get("product_code") != product_code:
            continue
        try:
            actual_year = int(row["year"])
            actual_month = int(row["month"])
            price = float(row["price"])
            normalized = float(row["price_per_equivalent"])
        except (KeyError, TypeError, ValueError):
            continue
        actual_index = _period_index(actual_year, actual_month)
        months_old = requested_index - actual_index
        if 0 <= months_old <= fallback_months:
            candidates.append(
                (
                    actual_index,
                    {
                        **row,
                        "_months_old": months_old,
                        "_price": price,
                        "_normalized": normalized,
                    },
                )
            )
    if not candidates:
        return None
    row = max(candidates, key=lambda item: item[0])[1]
    return {
        "product_code": row["product_code"],
        "product_name": row["product_name"],
        "commercial_unit": row["unit"],
        "equivalence_kg_or_l": float(row["equivalence_kg_lt"]),
        "observed_price_pen": round(row["_price"], 4),
        "normalized_price_pen_per_kg_or_l": round(row["_normalized"], 4),
        "requested_period": f"{requested_year:04d}-{requested_month:02d}",
        "actual_period": f"{int(row['year']):04d}-{int(row['month']):02d}",
        "months_old": row["_months_old"],
        "fallback_used": row["_months_old"] > 0,
        "source": row.get("source") or "MIDAGRI_SISAP",
        "price_kind": "wholesale_reference",
    }


def build_demo(*, requested_year: int = 2026, requested_month: int = 8) -> dict:
    """Construye un ejemplo real sin inventar gramos, equivalencias o costos."""
    recipes = _rows(_RECIPES)
    ingredients = _rows(_INGREDIENTS)
    prices = _rows(_PRICES)

    eligible = [
        row
        for row in recipes
        if int(row["age_min_months"]) <= _DEMO_AGE_MONTHS <= int(row["age_max_months"])
    ]
    recipe = next((row for row in eligible if row["recipe_id"] == _DEMO_RECIPE_ID), None)
    if not recipe:
        raise FoodDemoDataError("La receta oficial configurada para la demo no está disponible.")

    recipe_ingredients = [
        row for row in ingredients if row.get("recipe_id") == _DEMO_RECIPE_ID
    ]
    if not recipe_ingredients:
        raise FoodDemoDataError("La receta demostrativa no tiene ingredientes extraídos.")

    comparison = []
    for department in _DEMO_DEPARTMENTS:
        mapped = []
        for mapping in _DEMO_MAPPINGS:
            ingredient = next(
                (
                    row
                    for row in recipe_ingredients
                    if mapping["ingredient_contains"] in row["ingredient_original"].casefold()
                ),
                None,
            )
            price = _price_for(
                prices,
                department=department,
                product_code=mapping["product_code"],
                requested_year=requested_year,
                requested_month=requested_month,
                fallback_months=3,
            )
            mapped.append(
                {
                    "recipe_ingredient": ingredient["ingredient_original"] if ingredient else None,
                    "recipe_quantity": ingredient["quantity"] if ingredient else None,
                    "recipe_unit": ingredient["unit_original"] if ingredient else None,
                    "mapping_status": mapping["status"],
                    "mapping_note": mapping["note"],
                    "market_price": price,
                    "included_in_recipe_cost": False,
                    "exclusion_reason": (
                        "La cantidad original no tiene equivalencia documentada en gramos o mililitros."
                        if mapping["status"] == "validated_for_demo"
                        else "El producto comercial candidato todavía no fue validado."
                    ),
                }
            )

        validated_prices = sum(
            1
            for item in mapped
            if item["mapping_status"] == "validated_for_demo" and item["market_price"]
        )
        comparison.append(
            {
                "department": department,
                "ingredient_price_context": mapped,
                "validated_price_coverage_by_ingredient_count_pct": round(
                    100 * validated_prices / len(recipe_ingredients), 1
                ),
                "estimated_recipe_cost_pen": None,
                "cost_status": "not_calculable_without_documented_quantities",
                "cost_explanation": (
                    "Se encontraron precios regionales, pero no se multiplican por medidas "
                    "caseras sin una equivalencia oficial o validada."
                ),
            }
        )

    return {
        "demo": True,
        "generated_on": date.today().isoformat(),
        "scenario": {
            "child": "fictitious",
            "age_months": _DEMO_AGE_MONTHS,
            "departments_compared": list(_DEMO_DEPARTMENTS),
            "requested_price_period": f"{requested_year:04d}-{requested_month:02d}",
        },
        "eligibility": {
            "rule": "age_min_months <= age_months <= age_max_months",
            "eligible_official_recipes": len(eligible),
            "selected_for_trace": _DEMO_RECIPE_ID,
            "selection_note": (
                "La receta fue elegida para demostrar trazabilidad; no se afirma que sea "
                "superior a las demás recetas compatibles."
            ),
        },
        "official_recipe": {
            "recipe_id": recipe["recipe_id"],
            "name": recipe["recipe_name"],
            "age_min_months": int(recipe["age_min_months"]),
            "age_max_months": int(recipe["age_max_months"]),
            "energy_kcal": float(recipe["energy_kcal"]),
            "protein_g": float(recipe["protein_g"]),
            "iron_mg": float(recipe["iron_mg"]),
            "zinc_mg": float(recipe["zinc_mg"]),
            "retinol_ug": float(recipe["retinol_ug"]),
            "preparation": recipe["preparation"],
            "source_page": recipe["source_page"],
            "source_kind": "official_INS_CENAN_child_recipe",
            "ingredients": [
                {
                    "name": row["ingredient_original"],
                    "quantity": row["quantity"] or None,
                    "unit": row["unit_original"] or None,
                    "normalized_g": float(row["quantity_normalized_g"])
                    if row["quantity_normalized_g"]
                    else None,
                    "normalized_ml": float(row["quantity_normalized_ml"])
                    if row["quantity_normalized_ml"]
                    else None,
                }
                for row in recipe_ingredients
            ],
        },
        "regional_comparison": comparison,
        "ranking": {
            "status": "cost_ranking_disabled_for_this_demo",
            "reason": (
                "Un precio regional diferente no basta para calcular el costo de la receta "
                "mientras sus cantidades sigan expresadas en medidas caseras no normalizadas."
            ),
        },
        "data_labels": {
            "official": "Receta y nutrientes publicados por INS/CENAN.",
            "observed": "Precios mayoristas extraídos de MIDAGRI/SISAP.",
            "calculated": "Antigüedad, fallback y cobertura calculados por el prototipo.",
            "unavailable": "Costo no calculado; no se sustituyeron datos faltantes.",
        },
        "disclaimer": (
            "Demostración informativa con una persona ficticia. No constituye diagnóstico, "
            "prescripción ni precio final al consumidor."
        ),
    }
