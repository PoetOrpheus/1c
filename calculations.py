"""Business calculations for the sheet cutting order."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil, floor


@dataclass(frozen=True)
class CuttingLayout:
    orientation: str
    part_width: float
    part_height: float
    columns: int
    rows: int
    pieces_per_sheet: int
    sheets: int
    cut_lines: int
    utilization_pct: float
    waste_pct: float

    def to_dict(self) -> dict:
        return asdict(self)


def _candidate(
    sheet_width: float,
    sheet_height: float,
    part_width: float,
    part_height: float,
    quantity: int,
    orientation: str,
) -> CuttingLayout | None:
    columns = floor(sheet_width / part_width)
    rows = floor(sheet_height / part_height)
    pieces_per_sheet = columns * rows
    if pieces_per_sheet <= 0:
        return None

    sheets = ceil(quantity / pieces_per_sheet)
    full_sheet_lines = max(columns - 1, 0) + max(rows - 1, 0)
    full_sheets, remainder = divmod(quantity, pieces_per_sheet)
    cut_lines = full_sheets * full_sheet_lines
    if remainder:
        used_rows = ceil(remainder / columns)
        used_columns = min(columns, remainder)
        cut_lines += max(used_columns - 1, 0) + max(used_rows - 1, 0)
    used_area = quantity * part_width * part_height
    purchased_area = sheets * sheet_width * sheet_height
    utilization = min(100.0, used_area / purchased_area * 100.0)

    return CuttingLayout(
        orientation=orientation,
        part_width=part_width,
        part_height=part_height,
        columns=columns,
        rows=rows,
        pieces_per_sheet=pieces_per_sheet,
        sheets=sheets,
        cut_lines=cut_lines,
        utilization_pct=round(utilization, 2),
        waste_pct=round(100.0 - utilization, 2),
    )


def calculate_layout(
    sheet_width: float,
    sheet_height: float,
    part_width: float,
    part_height: float,
    quantity: int,
    allow_rotate: bool = True,
) -> CuttingLayout:
    """Return the most economical rectangular grid layout.

    The algorithm compares the straight and 90-degree rotated orientations.
    It intentionally does not mix orientations on one sheet; this keeps the
    result understandable and suitable for a simple production estimate.
    """
    values = (sheet_width, sheet_height, part_width, part_height)
    if any(value <= 0 for value in values):
        raise ValueError("Размеры должны быть больше нуля")
    if quantity <= 0:
        raise ValueError("Количество должно быть больше нуля")

    candidates: list[CuttingLayout] = []
    straight = _candidate(
        sheet_width, sheet_height, part_width, part_height, quantity, "Без поворота"
    )
    if straight:
        candidates.append(straight)

    if allow_rotate and part_width != part_height:
        rotated = _candidate(
            sheet_width,
            sheet_height,
            part_height,
            part_width,
            quantity,
            "Поворот 90°",
        )
        if rotated:
            candidates.append(rotated)

    if not candidates:
        raise ValueError("Деталь не помещается на выбранный лист")

    return min(
        candidates,
        key=lambda item: (
            item.sheets,
            -item.pieces_per_sheet,
            item.cut_lines,
            item.orientation != "Без поворота",
        ),
    )


def calculate_totals(
    sheet_price: float,
    sheets: int,
    price_per_cut: float,
    cut_count: int,
    setup_fee: float,
    extras: list[dict],
) -> dict[str, float]:
    material_total = round(sheet_price * sheets, 2)
    cutting_total = round(price_per_cut * cut_count + setup_fee, 2)
    extras_total = round(
        sum(float(item["price"]) * float(item["quantity"]) for item in extras), 2
    )
    grand_total = round(material_total + cutting_total + extras_total, 2)
    return {
        "material": material_total,
        "cutting": cutting_total,
        "extras": extras_total,
        "grand": grand_total,
    }
