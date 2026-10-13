"""Desktop UI for the cutting and order calculator."""

from __future__ import annotations

import os
import sqlite3
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from calculations import calculate_layout, calculate_totals
from database import Database
from reports import document_html, money, number
from production_ui import ProductionTab


APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
DATA_DIR = APP_DIR / "data"
DOCUMENTS_DIR = APP_DIR / "documents"

COLORS = {
    "yellow": "#f3c400",
    "yellow_dark": "#d7ad00",
    "sidebar": "#3f454a",
    "sidebar_hover": "#52595f",
    "background": "#f1f2f3",
    "card": "#ffffff",
    "border": "#c8cdd2",
    "text": "#25282a",
    "muted": "#6b7278",
    "blue": "#2d70b3",
    "green": "#277a43",
    "red": "#a63b32",
}


def parse_number(value: str, field_name: str) -> float:
    cleaned = value.strip().replace(" ", "").replace(",", ".")
    try:
        result = float(cleaned)
    except ValueError as error:
        raise ValueError(f"Поле «{field_name}» должно содержать число") from error
    if result < 0:
        raise ValueError(f"Поле «{field_name}» не может быть отрицательным")
    return result


def display_number(value: float) -> str:
    return number(float(value))


class DocumentPreview(tk.Toplevel):
    def __init__(self, parent: tk.Widget, order: dict, kind: str):
        super().__init__(parent)
        self.order = order
        self.kind = kind
        title = "Расчётный чек" if kind == "receipt" else "Отчёт по раскрою"
        self.title(title)
        self.geometry("900x760" if kind == "report" else "680x760")
        self.minsize(640, 540)
        self.transient(parent.winfo_toplevel())

        toolbar = ttk.Frame(self, padding=(12, 10))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text=title, style="Title.TLabel").pack(side="left")
        ttk.Button(toolbar, text="Сохранить HTML", command=self.save_as).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(toolbar, text="Печать", command=self.print_document).pack(side="right")

        body = tk.Frame(self, bg="#dfe2e5", padx=12, pady=12)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg="#dfe2e5", highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.page = tk.Frame(
            self.canvas,
            bg="white",
            padx=36 if kind == "report" else 30,
            pady=30,
            highlightbackground="#c7cbd0",
            highlightthickness=1,
        )
        self.page_window = self.canvas.create_window(0, 0, window=self.page, anchor="n")
        self.page.bind(
            "<Configure>",
            lambda _event: self._update_document_scroll(),
        )
        self.canvas.bind("<Configure>", self._resize_page)
        self.canvas.bind(
            "<MouseWheel>",
            lambda event: self.canvas.yview_scroll(int(-event.delta / 120), "units"),
        )

        if order.get("schema_version") == 2:
            self._build_production(kind)
        elif kind == "receipt":
            self._build_receipt()
        else:
            self._build_report()

    def _resize_page(self, event) -> None:
        max_width = 790 if self.kind == "report" else 500
        width = min(max_width, max(500, event.width - 36))
        self.canvas.itemconfigure(self.page_window, width=width)
        self.canvas.coords(self.page_window, event.width / 2, 12)
        self._update_document_scroll()

    def _update_document_scroll(self):
        bounds = self.canvas.bbox("all")
        if bounds:
            self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), bounds[3] + 12))

    def _build_production(self, kind):
        order = self.order
        calculation = order["calculation"]
        self._document_header("Расчёт заказа" if kind == "receipt" else "Спецификация заказа",
                              "РАСЧЁТНЫЙ ЧЕК" if kind == "receipt" else "ПРОИЗВОДСТВО")
        self._info_grid([("Заказчик", order.get("customer") or "Не указан"), ("Изделие", order["name"])])
        if kind != "receipt":
            self._section_title("Дерево спецификации")
            box = tk.Frame(self.page, bg="white")
            box.pack(fill="x")
            self._table_row(box, ("Изделие / деталь", "На узел", "Всего"), header=True)
            for node in calculation["tree"]:
                self._table_row(box, ("  " * node["depth"] + node["name"], number(node["quantity"]), number(node["total_quantity"])))
            self._section_title("Параметры производства")
            self._info_grid([("Площадь покраски", f"{number(calculation['paint_area'])} м²"),
                             ("Масса", f"{number(calculation['mass'])} кг"),
                             ("Количество гибов", number(calculation["bends"])),
                             ("Общее время", f"{number(calculation['minutes'])} мин")])
        self._section_title("Стоимость и этапы")
        box = tk.Frame(self.page, bg="white")
        box.pack(fill="x")
        self._table_row(box, ("Позиция", "Кол-во / мин", "Сумма"), header=True)
        self._table_row(box, ("Материал", "", money(order["totals"]["material"])))
        for stage in calculation["stages"]:
            if stage["enabled"]:
                self._table_row(box, (stage["name"] + (" · ОТК" if stage["otk"] else ""),
                                     f"{number(stage['minutes'])} мин", money(stage["cost"])))
        for item in order["extras"]:
            self._table_row(box, (item["name"], f"{number(item['quantity'])} {item['unit']}", money(item["sum"])))
        self._table_row(box, ("ИТОГО", "", money(order["totals"]["grand"])), total=True)
        if kind != "receipt":
            self._section_title("Нормы и параметры включённых этапов")
            from production import FIELDS, COMMON_FIELDS
            for stage in order["stages"]:
                if not stage["enabled"] or stage["kind"] == "welding":
                    continue
                self._label(self.page, stage["name"], font=("Segoe UI Semibold", 10), anchor="w").pack(fill="x", pady=(12, 4))
                values = [(title, number(stage["settings"][key])) for key, title, _ in FIELDS[stage["kind"]] + COMMON_FIELDS]
                for title, value in values:
                    self._label(self.page, f"{title}: {value}", font=("Segoe UI", 9), anchor="w", wraplength=650, justify="left").pack(fill="x")
        self._label(self.page, calculation["material_basis"], font=("Segoe UI", 9), fg=COLORS["muted"],
                    wraplength=440 if kind == "receipt" else 650, justify="left").pack(fill="x", pady=(16, 4))
        self._label(self.page, "Документ не является кассовым чеком." if kind == "receipt" else "Расчёт по заданным нормам. Сварка пока не рассчитывается.",
                    font=("Segoe UI", 9), fg=COLORS["muted"], wraplength=440 if kind == "receipt" else 650).pack(fill="x")

    @staticmethod
    def _label(parent, text, *, font=("Segoe UI", 10), fg="#25282a", bg="white", **kwargs):
        return tk.Label(parent, text=text, font=font, fg=fg, bg=bg, **kwargs)

    def _document_header(self, title: str, subtitle: str) -> None:
        brand = tk.Frame(self.page, bg=COLORS["yellow"], padx=16, pady=13)
        brand.pack(fill="x", pady=(0, 22))
        self._label(
            brand,
            "РАСКРОЙ · УПРАВЛЕНИЕ",
            font=("Segoe UI Semibold", 11),
            bg=COLORS["yellow"],
        ).pack(side="left")
        self._label(
            brand,
            subtitle,
            font=("Segoe UI", 9),
            fg="#665300",
            bg=COLORS["yellow"],
        ).pack(side="right")
        self._label(
            self.page,
            title,
            font=("Segoe UI Semibold", 21),
            anchor="w",
        ).pack(fill="x")
        self._label(
            self.page,
            f"Заказ {self.order['number']}  ·  {self.order['date']}",
            font=("Segoe UI", 10),
            fg=COLORS["muted"],
            anchor="w",
        ).pack(fill="x", pady=(4, 18))

    def _section_title(self, title: str) -> None:
        frame = tk.Frame(self.page, bg="white")
        frame.pack(fill="x", pady=(18, 8))
        self._label(
            frame,
            title.upper(),
            font=("Segoe UI Semibold", 9),
            fg="#4e555a",
            anchor="w",
        ).pack(side="left")
        tk.Frame(frame, bg="#d7dadd", height=1).pack(side="left", fill="x", expand=True, padx=(10, 0))

    def _info_grid(self, items: list[tuple[str, str]]) -> None:
        grid = tk.Frame(self.page, bg="white")
        grid.pack(fill="x")
        for column in range(2):
            grid.columnconfigure(column, weight=1, uniform="info")
        for index, (caption, value) in enumerate(items):
            cell = tk.Frame(grid, bg="#f5f6f7", padx=12, pady=9)
            cell.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=(0, 5) if index % 2 == 0 else (5, 0),
                pady=4,
            )
            self._label(
                cell,
                caption,
                font=("Segoe UI", 8),
                fg=COLORS["muted"],
                bg="#f5f6f7",
                anchor="w",
            ).pack(fill="x")
            value_label = self._label(
                cell,
                value,
                font=("Segoe UI Semibold", 10),
                bg="#f5f6f7",
                anchor="w",
            )
            value_label.pack(fill="x", pady=(2, 0))
            value_label.bind("<Configure>", lambda event: event.widget.configure(wraplength=max(40, event.width - 4)))

    def _table_row(
        self,
        parent: tk.Widget,
        values: tuple[str, ...],
        *,
        header: bool = False,
        total: bool = False,
    ) -> None:
        background = COLORS["yellow"] if total else ("#eceff1" if header else "white")
        font = ("Segoe UI Semibold", 9 if header else 10) if header or total else ("Segoe UI", 10)
        row = tk.Frame(parent, bg=background)
        row.pack(fill="x")
        widths = (5, 2, 3) if len(values) == 3 else (7, 3)
        for column, (value, weight) in enumerate(zip(values, widths)):
            row.columnconfigure(column, weight=weight, uniform="document_table")
            cell_label = self._label(
                row,
                value,
                font=font,
                bg=background,
                fg=COLORS["muted"] if header else COLORS["text"],
                anchor="e" if column == len(values) - 1 else "w",
                padx=10,
                pady=8,
                wraplength=(210 if self.kind == "receipt" else 320) if column == 0 else 145,
                justify="left" if column == 0 else "right",
            )
            cell_label.grid(row=0, column=column, sticky="ew")
            cell_label.bind("<Configure>", lambda event: event.widget.configure(wraplength=max(30, event.width - 22)))
        if not total:
            tk.Frame(parent, bg="#e1e3e5", height=1).pack(fill="x")

    def _totals(self) -> None:
        totals = self.order["totals"]
        box = tk.Frame(self.page, bg="white")
        box.pack(fill="x")
        for label, value in (
            ("Материал", totals["material"]),
            ("Распил", totals["cutting"]),
            ("Дополнительные услуги", totals["extras"]),
        ):
            self._table_row(box, (label, money(value)))
        self._table_row(box, ("ИТОГО", money(totals["grand"])), total=True)

    def _build_report(self) -> None:
        material = self.order["material"]
        part = self.order["part"]
        layout = self.order["layout"]
        cutting = self.order["cutting"]
        self._document_header("Отчёт по раскрою", "ПРОИЗВОДСТВЕННЫЙ РАСЧЁТ")

        self._section_title("Заказ")
        self._info_grid(
            [
                ("Заказчик", self.order.get("customer") or "Не указан"),
                ("Номер заказа", self.order["number"]),
            ]
        )

        self._section_title("Материал")
        self._info_grid(
            [
                ("Материал", material["name"]),
                ("Толщина", f"{number(material['thickness'])} мм"),
                ("Размер листа", f"{number(material['width'])} × {number(material['height'])} мм"),
                ("Цена листа", money(material["price_sheet"])),
            ]
        )

        self._section_title("Схема раскроя")
        self._info_grid(
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
        )

        self._section_title("Распил")
        self._info_grid(
            [
                ("Вид распила", cutting["name"]),
                ("Количество линий", str(cutting["count"])),
                ("Цена линии", money(cutting["price_per_cut"])),
                ("Подготовка", money(cutting["setup_fee"])),
            ]
        )

        if self.order["extras"]:
            self._section_title("Дополнительные услуги")
            table = tk.Frame(self.page, bg="white")
            table.pack(fill="x")
            self._table_row(table, ("Услуга", "Количество", "Сумма"), header=True)
            for item in self.order["extras"]:
                self._table_row(
                    table,
                    (
                        item["name"],
                        f"{number(item['quantity'])} {item['unit']}",
                        money(item["sum"]),
                    ),
                )

        self._section_title("Стоимость")
        self._totals()
        self._label(
            self.page,
            "Расчёт является предварительным и требует проверки технологом.",
            font=("Segoe UI", 9),
            fg=COLORS["muted"],
            anchor="w",
        ).pack(fill="x", pady=(18, 0))

    def _build_receipt(self) -> None:
        self._document_header("Расчёт заказа", "РАСЧЁТНЫЙ ЧЕК")
        self._label(
            self.page,
            f"Заказчик: {self.order.get('customer') or 'Не указан'}",
            font=("Segoe UI", 10),
            anchor="w",
        ).pack(fill="x", pady=(0, 14))

        table = tk.Frame(self.page, bg="white")
        table.pack(fill="x")
        self._table_row(table, ("Позиция", "Количество", "Сумма"), header=True)
        self._table_row(
            table,
            (
                self.order["material"]["name"],
                f"{self.order['layout']['sheets']} лист.",
                money(self.order["totals"]["material"]),
            ),
        )
        self._table_row(
            table,
            (
                self.order["cutting"]["name"],
                f"{self.order['cutting']['count']} лин.",
                money(self.order["totals"]["cutting"]),
            ),
        )
        for item in self.order["extras"]:
            self._table_row(
                table,
                (
                    item["name"],
                    f"{number(item['quantity'])} {item['unit']}",
                    money(item["sum"]),
                ),
            )
        self._table_row(table, ("ИТОГО", "", money(self.order["totals"]["grand"])), total=True)
        self._label(
            self.page,
            "Спасибо за заказ!",
            font=("Segoe UI Semibold", 12),
            anchor="center",
        ).pack(fill="x", pady=(24, 4))
        self._label(
            self.page,
            "Документ не является кассовым чеком.",
            font=("Segoe UI", 8),
            fg=COLORS["muted"],
            anchor="center",
        ).pack(fill="x")

    def _default_name(self) -> str:
        suffix = "чек" if self.kind == "receipt" else "отчет"
        return f"{self.order['number']}_{suffix}.html"

    def _write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document_html(self.order, self.kind), encoding="utf-8")

    def save_as(self) -> None:
        DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
        chosen = filedialog.asksaveasfilename(
            parent=self,
            title="Сохранить документ",
            initialdir=DOCUMENTS_DIR,
            initialfile=self._default_name(),
            defaultextension=".html",
            filetypes=[("HTML-документ", "*.html")],
        )
        if not chosen:
            return
        self._write(Path(chosen))
        messagebox.showinfo("Готово", "Документ сохранён.", parent=self)

    def print_document(self) -> None:
        try:
            target = DOCUMENTS_DIR / self._default_name()
            self._write(target)
            if os.name == "nt":
                os.startfile(target, "print")  # type: ignore[attr-defined]
            else:
                webbrowser.open(target.as_uri())
            messagebox.showinfo(
                "Печать",
                "Документ передан в систему печати. Если диалог не появился, "
                "откройте HTML-файл из папки documents.",
                parent=self,
            )
        except OSError as error:
            messagebox.showerror("Печать", f"Не удалось запустить печать:\n{error}", parent=self)


class ExtraDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, extras: list[dict]):
        super().__init__(parent)
        self.title("Добавить в заказ")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.result: dict | None = None
        self.records = extras
        self.mapping = {
            f"{item['name']} — {money(item['price'])}/{item['unit']}": item for item in extras
        }

        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Дополнительная услуга", style="Field.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.item_var = tk.StringVar(value=next(iter(self.mapping), ""))
        combo = ttk.Combobox(
            frame,
            textvariable=self.item_var,
            values=list(self.mapping),
            width=46,
            state="readonly",
        )
        combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 13))
        ttk.Label(frame, text="Количество", style="Field.TLabel").grid(
            row=2, column=0, sticky="w"
        )
        self.quantity_var = tk.StringVar(value="1")
        quantity = ttk.Entry(frame, textvariable=self.quantity_var, width=18)
        quantity.grid(row=3, column=0, sticky="w", pady=(4, 18))

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Добавить", style="Accent.TButton", command=self.accept).pack(
            side="left"
        )
        quantity.bind("<Return>", lambda _event: self.accept())
        quantity.focus_set()

    def accept(self) -> None:
        try:
            selected = self.mapping.get(self.item_var.get())
            if not selected:
                raise ValueError("Выберите услугу")
            quantity = parse_number(self.quantity_var.get(), "Количество")
            if quantity <= 0:
                raise ValueError("Количество должно быть больше нуля")
            self.result = {
                "id": selected["id"],
                "name": selected["name"],
                "unit": selected["unit"],
                "price": float(selected["price"]),
                "quantity": quantity,
            }
            self.destroy()
        except ValueError as error:
            messagebox.showwarning("Проверьте данные", str(error), parent=self)


class RecordDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, title: str, fields: list[tuple], record: dict | None):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.result: dict | None = None
        self.fields = fields
        self.variables: dict[str, tk.Variable] = {}

        frame = ttk.Frame(self, padding=18)
        frame.pack(fill="both", expand=True)
        first_entry = None
        for row, (key, label, kind) in enumerate(fields):
            ttk.Label(frame, text=label, style="Field.TLabel").grid(
                row=row, column=0, sticky="w", padx=(0, 16), pady=6
            )
            current = record.get(key) if record else None
            if kind == "bool":
                variable = tk.BooleanVar(value=bool(1 if current is None else current))
                widget = ttk.Checkbutton(frame, variable=variable, text="Используется")
            else:
                if current is None:
                    current = ""
                elif kind == "number":
                    current = display_number(float(current))
                variable = tk.StringVar(value=str(current))
                widget = ttk.Entry(frame, textvariable=variable, width=38)
                if first_entry is None:
                    first_entry = widget
            self.variables[key] = variable
            widget.grid(row=row, column=1, sticky="ew", pady=6)

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Отмена", command=self.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Сохранить", style="Accent.TButton", command=self.accept).pack(
            side="left"
        )
        self.bind("<Return>", lambda _event: self.accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        if first_entry:
            first_entry.focus_set()

    def accept(self) -> None:
        result = {}
        try:
            for key, label, kind in self.fields:
                value = self.variables[key].get()
                if kind == "number":
                    value = parse_number(str(value), label)
                elif kind == "bool":
                    value = int(bool(value))
                else:
                    value = str(value).strip()
                    if not value:
                        raise ValueError(f"Заполните поле «{label}»")
                result[key] = value
            self.result = result
            self.destroy()
        except ValueError as error:
            messagebox.showwarning("Проверьте данные", str(error), parent=self)


REFERENCE_SPECS = {
    "materials": {
        "title": "Материалы",
        "columns": [
            ("name", "Наименование", 260),
            ("thickness", "Толщина, мм", 105),
            ("width", "Ширина, мм", 105),
            ("height", "Высота, мм", 105),
            ("price_sheet", "Цена листа", 120),
            ("active", "Статус", 90),
        ],
        "fields": [
            ("name", "Наименование", "text"),
            ("thickness", "Толщина, мм", "number"),
            ("width", "Ширина листа, мм", "number"),
            ("height", "Высота листа, мм", "number"),
            ("price_sheet", "Цена листа, ₽", "number"),
            ("active", "Активен", "bool"),
        ],
    },
    "cut_types": {
        "title": "Виды распила",
        "columns": [
            ("name", "Наименование", 300),
            ("price_per_cut", "Цена линии", 140),
            ("setup_fee", "Подготовка", 140),
            ("active", "Статус", 100),
        ],
        "fields": [
            ("name", "Наименование", "text"),
            ("price_per_cut", "Цена линии, ₽", "number"),
            ("setup_fee", "Подготовка, ₽", "number"),
            ("active", "Активен", "bool"),
        ],
    },
    "extras": {
        "title": "Дополнительные услуги",
        "columns": [
            ("name", "Наименование", 330),
            ("unit", "Единица", 120),
            ("price", "Цена", 140),
            ("active", "Статус", 100),
        ],
        "fields": [
            ("name", "Наименование", "text"),
            ("unit", "Единица измерения", "text"),
            ("price", "Цена, ₽", "number"),
            ("active", "Активна", "bool"),
        ],
    },
}


class ReferenceManager(ttk.Frame):
    def __init__(self, parent: tk.Widget, db: Database, table: str, on_change):
        super().__init__(parent, padding=12)
        self.db = db
        self.table = table
        self.spec = REFERENCE_SPECS[table]
        self.on_change = on_change
        self.records: dict[int, dict] = {}

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Button(toolbar, text="＋ Добавить", style="Accent.TButton", command=self.add).pack(
            side="left"
        )
        ttk.Button(toolbar, text="Изменить", command=self.edit).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Удалить", command=self.delete).pack(side="left")
        ttk.Label(
            toolbar,
            text="Двойной щелчок открывает карточку",
            style="Muted.TLabel",
        ).pack(side="right")

        columns = [column[0] for column in self.spec["columns"]]
        self.tree = ttk.Treeview(self, columns=columns, show="headings", selectmode="browse")
        for key, title, width in self.spec["columns"]:
            if key in {"price", "price_sheet", "price_per_cut", "setup_fee"}:
                anchor = "e"
            elif key in {"name"}:
                anchor = "w"
            else:
                anchor = "center"
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=width, minwidth=70, anchor=anchor)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.edit())
        self.reload()

    def reload(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.records.clear()
        for record in self.db.list_records(self.table):
            record_id = int(record["id"])
            self.records[record_id] = record
            values = []
            for key, _title, _width in self.spec["columns"]:
                value = record[key]
                if key == "active":
                    value = "Активен" if value else "Отключён"
                elif key.startswith("price") or key == "setup_fee":
                    value = money(float(value))
                elif isinstance(value, float):
                    value = display_number(value)
                values.append(value)
            self.tree.insert("", "end", iid=str(record_id), values=values)

    def selected(self) -> dict | None:
        selection = self.tree.selection()
        return self.records.get(int(selection[0])) if selection else None

    def add(self) -> None:
        dialog = RecordDialog(
            self,
            f"{self.spec['title']}: новый элемент",
            self.spec["fields"],
            None,
        )
        self.wait_window(dialog)
        if dialog.result:
            self.db.insert_record(self.table, dialog.result)
            self.reload()
            self.on_change()

    def edit(self) -> None:
        record = self.selected()
        if not record:
            messagebox.showinfo("Справочник", "Выберите строку для изменения.", parent=self)
            return
        dialog = RecordDialog(
            self,
            f"{self.spec['title']}: изменение",
            self.spec["fields"],
            record,
        )
        self.wait_window(dialog)
        if dialog.result:
            self.db.update_record(self.table, int(record["id"]), dialog.result)
            self.reload()
            self.on_change()

    def delete(self) -> None:
        record = self.selected()
        if not record:
            messagebox.showinfo("Справочник", "Выберите строку для удаления.", parent=self)
            return
        if not messagebox.askyesno(
            "Удаление",
            f"Удалить «{record['name']}»?\n\nДля временного скрытия лучше снять флажок «Активен».",
            parent=self,
        ):
            return
        self.db.delete_record(self.table, int(record["id"]))
        self.reload()
        self.on_change()


class ReferencesTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, db: Database, on_change):
        super().__init__(parent)
        header = ttk.Frame(self, padding=(18, 14, 18, 8))
        header.pack(fill="x")
        ttk.Label(header, text="Справочники и цены", style="PageTitle.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="Настройка доступных позиций заказа",
            style="Muted.TLabel",
        ).pack(side="left", padx=16)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        self.managers = []
        for table in ("materials", "cut_types", "extras"):
            manager = ReferenceManager(notebook, db, table, on_change)
            self.managers.append(manager)
            notebook.add(manager, text=REFERENCE_SPECS[table]["title"])


class OrderTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, db: Database, status_callback, history_callback):
        super().__init__(parent)
        self.db = db
        self.set_status = status_callback
        self.history_callback = history_callback
        self.materials: dict[str, dict] = {}
        self.cut_types: dict[str, dict] = {}
        self.extra_records: list[dict] = []
        self.order_extras: list[dict] = []
        self.last_order: dict | None = None
        self._after_id = None
        self._building = True

        self.number_var = tk.StringVar()
        self.date_var = tk.StringVar()
        self.customer_var = tk.StringVar()
        self.material_var = tk.StringVar()
        self.material_info_var = tk.StringVar(value="Выберите материал")
        self.part_width_var = tk.StringVar(value="600")
        self.part_height_var = tk.StringVar(value="400")
        self.quantity_var = tk.StringVar(value="10")
        self.rotate_var = tk.BooleanVar(value=True)
        self.cut_type_var = tk.StringVar()
        self.manual_cuts_var = tk.BooleanVar(value=False)
        self.cut_count_var = tk.StringVar(value="0")
        self.result_vars = {
            "yield": tk.StringVar(value="—"),
            "sheets": tk.StringVar(value="—"),
            "usage": tk.StringVar(value="—"),
            "cuts": tk.StringVar(value="—"),
        }
        self.total_var = tk.StringVar(value=money(0))

        self._build()
        self.refresh_references()
        self.new_order()
        self._building = False
        for variable in (
            self.part_width_var,
            self.part_height_var,
            self.quantity_var,
            self.cut_count_var,
        ):
            variable.trace_add("write", lambda *_args: self.schedule_calculate())
        self.rotate_var.trace_add("write", lambda *_args: self.schedule_calculate())
        self.manual_cuts_var.trace_add("write", lambda *_args: self._manual_mode_changed())
        self.schedule_calculate()

    def _build(self) -> None:
        header = ttk.Frame(self, padding=(18, 13, 18, 8))
        header.pack(fill="x")
        ttk.Label(header, text="Заказ на раскрой", style="PageTitle.TLabel").pack(side="left")
        ttk.Button(header, text="Новый", command=self.new_order).pack(side="right")
        ttk.Button(header, text="Сохранить заказ", style="Accent.TButton", command=self.save).pack(
            side="right", padx=7
        )

        content = ttk.Frame(self, padding=(18, 0, 18, 18))
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=6)
        content.columnconfigure(1, weight=5)
        content.rowconfigure(0, weight=1)

        left = ttk.Frame(content)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = ttk.Frame(content)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        order_group = ttk.LabelFrame(left, text=" Заказ ", padding=12)
        order_group.pack(fill="x", pady=(0, 10))
        order_group.columnconfigure(1, weight=1)
        self._field_label(order_group, "Номер", 0, 0)
        ttk.Entry(order_group, textvariable=self.number_var, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=(8, 18), pady=4
        )
        self._field_label(order_group, "Дата", 0, 2)
        ttk.Entry(order_group, textvariable=self.date_var, state="readonly", width=14).grid(
            row=0, column=3, sticky="ew", padx=(8, 0), pady=4
        )
        self._field_label(order_group, "Заказчик", 1, 0)
        ttk.Entry(order_group, textvariable=self.customer_var).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=4
        )

        material_group = ttk.LabelFrame(left, text=" Материал ", padding=12)
        material_group.pack(fill="x", pady=(0, 10))
        material_group.columnconfigure(1, weight=1)
        self._field_label(material_group, "Пластина", 0, 0)
        self.material_combo = ttk.Combobox(
            material_group, textvariable=self.material_var, state="readonly"
        )
        self.material_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=4)
        self.material_combo.bind("<<ComboboxSelected>>", lambda _event: self.material_changed())
        ttk.Label(
            material_group,
            textvariable=self.material_info_var,
            style="Info.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(7, 0))

        cutting_group = ttk.LabelFrame(left, text=" Деталь и раскрой ", padding=12)
        cutting_group.pack(fill="x", pady=(0, 10))
        cutting_group.columnconfigure(1, weight=1)
        cutting_group.columnconfigure(3, weight=1)
        self._field_label(cutting_group, "Ширина детали, мм", 0, 0)
        ttk.Entry(cutting_group, textvariable=self.part_width_var, width=14).grid(
            row=0, column=1, sticky="ew", padx=(8, 18), pady=4
        )
        self._field_label(cutting_group, "Высота, мм", 0, 2)
        ttk.Entry(cutting_group, textvariable=self.part_height_var, width=14).grid(
            row=0, column=3, sticky="ew", padx=(8, 0), pady=4
        )
        self._field_label(cutting_group, "Количество, шт.", 1, 0)
        ttk.Entry(cutting_group, textvariable=self.quantity_var, width=14).grid(
            row=1, column=1, sticky="ew", padx=(8, 18), pady=4
        )
        ttk.Checkbutton(
            cutting_group,
            text="Разрешить поворот детали",
            variable=self.rotate_var,
        ).grid(row=1, column=2, columnspan=2, sticky="w", pady=4)
        self._field_label(cutting_group, "Вид распила", 2, 0)
        self.cut_type_combo = ttk.Combobox(
            cutting_group, textvariable=self.cut_type_var, state="readonly"
        )
        self.cut_type_combo.grid(
            row=2, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=4
        )
        self.cut_type_combo.bind("<<ComboboxSelected>>", lambda _event: self.schedule_calculate())
        self._field_label(cutting_group, "Линий распила", 3, 0)
        self.cut_count_entry = ttk.Entry(
            cutting_group, textvariable=self.cut_count_var, width=14, state="readonly"
        )
        self.cut_count_entry.grid(row=3, column=1, sticky="ew", padx=(8, 18), pady=4)
        ttk.Checkbutton(
            cutting_group,
            text="Задать вручную",
            variable=self.manual_cuts_var,
        ).grid(row=3, column=2, columnspan=2, sticky="w", pady=4)

        extra_group = ttk.LabelFrame(left, text=" Дополнительно ", padding=10)
        extra_group.pack(fill="both", expand=True)
        columns = ("name", "quantity", "price", "sum")
        self.extra_tree = ttk.Treeview(extra_group, columns=columns, show="headings", height=5)
        headings = {
            "name": ("Услуга", 230),
            "quantity": ("Количество", 90),
            "price": ("Цена", 95),
            "sum": ("Сумма", 105),
        }
        for key, (title, width) in headings.items():
            anchor = "w" if key == "name" else ("center" if key == "quantity" else "e")
            self.extra_tree.heading(key, text=title, anchor=anchor)
            self.extra_tree.column(key, width=width, anchor=anchor)
        self.extra_tree.pack(fill="both", expand=True)
        extra_buttons = ttk.Frame(extra_group)
        extra_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(extra_buttons, text="＋ Добавить", command=self.add_extra).pack(side="left")
        ttk.Button(extra_buttons, text="Удалить", command=self.remove_extra).pack(
            side="left", padx=6
        )

        summary = ttk.LabelFrame(right, text=" Результат расчёта ", padding=14)
        summary.pack(fill="x")
        grid = ttk.Frame(summary)
        grid.pack(fill="x")
        cards = [
            ("Выход с листа", "yield"),
            ("Нужно листов", "sheets"),
            ("Использование", "usage"),
            ("Линий распила", "cuts"),
        ]
        for index, (caption, key) in enumerate(cards):
            card = tk.Frame(grid, bg="#f7f8f8", highlightbackground=COLORS["border"], highlightthickness=1)
            card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=4, pady=4)
            grid.columnconfigure(index % 2, weight=1)
            tk.Label(
                card,
                text=caption,
                bg="#f7f8f8",
                fg=COLORS["muted"],
                font=("Segoe UI", 9),
            ).pack(anchor="w", padx=12, pady=(9, 0))
            tk.Label(
                card,
                textvariable=self.result_vars[key],
                bg="#f7f8f8",
                fg=COLORS["text"],
                font=("Segoe UI Semibold", 17),
            ).pack(anchor="w", padx=12, pady=(2, 9))

        price_group = ttk.LabelFrame(right, text=" Стоимость ", padding=12)
        price_group.pack(fill="both", expand=True, pady=(12, 0))
        self.price_tree = ttk.Treeview(
            price_group, columns=("item", "amount"), show="headings", height=7
        )
        self.price_tree.heading("item", text="Позиция", anchor="w")
        self.price_tree.heading("amount", text="Сумма", anchor="e")
        self.price_tree.column("item", width=275, anchor="w")
        self.price_tree.column("amount", width=125, anchor="e")
        self.price_tree.pack(fill="both", expand=True)

        total_frame = tk.Frame(price_group, bg=COLORS["yellow"], padx=14, pady=11)
        total_frame.pack(fill="x", pady=(10, 0))
        tk.Label(
            total_frame,
            text="ИТОГО",
            bg=COLORS["yellow"],
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 11),
        ).pack(side="left")
        tk.Label(
            total_frame,
            textvariable=self.total_var,
            bg=COLORS["yellow"],
            fg=COLORS["text"],
            font=("Segoe UI Semibold", 18),
        ).pack(side="right")

        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="Отчёт по раскрою", command=self.show_report).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        ttk.Button(
            actions,
            text="Расчётный чек",
            style="Accent.TButton",
            command=self.show_receipt,
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))

    @staticmethod
    def _field_label(parent, text, row, column) -> None:
        ttk.Label(parent, text=text, style="Field.TLabel").grid(
            row=row, column=column, sticky="w", pady=4
        )

    def refresh_references(self) -> None:
        current_material_id = self.selected_material().get("id") if self.selected_material() else None
        current_cut_id = self.selected_cut_type().get("id") if self.selected_cut_type() else None

        material_records = self.db.list_records("materials", active_only=True)
        self.materials = {
            f"{item['name']} · {display_number(item['width'])}×{display_number(item['height'])} мм": item
            for item in material_records
        }
        self.material_combo.configure(values=list(self.materials))
        selected_name = next(
            (name for name, item in self.materials.items() if item["id"] == current_material_id),
            next(iter(self.materials), ""),
        )
        self.material_var.set(selected_name)

        cut_records = self.db.list_records("cut_types", active_only=True)
        self.cut_types = {item["name"]: item for item in cut_records}
        self.cut_type_combo.configure(values=list(self.cut_types))
        selected_cut = next(
            (name for name, item in self.cut_types.items() if item["id"] == current_cut_id),
            next(iter(self.cut_types), ""),
        )
        self.cut_type_var.set(selected_cut)
        self.extra_records = self.db.list_records("extras", active_only=True)
        self.material_changed()

    def selected_material(self) -> dict | None:
        return self.materials.get(self.material_var.get())

    def selected_cut_type(self) -> dict | None:
        return self.cut_types.get(self.cut_type_var.get())

    def material_changed(self) -> None:
        material = self.selected_material()
        if material:
            self.material_info_var.set(
                f"Лист {display_number(material['width'])} × {display_number(material['height'])} мм  ·  "
                f"толщина {display_number(material['thickness'])} мм  ·  {money(material['price_sheet'])}"
            )
        else:
            self.material_info_var.set("Нет активных материалов. Добавьте материал в справочнике.")
        self.schedule_calculate()

    def _manual_mode_changed(self) -> None:
        self.cut_count_entry.configure(state="normal" if self.manual_cuts_var.get() else "readonly")
        self.schedule_calculate()

    def schedule_calculate(self) -> None:
        if self._building:
            return
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(180, self.calculate)

    def calculate(self, show_error: bool = False) -> dict | None:
        self._after_id = None
        try:
            material = self.selected_material()
            cut_type = self.selected_cut_type()
            if not material:
                raise ValueError("Выберите материал")
            if not cut_type:
                raise ValueError("Выберите вид распила")
            part_width = parse_number(self.part_width_var.get(), "Ширина детали")
            part_height = parse_number(self.part_height_var.get(), "Высота детали")
            quantity_value = parse_number(self.quantity_var.get(), "Количество")
            quantity = int(quantity_value)
            if quantity_value != quantity or quantity <= 0:
                raise ValueError("Количество деталей должно быть целым числом больше нуля")
            layout = calculate_layout(
                float(material["width"]),
                float(material["height"]),
                part_width,
                part_height,
                quantity,
                self.rotate_var.get(),
            )
            if self.manual_cuts_var.get():
                cut_value = parse_number(self.cut_count_var.get(), "Линий распила")
                cut_count = int(cut_value)
                if cut_value != cut_count:
                    raise ValueError("Количество линий распила должно быть целым")
            else:
                cut_count = layout.cut_lines
                self.cut_count_var.set(str(cut_count))

            extras = []
            for item in self.order_extras:
                prepared = dict(item)
                prepared["sum"] = round(prepared["quantity"] * prepared["price"], 2)
                extras.append(prepared)
            totals = calculate_totals(
                float(material["price_sheet"]),
                layout.sheets,
                float(cut_type["price_per_cut"]),
                cut_count,
                float(cut_type["setup_fee"]),
                extras,
            )
            now = datetime.now()
            order = {
                "number": self.number_var.get(),
                "date": self.date_var.get(),
                "created_at": now.isoformat(timespec="seconds"),
                "customer": self.customer_var.get().strip(),
                "material": {
                    "id": material["id"],
                    "name": material["name"],
                    "thickness": float(material["thickness"]),
                    "width": float(material["width"]),
                    "height": float(material["height"]),
                    "price_sheet": float(material["price_sheet"]),
                },
                "part": {
                    "width": part_width,
                    "height": part_height,
                    "quantity": quantity,
                    "allow_rotate": self.rotate_var.get(),
                },
                "layout": layout.to_dict(),
                "cutting": {
                    "id": cut_type["id"],
                    "name": cut_type["name"],
                    "price_per_cut": float(cut_type["price_per_cut"]),
                    "setup_fee": float(cut_type["setup_fee"]),
                    "count": cut_count,
                    "manual": self.manual_cuts_var.get(),
                },
                "extras": extras,
                "totals": totals,
            }
            self.last_order = order
            self._display_result(order)
            self.set_status("Расчёт актуален")
            return order
        except ValueError as error:
            self.last_order = None
            self._clear_result(str(error))
            if show_error:
                messagebox.showwarning("Проверьте заказ", str(error), parent=self)
            return None

    def _display_result(self, order: dict) -> None:
        layout = order["layout"]
        totals = order["totals"]
        self.result_vars["yield"].set(f"{layout['pieces_per_sheet']} шт.")
        self.result_vars["sheets"].set(f"{layout['sheets']} шт.")
        self.result_vars["usage"].set(f"{display_number(layout['utilization_pct'])}%")
        self.result_vars["cuts"].set(str(order["cutting"]["count"]))
        self.total_var.set(money(totals["grand"]))
        self.price_tree.delete(*self.price_tree.get_children())
        rows = [
            (f"Материал · {layout['sheets']} лист.", totals["material"]),
            (f"Распил · {order['cutting']['count']} лин.", totals["cutting"]),
            ("Дополнительные услуги", totals["extras"]),
        ]
        for title, amount in rows:
            self.price_tree.insert("", "end", values=(title, money(amount)))

    def _clear_result(self, error: str) -> None:
        for variable in self.result_vars.values():
            variable.set("—")
        self.total_var.set(money(0))
        self.price_tree.delete(*self.price_tree.get_children())
        self.set_status(error)

    def add_extra(self) -> None:
        if not self.extra_records:
            messagebox.showinfo(
                "Дополнительно",
                "В справочнике нет активных дополнительных услуг.",
                parent=self,
            )
            return
        dialog = ExtraDialog(self, self.extra_records)
        self.wait_window(dialog)
        if dialog.result:
            self.order_extras.append(dialog.result)
            self._reload_extras()

    def remove_extra(self) -> None:
        selection = self.extra_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self.order_extras):
            self.order_extras.pop(index)
            self._reload_extras()

    def _reload_extras(self) -> None:
        self.extra_tree.delete(*self.extra_tree.get_children())
        for index, item in enumerate(self.order_extras):
            item_sum = item["quantity"] * item["price"]
            self.extra_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    item["name"],
                    f"{display_number(item['quantity'])} {item['unit']}",
                    money(item["price"]),
                    money(item_sum),
                ),
            )
        self.schedule_calculate()

    def new_order(self) -> None:
        self.number_var.set(self.db.next_order_number())
        self.date_var.set(datetime.now().strftime("%d.%m.%Y"))
        self.customer_var.set("")
        self.order_extras.clear()
        if hasattr(self, "extra_tree"):
            self._reload_extras()
        self.last_order = None
        self.schedule_calculate()
        self.set_status("Новый заказ")

    def save(self) -> None:
        order = self.calculate(show_error=True)
        if not order:
            return
        try:
            self.db.save_order(order)
        except sqlite3.IntegrityError:
            messagebox.showwarning(
                "Сохранение",
                "Заказ с таким номером уже сохранён. Создан новый номер.",
                parent=self,
            )
            self.number_var.set(self.db.next_order_number())
            return
        self.history_callback()
        messagebox.showinfo(
            "Заказ сохранён",
            f"Заказ {order['number']} записан в базу данных.",
            parent=self,
        )
        self.number_var.set(self.db.next_order_number())
        self.set_status(f"Заказ {order['number']} сохранён")

    def show_report(self) -> None:
        order = self.calculate(show_error=True)
        if order:
            DocumentPreview(self, order, "report")

    def show_receipt(self) -> None:
        order = self.calculate(show_error=True)
        if order:
            DocumentPreview(self, order, "receipt")


class HistoryTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, db: Database):
        super().__init__(parent)
        self.db = db
        self.records: dict[int, dict] = {}
        self.open_specification = None
        header = ttk.Frame(self, padding=(18, 14, 18, 8))
        header.pack(fill="x")
        ttk.Label(header, text="История заказов", style="PageTitle.TLabel").pack(side="left")
        ttk.Button(header, text="Обновить", command=self.refresh).pack(side="right")

        content = ttk.Frame(self, padding=(18, 0, 18, 18))
        content.pack(fill="both", expand=True)
        columns = ("number", "date", "customer", "total")
        self.tree = ttk.Treeview(content, columns=columns, show="headings", selectmode="browse")
        for key, title, width in (
            ("number", "Номер", 210),
            ("date", "Дата и время", 180),
            ("customer", "Заказчик", 360),
            ("total", "Сумма", 150),
        ):
            anchor = "e" if key == "total" else ("w" if key == "customer" else "center")
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=width, anchor=anchor)
        scroll = ttk.Scrollbar(content, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.preview("report"))

        buttons = ttk.Frame(self, padding=(18, 0, 18, 18))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Открыть спецификацию", command=self.edit_specification).pack(side="left", padx=(0, 7))
        ttk.Button(buttons, text="Открыть отчёт", command=lambda: self.preview("report")).pack(
            side="left"
        )
        ttk.Button(
            buttons,
            text="Открыть чек",
            style="Accent.TButton",
            command=lambda: self.preview("receipt"),
        ).pack(side="left", padx=7)
        self.refresh()

    def edit_specification(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("История", "Выберите заказ.", parent=self)
            return
        order_id = int(selection[0])
        order = self.db.get_order(order_id)
        if order and order.get("schema_version") == 2 and self.open_specification:
            self.open_specification(order, order_id)
        else:
            messagebox.showinfo("История", "Это заказ старого простого раскроя. Он доступен в отчёте и чеке.", parent=self)

    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.records.clear()
        for item in self.db.list_orders():
            order_id = int(item["id"])
            self.records[order_id] = item
            try:
                created = datetime.fromisoformat(item["created_at"]).strftime("%d.%m.%Y %H:%M")
            except ValueError:
                created = item["created_at"]
            self.tree.insert(
                "",
                "end",
                iid=str(order_id),
                values=(item["number"], created, item["customer"] or "—", money(item["total"])),
            )

    def preview(self, kind: str) -> None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("История", "Выберите заказ.", parent=self)
            return
        order = self.db.get_order(int(selection[0]))
        if order:
            DocumentPreview(self, order, kind)


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Раскрой: Управление заказами")
        self.geometry(f"{min(1500, self.winfo_screenwidth()-40)}x{min(900, self.winfo_screenheight()-100)}")
        self.minsize(1280, 700)
        self.configure(bg=COLORS["background"])
        self.db = Database(DATA_DIR / "orders.db")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._configure_styles()
        self._build_shell()

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), foreground=COLORS["text"])
        style.configure("TFrame", background=COLORS["background"])
        style.configure("TLabel", background=COLORS["background"])
        style.configure("Field.TLabel", foreground="#42474b")
        style.configure("Muted.TLabel", foreground=COLORS["muted"])
        style.configure("Info.TLabel", background="#fff9da", foreground="#564b12", padding=8)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 13))
        style.configure("PageTitle.TLabel", font=("Segoe UI Semibold", 18))
        style.configure(
            "TButton",
            padding=(12, 7),
            borderwidth=1,
            background="#eceeef",
            relief="solid",
        )
        style.map("TButton", background=[("active", "#dfe2e4")])
        style.configure(
            "Accent.TButton",
            background=COLORS["yellow"],
            foreground="#1d1d1d",
            bordercolor=COLORS["yellow_dark"],
            padding=(14, 7),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#ffd52a"), ("pressed", COLORS["yellow_dark"])],
        )
        style.configure("TLabelframe", background=COLORS["background"], bordercolor=COLORS["border"])
        style.configure(
            "TLabelframe.Label",
            background=COLORS["background"],
            font=("Segoe UI Semibold", 10),
        )
        style.configure("TEntry", fieldbackground="white", padding=6)
        style.configure("TCombobox", fieldbackground="white", padding=5)
        style.configure(
            "Treeview",
            background="white",
            fieldbackground="white",
            rowheight=29,
            bordercolor=COLORS["border"],
        )
        style.configure(
            "Treeview.Heading",
            background="#e5e8ea",
            foreground=COLORS["text"],
            font=("Segoe UI Semibold", 9),
            padding=(7, 7),
        )
        style.map("Treeview", background=[("selected", COLORS["blue"])])
        style.configure("TNotebook", background=COLORS["background"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 9), background="#dfe2e4")
        style.map("TNotebook.Tab", background=[("selected", "white")])

    def _build_shell(self) -> None:
        top = tk.Frame(self, bg=COLORS["yellow"], height=60)
        top.pack(fill="x")
        top.pack_propagate(False)
        tk.Label(
            top,
            text="Р",
            bg="#b8161d",
            fg="white",
            width=2,
            font=("Segoe UI Black", 18),
        ).pack(side="left", padx=(16, 9), pady=10)
        tk.Label(
            top,
            text="РАСКРОЙ: УПРАВЛЕНИЕ",
            bg=COLORS["yellow"],
            fg="#202020",
            font=("Segoe UI Semibold", 16),
        ).pack(side="left")
        tk.Label(
            top,
            text="локальная база заказов",
            bg=COLORS["yellow"],
            fg="#635100",
            font=("Segoe UI", 9),
        ).pack(side="left", padx=14, pady=(29, 0))

        body = tk.Frame(self, bg=COLORS["background"])
        body.pack(fill="both", expand=True)
        sidebar = tk.Frame(body, bg=COLORS["sidebar"], width=155)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        main = ttk.Frame(body)
        main.pack(side="left", fill="both", expand=True)
        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Готово")
        status = tk.Frame(main, bg="#e2e5e7", height=26)
        status.pack(fill="x", side="bottom")
        tk.Label(
            status,
            textvariable=self.status_var,
            bg="#e2e5e7",
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=10, pady=3)
        tk.Label(
            status,
            text="SQLite · данные сохраняются автоматически",
            bg="#e2e5e7",
            fg=COLORS["muted"],
            font=("Segoe UI", 9),
        ).pack(side="right", padx=10)

        self.history_tab = HistoryTab(self.notebook, self.db)
        self.quick_order_tab = OrderTab(
            self.notebook,
            self.db,
            self.set_status,
            self.history_tab.refresh,
        )
        self.order_tab = ProductionTab(self.notebook, self.db, self.set_status,
                                       self.history_tab.refresh, DocumentPreview)
        self.history_tab.open_specification = self.open_specification
        self.references_tab = ReferencesTab(
            self.notebook,
            self.db,
            self.refresh_all_references,
        )
        self.notebook.add(self.order_tab, text="Заказ")
        self.notebook.add(self.history_tab, text="История")
        self.notebook.add(self.references_tab, text="Справочники")
        self.notebook.add(self.quick_order_tab, text="Простой раскрой")

        tk.Label(
            sidebar,
            text="РАЗДЕЛЫ",
            bg=COLORS["sidebar"],
            fg="#aeb5ba",
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w", padx=18, pady=(22, 8))
        self._nav_button(sidebar, "▣  Новый заказ", lambda: self.select_tab(self.order_tab))
        self._nav_button(sidebar, "☷  История заказов", lambda: self.select_tab(self.history_tab))
        self._nav_button(sidebar, "⚙  Справочники", lambda: self.select_tab(self.references_tab))
        tk.Frame(sidebar, bg="#687077", height=1).pack(fill="x", padx=16, pady=18)
        tk.Label(
            sidebar,
            text="Изделия и детали\nв одном заказе.\n\nНормы и тарифы\nзадаются по этапам.",
            justify="left",
            bg=COLORS["sidebar"],
            fg="#c4c9cc",
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=18)

    def refresh_all_references(self):
        self.quick_order_tab.refresh_references()
        self.order_tab.refresh_references()

    def open_specification(self, order, order_id):
        if not self.order_tab.flush():
            self.notebook.select(self.order_tab)
            return
        if self.order_tab.order["nodes"] and not messagebox.askyesno(
            "Открыть спецификацию", "Открыть выбранный заказ вместо текущего черновика? Несохранённые в историю изменения черновика будут заменены.", parent=self):
            return
        self.order_tab.load_order(order, order_id)
        self.notebook.select(self.order_tab)

    def _nav_button(self, parent: tk.Widget, text: str, command) -> None:
        button = tk.Button(
            parent,
            text=text,
            command=command,
            anchor="w",
            bg=COLORS["sidebar"],
            fg="white",
            activebackground=COLORS["sidebar_hover"],
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 10),
            padx=12,
            pady=11,
            cursor="hand2",
        )
        button.pack(fill="x")

    def select_tab(self, tab: ttk.Frame) -> None:
        self.notebook.select(tab)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def on_close(self) -> None:
        if hasattr(self, "order_tab") and not self.order_tab.flush():
            if not messagebox.askyesno("Некорректные параметры", "В полях есть ошибки. Закрыть окно без сохранения некорректных значений?", parent=self):
                return
        self.db.close()
        self.destroy()


def run() -> None:
    app = Application()
    app.mainloop()

