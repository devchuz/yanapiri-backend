from __future__ import annotations

import argparse
import html as html_lib
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


HOME_URL = "http://sistemas.midagri.gob.pe/sisap/portal2/ciudades/"
FILTER_URL = (
    "http://sistemas.midagri.gob.pe/"
    "sisap/portal2/ciudades/resumenes/filtrar"
)

RAW_DIR = Path("data/raw/midagri")
PROCESSED_DIR = Path("data/processed/midagri")

MONTH_MAP = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}


# ============================================================
# HELPERS
# ============================================================

def clean_text(value: str) -> str:
    value = html_lib.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def to_float(value: str | None) -> float | None:
    if not value:
        return None

    value = value.strip().replace(",", "")

    try:
        return float(value)
    except ValueError:
        return None


def chunks(values: list[str], size: int):
    for i in range(0, len(values), size):
        yield values[i:i + size]


# ============================================================
# SESSION
# ============================================================

def create_session() -> requests.Session:

    session = requests.Session()

    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=1,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504,
        ],
        allowed_methods=["GET"],
    )

    session.mount(
        "http://",
        HTTPAdapter(max_retries=retry),
    )

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "es-419,es;q=0.9",
            "Referer": HOME_URL,
            "X-Requested-With": "XMLHttpRequest",
        }
    )

    return session


# ============================================================
# DESCUBRIR CATÁLOGOS
# ============================================================

def get_home_html(
    session: requests.Session,
) -> str:

    response = session.get(
        HOME_URL,
        timeout=60,
    )

    response.raise_for_status()
    response.encoding = "iso-8859-1"

    return response.text


def discover_regions(
    html: str,
) -> dict[str, str]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    regions = {}

    # Buscar <select name="region">
    select = soup.find(
        "select",
        attrs={"name": "region"},
    )

    if select:

        for option in select.find_all("option"):

            value = clean_text(
                option.get("value", "")
            )

            name = clean_text(
                option.get_text(" ", strip=True)
            )

            if (
                value
                and value != "*"
                and value != "0"
            ):
                regions[value] = name

    # fallback: inputs si el portal cambia
    if not regions:

        for item in soup.find_all(
            ["input", "option"]
        ):

            value = item.get("value")

            if not value:
                continue

            value = value.strip()

            # Los códigos regionales observados tienen
            # estructura 010000, 020000...
            if re.fullmatch(r"\d{6}", value):

                name = clean_text(
                    item.get_text(" ", strip=True)
                    or item.get("data-name", "")
                    or value
                )

                regions[value] = name

    return regions


def discover_products(
    html: str,
) -> dict[str, str]:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    products = {}

    # --------------------------------------------------------
    # Caso 1: productos en SELECT
    # --------------------------------------------------------

    selects = soup.find_all("select")

    for select in selects:

        name = select.get("name", "")

        if "producto" not in name.lower():
            continue

        for option in select.find_all("option"):

            value = clean_text(
                option.get("value", "")
            )

            label = clean_text(
                option.get_text(" ", strip=True)
            )

            if (
                value
                and value not in {"*", "0"}
            ):
                products[value] = label

    # --------------------------------------------------------
    # Caso 2: productos como checkbox
    # --------------------------------------------------------

    for input_tag in soup.find_all(
        "input"
    ):

        name = input_tag.get(
            "name",
            "",
        )

        if "producto" not in name.lower():
            continue

        value = clean_text(
            input_tag.get(
                "value",
                "",
            )
        )

        if not value:
            continue

        label = ""

        input_id = input_tag.get("id")

        if input_id:

            label_tag = soup.find(
                "label",
                attrs={"for": input_id},
            )

            if label_tag:
                label = clean_text(
                    label_tag.get_text(
                        " ",
                        strip=True,
                    )
                )

        products[value] = (
            label or value
        )

    return products


# ============================================================
# REQUEST
# ============================================================

def build_params(
    region: str,
    product_ids: list[str],
    date_from: str,
    date_to: str,
    variable: str,
    periodicity: str,
):

    dt_from = datetime.strptime(
        date_from,
        "%d/%m/%Y",
    )

    dt_to = datetime.strptime(
        date_to,
        "%d/%m/%Y",
    )

    params = [
        ("region", region),
        ("variables[]", variable),
        ("fecha", date_to),
        ("desde", date_from),
        ("hasta", date_to),
    ]

    for year in range(
        dt_from.year,
        dt_to.year + 1,
    ):
        params.append(
            ("anios[]", str(year))
        )

    months = pd.date_range(
        dt_from.replace(day=1),
        dt_to.replace(day=1),
        freq="MS",
    )

    for month in months:
        params.append(
            (
                "meses[]",
                f"{month.month:02d}",
            )
        )

    for product_id in product_ids:
        params.append(
            (
                "productos[]",
                product_id,
            )
        )

    params.extend(
        [
            (
                "periodicidad",
                periodicity,
            ),
            (
                "__ajax_carga_final",
                "consulta",
            ),
            (
                "ajax",
                "true",
            ),
        ]
    )

    return params


