r"""Build a single offline HTML dashboard from saved Nevsky research results.

Windows: .\.venv\Scripts\python.exe .\build_dashboard.py

Only Python's standard library is used. No network access, BlocksNet calls,
spatial analysis, routing, imputation, or notebook execution is performed.
The main block GeoJSON is the base; the separately corrected provision layer
is authoritative for provision fields. CSV tables and findings are read as saved.
The saved travel-time CSV is packed losslessly as float64 for the browser.
"""

from __future__ import annotations

import argparse
from array import array
import base64
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import zlib


ROOT = Path(__file__).resolve().parent

# title, unit, explanation, group. Unknown saved fields remain available too.
FIELDS = {
    "block_id": ("Номер квартала", "", "Постоянный номер аналитического квартала.", "Идентификация"),
    "population": ("Расчётное население", "чел.", "Оценка по жилой площади, не данные переписи.", "Здания"),
    "building_count": ("Здания", "шт.", "Число объектов, отнесённых к кварталу.", "Здания"),
    "site_area": ("Площадь квартала", "м²", "Площадь аналитического квартала.", "Здания"),
    "footprint_area": ("Пятна зданий", "м²", "Сумма площадей контуров зданий.", "Здания"),
    "build_floor_area": ("Площадь этажей", "м²", "Суммарная расчётная площадь всех этажей.", "Здания"),
    "living_area": ("Жилая площадь", "м²", "Расчётная жилая площадь.", "Здания"),
    "non_living_area": ("Нежилая площадь", "м²", "Расчётная нежилая площадь.", "Здания"),
    "floors_imputed": ("Здания без этажности", "шт.", "Для этих зданий этажность заполнена по умолчанию.", "Качество"),
    "population_estimated": ("Здания с оценкой населения", "шт.", "Количество жилых зданий с оценённым населением.", "Качество"),
    "fsi": ("FSI · интенсивность", "", "Площадь всех этажей / площадь квартала. Не оценка качества среды.", "Плотность"),
    "gsi": ("GSI · доля застройки", "доля", "Площадь пятен / площадь квартала; в BlocksNet ограничена единицей.", "Плотность"),
    "mxi": ("MXI · доля жилья", "доля", "Жилая площадь / суммарная площадь этажей.", "Плотность"),
    "l": ("L · расчётная этажность", "эт.", "Площадь этажей / площадь пятен зданий, взвешенная этажность.", "Плотность"),
    "osr": ("OSR · открытое пространство", "", "Незастроенная площадь / площадь этажей. Это не доля зелени.", "Плотность"),
    "share_living": ("Жилая площадь / пятна", "", "Отношение жилой площади к площади пятен, может быть больше 1.", "Плотность"),
    "share_non_living": ("Нежилая площадь / пятна", "", "Отношение нежилой площади к площади пятен, может быть больше 1.", "Плотность"),
    "land_use": ("Землепользование OSM", "", "Преобладающая категория по OSM, не официальное зонирование.", "Тип территории"),
    "spacematrix": ("Тип Spacematrix", "", "Классификация по интенсивности, этажности и доле жилья.", "Тип территории"),
    "strelka": ("Тип Strelka", "", "Классификация застройки по фиксированным правилам.", "Тип территории"),
    "cluster": ("Кластер Spacematrix", "", "Номер группы, не рейтинг и не балл качества.", "Тип территории"),
    "school_count": ("Школы", "шт.", "Число школьных объектов, отнесённых к кварталу.", "Школы"),
    "capacity": ("Вместимость школ", "мест", "Сумма известных и оценённых вместимостей.", "Школы"),
    "capacity_imputed": ("Школы с оценённой вместимостью", "шт.", "Вместимость этих школ заполнена по конфигурации BlocksNet.", "Качество"),
    "demand": ("Потребность в школах", "мест", "Расчётный спрос по населению и выбранной потребности на 1000 жителей.", "Обеспеченность"),
    "demand_left": ("Необеспеченный спрос", "мест", "Спрос, которому модель не подобрала школьные места.", "Обеспеченность"),
    "demand_within": ("Спрос в пределах порога", "мест", "Распределённый спрос в пределах выбранного времени.", "Обеспеченность"),
    "demand_without": ("Спрос вне порога", "мест", "Распределённый спрос за пределами порога. При глубине 1 ожидается 0.", "Обеспеченность"),
    "capacity_left": ("Оставшиеся места", "мест", "Неиспользованная вместимость после распределения спроса.", "Обеспеченность"),
    "capacity_within": ("Использованные места в пределах порога", "мест", "Обслуживание собственного и других кварталов в пределах порога.", "Обеспеченность"),
    "capacity_without": ("Использованные места вне порога", "мест", "Обслуживание спроса из-за пределов выбранного порога.", "Обеспеченность"),
    "capacity_self_supply": ("Обслуживание своего квартала", "мест", "Места, выделенные жителям того же квартала; сохранённая поправка учёта.", "Обеспеченность"),
    "capacity_accounting_adjustment": ("Поправка счётчика мест", "мест", "Сохранённая поправка к результату BlocksNet 1.0.0a9, не дополнительные места.", "Качество"),
    "provision_strong": ("Обеспеченность в пределах порога", "доля", "Доля спроса, удовлетворённого в пределах автомобильного порога; учебный сценарий.", "Обеспеченность"),
    "provision_weak": ("Обеспеченность с внешними связями", "доля", "Доля спроса, обслуженного в пределах и вне порога. При глубине 1 совпадает с сильной.", "Обеспеченность"),
    "area_accessibility": ("Доступность территории", "мин", "Среднее исходящее время, взвешенное по площади кварталов. Недостижимость не равна нулю.", "Доступность"),
    "area_accessibility_unreachable": ("Есть недостижимая территория", "флаг", "1 означает, что полное среднее время не является конечным.", "Доступность"),
    "reachable_share": ("Доля достижимых кварталов", "доля", "Сохранённая доля достижимых направлений из данного квартала.", "Доступность"),
    "snap_distance_m": ("Расстояние привязки к сети", "м", "Расстояние от точки квартала до входа в автомобильную сеть; не время подъезда.", "Качество"),
    "footprint_exceeds_site": ("Пятна больше квартала", "флаг", "Признак для проверки пересечений зданий и краевых объектов.", "Качество"),
    "node_id": ("Узел автомобильной сети", "", "Сохранённый номер входа в сеть, используемый для маршрутизации.", "Идентификация"),
    "entity_id": ("Исходный ID GeoJSON", "", "Номер строки исходного пространственного слоя.", "Идентификация"),
    "land_use_share": ("Доля преобладающего назначения", "доля", "Сохранённая площадь пересечения с преобладающей категорией OSM / площадь квартала.", "Тип территории"),
}
LAND_USE = {"residential": "Жилая", "business": "Деловая", "industrial": "Промышленная",
            "recreation": "Рекреационная", "transport": "Транспортная", "special": "Специальная",
            "agriculture": "Сельскохозяйственная"}
for _key, _label in LAND_USE.items():
    FIELDS[f"land_use_{_key}"] = (f"OSM · {_label.lower()}", "доля",
                                "Сохранённая доля пересечения с категорией OSM; пересечения исходных зон могут накладываться.",
                                "Тип территории")


class DashboardError(ValueError):
    """An actionable error in saved input files."""


def block_key(value):
    if value is None or isinstance(value, bool):
        raise DashboardError("У квартала отсутствует корректный block_id/entity_id.")
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise DashboardError(f"Некорректный номер квартала: {value!r}")
        value = int(value)
    value = str(value).strip()
    if not value:
        raise DashboardError("Обнаружен пустой номер квартала.")
    return value


def feature_key(feature):
    props = feature.get("properties") or {}
    return block_key(props.get("block_id", props.get("entity_id", feature.get("id"))))


def read_geo(path):
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise DashboardError(f"Не удалось прочитать GeoJSON {path.name}: {exc}") from exc
    if document.get("type") != "FeatureCollection" or not isinstance(document.get("features"), list):
        raise DashboardError(f"{path.name}: ожидается GeoJSON FeatureCollection.")
    crs = str(document.get("crs", ""))
    if crs and not any(token in crs for token in ["4326", "CRS84"]):
        raise DashboardError(f"{path.name}: нужны сохранённые координаты GeoJSON EPSG:4326.")
    return {"type": "FeatureCollection", "features": document["features"]}


