"""Spreadsheet-style HTML and Tk previews, backed by the same cost rows."""
from html import escape
import tkinter as tk
from costing import HEADERS, cost_document, summary_rows


def html_document(order, receipt=False):
    doc = cost_document(order, receipt)
    out = ['<!doctype html><html lang="ru"><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>' + escape(doc['title']) + '</title>', '''<style>
@page {size:A4 landscape;margin:10mm}
* {box-sizing:border-box}
body {margin:0;background:#e6e6e6;font:14px Arial,sans-serif;color:#17324d}
main {width:1130px;margin:20px auto;padding:18px 12px;background-color:white;
background-image:linear-gradient(#e7e7e7 1px,transparent 1px),linear-gradient(90deg,#e7e7e7 1px,transparent 1px);background-size:26px 22px}
h1 {font-size:23px;color:#151515;text-align:center;border-bottom:2px solid #333;margin:0 0 16px;background:white;padding:4px}
.product {margin:6px 60px 12px;background:white;padding:3px;overflow-wrap:anywhere}
table {width:100%;table-layout:fixed;border-collapse:collapse;background:white}
th,td {border:1px solid #444;padding:2px 4px;vertical-align:middle}
th {color:#151515;font-size:16px;height:60px;text-align:center}
td:first-child {padding-left:28px;overflow-wrap:anywhere}
td.num {text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.group td {padding-left:3px}
.summary {margin:16px 0 28px 44%;width:56%}
.summary td {border:0;padding:5px 3px;text-align:right}
.summary td:first-child {width:58%;font-weight:bold}
.notes {background:white;font-size:12px;padding:8px;overflow-wrap:anywhere}
thead {display:table-header-group} tr {break-inside:avoid} .summary {break-inside:avoid}
@media screen and (max-width:1150px) {main {margin:0}}
@media print {body {background:white;font-size:10pt} main {width:100%;margin:0;padding:0;background:none} h1 {font-size:16pt} th {font-size:11pt;height:42px} .notes {font-size:9pt}}
</style><main>''', '<h1>' + escape(doc['title']) + '</h1>']
    if order.get('customer'):
        out.append('<p class="product">Заказчик: ' + escape(order['customer']) + '</p>')
    for name, q in doc['products']:
        out.append('<p class="product">Продукция:　 <b>' + escape(name) + '</b><br><br>Количество:　 ' + q + '</p>')
    out.append('<table><colgroup>' + ''.join(f'<col style="width:{w}%">' for w in (44,12,9,12,11.5,11.5)) + '</colgroup><thead><tr>')
    out.extend('<th>' + h + '</th>' for h in HEADERS)
    out.append('</tr></thead><tbody>')
    for group in ('Работы', 'Материалы'):
        out.append('<tr class="group"><td colspan="6">' + group + '</td></tr>')
        for r in doc['lines']:
            if r['group'] == group:
                out.append('<tr>' + ''.join('<td' + (' class="num"' if i else '') + '>' + escape(v) + '</td>' for i,v in enumerate(r['cells'])) + '</tr>')
    out.append('</tbody></table><table class="summary">')
    for row in summary_rows(doc):
        out.append('<tr>' + ''.join('<td>' + escape(v) + '</td>' for v in row) + '</tr>')
    out.append('</table><div class="notes">' + '<br>'.join(escape(n) for n in doc['notes']) + '</div></main></html>')
    return ''.join(out)


def build_preview(parent, order, receipt=False):
    doc = cost_document(order, receipt)
    def label(text, **kw):
        return tk.Label(parent, text=text, bg='white', fg='#17324d', font=('Arial', 10), **kw)
    heading = label(doc['title'], wraplength=1030, justify='center')
    heading.configure(font=('Arial', 16, 'bold'), fg='#151515')
    heading.pack(fill='x', pady=(0, 14))
    tk.Frame(parent, height=2, bg='#333333').pack(fill='x')
    if order.get('customer'):
        label('Заказчик: ' + order['customer'], anchor='w').pack(fill='x', pady=6)
    for name,q in doc['products']:
        label('Продукция:  ' + name + '\nКоличество:  ' + q, anchor='w', justify='left', wraplength=1020).pack(fill='x', padx=40, pady=10)
    grid = tk.Frame(parent, bg='#444444', padx=1, pady=1)
    grid.pack(fill='x', pady=(12, 0))
    widths = (440, 120, 90, 120, 115, 115)
    for i,w in enumerate(widths):
        grid.columnconfigure(i, weight=w, uniform='cost')
    row = 0
    def cells(values, header=False, group=False):
        nonlocal row
        for i,value in enumerate(values):
            l = tk.Label(grid, text=value, bg='white', fg='#151515' if header else '#17324d',
                         font=('Arial', 10, 'bold' if header else 'normal'),
                         anchor='center' if header else ('e' if i else 'w'),
                         justify='center' if header else ('right' if i else 'left'),
                         padx=4 if header or i or group else 24, pady=7 if header else 2)
            l.grid(row=row, column=i, columnspan=6 if group else 1, sticky='nsew', padx=(0,1), pady=(0,1))
            l.bind('<Configure>', lambda e: e.widget.configure(wraplength=max(30,e.width-12)))
        row += 1
    cells(HEADERS, header=True)
    for group in ('Работы', 'Материалы'):
        cells((group,), group=True)
        for r in doc['lines']:
            if r['group'] == group:
                cells(r['cells'])
    summary = tk.Frame(parent, bg='white')
    summary.pack(fill='x', pady=20)
    summary.columnconfigure(0, weight=1)
    for i,values in enumerate(summary_rows(doc)):
        for j,v in enumerate(values):
            tk.Label(summary, text=v, bg='white', fg='#17324d', font=('Arial',10,'bold' if j == 0 else 'normal'),
                     anchor='e', width=17 if j else 0).grid(row=i,column=j,sticky='ew',pady=4)
    for note in doc['notes']:
        label(note, anchor='w', justify='left', wraplength=1030).pack(fill='x',pady=2)
