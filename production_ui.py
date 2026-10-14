"""Compact desktop production specification editor."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from queue import Queue, Empty
from threading import Thread
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from production import (COMMON_FIELDS, FIELDS, new_order, new_node, numeric, uid,
                        flatten_nodes, descendants, move_node, copy_branch, calculate_production, transfer_laser_matches)
from reports import money, number


def label(parent, text, **kw):
    return ttk.Label(parent, text=text, style="Prod.TLabel", **kw)


def button(parent, text, command, accent=False):
    b = ttk.Button(parent, text=text, command=command,
                   style="ProdAccent.TButton" if accent else "Prod.TButton")
    b.pack(side="left", padx=(0, 5), pady=3)
    return b


def table(parent, columns, *, tree=False, height=10):
    host = ttk.Frame(parent, style="Prod.TFrame")
    host.pack(fill="both", expand=True)
    host.rowconfigure(0, weight=1)
    host.columnconfigure(0, weight=1)
    widget = ttk.Treeview(host, columns=[c[0] for c in columns], show="tree headings" if tree else "headings",
                         style="Prod.Treeview", selectmode="browse", height=height)
    for key, title, width, anchor in columns:
        widget.heading(key, text=title, anchor=anchor)
        widget.column(key, width=width, minwidth=width, stretch=anchor == "w", anchor=anchor)
    vertical = ttk.Scrollbar(host, command=widget.yview)
    horizontal = ttk.Scrollbar(host, orient="horizontal", command=widget.xview)
    widget.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    widget.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    widget.tag_configure("assembly", background="#eef8fa", font=("Segoe UI Semibold", 9))
    widget.tag_configure("alternate", background="#f6fcfd")
    widget.tag_configure("disabled", foreground="#969696")
    return widget


class FormDialog(tk.Toplevel):
    """Fields: key, label, default, type (text/number/int/bool/choices)."""
    def __init__(self, parent, title, fields, on_save, note=""):
        super().__init__(parent)
        self.title(title)
        self.geometry(f"720x{min(840, 170 + len(fields) * 36)}")
        self.minsize(620, 380)
        self.transient(parent.winfo_toplevel())
        self.fields, self.on_save, self.variables = fields, on_save, {}
        self.configure(bg="white")
        footer = ttk.Frame(self, padding=10)
        footer.pack(side="bottom", fill="x")
        self.error = tk.StringVar()
        ttk.Label(footer, textvariable=self.error, foreground="#c62828", wraplength=640).pack(fill="x")
        button(footer, "Записать и закрыть", self.save, True)
        button(footer, "Отмена", self.destroy)
        canvas = tk.Canvas(self, background="white", highlightthickness=0)
        scroll = ttk.Scrollbar(self, command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        form = ttk.Frame(canvas, style="Prod.TFrame", padding=14)
        window = canvas.create_window(0, 0, anchor="nw", window=form)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        form.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        form.columnconfigure(1, weight=1)
        if note:
            label(form, note, wraplength=650, foreground="#6b7278").grid(row=0, columnspan=2, sticky="w", pady=(0, 12))
        for row, (key, title, value, kind) in enumerate(fields, 1):
            var = tk.BooleanVar(value=value) if kind == "bool" else tk.StringVar(value=str(value))
            self.variables[key] = var
            label(form, title).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=5)
            if kind == "bool":
                entry = ttk.Checkbutton(form, variable=var, style="Prod.TCheckbutton")
            elif isinstance(kind, list):
                entry = ttk.Combobox(form, textvariable=var, values=kind, state="readonly", width=34)
            else:
                entry = ttk.Entry(form, textvariable=var, width=34, style="Prod.TEntry")
            entry.grid(row=row, column=1, sticky="ew", pady=5)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: self.save())
        self.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.grab_set()

    def save(self):
        try:
            values = {}
            for key, title, _, kind in self.fields:
                value = self.variables[key].get()
                if kind in ("number", "int"):
                    value = numeric(value, title, integer=kind == "int")
                values[key] = value.strip() if isinstance(value, str) else value
            self.on_save(values)
        except (ValueError, KeyError) as error:
            self.error.set(str(error))
            return
        self.destroy()

    def destroy(self):
        parent = self.master
        super().destroy()
        if isinstance(parent, tk.Toplevel) and parent.winfo_exists():
            parent.grab_set()


class ProductionTab(ttk.Frame):
    def __init__(self, parent, db, status, history_refresh, preview):
        super().__init__(parent, style="Prod.TFrame")
        self.db, self.status, self.history_refresh, self.preview = db, status, history_refresh, preview
        draft = db.get_state("production_draft")
        self.order = draft["order"] if draft else new_order(db.next_order_number())
        if not draft and db.get_state("production_stage_defaults"):
            self.order["stages"] = db.get_state("production_stage_defaults")
        self.saved_id = draft.get("saved_id") if draft else None
        self.pending = None
        self.loading = False
        self.setting_vars, self.stage_views, self.stage_flags, self.result_vars = {}, {}, {}, {}
        self._styles()
        self._header()
        self._footer()
        self.book = ttk.Notebook(self, style="Prod.TNotebook")
        self.book.pack(fill="both", expand=True, padx=8, pady=4)
        self.tabs = {}
        for key, title in [("tree", "Дерево спецификации"), ("stages", "Выбор этапов"),
                           ("laser", "Лазерная резка"), ("filing", "Напилка"), ("bending", "Гибка"),
                           ("welding", "Сварка"), ("painting", "Покраска"), ("assembly", "Сборка"),
                           ("packing", "Упаковка"), ("services", "Услуги переработки")]:
            tab = ttk.Frame(self.book, style="Prod.TFrame", padding=8)
            self.tabs[key] = tab
            self.book.add(tab, text=title)
        self._tree_tab()
        self._stages_tab()
        self._laser_tab()
        for kind in ("filing", "bending", "welding", "painting", "assembly", "packing"):
            self._settings_tab(kind)
        self._services_tab()
        self._load_widgets()

    def _styles(self):
        s = ttk.Style(self)
        s.configure("Prod.TFrame", background="white")
        s.configure("Prod.TLabel", background="white", foreground="#344964", font=("Segoe UI", 9))
        s.configure("Prod.TCheckbutton", background="white", font=("Segoe UI", 9))
        s.configure("Prod.TButton", padding=(8, 3), font=("Segoe UI", 9))
        s.configure("ProdAccent.TButton", background="#ffdf58", padding=(9, 3), font=("Segoe UI", 9))
        s.map("ProdAccent.TButton", background=[("active", "#ffe98a")])
        s.configure("Prod.TEntry", fieldbackground="#e0f9fc", padding=(4, 3), font=("Segoe UI", 9))
        s.configure("Prod.Treeview", rowheight=26, foreground="#254666", font=("Segoe UI", 9), borderwidth=1)
        s.configure("Prod.Treeview.Heading", background="#f2f2f2", foreground="#53677e", padding=(5, 5), font=("Segoe UI", 9))
        s.map("Prod.Treeview", background=[("selected", "#fff2c4")], foreground=[("selected", "#254666")])
        s.configure("Prod.TNotebook", background="white")
        s.configure("Prod.TNotebook.Tab", padding=(7, 5), font=("Segoe UI", 9))

    def _header(self):
        frame = ttk.Frame(self, style="Prod.TFrame", padding=(10, 6))
        frame.pack(fill="x")
        self.number_var = tk.StringVar(value=self.order["number"])
        self.customer_var = tk.StringVar(value=self.order["customer"])
        self.name_var = tk.StringVar(value=self.order["name"])
        label(frame, "Спецификация заказа", font=("Segoe UI Semibold", 13)).pack(side="left", padx=(0, 12))
        label(frame, "№").pack(side="left")
        ttk.Entry(frame, textvariable=self.number_var, width=23, state="readonly").pack(side="left", padx=5)
        label(frame, "Заказчик:").pack(side="left", padx=(10, 5))
        ttk.Entry(frame, textvariable=self.customer_var, width=28, style="Prod.TEntry").pack(side="left")
        label(frame, "Изделие:").pack(side="left", padx=(10, 5))
        ttk.Entry(frame, textvariable=self.name_var, style="Prod.TEntry").pack(side="left", fill="x", expand=True)
        self.customer_var.trace_add("write", self._changed)
        self.name_var.trace_add("write", self._changed)

    def _footer(self):
        footer = ttk.Frame(self, style="Prod.TFrame", padding=8)
        footer.pack(side="bottom", fill="x")
        self.error_var = tk.StringVar()
        label(footer, "", textvariable=self.error_var, foreground="#ba3529", wraplength=1100).pack(fill="x")
        bar = ttk.Frame(footer, style="Prod.TFrame")
        bar.pack(fill="x")
        button(bar, "Записать заказ", self.save_order, True)
        button(bar, "Отчёт", lambda: self.show_document("report"))
        button(bar, "Расчётный чек", lambda: self.show_document("receipt"))
        button(bar, "НДС и наценки", self.edit_finance)
        button(bar, "Новый заказ", self.reset)
        self.total_var = tk.StringVar()
        label(bar, "", textvariable=self.total_var, font=("Segoe UI Semibold", 11)).pack(side="right")

    def edit_finance(self):
        from costing import DEFAULT_FINANCE
        values = DEFAULT_FINANCE | self.order.get("finance", {})
        def save(result):
            self.order["finance"] = result
            self._changed()
        FormDialog(self, "Настройки стоимости заказа", [
            ("vat", "Ставка НДС, %", values["vat"], "number"),
            ("material_markup", "Наценка на материалы, %", values["material_markup"], "number"),
            ("work_markup", "Наценка на работы, %", values["work_markup"], "number")], save,
            "Все цены и тарифы вводятся с НДС. НДС выделяется из стоимости, а не добавляется сверху. "
            "Наценки определяют продажную сумму. Настройки сохраняются вместе с заказом.")

    def _tree_tab(self):
        tab = self.tabs["tree"]
        bar = ttk.Frame(tab, style="Prod.TFrame")
        bar.pack(fill="x")
        button(bar, "+ Изделие / узел", lambda: self.edit_node(kind="assembly"), True)
        button(bar, "+ Деталь", self.edit_node, True)
        button(bar, "Изменить", lambda: self.edit_node(edit=True))
        button(bar, "Копировать", self.duplicate)
        button(bar, "Переместить", self.reparent)
        button(bar, "↑", lambda: self.reorder(-1))
        button(bar, "↓", lambda: self.reorder(1))
        button(bar, "Удалить", self.remove_node)
        label(tab, "Количество задаётся на один родительский узел. Двойной щелчок открывает карточку позиции.",
              foreground="#7c8792").pack(anchor="w", pady=(4, 8))
        self.tree = table(tab, [("code", "Обозначение", 100, "w"), ("quantity", "Кол-во", 65, "center"),
                               ("total", "Всего", 65, "center"), ("material", "Материал", 180, "w"),
                               ("size", "Размеры, мм", 125, "center"), ("area", "Площадь, м²", 100, "e"),
                               ("mass", "Масса, кг", 90, "e"), ("bends", "Гибов", 60, "center")], tree=True)
        self.tree.heading("#0", text="Изделие / узел / деталь", anchor="w")
        self.tree.column("#0", width=270, minwidth=220)
        self.tree.bind("<Double-1>", lambda e: self.edit_node(edit=True))
        self.tree.bind("<Delete>", lambda e: self.remove_node())
        self.tree_summary = tk.StringVar()
        label(tab, "", textvariable=self.tree_summary).pack(anchor="w", pady=8)

    def selected_node(self):
        selected = self.tree.selection()
        return next((n for n in self.order["nodes"] if selected and n["id"] == selected[0]), None)

    def _parent_choices(self, excluded=()):
        return {"— Корень заказа —": "", **{f"{n['name']} [{n['id'][:6]}]": n["id"]
                for n in self.order["nodes"] if n["kind"] == "assembly" and n["id"] not in excluded}}

    def edit_node(self, kind="part", edit=False):
        selected = self.selected_node()
        if edit and not selected:
            return
        if not self.flush():
            return
        parent = selected["id"] if selected and selected["kind"] == "assembly" else (selected.get("parent_id", "") if selected else "")
        node = deepcopy(selected) if edit else new_node(kind, parent)
        parents = self._parent_choices(descendants(self.order["nodes"], node["id"]) if edit else ())
        materials = {f"{m['name']} · {number(m['thickness'])} мм [№{m['id']}]": m
                     for m in self.db.list_records("materials", active_only=True)}
        if node.get("material"):
            m = node["material"]
            materials.setdefault(f"{m['name']} · {number(m['thickness'])} мм [№{m['id']}]", m)
        chosen_material = next((key for key, m in materials.items() if m.get("id") == node.get("material", {}).get("id")), "— Не выбран —")
        fields = [("name", "Наименование", node["name"], "text"), ("code", "Обозначение / артикул", node["code"], "text"),
                  ("parent", "Родительский узел", next(k for k, v in parents.items() if v == node["parent_id"]), list(parents)),
                  ("quantity", "Количество на один узел, шт.", node["quantity"], "int")]
        if node["kind"] == "part":
            fields += [("material_choice", "Материал", chosen_material, ["— Не выбран —", *materials]),
                       ("width", "Ширина заготовки, мм", node["width"], "number"),
                       ("height", "Высота заготовки, мм", node["height"], "number"),
                       ("thickness", "Толщина, мм", node["thickness"], "number"),
                       ("area", "Площадь покраски одной детали, м² (0 = две стороны)", node["area"], "number"),
                       ("mass", "Масса одной детали, кг", node["mass"], "number"),
                       ("bends", "Количество гибов на одной детали", node["bends"], "int"),
                       ("filing_minutes", "Напилка одной детали, мин (0 = норма этапа)", node["filing_minutes"], "number"),
                       ("laser_minutes", "Резка одной детали, мин", node["laser_minutes"], "number"),
                       ("waste_pct", "Припуск к расходу материала, %", node["waste_pct"], "number"),
                       ("paint", "Включать в площадь покраски", node["paint"], "bool")]
        fields += [("note", "Комментарий", node["note"], "text")]

        def save(values):
            numeric(values["quantity"], "Количество", positive=True, integer=True)
            if not values["name"]:
                raise ValueError("Укажите наименование")
            values["parent_id"] = parents[values.pop("parent")]
            if node["kind"] == "part":
                numeric(values["width"], "Ширина", positive=True)
                numeric(values["height"], "Высота", positive=node.get("stock_kind") != "tube")
                values["material"] = deepcopy(materials.get(values.pop("material_choice"), {}))
                if values["material"] and values["material"].get("id") != node.get("material", {}).get("id"):
                    values["thickness"] = values["material"]["thickness"]
            node.update(values)
            if edit:
                self.order["nodes"][self.order["nodes"].index(selected)] = node
            else:
                self.order["nodes"].append(node)
            self.refresh()
            self.tree.selection_set(node["id"])
            self.tree.see(node["id"])

        FormDialog(self, "Карточка изделия" if node["kind"] == "assembly" else "Карточка детали", fields, save,
                   "Размеры и нормы времени указываются для одной детали. Материал и его цена сохраняются в заказе.")

    def duplicate(self):
        node = self.selected_node()
        if node and self.flush():
            key = copy_branch(self.order["nodes"], node["id"])
            self.refresh()
            self.tree.selection_set(key)

    def reparent(self):
        node = self.selected_node()
        if not node or not self.flush():
            return
        choices = self._parent_choices(descendants(self.order["nodes"], node["id"]))
        def save(values):
            move_node(self.order["nodes"], node["id"], choices[values["parent"]])
            self.refresh()
        FormDialog(self, "Переместить позицию", [("parent", "Новый родитель", next(iter(choices)), list(choices))], save)

    def reorder(self, direction):
        node = self.selected_node()
        if not node:
            return
        siblings = [n for n in self.order["nodes"] if n["parent_id"] == node["parent_id"]]
        target = siblings.index(node) + direction
        if 0 <= target < len(siblings):
            a, b = self.order["nodes"].index(node), self.order["nodes"].index(siblings[target])
            self.order["nodes"][a], self.order["nodes"][b] = self.order["nodes"][b], self.order["nodes"][a]
            self.refresh()
            self.tree.selection_set(node["id"])

    def remove_node(self):
        node = self.selected_node()
        if node and messagebox.askyesno("Удалить позицию", f"Удалить «{node['name']}» вместе с вложенными деталями?", parent=self):
            keys = descendants(self.order["nodes"], node["id"])
            self.order["nodes"] = [n for n in self.order["nodes"] if n["id"] not in keys]
            for row in self.order["laser_unmatched"]:
                if row.get("node_id") in keys:
                    row["node_id"] = ""
            self.refresh()

    def _stages_tab(self):
        tab = self.tabs["stages"]
        bar = ttk.Frame(tab, style="Prod.TFrame")
        bar.pack(fill="x")
        button(bar, "Настроить этап", self.edit_stage, True)
        button(bar, "Сохранить нормы по умолчанию", self.save_stage_defaults)
        label(bar, "Щелчок по флагу — включить. Двойной щелчок — настройка.").pack(side="left", padx=12)
        self.stages_tree = table(tab, [("n", "N", 38, "center"), ("name", "Имя этапа в дереве спецификации", 290, "w"),
                                      ("title", "Название этапа", 215, "w"), ("enabled", "Включить", 75, "center"),
                                      ("otk", "Есть ОТК", 75, "center"), ("description", "Расшифровка", 160, "w")])
        self.stages_tree.bind("<Button-1>", self.toggle_stage)
        self.stages_tree.bind("<Double-1>", self.stage_double_click)
        self.stages_tree.bind("<space>", lambda e: self.toggle_selected_stage())

    def save_stage_defaults(self):
        if self.flush():
            self.db.set_state("production_stage_defaults", self.order["stages"])
            self.status("Флаги, нормы и тарифы этапов сохранены для новых заказов")
            messagebox.showinfo("Нормы по умолчанию", "Текущие флаги, нормы и тарифы будут применяться к новым заказам. Существующие заказы сохраняют свои настройки.", parent=self)

    def stage_double_click(self, event):
        if self.stages_tree.identify_column(event.x) not in ("#4", "#5"):
            self.edit_stage()

    def toggle_selected_stage(self):
        selection = self.stages_tree.selection()
        if selection:
            self._toggle(selection[0], "enabled")
        return "break"

    def toggle_stage(self, event):
        key = self.stages_tree.identify_row(event.y)
        column = self.stages_tree.identify_column(event.x)
        if key and column in ("#4", "#5"):
            self.stages_tree.selection_set(key)
            self._toggle(key, "enabled" if column == "#4" else "otk")
            return "break"

    def _toggle(self, key, field):
        if not self.flush():
            return
        stage = next(s for s in self.order["stages"] if s["id"] == key)
        stage[field] = not stage[field]
        self._load_widgets()
        self.stages_tree.selection_set(key)

    def edit_stage(self, stage=None):
        if not self.flush():
            return
        if stage is None:
            selection = self.stages_tree.selection()
            if not selection:
                return
            stage = next(s for s in self.order["stages"] if s["id"] == selection[0])
        fields = [("name", "Имя этапа в дереве спецификации", stage["name"], "text"),
                  ("enabled", "Включить", stage["enabled"], "bool"), ("otk", "Есть ОТК", stage["otk"], "bool"),
                  ("description", "Расшифровка", stage["description"], "text")]
        if stage["kind"] != "welding":
            fields += [(key, title, stage["settings"][key], "number") for key, title, _ in FIELDS[stage["kind"]] + COMMON_FIELDS]
        def save(values):
            if not values["name"]:
                raise ValueError("Укажите имя этапа")
            if stage["kind"] == "painting" and values["oven_area"] <= 0:
                raise ValueError("Площадь печи должна быть больше нуля")
            for key in ("name", "enabled", "otk", "description"):
                stage[key] = values.pop(key)
            stage["settings"].update(values)
            self._load_widgets()
        FormDialog(self, "Настройка этапа: " + stage["name"], fields, save,
                   "Стоимость = время / 60 × тариф + дополнительная стоимость. ОТК учитывается только при включённом флаге."
                   if stage["kind"] != "welding" else "Сварка пока без параметров и расчёта стоимости.")

    def _scroll_form(self, parent):
        canvas = tk.Canvas(parent, bg="white", highlightthickness=0)
        scroll = ttk.Scrollbar(parent, command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        frame = ttk.Frame(canvas, style="Prod.TFrame")
        window = canvas.create_window(0, 0, anchor="nw", window=frame)
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        def wheel(event):
            widget = event.widget
            while widget is not None:
                if widget == frame:
                    canvas.yview_scroll(int(-event.delta / 120), "units")
                    return "break"
                widget = getattr(widget, "master", None)
        binding = self.winfo_toplevel().bind("<MouseWheel>", wheel, add="+")
        frame.bind("<Destroy>", lambda e: self.winfo_toplevel().unbind("<MouseWheel>", binding) if e.widget == frame else None)
        return frame

    def _settings_tab(self, kind):
        if kind == "welding":
            return  # Deliberately empty, as requested.
        form = self._scroll_form(self.tabs[kind])
        for stage in self.order["stages"]:
            if stage["kind"] == kind:
                self._stage_form(form, stage)

    def _stage_form(self, parent, stage):
        section = ttk.Frame(parent, style="Prod.TFrame", padding=(0, 4, 10, 14))
        section.pack(fill="x", anchor="n")
        bar = ttk.Frame(section, style="Prod.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        caption = label(bar, stage["name"], foreground="#00a878", font=("Segoe UI", 12))
        caption.pack(side="left", padx=(0, 20))
        self.stage_views[stage["id"]] = caption
        flags = {}
        for key, title in (("enabled", "Включить"), ("otk", "Есть ОТК")):
            var = tk.BooleanVar(value=stage[key])
            flags[key] = var
            ttk.Checkbutton(bar, text=title, variable=var, style="Prod.TCheckbutton", command=self._changed).pack(side="left", padx=6)
        self.stage_flags[stage["id"]] = flags
        form = ttk.Frame(section, style="Prod.TFrame")
        form.pack(anchor="w", fill="x")
        settings = {}
        row = 0
        for key, title, default in FIELDS[stage["kind"]] + COMMON_FIELDS:
            if stage["kind"] == "painting" and key in ("prep_per_m2", "transport_pct"):
                label(form, "Этап подготовки к покраске" if key == "prep_per_m2" else "Этап покраски",
                      foreground="#00a878", font=("Segoe UI", 11)).grid(row=row, columnspan=2, sticky="w", pady=(6, 5))
                row += 1
            var = tk.StringVar(value=stage["settings"].get(key, default))
            settings[key] = var
            label(form, title + ":").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
            ttk.Entry(form, textvariable=var, width=13, justify="right", style="Prod.TEntry").grid(row=row, column=1, sticky="w", pady=4)
            var.trace_add("write", self._changed)
            row += 1
            if stage["kind"] == "painting" and key == "volume_coef":
                self.paint_prep_var = tk.StringVar()
                label(form, "Общее время на этап подготовки (мин):", foreground="#c53525").grid(row=row, column=0, sticky="w", pady=4)
                ttk.Entry(form, textvariable=self.paint_prep_var, width=13, justify="right", state="readonly").grid(row=row, column=1, sticky="w", pady=4)
                row += 1
        self.setting_vars[stage["id"]] = settings
        result = tk.StringVar()
        self.result_vars[stage["id"]] = result
        label(section, "", textvariable=result, foreground="#b42d21", justify="left").pack(anchor="w", pady=8)
        explanations = {
            "filing": "Время = подготовка + сумма (время напилки детали × её итоговое количество). Нулевое время детали заменяется нормой этапа.",
            "bending": "Время = подготовка + общее число гибов × плановое время на один гиб. Гибы задаются в карточках деталей.",
            "painting": "Подготовка = площадь × норма × коэффициент. Циклы = округление площади / площади печи вверх.\nПокраска = циклы × время цикла; отвоз = время покраски × процент / 100. Расходы краски и грунта = площадь × нормы.",
            "assembly": "Укажите время на весь этап для всего заказа. Каждая сборка рассчитывается независимо.",
            "packing": "Укажите общее время упаковки всего заказа, без умножения на количество деталей.",
        }
        if stage["kind"] in explanations:
            label(section, explanations[stage["kind"]], foreground="#7c8792", wraplength=950).pack(anchor="w")

    def _laser_tab(self):
        tab = self.tabs["laser"]
        bar = ttk.Frame(tab, style="Prod.TFrame")
        bar.pack(fill="x")
        label(bar, "Имя каталога:").pack(side="left", padx=(0, 10))
        self.directory_var = tk.StringVar(value=self.order["laser_directory"])
        ttk.Entry(bar, textvariable=self.directory_var, width=30, style="Prod.TEntry").pack(side="left")
        button(bar, "…", self.choose_directory)
        button(bar, "Импорт файлов…", self.import_files)
        self.area_only_var = tk.BooleanVar(value=self.order["laser_area_only"])
        ttk.Checkbutton(bar, text="Только площадь и массу", variable=self.area_only_var,
                        style="Prod.TCheckbutton", command=self._changed).pack(side="left", padx=8)
        button(bar, "Импорт папки", self.import_directory)
        self.directory_var.trace_add("write", self._changed)
        label(tab, "Импорт: BIT (XLSX / PDF), TruTops (HTML), Sekirus (PDF). Перед добавлением — просмотр и сопоставление деталей.", foreground="#6b7278").pack(anchor="w", pady=4)
        params = ttk.Frame(tab, style="Prod.TFrame")
        params.pack(fill="x")
        stage = next(s for s in self.order["stages"] if s["kind"] == "laser")
        settings = {}
        for index, (key, title, default) in enumerate(FIELDS["laser"]):
            row, col = divmod(index, 2)
            label(params, title + ":").grid(row=row, column=col * 2, sticky="w", padx=(0, 6), pady=3)
            var = tk.StringVar(value=stage["settings"][key])
            settings[key] = var
            ttk.Entry(params, textvariable=var, width=8, justify="right", style="Prod.TEntry").grid(row=row, column=col*2+1, padx=(0, 18))
            var.trace_add("write", self._changed)
        self.setting_vars[stage["id"]] = settings
        button(bar, "Тариф / ОТК", lambda: self.edit_stage(next(s for s in self.order["stages"] if s["kind"] == "laser")))
        panes = ttk.Panedwindow(tab, orient="vertical")
        panes.pack(fill="both", expand=True, pady=(4, 0))
        upper, lower = ttk.Frame(panes, style="Prod.TFrame"), ttk.Frame(panes, style="Prod.TFrame")
        panes.add(upper, weight=3)
        panes.add(lower, weight=2)
        toolbar = ttk.Frame(upper, style="Prod.TFrame")
        toolbar.pack(fill="x")
        button(toolbar, "Добавить", self.edit_laser)
        button(toolbar, "Изменить", lambda: self.edit_laser(True))
        button(toolbar, "Удалить", lambda: self.remove_row("laser_rows", self.laser_tree))
        button(toolbar, "↑", lambda: self.reorder_row("laser_rows", self.laser_tree, -1))
        button(toolbar, "↓", lambda: self.reorder_row("laser_rows", self.laser_tree, 1))
        button(toolbar, "Источники импорта", self.show_import_sources)
        self.laser_search = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.laser_search, width=22).pack(side="right", padx=5)
        label(toolbar, "Поиск:").pack(side="right")
        self.laser_search.trace_add("write", lambda *a: self.refresh_laser())
        self.laser_tree = table(upper, [("n", "N", 35, "center"), ("material", "Материал", 230, "w"),
            ("characteristic", "Характеристика", 115, "w"), ("thickness", "Толщина", 75, "center"),
            ("coef", "Коэф.", 60, "e"), ("quantity", "Количество", 80, "e"), ("waste_pct", "% отходов", 80, "e"),
            ("center", "Вид рабочего центра", 170, "w"), ("cut_minutes", "Резка, мин", 85, "e"),
            ("sheets", "Листов", 65, "center"), ("change", "Смена, мин", 85, "e"), ("weight", "Вес ед., кг", 85, "e"),
            ("extra_minutes", "Доп., мин", 85, "e"), ("total", "Всего, мин", 90, "e"),
            ("blank", "Минимальная заготовка", 165, "w"), ("customer_material", "Сырьё давальца", 110, "center")], height=5)
        self.laser_tree.bind("<Double-1>", lambda e: self.edit_laser(True))
        comment = ttk.Frame(lower, style="Prod.TFrame")
        comment.pack(fill="x", pady=5)
        label(comment, "Комментарий технолога:").pack(side="left", padx=(0, 10))
        self.comment_var = tk.StringVar(value=self.order["laser_comment"])
        ttk.Entry(comment, textvariable=self.comment_var, width=50, style="Prod.TEntry").pack(side="left")
        self.comment_var.trace_add("write", self._changed)
        label(lower, "Нераспределённое время по изделиям", foreground="#00a878", font=("Segoe UI", 11)).pack(anchor="w")
        line = ttk.Frame(lower, style="Prod.TFrame")
        line.pack(fill="x")
        self.include_var = tk.BooleanVar(value=self.order["laser_include_filled"])
        ttk.Checkbutton(line, text="Включая заполненное время резки", variable=self.include_var, style="Prod.TCheckbutton", command=self._changed).pack(side="left")
        button(line, "Добавить", self.edit_unmatched)
        button(line, "Изменить", lambda: self.edit_unmatched(True))
        button(line, "Удалить", lambda: self.remove_row("laser_unmatched", self.unmatched_tree))
        button(line, "Перенести по сопоставленным изделиям", self.transfer_times)
        content = ttk.Frame(lower, style="Prod.TFrame")
        content.pack(fill="both", expand=True)
        totals = ttk.Frame(content, style="Prod.TFrame", padding=(14, 0))
        totals.pack(side="right", fill="y")
        self.laser_totals = tk.StringVar()
        label(totals, "Итоги:", foreground="#6b7278").pack(anchor="w")
        label(totals, "", textvariable=self.laser_totals, justify="left").pack(anchor="w", pady=5)
        self.unmatched_tree = table(content, [("name", "Наименование в файле", 170, "w"),
            ("quantity", "Количество", 75, "center"), ("minutes", "Время резки", 85, "e"),
            ("match", "Список для сопоставления", 185, "w"), ("area", "Площадь 1 шт.", 95, "e"), ("mass", "Масса 1 шт.", 90, "e")], height=4)
        self.unmatched_tree.bind("<Double-1>", lambda e: self.edit_unmatched(True))

    def choose_directory(self):
        chosen = filedialog.askdirectory(parent=self, title="Каталог файлов лазерной резки")
        if chosen:
            self.directory_var.set(chosen)

    def import_files(self):
        paths = filedialog.askopenfilenames(parent=self, title="Выберите один или несколько отчётов раскроя",
            initialdir=self.directory_var.get() or None,
            filetypes=[("Отчёты раскроя", "*.xlsx *.pdf *.html *.htm"), ("Все файлы", "*.*")])
        if paths:
            self.directory_var.set(str(Path(paths[0]).parent))
            self.start_import(paths)

    def import_directory(self):
        from cam_import import SUPPORTED
        directory = self.directory_var.get().strip()
        if not directory or not Path(directory).is_dir():
            directory = filedialog.askdirectory(parent=self, title="Папка с отчётами раскроя")
        if not directory:
            return
        self.directory_var.set(directory)
        try:
            paths = [p for p in Path(directory).iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED]
        except OSError as error:
            messagebox.showerror("Импорт папки", str(error), parent=self)
            return
        if not paths:
            messagebox.showinfo("Импорт папки", "В выбранной папке нет XLSX, PDF или HTML. Вложенные папки не просматриваются.", parent=self)
            return
        self.start_import(paths)

    def start_import(self, paths):
        if not self.flush():
            return
        if len(paths) > 200:
            messagebox.showerror("Импорт", "Выберите не больше 200 файлов за один раз.", parent=self)
            return
        from cam_import import read_batch
        from cam_import_ui import ImportPreview
        progress = tk.Toplevel(self)
        progress.title("Чтение файлов раскроя")
        progress.geometry("600x160")
        progress.transient(self.winfo_toplevel())
        progress.grab_set()
        progress.protocol("WM_DELETE_WINDOW", lambda: None)
        text = tk.StringVar(value="Чтение данных…")
        ttk.Label(progress, textvariable=text, wraplength=560).pack(padx=18, pady=15)
        bar = ttk.Progressbar(progress, mode="determinate", maximum=len(paths))
        bar.pack(fill="x", padx=18)
        queue = Queue()
        def worker():
            try:
                batch = read_batch(paths, lambda index, total, name: queue.put(("progress", (index, total, name))))
                queue.put(("done", batch))
            except Exception as error:
                queue.put(("error", str(error)))
        def poll():
            if not self.winfo_exists():
                return
            try:
                while True:
                    kind, payload = queue.get_nowait()
                    if kind == "progress":
                        index, total, name = payload
                        text.set(f"{index+1} из {total}: {name}")
                        bar["value"] = index
                    else:
                        progress.destroy()
                        if kind == "error":
                            messagebox.showerror("Импорт не выполнен", payload, parent=self)
                        else:
                            ImportPreview(self, payload)
                        return
            except Empty:
                self.after(80, poll)
        Thread(target=worker, daemon=True).start()
        self.after(80, poll)

    def show_import_sources(self):
        imports = self.order.get("cam_imports", [])
        if not imports:
            messagebox.showinfo("Источники импорта", "В этом заказе ещё нет импортированных раскроев.", parent=self)
            return
        window = tk.Toplevel(self)
        window.title("Источники и замечания импорта")
        window.geometry("950x650")
        window.transient(self.winfo_toplevel())
        box = tk.Text(window, wrap="word", padx=12, pady=12, font=("Segoe UI", 10))
        scroll = ttk.Scrollbar(window, command=box.yview)
        scroll.pack(side="right", fill="y")
        box.configure(yscrollcommand=scroll.set)
        box.pack(fill="both", expand=True)
        for doc in imports:
            box.insert("end", f"{doc['filename']} · {doc['family']} · {doc['program']}\n{doc['source']}\n")
            box.insert("end", "\n".join(doc["warnings"]) + "\n")
            for part in doc["parts"]:
                mass = part.get("source_mass", part.get("mass"))
                box.insert("end", f"  {part['name']}: {part['quantity']} шт.; исходная масса 1 шт.: {mass if mass is not None else 'нет'} кг\n")
            box.insert("end", "\n")
        box.configure(state="disabled")

    def edit_laser(self, edit=False):
        selected = self.laser_tree.selection()
        old = next((r for r in self.order["laser_rows"] if selected and r["id"] == selected[0]), None)
        if edit and not old:
            return
        row = deepcopy(old) if edit else dict(id=uid(), material="", characteristic="", thickness=1, coef=1,
               quantity=1, waste_pct=0, center="", cut_minutes=0, sheets=1, weight=0, extra_minutes=0,
               blank="", customer_material=False, price=0)
        materials = {f"{m['name']} [№{m['id']}]": m for m in self.db.list_records("materials", active_only=True)}
        fields = [("catalog", "Подставить материал из справочника", "— Оставить ручные значения —", ["— Оставить ручные значения —", *materials])]
        titles = [("material", "Материал", "text"), ("characteristic", "Характеристика / формат листа", "text"),
                  ("thickness", "Толщина, мм", "number"), ("coef", "Коэффициент расхода", "number"),
                  ("quantity", "Расход материала, ед.", "number"), ("price", "Цена единицы материала, ₽", "number"),
                  ("waste_pct", "Процент отходов (справочно)", "number"), ("center", "Вид рабочего центра", "text"),
                  ("cut_minutes", "Время резки всей строки, мин", "number"), ("sheets", "Количество сменяемых листов", "int"),
                  ("weight", "Вес единицы материала, кг", "number"), ("extra_minutes", "Дополнительное время, мин", "number"),
                  ("blank", "Минимальная заготовка", "text"), ("customer_material", "Сырьё давальца (материал без оплаты)", "bool")]
        fields += [(k, title, row[k], kind) for k, title, kind in titles]
        def save(values):
            material = materials.get(values.pop("catalog"))
            if material:
                values.update(material=material["name"], characteristic=f"{number(material['width'])} × {number(material['height'])}",
                              thickness=material["thickness"], price=material["price_sheet"])
            if not values["material"]:
                raise ValueError("Укажите материал или выберите его в справочнике")
            if values["waste_pct"] > 100:
                raise ValueError("Процент отходов должен быть от 0 до 100")
            row.update(values)
            if edit:
                self.order["laser_rows"][self.order["laser_rows"].index(old)] = row
            else:
                self.order["laser_rows"].append(row)
            self.refresh()
        FormDialog(self, "Строка лазерного раскроя", fields, save,
                   "При наличии строк раскроя стоимость материала берётся только из них: расход × коэффициент × цена.")

    def edit_unmatched(self, edit=False):
        selected = self.unmatched_tree.selection()
        old = next((r for r in self.order["laser_unmatched"] if selected and r["id"] == selected[0]), None)
        if edit and not old:
            return
        row = deepcopy(old) if edit else dict(id=uid(), name="", quantity=1, minutes=0, node_id="", area=0, mass=0)
        choices = {"— Не сопоставлено —": "", **{f"{n['name']} [{n['id'][:6]}]": n["id"] for n in self.order["nodes"] if n["kind"] == "part"}}
        fields = [("name", "Наименование в файле", row["name"], "text"), ("quantity", "Количество в файле", row["quantity"], "int"),
                  ("minutes", "Общее время резки строки, мин", row["minutes"], "number"),
                  ("match", "Сопоставить с деталью", next((k for k,v in choices.items() if v == row["node_id"]), next(iter(choices))), list(choices)),
                  ("area", "Площадь покраски одной детали, м²", row["area"], "number"), ("mass", "Масса одной детали, кг", row["mass"], "number")]
        def save(values):
            numeric(values["quantity"], "Количество", positive=True, integer=True)
            if not values["name"]:
                raise ValueError("Укажите наименование")
            values["node_id"] = choices[values.pop("match")]
            if edit:
                for field, flag in (("minutes", "missing_time"), ("area", "missing_area"), ("mass", "missing_mass")):
                    if values[field] != old[field]:
                        row[flag] = False
            row.update(values)
            if edit:
                self.order["laser_unmatched"][self.order["laser_unmatched"].index(old)] = row
            else:
                self.order["laser_unmatched"].append(row)
            self.refresh()
        FormDialog(self, "Сопоставление детали", fields, save)

    def transfer_times(self):
        if not self.flush():
            return
        count = transfer_laser_matches(self.order)
        self.refresh()
        messagebox.showinfo("Сопоставление", f"Обновлено деталей: {count}.\nВремя на одну деталь = время сопоставленных строк / их количество.\nПовторный перенос заменяет значения и не суммирует их.", parent=self)

    def remove_row(self, key, widget):
        selection = widget.selection()
        if selection and messagebox.askyesno("Удалить строку", "Удалить выбранную строку?", parent=self):
            self.order[key] = [r for r in self.order[key] if r["id"] != selection[0]]
            self.refresh()

    def reorder_row(self, key, widget, direction):
        selection = widget.selection()
        if not selection:
            return
        rows = self.order[key]
        i = next(i for i,r in enumerate(rows) if r["id"] == selection[0])
        j = i + direction
        if 0 <= j < len(rows):
            rows[i], rows[j] = rows[j], rows[i]
            self.refresh()
            widget.selection_set(selection[0])

    def _services_tab(self):
        tab = self.tabs["services"]
        bar = ttk.Frame(tab, style="Prod.TFrame")
        bar.pack(fill="x")
        button(bar, "Добавить услугу", self.edit_service, True)
        button(bar, "Изменить", lambda: self.edit_service(True))
        button(bar, "Удалить", lambda: self.remove_row("extras", self.extras_tree))
        label(tab, "Кооперация, дополнительные и вспомогательные этапы настраиваются двойным щелчком в «Выборе этапов».",
              foreground="#6b7278").pack(anchor="w", pady=8)
        self.extras_tree = table(tab, [("name", "Услуга", 350, "w"), ("unit", "Единица", 80, "center"),
                                     ("quantity", "Количество", 100, "center"), ("price", "Цена", 130, "e"), ("sum", "Сумма", 130, "e")])
        self.extras_tree.bind("<Double-1>", lambda e: self.edit_service(True))

    def edit_service(self, edit=False):
        selected = self.extras_tree.selection()
        old = next((r for r in self.order["extras"] if selected and r["id"] == selected[0]), None)
        if edit and not old:
            return
        row = deepcopy(old) if edit else dict(id=uid(), name="", unit="шт.", quantity=1, price=0)
        choices = {f"{r['name']} [№{r['id']}]": r for r in self.db.list_records("extras", active_only=True)}
        fields = [("catalog", "Из справочника", "— Ввести вручную —", ["— Ввести вручную —", *choices]),
                  ("name", "Наименование", row["name"], "text"), ("unit", "Единица", row["unit"], "text"),
                  ("quantity", "Количество", row["quantity"], "number"), ("price", "Цена, ₽", row["price"], "number"),
                  ("cost_group", "Раздел отчёта", row.get("cost_group", "Работы"), ["Работы", "Материалы"])]
        def save(values):
            ref = choices.get(values.pop("catalog"))
            if ref:
                values.update({k: ref[k] for k in ("name", "unit", "price")})
            if not values["name"]:
                raise ValueError("Укажите наименование услуги")
            numeric(values["quantity"], "Количество", positive=True)
            row.update(values)
            if edit:
                self.order["extras"][self.order["extras"].index(old)] = row
            else:
                self.order["extras"].append(row)
            self.refresh()
        FormDialog(self, "Услуга переработки", fields, save)

    def _changed(self, *args):
        if self.loading:
            return
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(350, self._recalculate)

    def _read_widgets(self):
        order = deepcopy(self.order)
        order.update(customer=self.customer_var.get().strip(), name=self.name_var.get().strip(),
                     laser_directory=self.directory_var.get(), laser_comment=self.comment_var.get(),
                     laser_area_only=self.area_only_var.get(), laser_include_filled=self.include_var.get())
        for stage in order["stages"]:
            for key, var in self.setting_vars.get(stage["id"], {}).items():
                stage["settings"][key] = numeric(var.get(), stage["name"] + ": " + key)
            for key, var in self.stage_flags.get(stage["id"], {}).items():
                stage[key] = var.get()
        return order

    def flush(self):
        if self.pending:
            self.after_cancel(self.pending)
            self.pending = None
        return self._recalculate()

    def _recalculate(self):
        self.pending = None
        try:
            order = calculate_production(self._read_widgets())
            self.order = order
            self.db.set_state("production_draft", {"order": order, "saved_id": self.saved_id})
            self._render_results()
            self.refresh_laser()
            return True
        except (ValueError, KeyError) as error:
            self.error_var.set(f"Расчёт не обновлён: {error}")
            return False

    def refresh(self):
        if self.flush():
            self._render_tree()
            self.refresh_laser()
            self._render_extras()

    def _load_widgets(self):
        self.loading = True
        self.number_var.set(self.order["number"])
        self.customer_var.set(self.order["customer"])
        self.name_var.set(self.order["name"])
        self.directory_var.set(self.order["laser_directory"])
        self.comment_var.set(self.order["laser_comment"])
        self.area_only_var.set(self.order["laser_area_only"])
        self.include_var.set(self.order["laser_include_filled"])
        for stage in self.order["stages"]:
            for key, var in self.setting_vars.get(stage["id"], {}).items():
                var.set(number(stage["settings"][key]))
            for key, var in self.stage_flags.get(stage["id"], {}).items():
                var.set(stage[key])
            if stage["id"] in self.stage_views:
                self.stage_views[stage["id"]].configure(text=stage["name"])
        self.loading = False
        self.refresh()

    def _render_results(self):
        c = self.order["calculation"]
        self.total_var.set(f"Деталей: {number(c['quantity'])}   ·   Время: {number(c['minutes'])} мин   ·   Итого: {money(self.order['totals']['grand'])}")
        warnings = c["warnings"]
        self.error_var.set((warnings[0] + (f"; ещё замечаний: {len(warnings)-1}" if len(warnings) > 1 else "")) if warnings else "")
        self.tree_summary.set(f"Площадь покраски: {number(c['paint_area'])} м²   ·   Масса: {number(c['mass'])} кг   ·   Гибов: {number(c['bends'])}   ·   Материал: {c['material_basis']}")
        selected = self.stages_tree.selection()
        position = self.stages_tree.yview()[0]
        self.stages_tree.delete(*self.stages_tree.get_children())
        for index, stage in enumerate(self.order["stages"], 1):
            self.stages_tree.insert("", "end", iid=stage["id"], values=(index, stage["name"], stage["title"],
                "☑" if stage["enabled"] else "☐", "☑" if stage["otk"] else "☐", stage["description"]),
                tags=("alternate",) if index % 2 == 0 else ())
        if selected:
            self.stages_tree.selection_set(selected)
        self.stages_tree.yview_moveto(position)
        for result in c["stages"]:
            if result["kind"] == "painting":
                self.paint_prep_var.set(number(result["details"]["Общее время на этап подготовки, мин"]))
            if result["id"] in self.result_vars:
                lines = [f"{key}: {number(value)}" for key, value in result["details"].items()]
                lines.append(f"Общее время этапа: {number(result['minutes'])} мин     Стоимость: {money(result['cost'])}" if result["enabled"] else "Этап выключен — время и стоимость не включены в заказ")
                self.result_vars[result["id"]].set("\n".join(lines))
        laser = c["laser"]
        self.laser_totals.set("\n".join(f"{title}:  {number(laser[key])}" for key, title in (
            ("cutting", "Время загруженное, мин"), ("sheets", "Количество листов"), ("change", "Смена листа, мин"),
            ("weight", "Вес металла, кг"), ("transport", "Доп. время, мин"), ("heads", "Смена головы, мин"), ("total", "Общее время, мин"))))
        self.status("Черновик спецификации сохранён")

    def _render_tree(self):
        selected = self.tree.selection()
        opened = {key for key in self._all_tree_ids() if self.tree.item(key, "open")}
        self.tree.delete(*self.tree.get_children())
        parts = {p["id"]: p for p in self.order["calculation"]["parts"]}
        for node in self.order["calculation"]["tree"]:
            part = parts.get(node["id"], {})
            self.tree.insert(node["parent_id"], "end", iid=node["id"], text=node["name"], open=True if not opened else node["id"] in opened,
                values=(node["code"], node["quantity"], node["total_quantity"], node.get("material", {}).get("name", ""),
                        f"{number(node['width'])} × {number(node['height'])}" if node["kind"] == "part" else "",
                        number(part.get("surface_area", 0)), number(part.get("total_mass", 0)), number(part.get("total_bends", 0))),
                tags=("assembly",) if node["kind"] == "assembly" else ())
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected)

    def _all_tree_ids(self, parent=""):
        for key in self.tree.get_children(parent):
            yield key
            yield from self._all_tree_ids(key)

    def refresh_laser(self):
        if not hasattr(self, "laser_tree"):
            return
        selected = self.laser_tree.selection()
        position = self.laser_tree.yview()[0]
        self.laser_tree.delete(*self.laser_tree.get_children())
        laser_stage = next(s for s in self.order["stages"] if s["kind"] == "laser")
        settings = laser_stage["settings"]
        for index, row in enumerate(self.order["laser_rows"], 1):
            if self.laser_search.get().casefold() not in (row["material"] + " " + row["center"]).casefold():
                continue
            change = row["sheets"] * settings["sheet_change"]
            total = row["cut_minutes"] * (1 + settings["transport_pct"] / 100) + change + row["extra_minutes"]
            values = (index, row["material"], row["characteristic"], number(row["thickness"]), number(row["coef"]),
                      number(row["quantity"]), number(row["waste_pct"]), row["center"], number(row["cut_minutes"]),
                      number(row["sheets"]), number(change), number(row["weight"]), number(row["extra_minutes"]),
                      number(total), row["blank"], "☑" if row["customer_material"] else "☐")
            self.laser_tree.insert("", "end", iid=row["id"], values=values, tags=("alternate",) if index % 2 == 0 else ())
        if selected and self.laser_tree.exists(selected[0]):
            self.laser_tree.selection_set(selected)
        self.laser_tree.yview_moveto(position)
        self.unmatched_tree.delete(*self.unmatched_tree.get_children())
        nodes = {n["id"]: n["name"] for n in self.order["nodes"]}
        for row in self.order["laser_unmatched"]:
            self.unmatched_tree.insert("", "end", iid=row["id"], values=(row["name"], row["quantity"], "—" if row.get("missing_time") else number(row["minutes"]),
                nodes.get(row["node_id"], "—"), "—" if row.get("missing_area") else number(row["area"]), "—" if row.get("missing_mass") else number(row["mass"])))

    def _render_extras(self):
        self.extras_tree.delete(*self.extras_tree.get_children())
        for row in self.order["extras"]:
            self.extras_tree.insert("", "end", iid=row["id"], values=(row["name"], row["unit"], number(row["quantity"]), money(row["price"]), money(row["sum"])))

    def calculate(self, show_error=False):
        if self.flush():
            return deepcopy(self.order)
        if show_error:
            messagebox.showerror("Проверьте параметры", self.error_var.get(), parent=self)
        return None

    def save_order(self):
        order = self.calculate(True)
        if not order:
            return
        if not order["calculation"]["parts"]:
            messagebox.showinfo("Заказ", "Добавьте хотя бы одну деталь в дерево спецификации.", parent=self)
            return
        if self.saved_id:
            self.db.update_order(self.saved_id, order)
        else:
            # A simple cutting order may have used the initial number meanwhile.
            order["number"] = self.db.next_order_number()
            self.saved_id = self.db.save_order(order)
            self.order["number"] = order["number"]
            self.number_var.set(order["number"])
        self.db.set_state("production_draft", {"order": self.order, "saved_id": self.saved_id})
        self.history_refresh()
        self.status(f"Заказ {order['number']} записан в историю")

    def show_document(self, kind):
        order = self.calculate(True)
        if order:
            self.preview(self, order, kind)

    def load_order(self, order, order_id):
        self.order = deepcopy(order)
        self.saved_id = order_id
        self._load_widgets()

    def reset(self):
        if not messagebox.askyesno("Новый заказ", "Создать новый заказ? Текущий черновик будет заменён. Записанные заказы останутся в истории.", parent=self):
            return
        self.order = new_order(self.db.next_order_number())
        if self.db.get_state("production_stage_defaults"):
            self.order["stages"] = self.db.get_state("production_stage_defaults")
        self.saved_id = None
        self._load_widgets()

    def refresh_references(self):
        # Existing specifications keep their historical price snapshots.
        pass