def fetch_prices(
    session: requests.Session,
    region: str,
    product_ids: list[str],
    date_from: str,
    date_to: str,
    variable: str,
    periodicity: str,
) -> str:

    params = build_params(
        region=region,
        product_ids=product_ids,
        date_from=date_from,
        date_to=date_to,
        variable=variable,
        periodicity=periodicity,
    )

    response = session.get(
        FILTER_URL,
        params=params,
        timeout=90,
    )

    response.raise_for_status()
    response.encoding = "iso-8859-1"

    return response.text


# ============================================================
# PARSER
# ============================================================

def extract_department(
    soup: BeautifulSoup,
) -> str | None:

    h1 = soup.find("h1")

    if not h1:
        return None

    title = clean_text(
        h1.get_text(
            " ",
            strip=True,
        )
    )

    match = re.search(
        r"Mercados mayoristas de\s+(.+?):",
        title,
        flags=re.IGNORECASE,
    )

    if match:
        return clean_text(
            match.group(1)
        )

    return None


def parse_sisap(
    html: str,
    region_code: str,
    variable: str,
) -> pd.DataFrame:

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    table = soup.find("table")

    if table is None:
        return pd.DataFrame()

    department = extract_department(
        soup
    )

    records = []

    product_code = None
    product_name = None
    unit = None
    equivalence = None

    rows = table.find_all(
        "tr",
        class_="contenido",
    )

    for row in rows:

        cells = row.find_all("td")

        if not cells:
            continue

        texts = [
            clean_text(
                cell.get_text(
                    " ",
                    strip=True,
                )
            )
            for cell in cells
        ]

        # ----------------------------------------------------
        # PRODUCTO
        # ----------------------------------------------------

        if (
            len(cells) >= 2
            and cells[0].find("b")
            and cells[1].find("b")
        ):

            product_code = clean_text(
                cells[0].get_text(
                    " ",
                    strip=True,
                )
            )

            product_name = clean_text(
                cells[1].get_text(
                    " ",
                    strip=True,
                )
            )

            unit = None
            equivalence = None

            continue

        # ----------------------------------------------------
        # UNIDAD
        # ----------------------------------------------------

        has_rel = any(
            cell.has_attr("rel")
            for cell in cells
        )

        if (
            not has_rel
            and len(texts) >= 3
            and not texts[0]
            and texts[1]
            and to_float(texts[2]) is not None
        ):

            unit = texts[1]
            equivalence = to_float(
                texts[2]
            )

            continue

        # ----------------------------------------------------
        # PRECIO
        # ----------------------------------------------------

        price_cells = [
            cell
            for cell in cells
            if cell.has_attr("rel")
        ]

        if (
            not price_cells
            or not product_code
        ):
            continue

        year = None

        if len(texts) >= 4:

            try:
                year = int(
                    texts[3].strip()
                )
            except ValueError:
                pass

        for price_cell in price_cells:

            price = to_float(
                price_cell.get_text(
                    " ",
                    strip=True,
                )
            )

            if price is None:
                continue

            rel = clean_text(
                price_cell.get(
                    "rel",
                    "",
                )
            )

            if "~" not in rel:
                continue

            period = rel.split(
                "~",
                1,
            )[0].strip()

            # Para nuestro dataset queremos
            # el dato mensual.
            if period.lower() == "anual":
                continue

            month = MONTH_MAP.get(
                period[:3].lower()
            )

            price_per_equivalent = None

            if (
                equivalence is not None
                and equivalence > 0
            ):
                price_per_equivalent = (
                    price / equivalence
                )

            records.append(
                {
                    "region_code": region_code,
                    "department": department,

                    "product_code": product_code,
                    "product_name": product_name,

                    "unit": unit,
                    "equivalence_kg_lt": equivalence,

                    "year": year,
                    "month": month,

                    "metric": variable,
                    "price": price,

                    "price_per_equivalent": (
                        price_per_equivalent
                    ),

                    "source": "MIDAGRI_SISAP",
                }
            )

    return pd.DataFrame(records)


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--from-date",
        required=True,
    )

    parser.add_argument(
        "--to-date",
        required=True,
    )

    parser.add_argument(
        "--variable",
        default="may_precio_prom",
    )

    parser.add_argument(
        "--periodicity",
        default="mensual",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
    )

    args = parser.parse_args()

    RAW_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    PROCESSED_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    session = create_session()

    # --------------------------------------------------------
    # CATÁLOGOS
    # --------------------------------------------------------

    print("Leyendo catálogo SISAP...")

    home_html = get_home_html(
        session
    )

    regions = discover_regions(
        home_html
    )

    products = discover_products(
        home_html
    )

    print(
        f"Regiones encontradas: {len(regions)}"
    )

    print(
        f"Grupos de productos encontrados: {len(products)}"
    )

    if not regions:
        raise RuntimeError(
            "No se pudieron descubrir "
            "las regiones SISAP."
        )

    if not products:
        raise RuntimeError(
            "No se pudieron descubrir "
            "los productos SISAP."
        )

    # Guardar catálogos descubiertos
    pd.DataFrame(
        [
            {
                "region_code": code,
                "region_name": name,
            }
            for code, name
            in regions.items()
        ]
    ).to_csv(
        PROCESSED_DIR
        / "sisap_regions.csv",
        index=False,
        encoding="utf-8-sig",
    )

    pd.DataFrame(
        [
            {
                "product_group_id": code,
                "product_group_name": name,
            }
            for code, name
            in products.items()
        ]
    ).to_csv(
        PROCESSED_DIR
        / "sisap_product_groups.csv",
        index=False,
        encoding="utf-8-sig",
    )

    product_ids = list(
        products.keys()
    )

    all_frames = []

    total_regions = len(regions)

    # --------------------------------------------------------
    # EXTRACCIÓN
    # --------------------------------------------------------

    for region_index, (
        region_code,
        region_name,
    ) in enumerate(
        regions.items(),
        start=1,
    ):

        print()
        print(
            f"[{region_index}/{total_regions}] "
            f"{region_name} ({region_code})"
        )

        region_frames = []

        product_chunks = list(
            chunks(
                product_ids,
                args.chunk_size,
            )
        )

        for chunk_index, product_chunk in enumerate(
            product_chunks,
            start=1,
        ):

            print(
                f"   Chunk "
                f"{chunk_index}/"
                f"{len(product_chunks)} "
                f"({len(product_chunk)} productos)"
            )

            try:

                html = fetch_prices(
                    session=session,
                    region=region_code,
                    product_ids=product_chunk,
                    date_from=args.from_date,
                    date_to=args.to_date,
                    variable=args.variable,
                    periodicity=args.periodicity,
                )

                df_chunk = parse_sisap(
                    html=html,
                    region_code=region_code,
                    variable=args.variable,
                )

                if not df_chunk.empty:
                    region_frames.append(
                        df_chunk
                    )

            except Exception as exc:

                print(
                    f"   ERROR: {exc}"
                )

            time.sleep(
                args.delay
            )

        if region_frames:

            df_region = pd.concat(
                region_frames,
                ignore_index=True,
            )

            df_region = (
                df_region
                .drop_duplicates()
                .reset_index(drop=True)
            )

            all_frames.append(
                df_region
            )

            # Guardamos un archivo por región
            region_path = (
                PROCESSED_DIR
                / f"sisap_{region_code}.parquet"
            )

            df_region.to_parquet(
                region_path,
                index=False,
            )

            print(
                f"   Registros: "
                f"{len(df_region):,}"
            )

    # --------------------------------------------------------
    # CONSOLIDADO
    # --------------------------------------------------------

    if not all_frames:
        raise RuntimeError(
            "No se obtuvo información."
        )

    df = pd.concat(
        all_frames,
        ignore_index=True,
    )

    df = (
        df
        .drop_duplicates()
        .sort_values(
            [
                "region_code",
                "product_name",
                "year",
                "month",
                "unit",
            ]
        )
        .reset_index(drop=True)
    )

    df["retrieved_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    csv_path = (
        PROCESSED_DIR
        / "midagri_prices.csv"
    )

    parquet_path = (
        PROCESSED_DIR
        / "midagri_prices.parquet"
    )

    df.to_csv(
        csv_path,
        index=False,
        encoding="utf-8-sig",
    )

    df.to_parquet(
        parquet_path,
        index=False,
    )

    print()
    print("=" * 70)
    print("EXTRACCIÓN TERMINADA")
    print("=" * 70)

    print(
        f"Regiones: "
        f"{df['region_code'].nunique()}"
    )

    print(
        f"Productos reales: "
        f"{df['product_code'].nunique()}"
    )

    print(
        f"Registros: {len(df):,}"
    )

    print(
        f"CSV: {csv_path}"
    )

    print(
        f"Parquet: {parquet_path}"
    )


if __name__ == "__main__":
    main()