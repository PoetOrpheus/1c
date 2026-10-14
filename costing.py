"""Shared, reconciled presentation of saved production costs (no recalculation of history)."""
from decimal import Decimal, ROUND_HALF_UP

HEADERS = ("Тип затрат, затрата", "Количество", "Цена без НДС", "Сумма без НДС", "Сумма", "Сумма продажная с НДС")
DEFAULT_FINANCE = dict(vat=0, material_markup=0, work_markup=0)


def dec(value):
    return Decimal(str(value))


def cents(value):
    return dec(value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def fmt(value, places=2):
    return f"{value:,.{places}f}".replace(',', ' ').replace('.', ',')


def cost_document(order, receipt=False):
    finance = DEFAULT_FINANCE | order.get('finance', {})
    for value in finance.values():
        if not dec(value).is_finite() or dec(value) < 0:
            raise ValueError('НДС и наценки должны быть конечными неотрицательными числами')
    divisor = 1 + dec(finance['vat']) / 100
    lines = []

    def add(group, name, quantity, unit, amount):
        lines.append(dict(group=group, name=name, quantity=dec(quantity), unit=unit, cost=cents(amount)))

    c = order['calculation']
    if order.get('laser_rows'):
        for r in order['laser_rows']:
            q = dec(r['quantity']) * dec(r['coef'])
            add('Материалы', r['material'] + ' ' + r.get('characteristic', '') + (' (давальческий)' if r.get('customer_material') else ''),
                q, 'заг.', 0 if r.get('customer_material') else q * dec(r.get('price', 0)))
    else:
        for p in c['parts']:
            m = p.get('material') or {}
            sheet_area = dec(m.get('width', 0)) * dec(m.get('height', 0))
            q = dec(p['width']) * dec(p['height']) * dec(p['total_quantity']) * (1 + dec(p['waste_pct']) / 100) / sheet_area if sheet_area else 0
            add('Материалы', m.get('name', 'Материал не выбран') + ' — ' + p['name'], q, 'лист', p['material_cost'])
    # Preserve the stored material total even when individual imported rows round differently.
    delta = cents(order['totals']['material']) - sum((r['cost'] for r in lines), Decimal(0))
    if delta:
        add('Материалы', 'Округление стоимости материалов', 1, '', delta)
    settings = {s['id']: s['settings'] for s in order['stages']}
    for s in c['stages']:
        if not s['enabled']:
            continue
        supplies = Decimal(0)
        supply_start = len(lines)
        raw_supplies = Decimal(0)
        if s['kind'] == 'painting':
            for title, key in [('Краска', 'paint'), ('Грунтовка', 'primer')]:
                q = s['details'].get(title + ', кг', 0)
                amount = cents(dec(q) * dec(settings[s['id']].get(key + '_price', 0)))
                if q:
                    add('Материалы', title, q, 'кг', amount)
                    supplies += amount
                    raw_supplies += dec(q) * dec(settings[s['id']].get(key + '_price', 0))
            if len(lines) > supply_start:
                rounded_supplies = min(cents(raw_supplies), cents(s['cost']))
                max(lines[supply_start:], key=lambda r: r['cost'])['cost'] += rounded_supplies - supplies
                supplies = rounded_supplies
        cfg = settings[s['id']]
        components = []
        remaining = dec(s['minutes'])
        def component(name, minutes):
            nonlocal remaining
            minutes = min(remaining, max(Decimal(0), dec(minutes)))
            if minutes:
                components.append((name, minutes))
                remaining -= minutes
        if s['otk']:
            component('Контроль ОТК (' + s['name'] + ')', cfg.get('otk_minutes', 0))
        if s['kind'] in ('filing', 'bending'):
            component('Переналадка оборудования (' + s['name'] + ')', cfg.get('setup', 0))
        if s['kind'] == 'painting':
            component('Подготовка к покраске', s['details'].get('Общее время на этап подготовки, мин', 0))
            component('Дополнительные работы (покраска)', s['details'].get('Отвоз изделий, мин', 0))
        if s['kind'] == 'laser' and order.get('laser_rows'):
            for r in order['laser_rows']:
                component('Резка металла (' + (r.get('center') or 'рабочий центр не указан') + ')', r['cut_minutes'])
            component('Дополнительные работы (резка)', remaining)
        if remaining or not components:
            components.append((s['name'], remaining))
        first_work = len(lines)
        fixed = dec(cfg.get('fixed_cost', 0)) if s['kind'] != 'welding' else Decimal(0)
        for name, minutes in components:
            add('Работы', name, minutes, 'мин', minutes * dec(cfg.get('rate', 0)) / 60)
        if fixed:
            add('Работы', 'Дополнительная стоимость (' + s['name'] + ')', 1, 'усл.', fixed)
        # Reconcile against the saved stage, not against rounded displayed norms.
        adjustment = cents(s['cost']) - supplies - sum(r['cost'] for r in lines[first_work:])
        target = max(lines[first_work:], key=lambda r: r['cost'])
        target['cost'] += adjustment
    for e in order['extras']:
        add(e.get('cost_group', 'Работы'), e['name'], e['quantity'], e['unit'], e['sum'])
    for r in lines:
        r['net'] = cents(r['cost'] / divisor)
        r['price'] = cents(r['cost'] / divisor / r['quantity']) if r['quantity'] else Decimal(0)
        markup = finance['material_markup' if r['group'] == 'Материалы' else 'work_markup']
        r['sale'] = cents(r['cost'] * (1 + dec(markup) / 100))
        r['cells'] = (r['name'], fmt(r['quantity'], 3) + ' ' + r['unit'], fmt(r['price']), fmt(r['net']), fmt(r['cost']), fmt(r['sale']))
    totals = {}
    for group in ('Работы', 'Материалы', 'Всего'):
        selected = [r for r in lines if group == 'Всего' or r['group'] == group]
        totals[group] = {key: sum((r[key] for r in selected), Decimal(0)) for key in ('net', 'cost', 'sale')}
    roots = [n for n in c['tree'] if not n.get('parent_id')]
    products = [(n.get('code', '') + ' ' + n['name'], fmt(dec(n['quantity']), 3)) for n in roots]
    title = ('Расчётный чек' if receipt else 'Плановая себестоимость') + f" по заказу №{order['number']} от {order['date']}"
    notes = [f"НДС: {fmt(dec(finance['vat']))}%. Введённые цены и тарифы включают НДС. Наценка: материалы {fmt(dec(finance['material_markup']))}%; работы {fmt(dec(finance['work_markup']))}%.",
             'Цена единицы показана с округлением; суммы рассчитаны до округления цены единицы.',
             c.get('material_basis', '')]
    if len(roots) > 1:
        notes.append('Общие этапы и раскрои относятся ко всему заказу; стоимость между изделиями не распределена.')
    notes += c.get('warnings', [])
    notes += ['Источник: ' + d['filename'] for d in order.get('cam_imports', [])]
    if order.get('note'):
        notes.append(order['note'])
    if receipt:
        notes.append('Документ не является кассовым чеком.')
    else:
        notes.append('Расчёт по заданным нормам. Сварка пока не рассчитывается.')
    return dict(title=title, products=products or [(order['name'], '—')], lines=lines, totals=totals, notes=notes)


def summary_rows(doc):
    total = doc['totals']['Всего']
    result = [('Цена без НДС:', fmt(total['net']), '')]
    result += [('Всего сумма заказа с НДС:', fmt(total['cost']), fmt(total['sale']))]
    for group, label in [('Материалы', 'в т.ч. материальные:'), ('Работы', 'в т.ч. оплата труда:')]:
        result.append((label, fmt(doc['totals'][group]['cost']), fmt(doc['totals'][group]['sale'])))
    return result