def unique_features(document, name):
    indexed = {}
    for feature in document["features"]:
        key = feature_key(feature)
        if key in indexed:
            raise DashboardError(f"{name}: повторяется номер квартала {key}.")
        indexed[key] = feature
    return indexed


def merge_layer(base, layer, name, *, override=False, rename=None):
    """Join saved attributes by ID only; do not derive any research metrics."""
    incoming = unique_features(layer, name)
    if set(incoming) != set(base):
        raise DashboardError(f"{name}: номера кварталов не совпадают с итоговым слоем. "
                             "Используйте результаты одного запуска ноутбука.")
    changed = set()
    for key, feature in incoming.items():
        target = base[key]["properties"]
        for field, value in (feature.get("properties") or {}).items():
            if field in ["entity_id", "block_id"]:
                continue
            field = (rename or {}).get(field, field)
            if override or field not in target:
                if field in target and target[field] != value:
                    changed.add(field)
                target[field] = value
    return changed


def read_csv_table(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        headers = next(reader, [])
        headers = [header or "Показатель" for header in headers]
        rows = []
        for values in reader:
            if len(values) != len(headers):
                raise DashboardError(f"{path.name}: строка CSV не соответствует заголовку.")
            rows.append(dict(zip(headers, values)))
    return {"columns": headers, "rows": rows}


def pack_matrix(path, expected_ids):
    """Encode existing times, including infinity; no distances or routes computed."""
    values = array("d")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader, None)
        if not header or len(header) < 2:
            raise DashboardError("Матрица времени CSV пуста.")
        columns = [block_key(value) for value in header[1:]]
        if len(set(columns)) != len(columns) or set(columns) != expected_ids:
            raise DashboardError("Столбцы матрицы времени не совпадают с номерами кварталов.")
        rows = []
        for row in reader:
            if len(row) != len(columns) + 1:
                raise DashboardError("В матрице времени обнаружена строка неверной длины.")
            rows.append(block_key(row[0]))
            try:
                values.extend(float(value) if value.strip() else math.nan for value in row[1:])
            except ValueError as exc:
                raise DashboardError("В матрице времени есть нечисловое значение.") from exc
        if len(set(rows)) != len(rows) or set(rows) != expected_ids:
            raise DashboardError("Строки матрицы времени не совпадают с номерами кварталов.")
    if sys.byteorder != "little":
        values.byteswap()
    return {"rows": rows, "columns": columns, "encoding": "zlib-float64-le",
            "data": base64.b64encode(zlib.compress(values.tobytes(), level=6)).decode("ascii")}


