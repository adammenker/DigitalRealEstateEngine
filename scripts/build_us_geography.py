#!/usr/bin/env python3
"""Build the offline U.S. geography index from public reference datasets."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DATASET_VERSION = "us-geography-2024.1"
REFERENCE_YEAR = 2024
STATE_BY_FIPS = {
    "01": "AL",
    "02": "AK",
    "04": "AZ",
    "05": "AR",
    "06": "CA",
    "08": "CO",
    "09": "CT",
    "10": "DE",
    "11": "DC",
    "12": "FL",
    "13": "GA",
    "15": "HI",
    "16": "ID",
    "17": "IL",
    "18": "IN",
    "19": "IA",
    "20": "KS",
    "21": "KY",
    "22": "LA",
    "23": "ME",
    "24": "MD",
    "25": "MA",
    "26": "MI",
    "27": "MN",
    "28": "MS",
    "29": "MO",
    "30": "MT",
    "31": "NE",
    "32": "NV",
    "33": "NH",
    "34": "NJ",
    "35": "NM",
    "36": "NY",
    "37": "NC",
    "38": "ND",
    "39": "OH",
    "40": "OK",
    "41": "OR",
    "42": "PA",
    "44": "RI",
    "45": "SC",
    "46": "SD",
    "47": "TN",
    "48": "TX",
    "49": "UT",
    "50": "VT",
    "51": "VA",
    "53": "WA",
    "54": "WV",
    "55": "WI",
    "56": "WY",
}
VALID_STATES = set(STATE_BY_FIPS.values())
SOURCE_URLS = {
    "places.zip": (
        "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
        "2024_Gazetteer/2024_Gaz_place_national.zip"
    ),
    "zcta.zip": (
        "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
        "2024_Gazetteer/2024_Gaz_zcta_national.zip"
    ),
    "population.dat": (
        "https://www2.census.gov/programs-surveys/acs/summary_file/2024/"
        "table-based-SF/data/5YRData/acsdt5y2024-b01003.dat"
    ),
    "place_county.txt": (
        "https://www2.census.gov/geo/docs/reference/codes2020/"
        "national_place_by_county2020.txt"
    ),
    "zcta_county.txt": (
        "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
        "tab20_zcta520_county20_natl.txt"
    ),
    "zcta_place.txt": (
        "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/"
        "tab20_zcta520_place20_natl.txt"
    ),
    "cbsa_county.xlsx": (
        "https://www2.census.gov/programs-surveys/metro-micro/geographies/"
        "reference-files/2023/delineation-files/list1_2023.xlsx"
    ),
    "cbsa_county_legacy.xls": (
        "https://www2.census.gov/programs-surveys/metro-micro/geographies/"
        "reference-files/2020/delineation-files/list1_2020.xls"
    ),
    "geonames-us.zip": "https://download.geonames.org/export/zip/US.zip",
}
LEGAL_SUFFIXES = (
    " consolidated government",
    " metropolitan government",
    " unified government",
    " urban county",
    " municipality",
    " city and borough",
    " city and county",
    " borough",
    " village",
    " town",
    " city",
    " cdp",
    " balance",
)


@dataclass(frozen=True)
class GazetteerRow:
    geoid: str
    name: str
    state: str | None
    latitude: float
    longitude: float
    land_area_sq_km: float
    water_area_sq_km: float


@dataclass(frozen=True)
class GeographyRow:
    id: str
    kind: str
    city: str
    state: str
    postal_code: str | None
    county: str
    county_fips: str
    metro: str
    metro_code: str | None
    metro_type: str
    latitude: float
    longitude: float
    population: int
    reference_population: int
    aliases: list[str]
    postal_codes: list[str]
    boundary_radius_km: float
    land_area_sq_km: float
    source_geoid: str


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path(".cache/us-geography"),
        help="Directory used for downloaded source files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/us_geography.sqlite3"),
        help="Output SQLite database.",
    )
    args = parser.parse_args()
    sources = _download_sources(args.source_dir)
    rows, metadata = _build_rows(sources)
    _write_database(args.output, rows, sources, metadata)
    print(
        f"Wrote {len(rows):,} geography records to {args.output} "
        f"({metadata['city_count']:,} cities, {metadata['zip_count']:,} ZIPs)."
    )


def _download_sources(source_dir: Path) -> dict[str, Path]:
    source_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, url in SOURCE_URLS.items():
        path = source_dir / name
        if not path.exists():
            print(f"Downloading {name}...")
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "DigitalRealEstateEngine geography builder"},
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                path.write_bytes(response.read())
        paths[name] = path
    return paths


def _build_rows(sources: dict[str, Path]) -> tuple[list[GeographyRow], dict[str, int]]:
    population = _read_population(sources["population.dat"])
    reference_population = population["0100000US"]
    places = _read_gazetteer(sources["places.zip"], kind="place")
    zctas = _read_gazetteer(sources["zcta.zip"], kind="zcta")
    place_counties = _read_place_counties(sources["place_county.txt"])
    zcta_counties = _read_zcta_counties(sources["zcta_county.txt"])
    place_zips, zcta_places = _read_zcta_places(sources["zcta_place.txt"])
    county_metros = _read_county_metros(
        sources["cbsa_county.xlsx"],
        sources["cbsa_county_legacy.xls"],
    )
    postal_aliases, postal_counties = _read_geonames(sources["geonames-us.zip"])

    rows: list[GeographyRow] = []
    excluded_cities = 0
    excluded_zips = 0
    for place in places.values():
        state = place.state
        place_population = population.get(f"1600000US{place.geoid}", 0)
        if state not in VALID_STATES or place_population <= 0:
            excluded_cities += 1
            continue
        city = _strip_legal_suffix(place.name)
        postal_codes = sorted(place_zips.get(place.geoid, {}))
        county_fips, county = _place_county(
            place.geoid,
            city,
            state,
            postal_codes,
            place_counties,
            zcta_counties,
            postal_counties,
        )
        if not county_fips or not county:
            excluded_cities += 1
            continue
        metro_code, metro, metro_type = county_metros.get(
            county_fips,
            (None, "Nonmetropolitan", "nonmetropolitan"),
        )
        aliases = _city_aliases(
            city,
            place.name,
            postal_codes,
            postal_aliases,
        )
        rows.append(
            GeographyRow(
                id=f"place:{place.geoid}",
                kind="city",
                city=city,
                state=state,
                postal_code=None,
                county=county,
                county_fips=county_fips,
                metro=metro,
                metro_code=metro_code,
                metro_type=metro_type,
                latitude=place.latitude,
                longitude=place.longitude,
                population=place_population,
                reference_population=reference_population,
                aliases=aliases,
                postal_codes=postal_codes,
                boundary_radius_km=_boundary_radius(
                    place.land_area_sq_km + place.water_area_sq_km,
                    minimum=3,
                ),
                land_area_sq_km=place.land_area_sq_km,
                source_geoid=place.geoid,
            )
        )

    for zcta in zctas.values():
        zip_population = population.get(f"860Z200US{zcta.geoid}", 0)
        county_match = zcta_counties.get(zcta.geoid)
        place_match = zcta_places.get(zcta.geoid)
        aliases = postal_aliases.get(zcta.geoid, [])
        state = (
            STATE_BY_FIPS.get(county_match[0][:2])
            if county_match
            else STATE_BY_FIPS.get(place_match[0][:2])
            if place_match
            else None
        )
        city = (
            _strip_legal_suffix(place_match[1])
            if place_match
            else aliases[0]
            if aliases
            else ""
        )
        if (
            state not in VALID_STATES
            or zip_population <= 0
            or not county_match
            or not city
        ):
            excluded_zips += 1
            continue
        county_fips, county = county_match
        metro_code, metro, metro_type = county_metros.get(
            county_fips,
            (None, "Nonmetropolitan", "nonmetropolitan"),
        )
        rows.append(
            GeographyRow(
                id=f"zcta:{zcta.geoid}",
                kind="postal_code",
                city=city,
                state=state,
                postal_code=zcta.geoid,
                county=county,
                county_fips=county_fips,
                metro=metro,
                metro_code=metro_code,
                metro_type=metro_type,
                latitude=zcta.latitude,
                longitude=zcta.longitude,
                population=zip_population,
                reference_population=reference_population,
                aliases=sorted(set([zcta.geoid, *aliases])),
                postal_codes=[zcta.geoid],
                boundary_radius_km=_boundary_radius(
                    zcta.land_area_sq_km + zcta.water_area_sq_km,
                    minimum=2,
                ),
                land_area_sq_km=zcta.land_area_sq_km,
                source_geoid=zcta.geoid,
            )
        )
    rows.sort(key=lambda row: (row.kind, row.state, row.city, row.postal_code or ""))
    return rows, {
        "city_count": sum(row.kind == "city" for row in rows),
        "zip_count": sum(row.kind == "postal_code" for row in rows),
        "excluded_city_count": excluded_cities,
        "excluded_zip_count": excluded_zips,
        "reference_population": reference_population,
    }


def _read_population(path: Path) -> dict[str, int]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter="|")
        return {
            row["GEO_ID"]: int(row["B01003_E001"])
            for row in reader
            if row.get("GEO_ID") and _positive_int(row.get("B01003_E001")) is not None
        }


def _read_gazetteer(path: Path, *, kind: str) -> dict[str, GazetteerRow]:
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".txt"))
        with archive.open(member) as binary:
            text = io.TextIOWrapper(binary, encoding="utf-8-sig")
            reader = csv.DictReader(text, delimiter="\t")
            reader.fieldnames = [
                field_name.strip() for field_name in reader.fieldnames or []
            ]
            output: dict[str, GazetteerRow] = {}
            for raw in reader:
                geoid = str(raw.get("GEOID") or "").strip()
                if not geoid:
                    continue
                output[geoid] = GazetteerRow(
                    geoid=geoid,
                    name=str(raw.get("NAME") or geoid).strip(),
                    state=str(raw.get("USPS") or "").strip() or None,
                    latitude=float(raw["INTPTLAT"]),
                    longitude=float(raw["INTPTLONG"]),
                    land_area_sq_km=float(raw["ALAND"]) / 1_000_000,
                    water_area_sq_km=float(raw["AWATER"]) / 1_000_000,
                )
    if kind == "place":
        return {
            geoid: row
            for geoid, row in output.items()
            if row.state in VALID_STATES
        }
    return output


def _read_place_counties(path: Path) -> dict[str, list[tuple[str, str]]]:
    output: dict[str, list[tuple[str, str]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle, delimiter="|"):
            state = str(raw.get("STATE") or "")
            if state not in VALID_STATES:
                continue
            place_geoid = f"{raw['STATEFP']}{raw['PLACEFP']}"
            county_fips = f"{raw['STATEFP']}{raw['COUNTYFP']}"
            item = (county_fips, str(raw["COUNTYNAME"]).strip())
            if item not in output[place_geoid]:
                output[place_geoid].append(item)
    return dict(output)


def _read_zcta_counties(path: Path) -> dict[str, tuple[str, str]]:
    best: dict[str, tuple[int, str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle, delimiter="|"):
            zcta = str(raw.get("GEOID_ZCTA5_20") or "").strip()
            county_fips = str(raw.get("GEOID_COUNTY_20") or "").strip()
            if not zcta or not county_fips or county_fips[:2] not in STATE_BY_FIPS:
                continue
            area = int(raw.get("AREALAND_PART") or 0)
            candidate = (area, county_fips, str(raw.get("NAMELSAD_COUNTY_20") or "").strip())
            if candidate > best.get(zcta, (-1, "", "")):
                best[zcta] = candidate
    return {
        zcta: (county_fips, county)
        for zcta, (_, county_fips, county) in best.items()
    }


def _read_zcta_places(
    path: Path,
) -> tuple[dict[str, dict[str, int]], dict[str, tuple[str, str]]]:
    place_zips: dict[str, dict[str, int]] = defaultdict(dict)
    best_place: dict[str, tuple[int, str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle, delimiter="|"):
            zcta = str(raw.get("GEOID_ZCTA5_20") or "").strip()
            place = str(raw.get("GEOID_PLACE_20") or "").strip()
            if not zcta or not place or place[:2] not in STATE_BY_FIPS:
                continue
            area = int(raw.get("AREALAND_PART") or 0)
            name = str(raw.get("NAMELSAD_PLACE_20") or "").strip()
            place_zips[place][zcta] = max(area, place_zips[place].get(zcta, 0))
            candidate = (area, place, name)
            if candidate > best_place.get(zcta, (-1, "", "")):
                best_place[zcta] = candidate
    return (
        dict(place_zips),
        {zcta: (place, name) for zcta, (_, place, name) in best_place.items()},
    )


def _read_county_metros(
    current_path: Path,
    legacy_path: Path,
) -> dict[str, tuple[str, str, str]]:
    rows = _read_xlsx_rows(current_path)
    output: dict[str, tuple[str, str, str]] = {}
    _append_county_metros(output, rows[3:])
    _append_county_metros(output, _read_xls_rows(legacy_path)[3:], only_missing=True)
    return output


def _append_county_metros(
    output: dict[str, tuple[str, str, str]],
    rows: list[list[str]],
    *,
    only_missing: bool = False,
) -> None:
    for row in rows:
        if len(row) < 11:
            continue
        cbsa_code = row[0].strip()
        title = row[3].strip()
        metro_type = row[4].strip()
        state_fips = row[9].strip().zfill(2)
        county_fips = row[10].strip().zfill(3)
        if (
            cbsa_code
            and title
            and state_fips in STATE_BY_FIPS
            and county_fips
        ):
            key = f"{state_fips}{county_fips}"
            if only_missing and key in output:
                continue
            output[key] = (
                cbsa_code,
                title,
                "metropolitan" if metro_type.startswith("Metropolitan") else "micropolitan",
            )


def _read_xlsx_rows(path: Path) -> list[list[str]]:
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = [
            "".join(node.text or "" for node in item.findall(".//x:t", namespace))
            for item in shared_root.findall("x:si", namespace)
        ]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows: list[list[str]] = []
    for row_node in sheet.findall(".//x:sheetData/x:row", namespace):
        values: dict[int, str] = {}
        for cell in row_node.findall("x:c", namespace):
            reference = str(cell.get("r") or "A1")
            column = _column_number(reference)
            value_node = cell.find("x:v", namespace)
            value = "" if value_node is None else value_node.text or ""
            if cell.get("t") == "s" and value:
                value = shared[int(value)]
            values[column] = value
        width = max(values, default=-1) + 1
        rows.append([values.get(index, "") for index in range(width)])
    return rows


def _read_xls_rows(path: Path) -> list[list[str]]:
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError(
            "Building the geography index requires the development dependency xlrd."
        ) from exc
    workbook = xlrd.open_workbook(path)
    sheet = workbook.sheet_by_index(0)
    return [
        [str(sheet.cell_value(row, column)).strip() for column in range(sheet.ncols)]
        for row in range(sheet.nrows)
    ]


def _column_number(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference)
    value = 0
    for letter in letters.group(0) if letters else "A":
        value = value * 26 + ord(letter) - ord("A") + 1
    return value - 1


def _read_geonames(
    path: Path,
) -> tuple[dict[str, list[str]], dict[tuple[str, str], tuple[str, str]]]:
    aliases: dict[str, set[str]] = defaultdict(set)
    counties: dict[tuple[str, str], tuple[str, str]] = {}
    with zipfile.ZipFile(path) as archive:
        with archive.open("US.txt") as binary:
            text = io.TextIOWrapper(binary, encoding="utf-8")
            for row in csv.reader(text, delimiter="\t"):
                if len(row) < 12:
                    continue
                postal_code, place_name, state = row[1].strip(), row[2].strip(), row[4].strip()
                county, county_code = row[5].strip(), row[6].strip()
                if state not in VALID_STATES or not re.fullmatch(r"\d{5}", postal_code):
                    continue
                if place_name:
                    aliases[postal_code].add(place_name)
                if county and county_code:
                    state_fips = next(
                        (fips for fips, abbreviation in STATE_BY_FIPS.items() if abbreviation == state),
                        "",
                    )
                    if state_fips:
                        counties[(postal_code, state)] = (
                            f"{state_fips}{county_code.zfill(3)}",
                            county,
                        )
    return (
        {postal_code: sorted(names) for postal_code, names in aliases.items()},
        counties,
    )


def _place_county(
    place_geoid: str,
    city: str,
    state: str,
    postal_codes: list[str],
    place_counties: dict[str, list[tuple[str, str]]],
    zcta_counties: dict[str, tuple[str, str]],
    postal_counties: dict[tuple[str, str], tuple[str, str]],
) -> tuple[str, str]:
    valid = place_counties.get(place_geoid, [])
    valid_names = dict(valid)
    valid_fips = set(valid_names)
    votes: dict[tuple[str, str], int] = defaultdict(int)
    for postal_code in postal_codes:
        county = postal_counties.get((postal_code, state)) or zcta_counties.get(postal_code)
        if county and (not valid_fips or county[0] in valid_fips):
            votes[county] += 1
    if votes:
        county_fips, county = max(votes, key=lambda item: (votes[item], item[0]))
        return county_fips, valid_names.get(county_fips, county)
    if valid:
        return valid[0]
    return "", ""


def _city_aliases(
    city: str,
    official_name: str,
    postal_codes: Iterable[str],
    postal_aliases: dict[str, list[str]],
) -> list[str]:
    aliases = {city, official_name}
    normalized_city = _normalize(city)
    for postal_code in postal_codes:
        for alias in postal_aliases.get(postal_code, []):
            ratio = _similarity(normalized_city, _normalize(alias))
            if ratio >= 0.82:
                aliases.add(alias)
    if city.startswith("St. "):
        aliases.add(f"Saint {city[4:]}")
        aliases.add(f"St {city[4:]}")
    if city.startswith("Saint "):
        aliases.add(f"St. {city[6:]}")
        aliases.add(f"St {city[6:]}")
    if city.startswith("Fort "):
        aliases.add(f"Ft. {city[5:]}")
        aliases.add(f"Ft {city[5:]}")
    if city.startswith("Ft. "):
        aliases.add(f"Fort {city[4:]}")
    return sorted(alias for alias in aliases if alias)


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0
    if left == right:
        return 1
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    token_overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    prefix = 1 if left.startswith(right) or right.startswith(left) else 0
    return max(token_overlap, prefix * min(len(left), len(right)) / max(len(left), len(right)))


def _strip_legal_suffix(value: str) -> str:
    result = value.strip()
    lowered = result.lower()
    for suffix in LEGAL_SUFFIXES:
        if lowered.endswith(suffix):
            return result[: -len(suffix)].strip()
    return result


def _boundary_radius(area_sq_km: float, *, minimum: float) -> float:
    equivalent_radius = math.sqrt(max(area_sq_km, 0.01) / math.pi)
    return round(max(minimum, min(50.0, equivalent_radius)), 2)


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _write_database(
    output: Path,
    rows: list[GeographyRow],
    sources: dict[str, Path],
    metadata: dict[str, int],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            CREATE TABLE geographies (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('city', 'postal_code')),
                city TEXT NOT NULL,
                state TEXT NOT NULL,
                postal_code TEXT,
                county TEXT NOT NULL,
                county_fips TEXT NOT NULL,
                metro TEXT NOT NULL,
                metro_code TEXT,
                metro_type TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                population INTEGER NOT NULL CHECK (population > 0),
                reference_population INTEGER NOT NULL CHECK (reference_population > 0),
                aliases_json TEXT NOT NULL,
                postal_codes_json TEXT NOT NULL,
                boundary_radius_km REAL NOT NULL CHECK (boundary_radius_km >= 1),
                land_area_sq_km REAL NOT NULL CHECK (land_area_sq_km >= 0),
                source_geoid TEXT NOT NULL,
                dataset_version TEXT NOT NULL
            );
            CREATE TABLE geography_aliases (
                alias_norm TEXT NOT NULL,
                geography_id TEXT NOT NULL REFERENCES geographies(id),
                alias TEXT NOT NULL,
                priority INTEGER NOT NULL,
                PRIMARY KEY (alias_norm, geography_id, alias)
            );
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE INDEX ix_geographies_city_state ON geographies(city, state);
            CREATE UNIQUE INDEX ix_geographies_postal_code
                ON geographies(postal_code) WHERE postal_code IS NOT NULL;
            CREATE INDEX ix_geography_aliases_norm ON geography_aliases(alias_norm);
            """
        )
        connection.executemany(
            """
            INSERT INTO geographies (
                id, kind, city, state, postal_code, county, county_fips, metro,
                metro_code, metro_type, latitude, longitude, population,
                reference_population, aliases_json, postal_codes_json,
                boundary_radius_km, land_area_sq_km, source_geoid, dataset_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row.id,
                    row.kind,
                    row.city,
                    row.state,
                    row.postal_code,
                    row.county,
                    row.county_fips,
                    row.metro,
                    row.metro_code,
                    row.metro_type,
                    row.latitude,
                    row.longitude,
                    row.population,
                    row.reference_population,
                    json.dumps(row.aliases, separators=(",", ":")),
                    json.dumps(row.postal_codes, separators=(",", ":")),
                    row.boundary_radius_km,
                    row.land_area_sq_km,
                    row.source_geoid,
                    DATASET_VERSION,
                )
                for row in rows
            ],
        )
        alias_rows: list[tuple[str, str, str, int]] = []
        for row in rows:
            for alias in row.aliases:
                alias_norm = _normalize(alias)
                if alias_norm:
                    priority = 100 if alias == row.city or alias == row.postal_code else 80
                    alias_rows.append((alias_norm, row.id, alias, priority))
        connection.executemany(
            """
            INSERT OR IGNORE INTO geography_aliases
                (alias_norm, geography_id, alias, priority)
            VALUES (?, ?, ?, ?)
            """,
            alias_rows,
        )
        source_manifest = {
            name: {
                "url": SOURCE_URLS[name],
                "sha256": _sha256(path),
            }
            for name, path in sorted(sources.items())
        }
        database_metadata = {
            "dataset_version": DATASET_VERSION,
            "reference_year": str(REFERENCE_YEAR),
            "built_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "sources": json.dumps(source_manifest, sort_keys=True, separators=(",", ":")),
            **{key: str(value) for key, value in metadata.items()},
        }
        connection.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            sorted(database_metadata.items()),
        )
        connection.commit()
        connection.execute("VACUUM")
    finally:
        connection.close()
    temporary.replace(output)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
