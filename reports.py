"""Human-readable and HTML documents for an order."""

from __future__ import annotations

from html import escape


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def number(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def report_text(order: dict) -> str:
    material = order["material"]
    part = order["part"]
    layout = order["layout"]
    cutting = order["cutting"]
    totals = order["totals"]
    lines = [
        "ОТЧЁТ ПО РАСКРОЮ",
        "=" * 66,
        f"Заказ: {order['number']}     Дата: {order['date']}",
        f"Заказчик: {order.get('customer') or 'не указан'}",
        "",
        "МАТЕРИАЛ",
        f"  {material['name']}, толщина {number(material['thickness'])} мм",
        f"  Лист: {number(material['width'])} × {number(material['height'])} мм",
        f"  Цена листа: {money(material['price_sheet'])}",
        "",
        "ДЕТАЛЬ И СХЕМА РАСКРОЯ",
        f"  Деталь: {number(part['width'])} × {number(part['height'])} мм — {part['quantity']} шт.",
        f"  Ориентация: {layout['orientation']}",
        f"  Сетка: {layout['columns']} × {layout['rows']} деталей на листе",
        f"  Выход с листа: {layout['pieces_per_sheet']} шт.",
        f"  Требуется листов: {layout['sheets']} шт.",
        f"  Использование площади: {number(layout['utilization_pct'])}%",
        f"  Технологический остаток: {number(layout['waste_pct'])}%",
        "",
        "РАСПИЛ",
        f"  {cutting['name']}",
        f"  Линий распила: {cutting['count']} × {money(cutting['price_per_cut'])}",
        f"  Подготовка: {money(cutting['setup_fee'])}",
    ]
    if order["extras"]:
        lines.extend(["", "ДОПОЛНИТЕЛЬНО"])
        for item in order["extras"]:
            lines.append(
                f"  {item['name']}: {number(item['quantity'])} {item['unit']} × "
                f"{money(item['price'])} = {money(item['sum'])}"
            )
    lines.extend(
        [
            "",
            "СТОИМОСТЬ",
            f"  Материал:            {money(totals['material'])}",
            f"  Распил:              {money(totals['cutting'])}",
            f"  Дополнительные:      {money(totals['extras'])}",
            "  " + "-" * 46,
            f"  ИТОГО:               {money(totals['grand'])}",
            "",
            "Расчёт раскроя является предварительным и требует проверки технологом.",
        ]
    )
    return "\n".join(lines)


def receipt_text(order: dict) -> str:
    totals = order["totals"]
    lines = [
        "РАСЧЁТ ЗАКАЗА",
        "-" * 42,
        f"Заказ {order['number']}",
        f"Дата: {order['date']}",
        f"Заказчик: {order.get('customer') or '—'}",
        "-" * 42,
        f"{order['material']['name']}",
        f"Листы: {order['layout']['sheets']} шт.   {money(totals['material'])}",
        f"{order['cutting']['name']}",
        f"Распил: {order['cutting']['count']} лин.   {money(totals['cutting'])}",
    ]
    for item in order["extras"]:
        lines.append(f"{item['name']} × {number(item['quantity'])}   {money(item['sum'])}")
    lines.extend(
        [
            "-" * 42,
            f"ИТОГО: {money(totals['grand'])}",
            "-" * 42,
            "Спасибо за заказ!",
        ]
    )
    return "\n".join(lines)


def document_html(order: dict, kind: str) -> str:
    if order.get("schema_version") == 2:
        from cost_print import html_document
        return html_document(order, kind == "receipt")
    is_receipt = kind == "receipt"
    title = "Расчёт заказа" if is_receipt else "Отчёт по раскрою"
    page_class = "page receipt" if is_receipt else "page"
    page_size = "A5" if is_receipt else "A4"
    content = _production_html(order, is_receipt) if order.get("schema_version") == 2 else (_receipt_html(order) if is_receipt else _report_html(order))
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    @page {{ size:{page_size}; margin:12mm; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:#e5e7e9; color:#25282a;
      font-family:'Segoe UI',Arial,sans-serif; font-size:14px; line-height:1.4; }}
    .page {{ width:calc(100% - 32px); max-width:820px; margin:24px auto; padding:38px 42px;
      background:#fff; box-shadow:0 3px 18px rgba(0,0,0,.18); }}
    .page.receipt {{ max-width:500px; padding:30px 32px; }}
    .brand {{ display:flex; justify-content:space-between; align-items:center; gap:16px;
      padding:14px 16px; margin-bottom:24px; background:#f3c400; font-weight:700; }}
    .brand small {{ color:#665300; font-weight:500; letter-spacing:.04em; }}
    h1 {{ margin:0; font-size:28px; line-height:1.15; }}
    .meta {{ margin:6px 0 20px; color:#6b7278; }}
    .section-title {{ display:flex; align-items:center; gap:10px; margin:24px 0 10px;
      color:#4e555a; font-size:12px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; }}
    .section-title::after {{ content:''; height:1px; background:#d7dadd; flex:1; }}
    .info-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 10px; }}
    .info {{ padding:10px 12px; background:#f5f6f7; overflow-wrap:anywhere; }}
    .info .label {{ margin-bottom:3px; color:#6b7278; font-size:12px; }}
    .info .value {{ font-weight:600; }}
    table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
    th,td {{ padding:9px 10px; border-bottom:1px solid #e1e3e5; text-align:left;
      vertical-align:top; overflow-wrap:anywhere; }}
    th {{ background:#eceff1; color:#5b6267; font-size:12px; font-weight:700; }}
    th.qty,td.qty {{ width:23%; text-align:center; }}
    th.amount,td.amount {{ width:28%; text-align:right; white-space:nowrap; }}
    .costs td:first-child {{ width:70%; }}
    .grand td {{ border:0; background:#f3c400; font-size:16px; font-weight:700; }}
    .note {{ margin-top:20px; color:#6b7278; font-size:12px; }}
    .thanks {{ margin:26px 0 4px; text-align:center; font-size:17px; font-weight:700; }}
    .legal {{ color:#6b7278; font-size:11px; text-align:center; }}
    tr {{ break-inside:avoid; }}
    thead {{ display:table-header-group; }}
    .section-title {{ break-after:avoid; }}
    @media (max-width:600px) {{
      .page,.page.receipt {{ width:100%; margin:0; padding:24px 20px; box-shadow:none; }}
      .info-grid {{ grid-template-columns:1fr; }}
      .brand {{ align-items:flex-start; flex-direction:column; gap:3px; }}
    }}
    @media print {{
      body {{ background:#fff; }}
      .page,.page.receipt {{ width:100%; max-width:none; margin:0; padding:0; box-shadow:none; }}
      .info,.brand,.grand td,th {{ print-color-adjust:exact; -webkit-print-color-adjust:exact; }}
    }}
  </style>
</head>
<body><main class="{page_class}">{content}</main></body>
</html>"""


def _safe(value) -> str:
    return escape(str(value))


def _production_html(order, receipt=False):
    from production import FIELDS, COMMON_FIELDS
    c = order["calculation"]
    parts = [_heading(order, "Расчёт заказа" if receipt else "Спецификация заказа", "РАСЧЁТНЫЙ ЧЕК" if receipt else "ПРОИЗВОДСТВО"),
             _info_grid([("Заказчик", order.get("customer") or "Не указан"), ("Изделие", order["name"])])]
    if not receipt:
        parts += [_section("Дерево спецификации"), '<table><thead><tr><th>Изделие / деталь</th><th class="qty">На узел</th><th class="qty">Всего</th></tr></thead><tbody>']
        for n in c["tree"]:
            parts.append(f'<tr><td style="padding-left:{10+n["depth"]*18}px">{_safe(n["name"])}</td><td class="qty">{number(n["quantity"])}</td><td class="qty">{number(n["total_quantity"])}</td></tr>')
        parts += ['</tbody></table>', _section("Детали и материалы"), '<table><thead><tr><th>Деталь / материал / размер</th><th class="qty">Площадь, м²</th><th class="qty">Масса, кг</th></tr></thead><tbody>']
        for p in c["parts"]:
            parts.append(f'<tr><td>{_safe(p["name"])}<br><small>{_safe(p.get("material", {}).get("name", "—"))} · {number(p["width"])} × {number(p["height"])} × {number(p["thickness"])} мм</small></td><td class="qty">{number(p["surface_area"])}</td><td class="qty">{number(p["total_mass"])}</td></tr>')
        parts += ['</tbody></table>', _info_grid([("Площадь покраски", f"{number(c['paint_area'])} м²"), ("Количество гибов", number(c["bends"])),
                                              ("Всего деталей", number(c["quantity"])), ("Общее время", f"{number(c['minutes'])} мин")])]
    parts += [_section("Стоимость и этапы"), '<table><thead><tr><th>Позиция</th><th class="qty">Время / кол-во</th><th class="amount">Сумма</th></tr></thead><tbody>',
              f'<tr><td>Материал</td><td></td><td class="amount">{money(order["totals"]["material"])}</td></tr>']
    for s in c["stages"]:
        if s["enabled"]:
            title = s["name"] + (" · ОТК" if s["otk"] else "") + (" · " + s["description"] if s["description"] else "")
            parts.append(f'<tr><td>{_safe(title)}</td><td class="qty">{number(s["minutes"])} мин</td><td class="amount">{money(s["cost"])}</td></tr>')
    for e in order["extras"]:
        parts.append(f'<tr><td>{_safe(e["name"])}</td><td class="qty">{number(e["quantity"])} {_safe(e["unit"])}</td><td class="amount">{money(e["sum"])}</td></tr>')
    parts += [f'<tr class="grand"><td>ИТОГО</td><td></td><td class="amount">{money(order["totals"]["grand"])}</td></tr></tbody></table>']
    if not receipt:
        for stage in order["stages"]:
            if not stage["enabled"] or stage["kind"] == "welding":
                continue
            parts += [_section("Параметры: " + stage["name"]), '<table><tbody>']
            for key, title, _ in FIELDS[stage["kind"]] + COMMON_FIELDS:
                parts.append(f'<tr><td>{_safe(title)}</td><td class="amount">{number(stage["settings"][key])}</td></tr>')
            result = next(s for s in c["stages"] if s["id"] == stage["id"])
            for title, value in result["details"].items():
                parts.append(f'<tr><td>{_safe(title)}</td><td class="amount">{number(value)}</td></tr>')
            parts += ['</tbody></table>']
        if order["laser_rows"]:
            parts += [_section("Строки лазерного раскроя"), '<table><thead><tr><th>Материал / рабочий центр</th><th class="qty">Расход</th><th class="qty">Резка, мин</th></tr></thead><tbody>']
            for r in order["laser_rows"]:
                parts.append(f'<tr><td>{_safe(r["material"])} · {_safe(r["characteristic"])}<br>{_safe(r["center"])}</td><td class="qty">{number(r["quantity"])}</td><td class="qty">{number(r["cut_minutes"])}</td></tr>')
            parts += ['</tbody></table>']
        parts += [f'<p class="note">Комментарий технолога: {_safe(order["laser_comment"])}</p>']
        if c["warnings"]:
            parts += ['<p class="note">' + '<br>'.join(_safe(w) for w in c["warnings"]) + '</p>']
        if order.get("cam_imports"):
            parts += [_section("Источники импорта")]
            for source in order["cam_imports"]:
                parts.append(f'<p class="note">{_safe(source["filename"])} · {_safe(source["family"])} · {_safe(source["program"])}</p>')
    if receipt and c["warnings"]:
        parts.append('<p class="note">В расчёте есть замечания или незаполненные данные. Перед оплатой проверьте полный отчёт.</p>')
    parts += [f'<p class="note">{_safe(c["material_basis"])}</p>',
              '<p class="legal">Документ не является кассовым чеком.</p>' if receipt else '<p class="note">Расчёт по заданным нормам. Сварка пока не рассчитывается.</p>']
    return ''.join(parts)


def _brand(label: str) -> str:
    return (
        '<div class="brand"><span>РАСКРОЙ · УПРАВЛЕНИЕ</span>'
        f'<small>{_safe(label)}</small></div>'
    )


def _heading(order: dict, title: str, label: str) -> str:
    return (
        _brand(label)
        + f"<h1>{_safe(title)}</h1>"
        + f'<div class="meta">Заказ {_safe(order["number"])} &nbsp;·&nbsp; '
        + f'{_safe(order["date"])}</div>'
    )


def _section(title: str) -> str:
    return f'<div class="section-title">{_safe(title)}</div>'


def _info_grid(items: list[tuple[str, str]]) -> str:
    cards = "".join(
        '<div class="info">'
        f'<div class="label">{_safe(label)}</div>'
        f'<div class="value">{_safe(value)}</div>'
        "</div>"
        for label, value in items
    )
    return f'<div class="info-grid">{cards}</div>'


def _extras_table(extras: list[dict]) -> str:
    rows = "".join(
        "<tr>"
        f'<td>{_safe(item["name"])}</td>'
        f'<td class="qty">{_safe(number(item["quantity"]))} {_safe(item["unit"])}</td>'
        f'<td class="amount">{_safe(money(item["sum"]))}</td>'
        "</tr>"
        for item in extras
    )
    return (
        "<table><thead><tr><th>Услуга</th><th class=\"qty\">Количество</th>"
        '<th class="amount">Сумма</th></tr></thead><tbody>'
        + rows
        + "</tbody></table>"
    )


def _totals_table(order: dict) -> str:
    totals = order["totals"]
    rows = "".join(
        f'<tr><td>{_safe(label)}</td><td class="amount">{_safe(money(value))}</td></tr>'
        for label, value in (
            ("Материал", totals["material"]),
            ("Распил", totals["cutting"]),
            ("Дополнительные услуги", totals["extras"]),
        )
    )
    return (
        '<table class="costs"><tbody>'
        + rows
        + f'<tr class="grand"><td>ИТОГО</td><td class="amount">{_safe(money(totals["grand"]))}</td></tr>'
        + "</tbody></table>"
    )


def _report_html(order: dict) -> str:
    material = order["material"]
    part = order["part"]
    layout = order["layout"]
    cutting = order["cutting"]
    parts = [
        _heading(order, "Отчёт по раскрою", "ПРОИЗВОДСТВЕННЫЙ РАСЧЁТ"),
        _section("Заказ"),
        _info_grid(
            [
                ("Заказчик", order.get("customer") or "Не указан"),
                ("Номер заказа", order["number"]),
            ]
        ),
        _section("Материал"),
        _info_grid(
            [
                ("Материал", material["name"]),
                ("Толщина", f"{number(material['thickness'])} мм"),
                ("Размер листа", f"{number(material['width'])} × {number(material['height'])} мм"),
                ("Цена листа", money(material["price_sheet"])),
            ]
        ),
        _section("Схема раскроя"),
        _info_grid(
            [
                ("Размер детали", f"{number(part['width'])} × {number(part['height'])} мм"),
                ("Количество", f"{part['quantity']} шт."),
                ("Ориентация", layout["orientation"]),
                ("Сетка на листе", f"{layout['columns']} × {layout['rows']}"),
                ("Выход с листа", f"{layout['pieces_per_sheet']} шт."),
                ("Требуется листов", f"{layout['sheets']} шт."),
                ("Использование площади", f"{number(layout['utilization_pct'])}%"),
                ("Технологический остаток", f"{number(layout['waste_pct'])}%"),
            ]
        ),
        _section("Распил"),
        _info_grid(
            [
                ("Вид распила", cutting["name"]),
                ("Количество линий", str(cutting["count"])),
                ("Цена линии", money(cutting["price_per_cut"])),
                ("Подготовка", money(cutting["setup_fee"])),
            ]
        ),
    ]
    if order["extras"]:
        parts.extend([_section("Дополнительные услуги"), _extras_table(order["extras"])])
    parts.extend(
        [
            _section("Стоимость"),
            _totals_table(order),
            '<div class="note">Расчёт является предварительным и требует проверки технологом.</div>',
        ]
    )
    return "".join(parts)


def _receipt_html(order: dict) -> str:
    rows = [
        (
            order["material"]["name"],
            f"{order['layout']['sheets']} лист.",
            money(order["totals"]["material"]),
        ),
        (
            order["cutting"]["name"],
            f"{order['cutting']['count']} лин.",
            money(order["totals"]["cutting"]),
        ),
    ]
    rows.extend(
        (item["name"], f"{number(item['quantity'])} {item['unit']}", money(item["sum"]))
        for item in order["extras"]
    )
    row_html = "".join(
        "<tr>"
        f"<td>{_safe(name)}</td>"
        f'<td class="qty">{_safe(quantity)}</td>'
        f'<td class="amount">{_safe(amount)}</td>'
        "</tr>"
        for name, quantity, amount in rows
    )
    return (
        _heading(order, "Расчёт заказа", "РАСЧЁТНЫЙ ЧЕК")
        + f'<div class="meta">Заказчик: {_safe(order.get("customer") or "Не указан")}</div>'
        + "<table><thead><tr><th>Позиция</th><th class=\"qty\">Количество</th>"
        + '<th class="amount">Сумма</th></tr></thead><tbody>'
        + row_html
        + f'<tr class="grand"><td>ИТОГО</td><td></td><td class="amount">'
        + _safe(money(order["totals"]["grand"]))
        + "</td></tr></tbody></table>"
        + '<div class="thanks">Спасибо за заказ!</div>'
        + '<div class="legal">Документ не является кассовым чеком.</div>'
    )