def load_inputs(data_dir):
    results, prepared = data_dir / "results", data_dir / "prepared"
    main = results / "nevsky_blocks_results.geojson"
    if not main.is_file():
        raise DashboardError(f"Не найден {main}. Сначала сохраните итоги в разделе 15 ноутбука.")
    warnings, sources = [], []

    def record(path):
        sources.append({"file": str(path.relative_to(data_dir)), "bytes": path.stat().st_size,
                        "modified_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()})

    blocks = read_geo(main)
    indexed = unique_features(blocks, main.name)
    if not indexed:
        raise DashboardError("Итоговый слой кварталов пуст.")
    record(main)
    for key, feature in indexed.items():
        props = feature.setdefault("properties", {})
        props.setdefault("block_id", props.get("entity_id", key))
        if (feature.get("geometry") or {}).get("type") not in ["Polygon", "MultiPolygon"]:
            raise DashboardError(f"Квартал {key}: нужна геометрия Polygon или MultiPolygon.")

    # Additional saved columns, e.g. land-use shares, remain discoverable in UI.
    layers = [(results / "blocks_density.geojson", {}),
              (results / "blocks_accessibility.geojson", {}),
              (results / "blocks_spacematrix.geojson", {"morphotype": "spacematrix"}),
              (results / "blocks_strelka.geojson", {"morphotype": "strelka"})]
    for path, renames in layers:
        if path.is_file():
            merge_layer(indexed, read_geo(path), path.name, rename=renames)
            record(path)
    land_path = prepared / "blocks_land_use.geojson"
    if land_path.is_file():
        renames = {key: f"land_use_{key}" for key in LAND_USE}
        renames["share"] = "land_use_share"
        merge_layer(indexed, read_geo(land_path), land_path.name, rename=renames)
        record(land_path)

    corrected_path = results / "blocks_school_provision_checked.geojson"
    changed = set()
    if corrected_path.is_file():
        changed = merge_layer(indexed, read_geo(corrected_path), corrected_path.name, override=True)
        record(corrected_path)
        if changed:
            warnings.append("Поля обеспеченности взяты из отдельного исправленного слоя. "
                            "Основной итоговый GeoJSON содержит более ранние значения: " + ", ".join(sorted(changed)) + ".")
    else:
        warnings.append("Исправленный слой школьной вместимости не найден. "
                        "Показаны сохранённые значения итогового GeoJSON; поправка здесь не рассчитывается.")

    def optional_json(name):
        path = results / name
        if path.is_file():
            record(path)
            return json.loads(path.read_text(encoding="utf-8-sig"))
        warnings.append(f"Не найден {name}; соответствующие сведения не включены.")
        return {}

    summary = optional_json("summary.json")
    run_info = optional_json("run_info.json")
    tables = []
    titles = {"quality_checks.csv": "Контрольные показатели качества",
              "indicators_summary.csv": "Сохранённая статистика показателей",
              "spacematrix_clusters.csv": "Кластеры Spacematrix",
              "top_school_demand_left.csv": "Сохранённая выборка кварталов с дефицитом",
              "school_links.csv": "Распределение спроса между кварталами"}
    for filename, title in titles.items():
        path = results / filename
        if not path.is_file():
            warnings.append(f"Не найдена таблица {filename}.")
            continue
        table = read_csv_table(path)
        table.update({"title": title, "file": filename, "note": "Готовая таблица из ноутбука."})
        record(path)
        if filename == "indicators_summary.csv" and changed:
            table["columns"].append("Статус")
            for row in table["rows"]:
                if row.get("Показатель") in changed:
                    for column in table["columns"]:
                        if column not in ["Показатель", "Статус"]:
                            row[column] = None
                    row["Статус"] = "Устарело после поправки: значения смотрите в таблице кварталов"
                else:
                    row["Статус"] = "Сохранено в ноутбуке"
            table["note"] = "Статистика не пересчитывается. Устаревшие после поправки строки помечены; их числа скрыты."
        if filename == "top_school_demand_left.csv":
            for row in table["rows"]:
                props = indexed.get(block_key(row.get("block_id")), {}).get("properties", {})
                for field in list(row):
                    if field in changed:
                        row[field] = props.get(field)
            table["note"] = "Состав выборки сохранён из ноутбука; поля обеспеченности берутся из исправленного слоя."
        tables.append(table)

    extras = {}
    for label, path in [("schools", prepared / "schools.geojson"),
                        ("links", results / "school_links.geojson"),
                        ("context", prepared / "graph_context.geojson"),
                        ("unassigned", prepared / "buildings_unassigned.geojson"),
                        ("water", data_dir / "raw" / "water.geojson")]:
        if path.is_file():
            extras[label] = read_geo(path)
            record(path)
    matrix_path = results / "travel_time_minutes.csv"
    matrix = None
    if matrix_path.is_file():
        matrix = pack_matrix(matrix_path, set(indexed))
        record(matrix_path)
    else:
        warnings.append("Нет travel_time_minutes.csv: таблица времени не включена. Маршруты здесь не рассчитываются.")

    reviewed = results / "findings_reviewed.txt"
    findings_path = reviewed if reviewed.is_file() else results / "findings_draft.txt"
    findings = findings_path.read_text(encoding="utf-8-sig") if findings_path.is_file() else "Выводы не сохранены."
    if findings_path.is_file():
        record(findings_path)
    chart_path = results / "research_charts.png"
    chart = None
    if chart_path.is_file():
        image = chart_path.read_bytes()
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DashboardError("research_charts.png не является PNG-файлом.")
        chart = base64.b64encode(image).decode("ascii")
        record(chart_path)
    columns = set().union(*(feature["properties"].keys() for feature in indexed.values()))
    fields = {key: {"title": value[0], "unit": value[1], "description": value[2], "group": value[3]}
              for key, value in FIELDS.items() if key in columns}
    for key in sorted(columns - fields.keys()):
        fields[key] = {"title": key, "unit": "", "description": "Дополнительное сохранённое поле.", "group": "Прочее"}
    return {"blocks": blocks, "fields": fields, "landUse": LAND_USE, "summary": summary,
            "run": run_info, "tables": tables, "extras": extras, "matrix": matrix,
            "findings": findings, "reviewed": reviewed.is_file(), "chart": chart,
            "warnings": warnings, "sources": sources,
            "corrected": corrected_path.is_file(), "changedFields": sorted(changed),
            "builtUtc": datetime.now(timezone.utc).isoformat()}


def inline_json(value):
    # Prevent a saved name/note from closing the JSON script element.
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


CSS = r"""
[hidden]{display:none!important}
:root{--bg:#f2f1eb;--paper:#fffefa;--ink:#183b40;--muted:#647675;--line:#dce2da;--accent:#ca714d;--teal:#247d80;--soft:#e7eeea;--blue:#dbeaf0;--shadow:0 8px 26px #173f4010}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 'Segoe UI',Arial,sans-serif}button,input,select{font:inherit}button,select,input{border:1px solid #c8d5ce;border-radius:8px;background:white;color:var(--ink);padding:9px 12px}button{cursor:pointer}button:hover{background:var(--soft)}button:focus-visible,a:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid #d99854;outline-offset:3px}button:disabled{opacity:.4;cursor:default}a{color:var(--teal)}h1,h2,h3,p{margin-top:0}h2{font-size:23px;letter-spacing:-.5px}h3{font-size:17px}small,.muted{color:var(--muted)}.wrap{max-width:1500px;margin:auto;padding:0 32px}.hero{background:#173c42;color:#fffefa;padding:34px 0 28px;position:relative;overflow:hidden}.hero:after{content:'';position:absolute;width:420px;height:420px;border:1px solid #ffffff16;border-radius:50%;right:8%;top:-260px;box-shadow:0 0 0 70px #ffffff05,0 0 0 140px #ffffff04;pointer-events:none}.eyebrow{text-transform:uppercase;letter-spacing:3px;font-size:11px;color:#c6d7cd;margin:0 0 8px}h1{font-size:44px;font-weight:600;letter-spacing:-1.8px;margin:0}.subtitle{color:#cfddd7;margin:8px 0 18px;max-width:850px}.pills{display:flex;gap:8px;flex-wrap:wrap}.pill{background:#ffffff12;border:1px solid #ffffff22;border-radius:20px;padding:4px 11px;font-size:12px}.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:22px 0}.kpi{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:17px 18px;box-shadow:var(--shadow)}.kpi .value{font-size:29px;font-weight:650;letter-spacing:-1px}.kpi .label{font-size:12px;color:var(--muted)}.kpi .note{font-size:11px;color:var(--muted);margin-top:3px}.notice{border-left:3px solid #cc9952;background:#faf4e6;padding:12px 16px;margin:0 0 18px;border-radius:0 9px 9px 0;font-size:13px}.notice p:last-child{margin-bottom:0}.tabs{display:flex;gap:4px;border-bottom:1px solid #cdd8d0;margin:4px 0 22px;overflow:auto}.tabs button{border:0;border-radius:8px 8px 0 0;background:transparent;padding:12px 18px;white-space:nowrap}.tabs button.active{background:var(--ink);color:white}.panel[hidden]{display:none}.card{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:22px;box-shadow:var(--shadow);margin-bottom:18px}.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.between{justify-content:space-between}.field{display:flex;flex-direction:column;gap:5px;font-size:12px}.field select,.field input{font-size:14px;max-width:100%}.filters{margin-bottom:18px;align-items:end}.grow{flex:1;min-width:220px}.filter-count{font-size:12px;color:var(--muted);padding:10px 0}.map-layout{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:18px}.map-card{padding:0;overflow:hidden;position:relative}.map-head{padding:17px 20px;border-bottom:1px solid var(--line)}#map{display:block;width:100%;height:610px;background:#eef2ec;touch-action:none;cursor:grab}#map.dragging{cursor:grabbing}.block{stroke:#fffefa;stroke-width:.65;vector-effect:non-scaling-stroke;cursor:pointer;transition:opacity .12s}.block:hover{stroke:#162e36;stroke-width:1.5}.block.selected{stroke:#112c37;stroke-width:3;filter:drop-shadow(0 0 2px #fff)}.block.dim{opacity:.14}.map-controls{position:absolute;left:14px;top:100px;display:flex;flex-direction:column;gap:5px}.map-controls button{width:38px;padding:6px;font-size:20px;background:#fffefaeF;box-shadow:0 2px 6px #0001}.map-caption{padding:10px 18px;border-top:1px solid var(--line);font-size:11px;color:var(--muted)}.legend{display:flex;gap:7px;align-items:center;flex-wrap:wrap;font-size:11px;margin-top:6px}.swatch{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:3px;vertical-align:middle}.legend-item{white-space:nowrap}.ramp{width:140px;height:9px;border-radius:3px;background:linear-gradient(90deg,#dce8df,#287c82,#be5f41)}.side{max-height:720px;overflow:auto}.check{display:flex;align-items:center;gap:8px;margin:9px 0;font-size:13px}.check input{accent-color:var(--teal)}.details-grid{margin:0}.details-grid div{padding:8px 0;border-bottom:1px solid #edf0e9}.details-grid dt{font-size:11px;color:var(--muted)}.details-grid dd{margin:1px 0 0;font-weight:600;overflow-wrap:anywhere}.section-label{font-size:10px;text-transform:uppercase;letter-spacing:1.8px;color:var(--muted);margin:20px 0 5px}.tag{display:inline-block;background:var(--soft);border-radius:4px;padding:2px 6px;font-size:11px}.tooltip{position:fixed;display:none;z-index:10;pointer-events:none;max-width:300px;background:#163a40;color:white;border-radius:8px;padding:10px 13px;box-shadow:0 6px 25px #0003;font-size:12px}.chart{display:block;width:100%;height:350px}.chart text{fill:var(--muted);font:11px 'Segoe UI',Arial}.chart .chart-title{font-size:14px;fill:var(--ink)}.chart .bar{fill:var(--teal);cursor:pointer}.chart .bar:hover{fill:var(--accent)}.chart .point{fill:var(--teal);opacity:.65;cursor:pointer}.chart .point:hover{opacity:1;fill:var(--accent)}.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:8px;max-height:620px}table{border-collapse:collapse;width:100%;font-size:12px;text-align:left}th,td{padding:10px 12px;border-bottom:1px solid #e5e9e2;white-space:nowrap;vertical-align:top}th{position:sticky;top:0;z-index:1;background:#eaf0e9;color:#365453;font-size:11px;font-weight:600}th button{padding:0;border:0;background:none;font-size:11px;text-align:left;border-radius:0;white-space:normal;min-width:95px}tr.selectable{cursor:pointer}tr.selectable:hover td{background:#f0f4ed}tr.selected-row td{background:#e0eae5}td.wrap-cell{white-space:normal;min-width:160px;max-width:500px}td.empty{color:#899792}.pagination{display:flex;justify-content:space-between;align-items:center;margin-top:13px;font-size:12px;gap:10px;flex-wrap:wrap}.pagination button{padding:6px 12px}.table-tools{margin-bottom:13px}.table-tools select{max-width:100%}.empty-state{padding:35px;text-align:center;color:var(--muted)}.pair-value{font-size:38px;letter-spacing:-1px;margin:15px 0 3px;font-weight:600}.quality-row{display:grid;grid-template-columns:1fr auto;gap:15px;padding:12px 0;border-bottom:1px solid var(--line)}.quality-row strong{font-size:19px}.prose{white-space:pre-wrap;line-height:1.8;font-size:15px;overflow-wrap:anywhere}.small-prose{font-size:13px;line-height:1.7}.saved-chart{width:100%;height:auto;display:block}.sources{font-size:11px;overflow-wrap:anywhere}.footer{padding:18px 0 35px;font-size:11px;color:var(--muted)}.north{position:absolute;right:18px;top:105px;font-size:11px;text-align:center;pointer-events:none}.north b{display:block;font-size:22px}.radio-note{font-size:12px;color:var(--muted)}details summary{cursor:pointer}#dictionary td{white-space:normal;max-width:450px}#dictionary td:first-child{font-family:Consolas,monospace}#warnings:empty{display:none}
@media(max-width:1050px){.map-layout{grid-template-columns:1fr}.side{max-height:420px}.kpis{grid-template-columns:repeat(3,1fr)}.two{grid-template-columns:1fr}#map{height:550px}}@media(max-width:650px){.wrap{padding:0 14px}.hero{padding:25px 0}h1{font-size:34px}.kpis{grid-template-columns:repeat(2,1fr)}.kpi{padding:12px}.kpi .value{font-size:24px}.card{padding:15px}.map-card{padding:0}#map{height:440px}.tabs button{padding:11px 12px}.map-controls{top:125px}.north{top:125px}}
@media print{.tabs,.filters,button,.map-controls,.tooltip{display:none!important}.panel[hidden]{display:block}.panel{break-inside:avoid}.hero{background:white;color:#183b40}.hero .subtitle,.eyebrow{color:#183b40}.card{box-shadow:none}body{background:white}.table-wrap{max-height:none}.wrap{padding:0}.chart{height:300px}}
"""


MARKUP = r"""
<header class="hero"><div class="wrap"><p class="eyebrow">Учебный атлас · Санкт-Петербург</p>
<h1>Невский район</h1><p class="subtitle">Кварталы, застройка, школы и автомобильная доступность. Сохранённые результаты исследования.</p>
<div class="pills"><span class="pill" id="version"></span><span class="pill" id="study-date"></span><span class="pill" id="buffer"></span><span class="pill">Работает без интернета</span></div></div></header>
<main class="wrap">
<section class="kpis" id="kpis" aria-label="Основные результаты"></section>
<div class="notice">Население и часть вместимости школ оценены по открытым данным. Автомобильная доступность — учебный сценарий, а не проверка нормативной пешей доступности.</div>
<div id="warnings"></div>
<nav class="tabs" aria-label="Разделы дашборда">
<button data-tab="map-panel" class="active" aria-pressed="true">Карта</button><button data-tab="indicators-panel" aria-pressed="false">Показатели</button><button data-tab="travel-panel" aria-pressed="false">Время в пути</button><button data-tab="tables-panel" aria-pressed="false">Таблицы</button><button data-tab="findings-panel" aria-pressed="false">Выводы и качество</button></nav>
<div class="row filters" id="global-filters"><label class="field grow">Поиск квартала или значения<input id="search" type="search" placeholder="Номер квартала, тип территории…"></label>
<label class="field">Землепользование<select id="land-filter"><option value="">Все категории</option></select></label>
<label class="field grow">Показатель карты и диаграммы<select id="metric"></select></label>
<label class="check"><input id="missing-only" type="checkbox">Только без значения</label><button id="clear-filters">Сбросить</button><span class="filter-count" id="filter-count" aria-live="polite"></span></div>
<section id="map-panel" class="panel"><div class="map-layout"><div class="card map-card">
<div class="map-head"><strong id="map-title"></strong><div id="legend" class="legend"></div></div>
<svg id="map" viewBox="0 0 1000 650" tabindex="0" role="img" aria-label="Интерактивная карта кварталов. Колесо для масштаба, перетаскивание для перемещения; выбор также доступен в таблице."><g id="map-scene"><g id="water-layer"></g><g id="blocks-layer"></g><g id="context-layer"></g><g id="links-layer"></g><g id="schools-layer"></g><g id="unassigned-layer"></g></g></svg>
<div class="map-controls"><button id="zoom-in" aria-label="Увеличить">+</button><button id="zoom-out" aria-label="Уменьшить">−</button><button id="map-reset" aria-label="Показать все кварталы">⌂</button></div><div class="north"><b>↑</b>СЕВЕР</div>
<div class="map-caption">Карта из локальных GeoJSON · © участники OpenStreetMap, ODbL · Без онлайн-подложки. Линии школьных связей условные, не маршруты.</div></div>
<aside class="card side"><h3>Слои карты</h3><label class="check"><input type="checkbox" data-layer="water" checked>Вода</label><label class="check"><input type="checkbox" data-layer="schools" checked>Школы</label><label class="check"><input type="checkbox" data-layer="links">Связи со школами</label><label class="check"><input type="checkbox" data-layer="context">Граница области графа</label><label class="check"><input type="checkbox" data-layer="unassigned">Непривязанные здания</label><hr style="border:0;border-top:1px solid var(--line);margin:20px 0"><h3 id="selected-title">Выберите квартал</h3><p class="muted" id="selected-help">Нажмите на квартал на карте или выберите строку таблицы. Маленькие кварталы удобнее найти через поиск.</p><div id="selected-details"></div></aside></div>
<div class="card"><div class="row between"><div><p class="eyebrow" style="color:var(--muted)">Сравнение кварталов</p><h2 id="chart-title"></h2></div><span class="tag">Текущий фильтр</span></div><p id="metric-description" class="muted"></p><svg id="rank-chart" class="chart" viewBox="0 0 1000 350" role="img" aria-label="Диаграмма выбранного показателя"></svg><p class="radio-note">Числовой показатель: до 20 наибольших сохранённых значений. Категории: число кварталов. Нажатие на столбец выбирает квартал или категорию.</p></div></section>
<section id="indicators-panel" class="panel" hidden><div class="card"><div class="row between"><div><h2>Все показатели кварталов</h2><p class="muted">Нажмите заголовок для сортировки, строку — для выбора квартала.</p></div><button id="export-blocks">Скачать текущую таблицу CSV</button></div><div class="row table-tools"><label class="field">Столбцы<select id="column-mode"><option value="main">Основные</option><option value="all">Все сохранённые поля</option><option value="metric">Выбранный показатель</option></select></label><button id="show-selected">Выбранный квартал на карте</button></div><div id="blocks-table"></div></div><div class="card"><h2>Как читать показатели</h2><p class="muted">Словарь всех полей, найденных в готовых слоях.</p><div class="table-wrap" id="dictionary"></div></div></section>
<section id="travel-panel" class="panel" hidden><div class="card"><h2>Время между кварталами</h2><p class="muted">Готовая матрица из ноутбука. Движение по автомобильной сети, в минутах; без пробок, парковки и времени подъезда к сети.</p><div class="row"><label class="field grow">Откуда<select id="travel-from"></select></label><label class="field grow">Куда<select id="travel-to"></select></label><button id="swap-travel">Поменять местами</button></div><div class="pair-value" id="pair-time" aria-live="polite">Выберите пару кварталов</div><p id="pair-note" class="muted"></p><p id="matrix-status" class="radio-note"></p></div><div class="card"><h3>Все направления из выбранного квартала</h3><label class="field" style="max-width:320px">Найти квартал назначения<input type="search" id="travel-search" placeholder="Номер квартала"></label><div id="travel-table" style="margin-top:15px"></div></div></section>
<section id="tables-panel" class="panel" hidden><div class="card"><h2>Сохранённые таблицы исследования</h2><div class="row table-tools"><label class="field grow">Таблица<select id="dataset"></select></label><label class="field grow">Поиск по таблице<input id="dataset-search" type="search" placeholder="Найти строку…"></label></div><p id="dataset-note" class="muted"></p><div id="dataset-table"></div></div><div class="card" id="saved-chart-card"><h2>Графики из ноутбука</h2><p class="muted">Сохранённое изображение, без повторного построения исследовательских показателей.</p><img id="saved-chart" class="saved-chart" alt="Сохранённые графики FSI, обеспеченности школами, землепользования и типов Strelka"></div></section>
<section id="findings-panel" class="panel" hidden><div class="two"><div class="card"><div class="row between"><h2>Выводы исследования</h2><span id="findings-status" class="tag"></span></div><div id="findings" class="prose"></div></div><div class="card"><h2>Качество исходных данных</h2><p class="muted">Значения из сохранённой контрольной таблицы. Они не означают автоматического подтверждения всех результатов.</p><div id="quality"></div></div></div><div class="card"><h2>Границы интерпретации</h2><ul class="small-prose"><li>Расчётное население зависит от этажности и принятой жилой площади на человека.</li><li>Оценённая вместимость школ может существенно отличаться от фактической. Школы и спрос соседних районов не учтены.</li><li>Типы застройки описывают различия кварталов и не являются рейтингом качества жизни.</li><li>Готовая автомобильная сеть и её буфер могут ограничивать доступные маршруты. Текущая дорожная ситуация не учитывается.</li><li>Пустое значение не заменено нулём. Для доступности отдельно показан признак недостижимости.</li></ul><p id="accounting-note" class="small-prose"></p></div><div class="card"><h2>Источники и параметры запуска</h2><div id="run-details" class="sources"></div><details style="margin-top:16px"><summary>Файлы, включённые в эту страницу</summary><div id="source-files" class="sources"></div></details></div></section>
<noscript><div class="notice">Для интерактивной карты и таблиц включите JavaScript в браузере. Интернет не требуется.</div></noscript>
<footer class="footer">Учебное исследование Невского района · Все сведения встроены в этот HTML. Страница не обращается к сети и не выполняет расчёты BlocksNet. <span id="built-date"></span></footer>
</main><div id="tooltip" class="tooltip" role="status"></div>
"""


JS = r"""
'use strict';
const D=JSON.parse(document.getElementById('dashboard-data').textContent);
const $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const sv=(tag,attrs={},text)=>{const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n;};
const numeric=v=>typeof v==='number'&&Number.isFinite(v);
const num=(v,d=2)=>v===null||v===undefined||v===''?'—':Number.isFinite(Number(v))?new Intl.NumberFormat('ru-RU',{maximumFractionDigits:d}).format(Number(v)):String(v);
const meta=k=>D.fields[k]||{title:k,unit:'',description:'',group:'Прочее'};
const fmt=(v,k)=>v===null||v===undefined||v===''?'Нет данных':typeof v==='boolean'?(v?'Да':'Нет'):k==='land_use'?(D.landUse[v]||v):typeof v==='number'?meta(k).unit==='доля'?num(v*100,1)+' %':num(v,meta(k).unit==='шт.'||meta(k).unit==='мест'||meta(k).unit==='чел.'?0:3):String(v);
const date=s=>{const d=new Date(s);return Number.isNaN(d.valueOf())?'не указана':d.toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})+' МСК';};
const rows=D.blocks.features.map(f=>({...f.properties,_id:String(f.properties.block_id),_feature:f}));
const byId=new Map(rows.map(r=>[r._id,r]));
const fieldKeys=Object.keys(D.fields), primary=['block_id','population','fsi','gsi','l','mxi','school_count','capacity','demand_left','provision_strong','reachable_share'];
const state={metric:fieldKeys.includes('fsi')?'fsi':fieldKeys[0],search:'',land:'',missing:false,selected:null,page:0,sort:'block_id',direction:1,mode:'main'};
const catFields=new Set(['land_use','spacematrix','strelka','cluster','node_id','entity_id','block_id']);
const palette=['#267d80','#c57752','#6d84a6','#82985c','#a77899','#d1a049','#497c9f','#a3a360','#588e76','#b6816c','#7c91a0'];
function filtered(){return rows.filter(r=>(!state.search||fieldKeys.some(k=>String(r[k]??'').toLocaleLowerCase('ru').includes(state.search)))&&(!state.land||(state.land==='__null__'?r.land_use==null:r.land_use===state.land))&&(!state.missing||r[state.metric]===null||r[state.metric]===undefined));}
function fillSelect(node,values){node.replaceChildren();for(const[value,label]of values){const op=el('option',label);op.value=value;node.append(op);}}
function switchTab(id){document.querySelectorAll('.panel').forEach(p=>p.hidden=p.id!==id);document.querySelectorAll('[data-tab]').forEach(b=>{const on=b.dataset.tab===id;b.classList.toggle('active',on);b.setAttribute('aria-pressed',on);});$('global-filters').hidden=!['map-panel','indicators-panel'].includes(id);if(id==='travel-panel')showTravel();}
document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>switchTab(b.dataset.tab)));
function showTip(event,lines){const tip=$('tooltip');tip.replaceChildren(...lines.map(t=>el('div',t)));tip.style.display='block';tip.style.left=Math.min(event.clientX+14,innerWidth-310)+'px';tip.style.top=Math.min(event.clientY+12,innerHeight-130)+'px';}
function hideTip(){$('tooltip').style.display='none';}
function showSummary(){const s=D.summary;const items=[['blocks','Кварталы','аналитические участки'],['population_estimate','Население','расчётная оценка'],['school_count','Школы','учтённые объекты'],['school_capacity_estimate','Школьные места','известные и оценённые'],['provision_strong_total','Обеспеченность школами','автомобильный сценарий']];for(const[key,label,note]of items){const card=el('div',undefined,'kpi');const val=s[key];card.append(el('div',val==null?'—':key==='provision_strong_total'?num(val*100,1)+' %':num(val,0),'value'),el('div',label,'label'),el('div',note,'note'));$('kpis').append(card);}$('version').textContent='BlocksNet '+(D.run.versions?.blocksnet||'версия не указана');$('study-date').textContent='Расчёт: '+date(D.run.utc);$('buffer').textContent='Буфер графа: '+num(D.run.graph_selection?.buffer_m,0)+' м';$('built-date').textContent='Страница собрана: '+date(D.builtUtc);for(const warning of D.warnings)$('warnings').append(el('div',warning,'notice'));}
showSummary();

// The following projection is for drawing only, not routing or research metrics.
function mercator([lon,lat]){const y=Math.max(-85,Math.min(85,lat))*Math.PI/180;return[lon*Math.PI/180,-Math.log(Math.tan(Math.PI/4+y/2))];}
function eachPoint(g,fn){if(!g)return;if(g.type==='GeometryCollection'){g.geometries.forEach(x=>eachPoint(x,fn));return;}function visit(c){if(typeof c[0]==='number')fn(c);else c.forEach(visit);}visit(g.coordinates);}
let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
for(const r of rows)eachPoint(r._feature.geometry,c=>{const[x,y]=mercator(c);minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y);});
const geoWidth=Math.max(maxX-minX,1e-9),geoHeight=Math.max(maxY-minY,1e-9),scale=Math.min(920/geoWidth,570/geoHeight);
const offsetX=(1000-geoWidth*scale)/2,offsetY=(650-geoHeight*scale)/2;
function xy(c){const[x,y]=mercator(c);return[(x-minX)*scale+offsetX,(y-minY)*scale+offsetY];}
function pathLine(coords,closed=false){return coords.map((c,i)=>{const[x,y]=xy(c);return(i?'L':'M')+x.toFixed(3)+','+y.toFixed(3);}).join('')+(closed?'Z':'');}
function pathGeometry(g){if(!g)return'';switch(g.type){case'Polygon':return g.coordinates.map(c=>pathLine(c,true)).join('');case'MultiPolygon':return g.coordinates.flatMap(p=>p.map(c=>pathLine(c,true))).join('');case'LineString':return pathLine(g.coordinates);case'MultiLineString':return g.coordinates.map(c=>pathLine(c)).join('');case'GeometryCollection':return g.geometries.map(pathGeometry).join('');default:return'';}}
const paths=new Map();
for(const r of rows){const p=sv('path',{d:pathGeometry(r._feature.geometry),'fill-rule':'evenodd',class:'block','data-id':r._id});p.addEventListener('pointermove',e=>showTip(e,['Квартал '+r._id,meta(state.metric).title+': '+fmt(r[state.metric],state.metric)]));p.addEventListener('pointerleave',hideTip);p.addEventListener('click',()=>{if(!dragMoved)selectBlock(r._id);});$('blocks-layer').append(p);paths.set(r._id,p);}
function drawExtra(name,color,stroke,fill=true){const layer=$(name+'-layer');const data=D.extras[name];if(!data)return;for(const f of data.features){const g=f.geometry;if(!g)continue;let node;if(g.type==='Point'){const[x,y]=xy(g.coordinates);node=sv('circle',{cx:x,cy:y,r:name==='schools'?3.5:5,fill:color,stroke:'#fff','stroke-width':1,'vector-effect':'non-scaling-stroke'});}else node=sv('path',{d:pathGeometry(g),fill:fill?color:'none',stroke:stroke||color,'stroke-width':name==='links'?1.5:.8,'vector-effect':'non-scaling-stroke','fill-rule':'evenodd'});if(name==='context'||name==='links')node.setAttribute('stroke-dasharray',name==='context'?'8 5':'4 4');if(name==='schools'){const props=f.properties||{};node.addEventListener('pointermove',e=>showTip(e,[props.name||'Школа','Вместимость: '+num(props.capacity,0)+' мест',props.capacity_imputed?'Вместимость оценена':'Значение вместимости из исходных данных']));node.addEventListener('pointerleave',hideTip);}layer.append(node);}}
drawExtra('water','#c8e1e8','#b5d4df');drawExtra('context','#9e6e43',null,false);drawExtra('links','#596484',null,false);drawExtra('schools','#886595','#674f70');drawExtra('unassigned','#c35f43',null,false);
document.querySelectorAll('[data-layer]').forEach(c=>{const name=c.dataset.layer;if(!D.extras[name]){c.disabled=true;c.parentNode.title='Соответствующий GeoJSON не найден';}$(name+'-layer').style.display=c.checked?'':'none';c.addEventListener('change',()=>$(name+'-layer').style.display=c.checked?'':'none');});
const view={x:0,y:0,w:1000,h:650};function applyView(){$('map').setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`);}
function zoom(factor,x=view.x+view.w/2,y=view.y+view.h/2){const nw=Math.max(12,Math.min(7000,view.w*factor)),nh=nw*.65,fx=(x-view.x)/view.w,fy=(y-view.y)/view.h;view.x=x-fx*nw;view.y=y-fy*nh;view.w=nw;view.h=nh;applyView();}
function localPoint(e){return new DOMPoint(e.clientX,e.clientY).matrixTransform($('map').getScreenCTM().inverse());}
let dragging=null,dragMoved=false;
$('map').addEventListener('wheel',e=>{e.preventDefault();const p=localPoint(e);zoom(e.deltaY>0?1.18:1/1.18,p.x,p.y);},{passive:false});
$('map').addEventListener('pointerdown',e=>{if(e.button!==0)return;dragMoved=false;dragging={id:e.pointerId,x:e.clientX,y:e.clientY,vx:view.x,vy:view.y};$('map').classList.add('dragging');});
window.addEventListener('pointermove',e=>{if(!dragging||e.pointerId!==dragging.id)return;const bounds=$('map').getBoundingClientRect();const factor=Math.min(bounds.width/view.w,bounds.height/view.h);const dx=(e.clientX-dragging.x)/factor,dy=(e.clientY-dragging.y)/factor;if(Math.abs(e.clientX-dragging.x)+Math.abs(e.clientY-dragging.y)>5)dragMoved=true;view.x=dragging.vx-dx;view.y=dragging.vy-dy;applyView();if(dragMoved)hideTip();});
window.addEventListener('pointerup',()=>{dragging=null;$('map').classList.remove('dragging');});
$('zoom-in').onclick=()=>zoom(.7);$('zoom-out').onclick=()=>zoom(1/.7);$('map-reset').onclick=()=>{Object.assign(view,{x:0,y:0,w:1000,h:650});applyView();};
$('map').addEventListener('keydown',e=>{if(e.key==='+'||e.key==='=')zoom(.7);else if(e.key==='-')zoom(1/.7);else if(e.key==='0')$('map-reset').click();else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();view.x+=e.key==='ArrowLeft'?-view.w*.1:e.key==='ArrowRight'?view.w*.1:0;view.y+=e.key==='ArrowUp'?-view.h*.1:e.key==='ArrowDown'?view.h*.1:0;applyView();}});
function focusBlock(id){const r=byId.get(id);if(!r)return;let a=Infinity,b=Infinity,c=-Infinity,d=-Infinity;eachPoint(r._feature.geometry,p=>{const[x,y]=xy(p);a=Math.min(a,x);c=Math.max(c,x);b=Math.min(b,y);d=Math.max(d,y);});view.w=Math.max(25,(c-a)*2,(d-b)*2/.65);view.h=view.w*.65;view.x=(a+c-view.w)/2;view.y=(b+d-view.h)/2;applyView();}
function selectBlock(id,focus=false){state.selected=id;for(const[key,path]of paths)path.classList.toggle('selected',key===id);const r=byId.get(id);if(!r)return;$('selected-title').textContent='Квартал '+id;$('selected-help').textContent='Сохранённые значения всех показателей.';const detail=$('selected-details');detail.replaceChildren();const groups=new Map();for(const key of fieldKeys){const group=meta(key).group;if(!groups.has(group))groups.set(group,[]);groups.get(group).push(key);}for(const[group,keys]of groups){detail.append(el('div',group,'section-label'));const dl=el('dl',undefined,'details-grid');for(const key of keys){const item=el('div');const dt=el('dt',meta(key).title);dt.title=meta(key).description+' ['+key+']';let value=fmt(r[key],key);if(key==='area_accessibility'&&r[key]==null&&r.area_accessibility_unreachable)value='Не определено: есть недостижимые направления';item.append(dt,el('dd',value));dl.append(item);}detail.append(dl);}if(focus)focusBlock(id);renderBlocksTable();if(D.matrix)$('travel-from').value=id;}
function currentScale(){const categorical=catFields.has(state.metric)||rows.some(r=>r[state.metric]!=null&&typeof r[state.metric]!=='number');if(categorical){const cats=[...new Set(rows.map(r=>r[state.metric]).filter(v=>v!=null))].sort((a,b)=>String(a).localeCompare(String(b),'ru'));const colors=new Map(cats.map((v,i)=>[v,palette[i%palette.length]]));return{categorical,cats,color:v=>v==null?'#cbd2cc':colors.get(v)||'#cbd2cc'};}const vals=rows.map(r=>r[state.metric]).filter(numeric);const lo=vals.length?Math.min(...vals):0,hi=vals.length?Math.max(...vals):0;return{categorical:false,lo,hi,has:vals.length>0,color:v=>{if(!numeric(v))return'#cbd2cc';const t=hi===lo?.5:(v-lo)/(hi-lo);const a=t<.5?[220,232,223]:[40,124,130],b=t<.5?[40,124,130]:[190,95,65],u=t<.5?t*2:(t-.5)*2;return`rgb(${a.map((x,i)=>Math.round(x+(b[i]-x)*u)).join(',')})`;}};}
function refresh(){const visible=filtered(),ids=new Set(visible.map(r=>r._id)),scale=currentScale();$('filter-count').textContent=`Показано ${num(visible.length,0)} из ${num(rows.length,0)}`;$('map-title').textContent=meta(state.metric).title;$('metric-description').textContent=meta(state.metric).description;$('chart-title').textContent=meta(state.metric).title;for(const r of rows){const path=paths.get(r._id);path.setAttribute('fill',scale.color(r[state.metric]));path.classList.toggle('dim',!ids.has(r._id));}const legend=$('legend');legend.replaceChildren();if(scale.categorical){for(const cat of scale.cats.slice(0,18)){const label=el('span',undefined,'legend-item'),swatch=el('i',undefined,'swatch');swatch.style.background=scale.color(cat);label.append(swatch,document.createTextNode(fmt(cat,state.metric)));legend.append(label);}if(scale.cats.length>18)legend.append(el('span','Все категории доступны в таблице.'));}else if(scale.has){legend.append(el('span',fmt(scale.lo,state.metric)),el('span',undefined,'ramp'),el('span',fmt(scale.hi,state.metric)));}else legend.append(el('span','Нет конечных значений. Для доступности проверьте долю достижимых кварталов.'));const missing=el('span');const swatch=el('i',undefined,'swatch');swatch.style.background='#cbd2cc';missing.append(swatch,document.createTextNode('Нет данных / недостижимость'));legend.append(missing);renderRank(visible,scale.categorical);renderBlocksTable();}
function renderRank(visible,categorical){const svg=$('rank-chart');svg.replaceChildren();let bars;if(categorical){const groups=new Map();for(const r of visible){const key=r[state.metric]??'Нет данных';groups.set(key,(groups.get(key)||0)+1);}bars=[...groups].sort((a,b)=>b[1]-a[1]).slice(0,15).map(([key,value])=>({label:fmt(key,state.metric),value,category:key}));}else bars=visible.filter(r=>numeric(r[state.metric])).sort((a,b)=>b[state.metric]-a[state.metric]).slice(0,20).map(r=>({label:r._id,value:r[state.metric],id:r._id}));if(!bars.length){svg.append(sv('text',{x:500,y:170,'text-anchor':'middle'},'Нет значений для выбранного фильтра'));return;}const max=Math.max(...bars.map(b=>b.value),1e-9),left=55,bottom=275,width=920,barWidth=width/bars.length;for(let i=0;i<=4;i++){const y=bottom-i*55;svg.append(sv('line',{x1:left,y1:y,x2:975,y2:y,stroke:'#dce2da'}),sv('text',{x:48,y:y+4,'text-anchor':'end'},num(max*i/4,1)));}bars.forEach((bar,i)=>{const height=Math.max(0,bar.value/max*220),x=left+i*barWidth+3;const rect=sv('rect',{x,y:bottom-height,width:Math.max(2,barWidth-6),height:Math.max(1,height),rx:3,class:'bar',tabindex:0,role:'button','aria-label':bar.label+': '+num(bar.value)});const choose=()=>{if(bar.id){selectBlock(bar.id,true);switchTab('map-panel');}else if(state.metric==='land_use'){state.land=bar.category==='Нет данных'?'__null__':String(bar.category);$('land-filter').value=state.land;state.page=0;refresh();}};rect.addEventListener('click',choose);rect.addEventListener('keydown',e=>{if(e.key==='Enter')choose();});rect.addEventListener('pointermove',e=>showTip(e,[bar.id?'Квартал '+bar.id:bar.label,categorical?'Кварталов: '+num(bar.value,0):fmt(bar.value,state.metric)]));rect.addEventListener('pointerleave',hideTip);svg.append(rect);const label=bar.label.length>19?bar.label.slice(0,17)+'…':bar.label;svg.append(sv('text',{x:x+(barWidth-6)/2,y:294,'text-anchor':categorical?'end':'middle',transform:categorical?`rotate(-25 ${x+(barWidth-6)/2} 294)`:''},label));});}
function cellText(value,key){if(value===null||value===undefined||value==='')return'—';if(typeof value==='number'||typeof value==='boolean')return fmt(value,key);return String(value);}
function compare(a,b){if(a==null||a==='')return b==null||b===''?0:1;if(b==null||b==='')return-1;const na=Number(a),nb=Number(b);return Number.isFinite(na)&&Number.isFinite(nb)?na-nb:String(a).localeCompare(String(b),'ru');}
function table(container,columns,data,options={}){container.replaceChildren();const per=50,total=data.length,pages=Math.max(1,Math.ceil(total/per)),page=Math.min(options.page||0,pages-1),section=el('div',undefined,'table-wrap');const t=el('table'),thead=el('thead'),head=el('tr');for(const key of columns){const th=el('th');if(options.sort){const b=el('button',(options.raw?key:meta(key).title)+(options.sortKey===key?(options.direction===1?' ↑':' ↓'):''));b.title=options.raw?key:meta(key).description+' ['+key+']';b.onclick=()=>options.sort(key);th.append(b);}else th.textContent=options.raw?key:meta(key).title;head.append(th);}thead.append(head);t.append(thead);const body=el('tbody');for(const row of data.slice(page*per,(page+1)*per)){const tr=el('tr');if(options.choose){tr.className='selectable'+(row._id===state.selected?' selected-row':'');tr.tabIndex=0;tr.addEventListener('click',()=>options.choose(row));tr.addEventListener('keydown',e=>{if(e.key==='Enter')options.choose(row);});}for(const key of columns){const text=cellText(row[key],options.raw?'':key),td=el('td',text);if(text==='—')td.classList.add('empty');if(text.length>60)td.classList.add('wrap-cell');tr.append(td);}body.append(tr);}t.append(body);section.append(t);container.append(section);if(!data.length)container.append(el('div','Нет строк для выбранного фильтра.','empty-state'));const pager=el('div',undefined,'pagination'),buttons=el('div',undefined,'row'),prev=el('button','← Назад'),next=el('button','Далее →');prev.disabled=page===0;next.disabled=page>=pages-1;prev.onclick=()=>options.onPage?.(page-1);next.onclick=()=>options.onPage?.(page+1);buttons.append(prev,next);pager.append(el('span',`${total? page*per+1:0}–${Math.min((page+1)*per,total)} из ${num(total,0)} · страница ${page+1}/${pages}`),buttons);container.append(pager);}
function visibleColumns(){return state.mode==='all'?fieldKeys:state.mode==='metric'?[...new Set(['block_id',state.metric])]:primary.filter(k=>fieldKeys.includes(k));}
function renderBlocksTable(){const data=filtered().slice().sort((a,b)=>state.direction*compare(a[state.sort],b[state.sort]));table($('blocks-table'),visibleColumns(),data,{page:state.page,sortKey:state.sort,direction:state.direction,sort:key=>{state.direction=state.sort===key?-state.direction:1;state.sort=key;state.page=0;renderBlocksTable();},onPage:p=>{state.page=p;renderBlocksTable();},choose:r=>selectBlock(r._id)});}
fillSelect($('metric'),fieldKeys.filter(k=>k!=='entity_id'&&k!=='node_id').map(k=>[k,meta(k).title+(meta(k).unit?' · '+meta(k).unit:'')]));$('metric').value=state.metric;
fillSelect($('land-filter'),[['','Все категории'],...[...new Set(rows.map(r=>r.land_use).filter(Boolean))].sort().map(v=>[v,D.landUse[v]||v]),['__null__','Нет данных']]);
$('metric').onchange=e=>{state.metric=e.target.value;state.page=0;refresh();};$('search').oninput=e=>{state.search=e.target.value.toLocaleLowerCase('ru').trim();state.page=0;refresh();};$('land-filter').onchange=e=>{state.land=e.target.value;state.page=0;refresh();};$('missing-only').onchange=e=>{state.missing=e.target.checked;state.page=0;refresh();};$('clear-filters').onclick=()=>{state.search='';state.land='';state.missing=false;state.page=0;$('search').value='';$('land-filter').value='';$('missing-only').checked=false;refresh();};$('column-mode').onchange=e=>{state.mode=e.target.value;state.page=0;renderBlocksTable();};$('show-selected').onclick=()=>{if(state.selected){switchTab('map-panel');focusBlock(state.selected);}else alert('Сначала выберите строку квартала.');};
function csvEscape(v){return'"'+String(v??'').replaceAll('"','""')+'"';}
$('export-blocks').onclick=()=>{const cols=visibleColumns(),lines=[cols.map(csvEscape).join(';'),...filtered().map(r=>cols.map(k=>csvEscape(r[k])).join(';'))];const url=URL.createObjectURL(new Blob(['\uFEFF'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'}));const a=el('a');a.href=url;a.download='nevsky_dashboard_selection.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
const dict=el('table'),dh=el('tr');for(const h of ['Поле','Название','Единица','Пояснение'])dh.append(el('th',h));const dhead=el('thead');dhead.append(dh);dict.append(dhead);const db=el('tbody');for(const key of fieldKeys){const row=el('tr');for(const text of [key,meta(key).title,meta(key).unit||'—',meta(key).description])row.append(el('td',text));db.append(row);}dict.append(db);$('dictionary').append(dict);

let dataPage=0,dataSort='',dataDirection=1;
fillSelect($('dataset'),D.tables.map((t,i)=>[String(i),t.title]));
function renderDataset(){const data=D.tables[Number($('dataset').value)||0];if(!data){$('dataset-note').textContent='Дополнительные таблицы не найдены.';return;}$('dataset-note').textContent=data.note+' Файл: '+data.file;const q=$('dataset-search').value.toLocaleLowerCase('ru').trim();let selected=data.rows.filter(r=>!q||Object.values(r).some(v=>String(v??'').toLocaleLowerCase('ru').includes(q)));if(dataSort)selected=selected.slice().sort((a,b)=>dataDirection*compare(a[dataSort],b[dataSort]));table($('dataset-table'),data.columns,selected,{raw:true,page:dataPage,sortKey:dataSort,direction:dataDirection,sort:key=>{dataDirection=key===dataSort?-dataDirection:1;dataSort=key;dataPage=0;renderDataset();},onPage:p=>{dataPage=p;renderDataset();}});}
$('dataset').onchange=()=>{dataPage=0;dataSort='';$('dataset-search').value='';renderDataset();};$('dataset-search').oninput=()=>{dataPage=0;renderDataset();};renderDataset();
if(D.chart)$('saved-chart').src='data:image/png;base64,'+D.chart;else $('saved-chart-card').hidden=true;

let matrixPromise=null,matrixValues=null,travelPage=0,travelSort='Квартал назначения',travelDirection=1;
const sourcePositions=new Map((D.matrix?.rows||[]).map((id,i)=>[id,i])),targetPositions=new Map((D.matrix?.columns||[]).map((id,i)=>[id,i]));
if(D.matrix){const opts=rows.map(r=>[r._id,'Квартал '+r._id]);fillSelect($('travel-from'),opts);fillSelect($('travel-to'),opts);if(opts.length>1)$('travel-to').value=opts[1][0];}
async function unpackMatrix(){if(matrixValues)return matrixValues;if(!matrixPromise)matrixPromise=(async()=>{if(!('DecompressionStream'in window))throw Error('Для встроенной матрицы откройте страницу в современном Microsoft Edge или Chrome.');$('matrix-status').textContent='Читаем встроенную матрицу. Обращений к серверу и расчёта маршрутов нет.';const binary=atob(D.matrix.data),packed=Uint8Array.from(binary,c=>c.charCodeAt(0));const stream=new Blob([packed]).stream().pipeThrough(new DecompressionStream('deflate'));const buffer=await new Response(stream).arrayBuffer();if(buffer.byteLength!==D.matrix.rows.length*D.matrix.columns.length*8)throw Error('Размер встроенной матрицы не совпадает с её описанием.');matrixValues=new Float64Array(buffer);return matrixValues;})();return matrixPromise;}
function travelValue(from,to){return matrixValues[sourcePositions.get(from)*D.matrix.columns.length+targetPositions.get(to)];}
async function showTravel(){if(!D.matrix){$('pair-time').textContent='Матрица не включена';$('matrix-status').textContent='Не найден сохранённый CSV. Новые маршруты эта страница не строит.';return;}try{await unpackMatrix();const from=$('travel-from').value,to=$('travel-to').value,value=travelValue(from,to);$('pair-time').textContent=Number.isNaN(value)?'Нет значения':Number.isFinite(value)?num(value,3)+' мин':'Нет маршрута';$('pair-note').textContent=from===to?'Ноль на диагонали задан по определению модели.':value===0?'Ноль может означать привязку разных кварталов к одному входу сети.':'Направление: квартал '+from+' → квартал '+to+'. В обратную сторону время может отличаться.';$('matrix-status').textContent='Показаны сохранённые времена; недостижимость сохранена отдельно от нулей.';renderTravelTable();}catch(error){$('pair-time').textContent='Матрица недоступна';$('matrix-status').textContent=error.message;}}
function renderTravelTable(){if(!matrixValues)return;const from=$('travel-from').value,q=$('travel-search').value.trim();let data=D.matrix.columns.filter(id=>!q||id.includes(q)).map(id=>{const value=travelValue(from,id);return{_id:id,'Квартал назначения':id,'Время, мин':Number.isFinite(value)?value:Number.isNaN(value)?null:'Нет маршрута'};});data.sort((a,b)=>travelDirection*compare(a[travelSort],b[travelSort]));table($('travel-table'),['Квартал назначения','Время, мин'],data,{raw:true,page:travelPage,sortKey:travelSort,direction:travelDirection,sort:key=>{travelDirection=travelSort===key?-travelDirection:1;travelSort=key;travelPage=0;renderTravelTable();},onPage:p=>{travelPage=p;renderTravelTable();},choose:r=>{$('travel-to').value=r._id;showTravel();}});}
$('travel-from').onchange=()=>{travelPage=0;showTravel();};$('travel-to').onchange=showTravel;$('swap-travel').onclick=()=>{const old=$('travel-from').value;$('travel-from').value=$('travel-to').value;$('travel-to').value=old;travelPage=0;showTravel();};$('travel-search').oninput=()=>{travelPage=0;renderTravelTable();};
$('findings').textContent=D.findings;$('findings-status').textContent=D.reviewed?'Проверенный текст':'Сохранённый черновик';
const quality=D.tables.find(t=>t.file==='quality_checks.csv');if(quality){for(const row of quality.rows){const item=el('div',undefined,'quality-row'),left=el('div');left.append(el('div',row['Проверка']),el('small',row['Как читать']));const value=Number(row['Значение']);const shown=row['Проверка'].includes('доля')?num(value*100,1)+' %':num(value,3);item.append(left,el('strong',shown));$('quality').append(item);}}else $('quality').textContent='Контрольная таблица не сохранена.';
$('accounting-note').textContent=D.corrected?'Для школьных показателей использован отдельный исправленный GeoJSON. Поправка учёта BlocksNet здесь не выполняется.':'Отдельный исправленный слой школьного учёта не найден; используется итоговый слой.';
const runItems=[['Территория',D.run.place],['Версия BlocksNet',D.run.versions?.blocksnet],['Время сохранённого запуска',date(D.run.utc)],['Транспорт','Легковой автомобиль, модель без пробок'],['Источник готового графа',D.run.graph?.page],['Дата сети',D.run.graph?.network_date],['Буфер графа, м',D.run.graph_selection?.buffer_m],['Потребность в местах / 1000 жителей',D.summary.school_demand_per_1000],['Автомобильный порог, мин',D.summary.school_threshold_car_minutes],['Жилая площадь на человека, м²',D.run.parameters?.m2_per_person],['Этажность по умолчанию',D.run.parameters?.floors],['Параметры исследования',JSON.stringify(D.run.parameters||{})]];
for(const[label,value]of runItems){const p=el('p');p.append(el('strong',label+': '),document.createTextNode(value==null?'не указано':String(value)));$('run-details').append(p);}const sourceList=el('ul');for(const source of D.sources)sourceList.append(el('li',source.file+' · '+num(source.bytes/1024,1)+' КБ · '+date(source.modified_utc)));$('source-files').append(sourceList);
refresh();
"""


def render_html(payload):
    return ("<!doctype html>\n<html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
            "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; "
            "connect-src 'none'; base-uri 'none'; form-action 'none'\">"
            "<title>Невский район · Учебный дашборд</title>"
            "<style>" + CSS + "</style></head><body>" + MARKUP +
            '<script id="dashboard-data" type="application/json">' + inline_json(payload) +
            "</script><script>" + JS + "</script></body></html>\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Собрать автономный dashboard.html из готовых результатов исследования.")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "examples" / "data" / "nevsky",
                        help="Папка с results, prepared и сохранёнными исходными GeoJSON.")
    parser.add_argument("--output", type=Path, default=ROOT / "dashboard.html", help="Путь итогового HTML.")
    args = parser.parse_args(argv)
    data_dir, output = args.data_dir.resolve(), args.output.resolve()
    if output.suffix.lower() != ".html":
        parser.error("Выходной файл должен иметь расширение .html.")
    try:
        payload = load_inputs(data_dir)
        document = render_html(payload)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".html.tmp")
        try:
            temporary.write_text(document, encoding="utf-8")
            temporary.replace(output)
        finally:
            if temporary.exists():
                temporary.unlink()
    except (OSError, ValueError, csv.Error) as exc:
        print(f"Не удалось собрать страницу: {exc}", file=sys.stderr)
        return 1
    print(f"Готово: {output}")
    print("Откройте этот файл двойным щелчком в Microsoft Edge или Chrome. Интернет не требуется.")
    print("Исследовательские показатели прочитаны из файлов. Загрузки и расчёты BlocksNet не выполнялись.")
    for warning in payload["warnings"]:
        print("Примечание:", warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
