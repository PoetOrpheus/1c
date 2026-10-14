"""Read-only parsers for the supplied BIT, TruTops and Sekirus report families.

All source units are retained; no prices, material density or missing machining
times are inferred. Parsing and applying an import are separate operations.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path, PureWindowsPath
import re
import sys
import warnings

from production import new_node, numeric, flatten_nodes, uid

# Development dependencies are local. PyInstaller collects the same imports.
_vendor = Path(__file__).resolve().parent / ".vendor"
if _vendor.is_dir() and str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))

SUPPORTED = {".xlsx", ".pdf", ".html", ".htm"}
NUM = r"[0-9]+(?:[.,][0-9]+)?"


def clean(value):
    return " ".join(str(value).replace("\xa0", " ").split())


def key_text(value):
    return clean(value).casefold().replace("ё", "е")


def value_number(value, label="Число"):
    match = re.match(rf"^\s*({NUM})(?:\s|%|$)", str(value))
    if not match:
        raise ValueError(f"{label}: не распознано значение «{value}»")
    return numeric(match[1], label)


def duration(value):
    match = re.search(r"(\d+)\s*:\s*(\d+)\s*:\s*(\d+(?:[.,]\d+)?)", str(value))
    if not match:
        raise ValueError(f"Не распознано время Ч:М:С: {value}")
    hours, minutes, seconds = (numeric(v) for v in match.groups())
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"Некорректное время: {value}")
    return hours * 60 + minutes + seconds / 60


def dimensions(value, count=2):
    match = re.search(rf"({NUM})\s*[xх×]\s*({NUM})(?:\s*[xх×]\s*({NUM}))?", str(value), re.I)
    if not match or (count == 3 and match[3] is None):
        raise ValueError(f"Не распознаны размеры: {value}")
    return tuple(numeric(v) for v in match.groups()[:count])


def code_from_name(name):
    first = name.split()[0] if name.strip() else ""
    return first if re.search(r"\d", first) and "." in first else ""


def part_record(name, quantity, width, height, area, mass, minutes=None, perimeter=None):
    return dict(name=clean(name), code=code_from_name(name), quantity=numeric(quantity, "Количество деталей", positive=True, integer=True),
                width=width, height=height, flat_area=area, mass=mass, minutes=minutes, perimeter=perimeter)


class HTMLRows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(clean("".join(self.cell)))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if any(self.row):
                self.rows.append(self.row)
            self.row = None


def parse_trutops(path):
    raw = path.read_bytes()
    charset = re.search(br"charset\s*=\s*['\"]?([\w-]+)", raw[:5000], re.I)
    encoding = charset[1].decode("ascii") if charset else "utf-8-sig"
    try:
        text = raw.decode(encoding)
    except (UnicodeError, LookupError):
        raise ValueError(f"Не удалось прочитать кодировку HTML ({encoding})") from None
    if "TruTops" not in text or "ПЛАН НАЛАДКИ" not in text:
        raise ValueError("HTML не является планом наладки TruTops")
    parser = HTMLRows()
    parser.feed(text)
    header, blocks, current = {}, [], None
    for row in parser.rows:
        cells = [v for v in row if v]
        if len(cells) < 2:
            continue
        label, val = cells[0].rstrip(":"), cells[1]
        if label == "НОМЕР ДЕТАЛИ":
            current = {}
            blocks.append(current)
        (header if current is None else current)[label] = val
    w, h, thickness = dimensions(header["ЗАГОТОВКА"], 3)
    runs = numeric(header["КОЛ-ВО ПРОГОНОВ ПРОГРАММЫ"], "Прогоны программы", positive=True, integer=True)
    parts = []
    for block in blocks:
        name = PureWindowsPath(block["ИМЯ ГЕОМ. ФАЙЛА"]).stem
        pw, ph = dimensions(block["РАЗМЕРЫ"])
        part = part_record(name, numeric(block["КОЛИЧЕСТВО"]) * runs, pw, ph,
                           value_number(block["ПОВЕРХНОСТЬ"]) / 1_000_000,
                           value_number(block["ВЕС"]), value_number(block["ВРЕМЯ ОБРАБОТКИ"]),
                           value_number(block["ДЛИНА РЕЗКИ"]))
        part["source_geometry"] = block["ИМЯ ГЕОМ. ФАЙЛА"]
        parts.append(part)
    return dict(family="TruTops", program=header["ИМЯ ПРОГРАММЫ"].removesuffix(" ()"),
                material=header["МАТЕРИАЛ (TT)"], thickness=thickness, stock_kind="sheet",
                width=w, height=h, actual_size=dimensions(header["МИНИМАЛЬНАЯ ЗАГОТОВКА"]),
                stock_count=runs, stock_mass=value_number(header["ВЕС"]), total_part_mass=None,
                cut_minutes=duration(header["МАШИННОЕ ВРЕМЯ"]) * runs,
                piercing_minutes=None, waste_pct=value_number(header["ОТХОДЫ"]),
                center=header["СТАНОК"], parts=parts,
                warnings=["Время обработки деталей округлено в исходном HTML; общее время берётся из «Машинного времени»."],
                raw_summary=header)


BIT_LABELS = {"Material", "Sheet Size", "ActualSize", "Thickness", "Utilization", "Part Count", "Quantity",
              "sheet Count", "Move Distance", "Piercing Count", "Cut Length", "Estimated Time", "Piercing Time", "Count:"}


def bit_document(program, meta, parts):
    w, h = dimensions(meta["Sheet Size"])
    sheet_count = numeric(value_number(meta["sheet Count"]), "Число листов", positive=True, integer=True)
    if sheet_count != 1 or numeric(meta.get("Count:", 1)) != 1:
        raise ValueError("BIT: в этом варианте отчёта Count/sheet Count не равен 1. Нужен образец с несколькими листами для проверки единиц итогов.")
    expected = numeric(value_number(meta["Part Count"]), integer=True)
    if sum(p["quantity"] for p in parts) != expected:
        raise ValueError(f"BIT: количество прочитанных деталей не совпало с Part Count ({expected})")
    return dict(family="BIT", program=program, material=meta["Material"],
                thickness=value_number(meta["Thickness"]), stock_kind="sheet", width=w, height=h,
                actual_size=dimensions(meta["ActualSize"]), stock_count=sheet_count,
                stock_mass=None, total_part_mass=value_number(meta["Quantity"]),
                cut_minutes=duration(meta["Estimated Time"]), piercing_minutes=duration(meta["Piercing Time"]),
                waste_pct=100 - value_number(meta["Utilization"]), center="", parts=parts,
                warnings=["В отчёте BIT нет времени отдельной детали и названия станка. Время врезания сохранено справочно и отдельно не прибавляется."],
                raw_summary=meta)


def parse_bit_excel(path):
    from openpyxl import load_workbook
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Workbook contains no default style")
        book = load_workbook(path, data_only=True, read_only=True)
    try:
        if any(sheet.max_row > 50000 or sheet.max_column > 200 for sheet in book):
            raise ValueError("Книга слишком велика для отчёта раскроя (более 50 000 строк или 200 колонок)")
        all_rows = [[clean(c) for c in row if c is not None and clean(c)] for sheet in book for row in sheet.iter_rows(values_only=True)]
    finally:
        book.close()
    if not any("Nest Report (BIT)" in row for row in all_rows):
        raise ValueError("Excel не является Nest Report (BIT)")
    meta, parts, programs = {}, [], []
    for row in all_rows:
        if len(row) == 1 and re.fullmatch(r"\S+-\d+-\d+", row[0]):
            programs.append(row[0])
        for index, value in enumerate(row[:-1]):
            if value in BIT_LABELS:
                meta[value] = row[index + 1]
        if len(row) == 7 and row[0].isdigit():
            w, h = dimensions(row[2])
            parts.append(part_record(row[1], row[6], w, h, value_number(row[5]) / 1_000_000,
                                     value_number(row[3]), perimeter=value_number(row[4])))
    if len(set(programs)) != 1:
        raise ValueError("Не удалось однозначно определить программу BIT в книге Excel")
    return bit_document(programs[0], meta, parts)


def match_value(pattern, text, title):
    match = re.search(pattern, text, re.I | re.M)
    if not match:
        raise ValueError(f"Не найдено поле «{title}» в PDF")
    return match


def parse_bit_pdf(text):
    head = match_value(rf"^\s*(\S+)\s+Material:\s*(.*?)\s+Thickness:\s*({NUM})\s*mm\s+Count:\s*(\d+)", text, "программа")
    meta = {"Material": head[2], "Thickness": head[3], "Count:": head[4]}
    for field, pattern in {
        "Sheet Size": rf"Sheet Size\s+({NUM}\s*x\s*{NUM}\s*mm)",
        "ActualSize": rf"ActualSize\s+({NUM}\s*x\s*{NUM}\s*mm)",
        "Utilization": rf"Utilization\s+({NUM})%",
        "Part Count": r"Part Count\s+(\d+)",
        "Quantity": rf"(?<!Part )Quantity\s+({NUM})\s*kg",
        "sheet Count": r"sheet Count\s+(\d+)",
        "Estimated Time": r"Estimated Time\s+(\d+:\d+:\d+)",
        "Piercing Time": r"Piercing Time\s+(\d+:\d+:\d+)",
    }.items():
        meta[field] = match_value(pattern, text, field)[1]
    pattern = rf"^\s*\d+\s+(.+?)\s+({NUM})\s*x\s*({NUM})\s*mm\s+({NUM})\s*kg\s+({NUM})\s*mm\s+({NUM})\s*mm\^?2\s+(\d+)\s*$"
    parts = [part_record(m[1], m[7], numeric(m[2]), numeric(m[3]), numeric(m[6]) / 1_000_000,
                         numeric(m[4]), perimeter=numeric(m[5])) for m in re.finditer(pattern, text, re.M)]
    return bit_document(head[1], meta, parts)


def parse_sekirus(text):
    if "Технологический план" not in text or "Инф. о трубе" not in text:
        raise ValueError("Неизвестный формат PDF (ожидался BIT или Sekirus)")
    top, tube = text.split("Инф. о трубе", 1)
    section = match_value(r"^\s*\d+\s+(Square tube Width[^\r\n]+)", top, "сечение")[1].strip()
    stock = match_value(rf"^\s*\d+\s+(\d+)\s*/\s*(\d+)\s+({NUM})\s*mm\s+({NUM})\s*mm", tube, "труба")
    count, length, dead_zone = int(stock[1]), numeric(stock[3]), numeric(stock[4])
    parts = []
    for m in re.finditer(rf"^\s*\d+\s+(.+?)\s+(\d+)\s*/\s*(\d+)\s+({NUM})\s*mm\s+({NUM})(?:\s*mm)?\s*$", top, re.M):
        if m[2] != m[3]:
            raise ValueError("Sekirus: количество размещённых и требуемых деталей различается; проверьте раскрой")
        parts.append(part_record(m[1], m[2], numeric(m[4]), None, None, None, perimeter=numeric(m[5])))
    # Validate the repeated nesting list without counting it as another set of parts.
    tail = tube.split("Список вкладыв.:")[-1]
    nested = {}
    for m in re.finditer(rf"^\s*\d+\s+(.+?)\s+(\d+)\s+({NUM})\s*mm\s+({NUM})\s*mm\s*$", tail, re.M):
        nested[clean(m[1])] = nested.get(clean(m[1]), 0) + int(m[2])
    if not parts or nested != {p["name"]: p["quantity"] for p in parts}:
        raise ValueError("Sekirus: список вложенных деталей не совпал с итоговой таблицей")
    nest = match_value(rf"^\s*({re.escape(section)}_Nest\s+\d+)\s+(\d+)\s+({NUM})\s*mm\s+({NUM})\s*mm\s+({NUM})", tail, "вложение")
    if int(nest[2]) != count or numeric(nest[3]) != length:
        raise ValueError("Sekirus: количество или длина трубы не совпадают в таблицах")
    return dict(family="Sekirus", program=clean(nest[1]), material=section, thickness=None, stock_kind="tube",
                width=length, height=None, actual_size=(length - numeric(nest[4]), None), stock_count=count,
                stock_mass=None, total_part_mass=None, cut_minutes=None, piercing_minutes=None,
                waste_pct=numeric(nest[4]) / length * 100, center="", parts=parts,
                warnings=["Sekirus: в PDF нет марки металла, толщины стенки, массы, площади и времени резки. Заполните эти данные вручную; R3 — радиус, не толщина.",
                          "999 — доступное количество труб, не расход. Повторные строки вложения проверены и не посчитаны второй раз."],
                raw_summary=dict(section=section, dead_zone_mm=dead_zone, remainder_mm=numeric(nest[4]),
                                 available_tubes=int(stock[2]), cut_length_mm=numeric(nest[5])))


def parse_pdf(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("PDF защищён паролем")
    if len(reader.pages) > 200:
        raise ValueError("PDF содержит больше 200 страниц; разделите его на отдельные раскрои")
    text = "\n".join(page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False) for page in reader.pages)
    if not text.strip():
        raise ValueError("В PDF нет текстового слоя; скан без распознавания не поддерживается")
    return parse_bit_pdf(text) if "Nest Report (BIT)" in text else parse_sekirus(text)


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def finalize_document(doc, path):
    if not doc["parts"]:
        raise ValueError("В документе не найдено ни одной детали")
    if doc["width"] <= 0 or (doc["stock_kind"] == "sheet" and doc["height"] <= 0):
        raise ValueError("Некорректный размер исходной заготовки")
    if not 0 <= doc["waste_pct"] <= 100:
        raise ValueError("Некорректный процент отходов")
    for p in doc["parts"]:
        p.update(thickness=doc["thickness"], material_name=doc["material"], stock_kind=doc["stock_kind"])
        p["part_key"] = part_key(p)
    if doc["total_part_mass"] is not None:
        part_mass = sum((p["mass"] or 0) * p["quantity"] for p in doc["parts"])
        if abs(part_mass - doc["total_part_mass"]) > max(.02, doc["total_part_mass"] * .02):
            doc["warnings"].append(f"Масса в исходнике не сходится: сумма деталей {part_mass:.4f} кг; итог отчёта {doc['total_part_mass']:.4f} кг. Значения сохранены без исправления.")
    doc["key"] = digest([doc[k] for k in ("family", "program", "material", "thickness", "width", "height")])
    canonical = {k: doc[k] for k in ("family", "program", "material", "thickness", "stock_kind", "width", "height", "actual_size",
                                    "stock_count", "stock_mass", "total_part_mass", "cut_minutes", "piercing_minutes", "waste_pct")}
    canonical["parts"] = sorted([{k: p[k] for k in ("name", "quantity", "width", "height", "flat_area", "mass", "minutes", "perimeter")}
                                  for p in doc["parts"]], key=lambda p: p["name"])
    doc["fingerprint"] = digest(canonical)
    doc["source"] = str(path.resolve())
    doc["filename"] = path.name
    doc["file_hash"] = sha256(path.read_bytes()).hexdigest()
    return doc


def part_key(part):
    return digest([key_text(part.get("code") or part["name"]), key_text(part["material_name"]), part["thickness"],
                   part["width"], part["height"], part["stock_kind"]])


def read_document(path):
    path = Path(path)
    if path.stat().st_size > 40 * 1024 * 1024:
        raise ValueError("Размер файла больше 40 МБ")
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        doc = parse_trutops(path)
    elif suffix == ".xlsx":
        doc = parse_bit_excel(path)
    elif suffix == ".pdf":
        doc = parse_pdf(path)
    else:
        raise ValueError("Поддерживаются XLSX, PDF, HTML")
    return finalize_document(doc, path)


def read_batch(paths, progress=None):
    documents, errors, duplicates = [], [], []
    by_key, conflicts = {}, set()
    paths = sorted({Path(p).resolve() for p in paths}, key=lambda p: (p.suffix.lower() != ".xlsx", str(p).casefold()))
    for index, path in enumerate(paths):
        if progress:
            progress(index, len(paths), path.name)
        try:
            doc = read_document(path)
            previous = by_key.get(doc["key"])
            if previous:
                if previous["fingerprint"] == doc["fingerprint"]:
                    duplicates.append(f"{path.name} — тот же раскрой, что {previous['filename']}; оставлена одна копия")
                    previous.setdefault("also_sources", []).append(str(path))
                else:
                    conflicts.add(doc["key"])
                    errors.append(f"{path.name} и {previous['filename']}: одинаковая программа, но разные данные. Выберите нужный файл отдельно.")
            else:
                by_key[doc["key"]] = doc
        except Exception as error:
            errors.append(f"{path.name}: {type(error).__name__}: {error}")
    documents = [d for k, d in by_key.items() if k not in conflicts]
    return dict(documents=documents, errors=errors, duplicates=duplicates)


def aggregate_parts(documents):
    result = {}
    for doc in documents:
        for p in doc["parts"]:
            key = p["part_key"]
            if key not in result:
                result[key] = dict(deepcopy(p), quantity=0, total_minutes=0., sources=[], missing_time=False,
                                   doc_keys=[], mass_sum=0., area_sum=0., known_mass=0, known_area=0)
            row = result[key]
            row["quantity"] += p["quantity"]
            row["doc_keys"].append(doc["key"])
            row["sources"].append(doc["filename"])
            if p["minutes"] is None:
                row["missing_time"] = True
            else:
                row["total_minutes"] += p["minutes"] * p["quantity"]
            for field, sum_key, known in (("mass", "mass_sum", "known_mass"), ("flat_area", "area_sum", "known_area")):
                if p[field] is not None:
                    row[sum_key] += p[field] * p["quantity"]
                    row[known] += p["quantity"]
    for p in result.values():
        p["mass"] = p["mass_sum"] / p["known_mass"] if p["known_mass"] == p["quantity"] else None
        p["flat_area"] = p["area_sum"] / p["known_area"] if p["known_area"] == p["quantity"] else None
        p["minutes"] = None if p["missing_time"] else p["total_minutes"] / p["quantity"]
    return result


def default_mapping(order, part):
    imported = [n for n in order["nodes"] if n.get("cam_part_key") == part["part_key"]]
    if len(imported) == 1:
        return imported[0]["id"]
    candidates = [n for n in order["nodes"] if n["kind"] == "part" and
                  ((part["code"] and key_text(n.get("code", "")) == key_text(part["code"])) or key_text(n["name"]) == key_text(part["name"]))]
    if len(candidates) == 1:
        n = candidates[0]
        if part["thickness"] is None or abs(float(n.get("thickness", 0)) - part["thickness"]) < .001:
            return n["id"]
    return "unmatched" if candidates else "new"


def material_snapshot(doc, config):
    if config.get("material"):
        return deepcopy(config["material"])
    return dict(id="cam-" + doc["key"][:12], name=doc["material"], thickness=doc["thickness"] or 0,
                width=doc["width"], height=doc["height"] or 0, price_sheet=config.get("price", 0), active=1)


def laser_row(doc, config):
    basis = config.get("basis", "full")
    count = doc["stock_count"]
    if basis == "blank":
        count *= doc["actual_size"][0] / doc["width"]
        if doc["stock_kind"] == "sheet":
            count *= doc["actual_size"][1] / doc["height"]
    material = material_snapshot(doc, config)
    cut = config.get("cut_minutes", doc["cut_minutes"])
    mass = config.get("stock_mass", doc["stock_mass"])
    return dict(id=uid(), import_key=doc["key"], source_file=doc["filename"], program=doc["program"],
                material=material["name"], characteristic=f"{doc['width']:g}" + (f" × {doc['height']:g} мм" if doc["height"] else " мм (труба)"),
                thickness=doc["thickness"] or material.get("thickness", 0), coef=1, quantity=count,
                waste_pct=doc["waste_pct"], center=config.get("center", doc["center"]), cut_minutes=cut or 0,
                sheets=doc["stock_count"] if doc["stock_kind"] == "sheet" else 0,
                stock_count=doc["stock_count"], stock_kind=doc["stock_kind"], weight=mass or 0,
                extra_minutes=0, blank=f"{doc['actual_size'][0]:g}" + (f" × {doc['actual_size'][1]:g}" if doc['actual_size'][1] else " мм"),
                customer_material=False, price=config.get("price", material["price_sheet"]),
                missing_time=cut is None, missing_mass=mass is None, source_total_part_mass=doc["total_part_mass"],
                import_basis=basis)


def apply_import(source, documents, *, configs=None, mappings=None, parent_id="", group_name="Импорт раскроев",
                 paint_sides=2, update_quantities=False, replace=False, area_only=False, accept_conflicting_mass=False,
                 update_geometry=True):
    """Return a new order, never mutate source. Commit only after validation."""
    configs, mappings = configs or {}, mappings or {}
    order = deepcopy(source)
    records = {d["key"]: d for d in order.get("cam_imports", [])}
    selected, skipped = [], []
    for doc in documents:
        if doc["key"] in records and not replace and not area_only:
            skipped.append(doc["filename"])
        else:
            selected.append(deepcopy(doc))
    if not selected:
        return order, dict(imported=0, skipped=skipped, created=0, matched=0)
    if not accept_conflicting_mass:
        for doc in selected:
            if doc["family"] == "BIT" and any("Масса в исходнике не сходится" in w for w in doc["warnings"]):
                for p in doc["parts"]:
                    p["source_mass"] = p["mass"]
                    p["mass"] = None
                doc["warnings"].append("Противоречивая масса деталей не перенесена. Исходные массы сохранены для сверки.")
    if area_only and any(mappings.get(k, default_mapping(order, p)) in ("new", "unmatched") for k, p in aggregate_parts(selected).items()):
        raise ValueError("В режиме «Только площадь и массу» сопоставьте все детали с существующими позициями.")
    parent_quantity = 1
    if parent_id:
        parents = [n for n in flatten_nodes(order["nodes"]) if n["id"] == parent_id and n["kind"] == "assembly"]
        if not parents:
            raise ValueError("Выберите существующее изделие / узел")
        parent_quantity = parents[0]["total_quantity"]
    keys = {d["key"] for d in selected}
    old_unmatched = [r for r in order["laser_unmatched"] if r.get("import_key") in keys]
    if not area_only:
        order["laser_rows"] = [r for r in order["laser_rows"] if r.get("import_key") not in keys]
        order["laser_unmatched"] = [r for r in order["laser_unmatched"] if r.get("import_key") not in keys]
    combined = aggregate_parts(selected)
    nodes = {n["id"]: n for n in order["nodes"]}
    resolved, created, matched = {}, 0, 0
    new_group = None
    for key, part in combined.items():
        mapping = mappings.get(key, default_mapping(order, part))
        node = None
        if mapping == "new":
            if part["quantity"] % parent_quantity:
                raise ValueError(f"{part['name']}: {part['quantity']} шт. не делятся на количество родительского узла {parent_quantity}. Выберите корень заказа.")
            if not parent_id and new_group is None:
                new_group = new_node("assembly")
                new_group["name"] = group_name.strip() or "Импорт раскроев"
                order["nodes"].append(new_group)
            node = new_node("part", parent_id or new_group["id"])
            node.update(name=part["name"], code=part["code"], quantity=part["quantity"] // parent_quantity,
                        width=part["width"] or 0, height=part["height"] or 0, thickness=part["thickness"] or 0,
                        cam_generated=True, cam_part_key=key, stock_kind=part["stock_kind"])
            doc = next(d for d in selected if d["key"] == part["doc_keys"][0])
            node["material"] = material_snapshot(doc, configs.get(doc["key"], {}))
            order["nodes"].append(node)
            nodes[node["id"]] = node
            created += 1
        elif mapping != "unmatched":
            node = nodes.get(mapping)
            if not node or node["kind"] != "part":
                raise ValueError(f"{part['name']}: сопоставленная деталь отсутствует")
            if mapping in resolved.values():
                raise ValueError(f"{part['name']}: разные детали файлов сопоставлены с одной позицией заказа. Проверьте сопоставление.")
            matched += 1
        resolved[key] = node["id"] if node else ""
        if node:
            if not area_only and update_geometry:
                for field in ("width", "height", "thickness"):
                    if part[field] is not None:
                        node[field] = part[field]
            if part["flat_area"] is not None:
                node["area"] = part["flat_area"] * paint_sides
            if part["mass"] is not None:
                node["mass"] = part["mass"]
            if not area_only and part["minutes"] is not None:
                node["laser_minutes"] = part["minutes"]
            if mapping == "new":
                node["note"] = "Из файлов: " + ", ".join(sorted(set(part["sources"])))
                node["cam_initial_quantity"] = node["quantity"]
    if area_only:
        return order, dict(imported=len(selected), skipped=skipped, created=0, matched=matched)
    for doc in selected:
        config = configs.get(doc["key"], {})
        order["laser_rows"].append(laser_row(doc, config))
        for part in doc["parts"]:
            order["laser_unmatched"].append(dict(id=uid(), import_key=doc["key"], source_file=doc["filename"],
                part_key=part["part_key"], name=part["name"], quantity=part["quantity"],
                minutes=(part["minutes"] or 0) * part["quantity"], node_id=resolved[part["part_key"]],
                area=(part["flat_area"] or 0) * paint_sides, mass=part["mass"] or 0,
                missing_time=part["minutes"] is None, missing_area=part["flat_area"] is None,
                missing_mass=part["mass"] is None))
        records[doc["key"]] = doc
    # A replacement must not leave obsolete generated parts in painting totals.
    old_ids = {r.get("node_id") for r in old_unmatched if r.get("node_id")}
    still_used = {r.get("node_id") for r in order["laser_unmatched"] if r.get("node_id")}
    for old_id in old_ids - still_used:
        old_node = nodes.get(old_id)
        if old_node and old_node.get("cam_generated"):
            raise ValueError(f"После замены исчезает деталь «{old_node['name']}». Удалите её из дерева или явно сопоставьте с новой деталью перед заменой.")
    quantities = {}
    for row in order["laser_unmatched"]:
        if row.get("import_key") and row.get("node_id"):
            quantities[row["node_id"]] = quantities.get(row["node_id"], 0) + row["quantity"]
    effective = {n["id"]: n["total_quantity"] for n in flatten_nodes(order["nodes"])}
    for node_id in set(resolved.values()) - {""}:
        node = nodes[node_id]
        managed = node.get("cam_generated") and node.get("quantity") == node.get("cam_initial_quantity")
        if update_quantities or managed:
            parent_q = effective.get(node["parent_id"], 1)
            q = quantities[node_id]
            if q % parent_q:
                raise ValueError(f"{node['name']}: импортированное количество {q} не делится на количество родительских узлов {parent_q}")
            node["quantity"] = q // parent_q
            if managed:
                node["cam_initial_quantity"] = node["quantity"]
    order["cam_imports"] = list(records.values())
    return order, dict(imported=len(selected), skipped=skipped, created=created, matched=matched)
