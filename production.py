"""Specification tree and transparent production estimates."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from math import ceil, isfinite
from uuid import uuid4


def uid():
    return uuid4().hex


def numeric(value, title="Значение", *, positive=False, integer=False):
    try:
        result = float(str(value).replace(" ", "").replace(",", "."))
    except (ValueError, TypeError):
        raise ValueError(f"{title}: введите число") from None
    if not isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError(f"{title}: требуется {'положительное' if positive else 'неотрицательное'} конечное число")
    if integer and not result.is_integer():
        raise ValueError(f"{title}: требуется целое число")
    return int(result) if integer else result


COMMON_FIELDS = [
    ("rate", "Стоимость часа работы, ₽", 0),
    ("fixed_cost", "Дополнительная стоимость этапа, ₽", 0),
    ("otk_minutes", "Время ОТК на весь этап, мин", 0),
]
FIELDS = {
    "filing": [("per_part", "Плановое время напилки одной детали, мин", 0),
               ("setup", "Подготовительное время на этап, мин", 0)],
    "bending": [("per_bend", "Плановое время на один гиб, мин", 1),
                ("setup", "Подготовительное время на этап гибки, мин", 0)],
    "laser": [("sheet_change", "Время на смену одного листа, мин", 2),
              ("transport_pct", "Процент для расчёта времени отвоза изделий", 30),
              ("head_3030", "Время на смену головы Trumpf3030, мин", 0),
              ("head_2530", "Время на смену головы Trumpf2530, мин", 0)],
    "painting": [("prep_per_m2", "Плановое время подготовки изделия к покраске, мин/м²", 2),
                 ("volume_coef", "Коэффициент объёмного изделия", 2),
                 ("transport_pct", "Процент для расчёта времени отвоза изделий", 100),
                 ("paint_per_m2", "Плановый расход краски на 1 м², кг", .180),
                 ("primer_per_m2", "Плановый расход грунтовки на 1 м², кг", 0),
                 ("oven_area", "Плановая площадь печи, м²", 30),
                 ("cycle_minutes", "Плановое время одного цикла печи, мин", 0),
                 ("paint_price", "Цена краски, ₽/кг", 0),
                 ("primer_price", "Цена грунтовки, ₽/кг", 0)],
    "assembly": [("minutes", "Общее время на этап сборки, мин", 0)],
    "packing": [("minutes", "Общее время на этап упаковки, мин", 0)],
    "cooperation": [("minutes", "Общее время на этап, мин", 0)],
    "additional": [("minutes", "Общее время на этап, мин", 0)],
    "auxiliary": [("minutes", "Общее время на этап, мин", 0)],
    "welding": [],
}


def default_stages():
    main = [("Напилка", "-- Сборка /напилка/", "filing", True),
            ("Резка", "-- Лазер", "laser", True),
            ("Сварка", "-- Сварка /…/", "welding", False),
            ("Гибка", "-- Гибка", "bending", True),
            ("Сборка1", "-- Сборка /…/", "assembly", False),
            ("Сварка1", "-- Сварка /…/", "welding", True),
            ("Сборка2", "-- Сборка /…/", "assembly", True),
            ("Покрытие", "-- Покраска", "painting", True)]
    stages = []

    def add(name, title, kind, enabled=False, description=""):
        stages.append(dict(id=f"stage_{len(stages)+1}", name=name, title=title, kind=kind,
                           enabled=enabled, otk=False, description=description,
                           settings={k: v for k, _, v in FIELDS[kind] + COMMON_FIELDS}))

    for index, (name, title, kind, enabled) in enumerate(main, 1):
        add(name, title, kind, enabled)
        add(f"Кооперация_{index}", "Кооперация", "cooperation", index == 1,
            "Закупка" if index == 1 else "")
        add(f"Дополнительный_{index}", "Дополнительный", "additional")
    add("Сборка", "-- Сборка (окончательная)", "assembly", True)
    add("Упаковка", "-- Упаковка", "packing", True)
    for index in range(1, 9):
        add(f"Вспомогательный_{index}", "Вспомогательный", "auxiliary", index <= 3,
            {1: "Изготовление", 2: "Гальваник1", 3: "Мехобработка"}.get(index, ""))
    return stages


def new_order(number):
    now = datetime.now()
    return dict(schema_version=2, number=number, date=now.strftime("%d.%m.%Y"),
                created_at=now.isoformat(timespec="seconds"), customer="", name="Новое изделие",
                nodes=[], stages=default_stages(), laser_rows=[], laser_unmatched=[],
                laser_directory="", laser_area_only=False, laser_include_filled=False,
                laser_comment="", extras=[], note="")


def new_node(kind="part", parent_id=""):
    return dict(id=uid(), parent_id=parent_id, kind=kind, name="Новое изделие" if kind == "assembly" else "Новая деталь",
                code="", quantity=1, material={}, width=100, height=100, thickness=1,
                area=0, mass=0, bends=0, filing_minutes=0, laser_minutes=0,
                waste_pct=0, paint=True, note="")


def flatten_nodes(nodes):
    """Validate hierarchy and calculate quantities through every ancestor."""
    by_id = {n["id"]: n for n in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("Повторяющийся идентификатор детали")
    visiting, cache = set(), {}

    def resolve(node_id):
        if node_id in visiting:
            raise ValueError("В дереве спецификации обнаружен цикл")
        if node_id in cache:
            return cache[node_id]
        visiting.add(node_id)
        node = by_id[node_id]
        if node["kind"] not in ("part", "assembly"):
            raise ValueError("Неизвестный тип позиции")
        if not node["name"].strip():
            raise ValueError("Укажите наименование позиции")
        quantity = numeric(node["quantity"], node["name"] + ": количество", positive=True, integer=True)
        parent_id = node.get("parent_id", "")
        depth = 0
        if parent_id:
            if parent_id not in by_id or by_id[parent_id]["kind"] != "assembly":
                raise ValueError("Родителем детали должно быть существующее изделие / узел")
            parent_quantity, parent_depth = resolve(parent_id)
            quantity *= parent_quantity
            depth = parent_depth + 1
        visiting.remove(node_id)
        cache[node_id] = quantity, depth
        return cache[node_id]

    result = []

    def append_children(parent_id):
        for node in nodes:
            if node.get("parent_id", "") == parent_id:
                quantity, depth = cache[node["id"]]
                result.append(dict(deepcopy(node), total_quantity=quantity, depth=depth))
                append_children(node["id"])

    for node_id in by_id:
        resolve(node_id)
    append_children("")
    return result


def descendants(nodes, node_id):
    found = {node_id}
    while True:
        updated = found | {n["id"] for n in nodes if n.get("parent_id") in found}
        if updated == found:
            return found
        found = updated


def move_node(nodes, node_id, parent_id):
    if parent_id in descendants(nodes, node_id):
        raise ValueError("Нельзя поместить узел внутрь самого себя или его потомка")
    if parent_id and not any(n["id"] == parent_id and n["kind"] == "assembly" for n in nodes):
        raise ValueError("Выберите изделие или корень заказа")
    next(n for n in nodes if n["id"] == node_id)["parent_id"] = parent_id


def copy_branch(nodes, node_id):
    selected = descendants(nodes, node_id)
    mapping = {key: uid() for key in selected}
    copies = []
    for n in nodes:
        if n["id"] in selected:
            item = deepcopy(n)
            item["id"] = mapping[n["id"]]
            item["parent_id"] = mapping.get(n.get("parent_id"), n.get("parent_id", ""))
            if n["id"] == node_id:
                item["name"] += " (копия)"
            copies.append(item)
    nodes.extend(copies)
    return mapping[node_id]


def transfer_laser_matches(order):
    """Replace per-part values; repeated transfers never accumulate time."""
    grouped = {}
    for row in order["laser_unmatched"]:
        if row.get("node_id"):
            grouped.setdefault(row["node_id"], []).append(row)
    count = 0
    for node in order["nodes"]:
        rows = grouped.get(node["id"])
        if not rows or node["kind"] != "part":
            continue
        quantity = sum(numeric(r["quantity"], "Количество сопоставленной детали", positive=True) for r in rows)
        if not any(r.get("missing_time") for r in rows) and not order["laser_area_only"] and (not node["laser_minutes"] or order["laser_include_filled"]):
            node["laser_minutes"] = sum(r["minutes"] for r in rows) / quantity
        if not any(r.get("missing_area") for r in rows):
            node["area"] = sum(r["area"] * r["quantity"] for r in rows) / quantity
        if not any(r.get("missing_mass") for r in rows):
            node["mass"] = sum(r["mass"] * r["quantity"] for r in rows) / quantity
        count += 1
    return count


def calculate_production(source):
    order = deepcopy(source)
    parts, warnings = [], []
    tree = flatten_nodes(order["nodes"])
    material_total = 0
    for node in tree:
        if node["kind"] != "part":
            continue
        for key in ("width", "height", "thickness", "area", "mass", "bends", "filing_minutes", "laser_minutes", "waste_pct"):
            node[key] = numeric(node.get(key, 0), node["name"] + ": " + key)
        q = node["total_quantity"]
        flat_area = node["width"] * node["height"] / 1_000_000
        # Explicit area is the painted surface of ONE part; fallback is two faces.
        node["surface_area"] = (node["area"] or flat_area * 2) * q
        node["total_mass"] = node["mass"] * q
        node["total_bends"] = node["bends"] * q
        material = node.get("material") or {}
        sheet_area = float(material.get("width", 0)) * float(material.get("height", 0)) / 1_000_000
        node["material_cost"] = round(flat_area * q * (1 + node["waste_pct"] / 100) / sheet_area
                                      * float(material.get("price_sheet", 0)), 2) if sheet_area else 0
        if not material:
            warnings.append(f"{node['name']}: материал не выбран")
        if node.get("cam_generated") and not node["mass"]:
            warnings.append(f"{node['name']}: масса не задана")
        if node.get("stock_kind") == "tube" and (not node["area"] or not node["thickness"]):
            warnings.append(f"{node['name']}: заполните площадь покраски и толщину стенки трубы")
        material_total += node["material_cost"]
        parts.append(node)

    laser = dict(cutting=0., sheets=0., weight=0., material=0., change=0., heads=0., transport=0., total=0.)
    for row in order["laser_rows"]:
        for key in ("thickness", "coef", "quantity", "waste_pct", "cut_minutes", "sheets", "weight", "extra_minutes"):
            row[key] = numeric(row.get(key, 0), "Лазерная резка: " + key)
        if row["waste_pct"] > 100:
            raise ValueError("Процент отходов в раскрое не должен превышать 100")
        if row.get("missing_time") and not row["cut_minutes"]:
            warnings.append(f"{row['material']}: время резки отсутствует в файле — итог времени неполный")
        if row.get("missing_mass") and not row["weight"]:
            warnings.append(f"{row['material']}: масса заготовки не задана — итог веса неполный")
        if not row.get("customer_material") and not row.get("price"):
            warnings.append(f"{row['material']}: цена материала равна нулю — итог стоимости неполный")
        laser["cutting"] += row["cut_minutes"]
        laser["sheets"] += row["sheets"]
        laser["weight"] += row["weight"] * row["quantity"]
        laser["transport"] += row["extra_minutes"]
        if not row.get("customer_material"):
            laser["material"] += row["quantity"] * row["coef"] * numeric(row.get("price", 0), "Цена материала")
    if order["laser_rows"]:
        material_total = laser["material"]
    else:
        laser["cutting"] = sum(p["laser_minutes"] * p["total_quantity"] for p in parts)

    area = sum(p["surface_area"] for p in parts if p.get("paint", True))
    bends = sum(p["total_bends"] for p in parts)
    results = []
    for stage in order["stages"]:
        settings = {k: numeric(v, stage["name"] + ": " + k) for k, v in stage["settings"].items()}
        kind = stage["kind"]
        minutes, consumables, details = 0., 0., {}
        if kind == "filing":
            minutes = settings["setup"] + sum((p["filing_minutes"] or settings["per_part"]) * p["total_quantity"] for p in parts)
        elif kind == "bending":
            minutes = settings["setup"] + bends * settings["per_bend"]
            details = {"Количество гибов": bends}
        elif kind == "laser":
            laser["change"] = laser["sheets"] * settings["sheet_change"]
            laser["heads"] = settings["head_3030"] + settings["head_2530"]
            laser["transport"] += laser["cutting"] * settings["transport_pct"] / 100
            minutes = sum(laser[k] for k in ("cutting", "change", "heads", "transport"))
            laser["total"] = minutes
            details = {"Время резки, мин": laser["cutting"], "Количество листов": laser["sheets"],
                       "Время на смену листа, мин": laser["change"], "Вес металла, кг": laser["weight"],
                       "Дополнительное время, мин": laser["transport"], "Время на смену головы, мин": laser["heads"]}
        elif kind == "painting":
            if settings["oven_area"] <= 0:
                raise ValueError("Плановая площадь печи должна быть больше нуля")
            preparation = area * settings["prep_per_m2"] * settings["volume_coef"]
            batches = ceil(area / settings["oven_area"])
            painting = batches * settings["cycle_minutes"]
            transport = painting * settings["transport_pct"] / 100
            paint = area * settings["paint_per_m2"]
            primer = area * settings["primer_per_m2"]
            minutes = preparation + painting + transport
            consumables = paint * settings["paint_price"] + primer * settings["primer_price"]
            details = {"Площадь покраски, м²": area, "Общее время на этап подготовки, мин": preparation,
                       "Количество загрузок печи": batches, "Покраска, мин": painting,
                       "Отвоз изделий, мин": transport, "Краска, кг": paint, "Грунтовка, кг": primer}
        elif kind != "welding":
            minutes = settings["minutes"]
        if kind != "welding" and stage["otk"]:
            minutes += settings["otk_minutes"]
        cost = minutes / 60 * settings["rate"] + settings["fixed_cost"] + consumables if kind != "welding" else 0
        if not stage["enabled"]:
            minutes, cost = 0., 0.
        if stage["enabled"] and kind != "welding" and minutes and not settings["rate"]:
            warnings.append(f"{stage['name']}: тариф часа равен нулю")
        results.append(dict(id=stage["id"], name=stage["name"], kind=kind, enabled=stage["enabled"],
                            otk=stage["otk"], description=stage["description"], minutes=round(minutes, 3),
                            cost=round(cost, 2), details=details))
    extras = 0.
    for item in order["extras"]:
        item["quantity"] = numeric(item["quantity"], "Количество услуги", positive=True)
        item["price"] = numeric(item["price"], "Цена услуги")
        item["sum"] = round(item["quantity"] * item["price"], 2)
        extras += item["sum"]
    stage_total = round(sum(s["cost"] for s in results), 2)
    for doc in order.get("cam_imports", []):
        warnings.extend(f"{doc['filename']}: {message}" for message in doc.get("warnings", []))
    order["calculation"] = dict(tree=tree, parts=parts, stages=results, laser=laser, paint_area=area,
                                 quantity=sum(p["total_quantity"] for p in parts), bends=bends,
                                 mass=sum(p["total_mass"] for p in parts),
                                 minutes=round(sum(s["minutes"] for s in results), 3), warnings=warnings,
                                 material_basis="По строкам раскроя" if order["laser_rows"] else "По площади деталей с припуском; без оптимизации раскроя")
    order["totals"] = dict(material=round(material_total, 2), stages=stage_total, extras=round(extras, 2),
                           grand=round(material_total + stage_total + extras, 2))
    return order
