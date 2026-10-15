"""Compact desktop production specification editor."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from queue import Queue, Empty
from threading import Thread
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from ui_theme import AutoScrollbar, wheel_area, ui_font, FlagButton

from production import (COMMON_FIELDS, FIELDS, new_order, new_node, numeric, uid,
                        flatten_nodes, descendants, move_node, copy_branch, calculate_production, transfer_laser_matches,
                        new_auxiliary_stage, prepare_order_stages)
from reports import money, number


LASER_COLUMNS = [
    ('n', '№', 42, 'center'), ('material', 'Материал', 230, 'w'),
    ('characteristic', 'Формат листа / трубы', 170, 'w'), ('thickness', 'Толщина, мм', 115, 'center'),
    ('coef', 'Коэф. расхода', 120, 'e'), ('quantity', 'Расход, ед.', 110, 'e'),
    ('waste_pct', 'Отходы, %', 105, 'e'), ('center', 'Рабочий центр', 170, 'w'),
    ('cut_minutes', 'Резка, мин', 110, 'e'), ('sheets', 'Сменяемых листов', 155, 'center'),
    ('change', 'Смена листов, мин', 155, 'e'), ('weight', 'Масса ед., кг', 115, 'e'),
    ('extra_minutes', 'Доп. время, мин', 140, 'e'), ('total', 'Всего, мин', 110, 'e'),
    ('blank', 'Минимальная заготовка', 200, 'w'), ('customer_material', 'Сырьё давальца', 145, 'center'),
]


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
    vertical = AutoScrollbar(host, command=widget.yview)
    horizontal = AutoScrollbar(host, orient="horizontal", command=widget.xview)
    widget.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    widget.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    widget.tag_configure("assembly", background="#f1f5f9", font=(ui_font(parent), 11, 'bold'))
    widget.tag_configure("alternate", background="#fafbfd")
    widget.tag_configure("disabled", foreground="#969696")
    return widget


class FormPanel(ttk.Frame):
    """Fields: key, label, default, type (text/number/int/bool/choices)."""
    def __init__(self, parent, fields, on_save, on_close, note=""):
        super().__init__(parent)
        self.on_close = on_close
        self.fields, self.on_save, self.variables = fields, on_save, {}
        footer = ttk.Frame(self, padding=10)
        footer.pack(side="bottom", fill="x")
        self.error = tk.StringVar()
        ttk.Label(footer, textvariable=self.error, foreground="#c62828", wraplength=640).pack(fill="x")
        button(footer, "Записать и закрыть", self.save, True)
        button(footer, "Отмена", self.on_close)
        canvas = tk.Canvas(self, background="white", highlightthickness=0)
        scroll = AutoScrollbar(self, command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        form = ttk.Frame(canvas, style="Prod.TFrame", padding=14)
        window = canvas.create_window(0, 0, anchor="nw", window=form)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        form.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        wheel_area(canvas, form)
        form.columnconfigure(0, weight=1, uniform='fields')
        form.columnconfigure(1, weight=1, uniform='fields')
        if note:
            label(form, note, wraplength=650, foreground="#6b7278").grid(row=0, columnspan=2, sticky="w", pady=(0, 12))
        for row, (key, title, value, kind) in enumerate(fields, 1):
            var = tk.BooleanVar(value=value) if kind == "bool" else tk.StringVar(value=str(value))
            self.variables[key] = var
            caption = label(form, title, wraplength=320)
            caption.grid(row=row, column=0, sticky="w", padx=(0, 14), pady=7)
            caption.bind('<Configure>', lambda e: e.widget.configure(wraplength=max(160, form.winfo_width()//2-32)))
            if kind == "bool":
                entry = ttk.Checkbutton(form, variable=var, style="Prod.TCheckbutton")
            elif isinstance(kind, list):
                entry = ttk.Combobox(form, textvariable=var, values=kind, state="readonly", width=34)
            else:
                entry = ttk.Entry(form, textvariable=var, width=34, style="Prod.TEntry")
            entry.grid(row=row, column=1, sticky="ew", pady=5)

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
        self.on_close()


class FormDialog(tk.Toplevel):
    def __init__(self, parent, title, fields, on_save, note=""):
        super().__init__(parent)
        self.title(title)
        self.configure(bg='#f5f7fa')
        self.geometry(f"820x{min(780, self.winfo_screenheight()-120, 190 + len(fields)*46)}")
        self.minsize(680, 360)
        self.transient(parent.winfo_toplevel())
        self.panel = FormPanel(self, fields, on_save, self.destroy, note)
        self.panel.pack(fill='both', expand=True)
        self.variables, self.error = self.panel.variables, self.panel.error
        self.bind('<Escape>', lambda e: self.destroy())
        self.bind('<Control-Return>', lambda e: self.save())
        self.wait_visibility()
        self.grab_set()

    def save(self):
        self.panel.save()

    def destroy(self):
        parent = self.master
        super().destroy()
        if isinstance(parent, tk.Toplevel) and parent.winfo_exists():
            parent.grab_set()


class ProductionTab(ttk.Frame):
    SECTION_KINDS = {
        "laser": {"laser"},
        "filing": {"filing"},
        "bending": {"bending"},
        "welding": {"welding"},
        "painting": {"painting"},
        "assembly": {"assembly"},
        "packing": {"packing"},
        "services": {"cooperation", "additional", "auxiliary"},
    }

    def __init__(self, parent, db, status, history_refresh, preview, draft_key="production_draft"):
        super().__init__(parent, style="Prod.TFrame")
        self.db, self.status, self.history_refresh, self.preview = db, status, history_refresh, preview
        self.draft_key = draft_key
        draft = db.get_state(draft_key)
        self.order = draft["order"] if draft else new_order(db.next_order_number())
        if not draft and db.get_state("production_stage_defaults"):
            self.order["stages"] = db.get_state("production_stage_defaults")
        self.order = prepare_order_stages(self.order)
        self.saved_id = draft.get("saved_id") if draft else None
        self.pending = None
        self.loading = False
        self.setting_vars, self.stage_views, self.stage_flags, self.result_vars = {}, {}, {}, {}
        self._styles()
        self._header()
        self._footer()
        self.book = ttk.Notebook(self, style="Shell.TNotebook")
        self.book.pack(fill="both", expand=True, padx=8, pady=4)
        self.tabs = {}
        for key, title in [("tree", "Состав заказа"), ("stages", "Операции и расчёт"),
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
        from ui_theme import apply_theme
        apply_theme(self)

    def _header(self):
        frame = ttk.Frame(self, padding=(12,14,12,12))
        frame.pack(fill="x")
        title = ttk.Frame(frame)
        title.pack(fill="x", pady=(0,10))
        self.number_var = tk.StringVar(value=self.order["number"])
        self.customer_var = tk.StringVar(value=self.order["customer"])
        self.name_var = tk.StringVar(value=self.order["name"])
        ttk.Label(title, text="Заказ", style="PageTitle.TLabel").pack(side="left", padx=(0,8))
        ttk.Entry(title, textvariable=self.number_var, width=22,
                  font=(ui_font(self), 14)).pack(side="left")
        ttk.Button(title, text="Сохранить заказ", style="Accent.TButton", command=self.save_order).pack(side="right")
        metadata = ttk.Frame(frame)
        metadata.pack(fill="x")
        metadata.columnconfigure(1, weight=1)
        metadata.columnconfigure(3, weight=1)
        label(metadata, "Заказчик").grid(row=0, column=0, padx=(0,10))
        ttk.Entry(metadata, textvariable=self.customer_var, width=20).grid(row=0, column=1, sticky='ew')
        label(metadata, "Изделие").grid(row=0, column=2, padx=(24,10))
        ttk.Entry(metadata, textvariable=self.name_var, width=26).grid(row=0, column=3, sticky='ew')
        self.number_var.trace_add("write", self._changed)
        self.customer_var.trace_add("write", self._changed)
        self.name_var.trace_add("write", self._changed)

    def _footer(self):
        footer=ttk.Frame(self,padding=(12,10))
        footer.pack(side="bottom",fill="x")
        self.error_var=tk.StringVar()
        warning=ttk.Label(footer,textvariable=self.error_var,foreground="#a87930")
        warning.bind("<Configure>",lambda e:warning.configure(wraplength=max(100,e.width-10)))
        card=ttk.Frame(footer,style="Card.TFrame",padding=(16,10))
        card.pack(fill="x")
        def show_warning(*args):
            if self.error_var.get():
                warning.pack(fill='x', pady=(0,8), before=card)
            else:
                warning.pack_forget()
        self.error_var.trace_add('write', show_warning)
        card.columnconfigure(2,weight=1)
        self.overview_vars={}
        for index,(key,title) in enumerate((("material","Материалы"),("work","Работы"),("sale","Итого с НДС"))):
            column=3 if key=="sale" else index
            ttk.Label(card,text=title,style="Card.TLabel",foreground="#64748b").grid(row=0,column=column,sticky="w",padx=(0,24))
            var=tk.StringVar(value="0,00 ₽")
            self.overview_vars[key]=var
            ttk.Label(card,textvariable=var,style="Card.TLabel",font=(ui_font(self),14,'bold')).grid(row=1,column=column,sticky="w",padx=(0,24),pady=(6,0))
        actions=ttk.Frame(card,style="Surface.TFrame")
        actions.grid(row=0,column=4,rowspan=2,sticky="e")
        ttk.Button(actions,text="Отчёт",style="Accent.TButton",command=lambda:self.show_document("report")).grid(row=0,column=0,padx=3)
        ttk.Button(actions,text="Чек",command=lambda:self.show_document("receipt")).grid(row=0,column=1,padx=3)
        ttk.Button(actions,text="НДС и наценки",command=self.edit_finance).grid(row=0,column=2,padx=3)
        self.total_var=tk.StringVar()
        ttk.Label(footer,textvariable=self.total_var,style="Muted.TLabel").pack(anchor="w",pady=(7,0))

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
        for title,command in (("+ Изделие",lambda:self.edit_node(kind="assembly", root=True)),
                              ("+ Узел",lambda:self.edit_node(kind="assembly")),
                              ("+ Деталь",self.open_parts_catalog)):
            ttk.Button(bar,text=title,style="Compact.TButton",command=command).pack(side="left",padx=(0,6),pady=5)
        ttk.Button(bar,text="Проверить расчёт",style="Compact.TButton",command=self.check_calculation).pack(side="right",padx=4)
        more=ttk.Menubutton(bar,text="Ещё",style="Compact.TMenubutton")
        more.pack(side="left",padx=6)
        self.node_menu=tk.Menu(more,tearoff=False)
        self.node_menu.bind('<Button-3>', self._dismiss_node_menu)
        self.node_menu.add_command(label="Создать новую деталь вручную", command=self.edit_node)
        self.node_menu.add_separator()
        for title,command in (("Изменить",lambda:self.edit_node(edit=True)),("Копировать",self.duplicate),
                              ("Переместить",self.reparent),("Выше",lambda:self.reorder(-1)),("Ниже",lambda:self.reorder(1)),("Удалить",self.remove_node)):
            self.node_menu.add_command(label=title,command=command)
        more.configure(menu=self.node_menu)
        label(tab, "Количество задаётся на один родительский узел. Двойной щелчок открывает карточку позиции.",
              foreground="#7c8792").pack(anchor="w", pady=(4, 8))
        panes = ttk.Panedwindow(tab, orient='vertical')
        panes.pack(fill='both', expand=True)
        split = ttk.Frame(panes)
        panes.add(split, weight=3)
        from ui_theme import side_card
        self.detail_panel = side_card(split, 300)
        self.node_title = tk.StringVar(value="Выберите позицию")
        self.node_info = tk.StringVar(value="Выберите изделие или деталь в составе заказа.")
        ttk.Label(self.detail_panel, text="КАРТОЧКА ПОЗИЦИИ", style="Card.TLabel", foreground="#64748b").pack(anchor="w", pady=(0,12))
        ttk.Label(self.detail_panel, textvariable=self.node_title, style="Card.TLabel", font=(ui_font(self),14,'bold'), wraplength=250).pack(anchor="w")
        ttk.Separator(self.detail_panel).pack(fill="x", pady=16)
        ttk.Label(self.detail_panel, textvariable=self.node_info, style="Card.TLabel", wraplength=250, justify="left").pack(anchor="w")
        ttk.Button(self.detail_panel, text="Изменить позицию", style="Accent.TButton", command=lambda:self.edit_node(edit=True)).pack(fill="x", pady=20)
        list_host = ttk.Frame(split, style="Card.TFrame", padding=1)
        list_host.pack(side="left", fill="both", expand=True)
        self.tree = table(list_host, [("code", "Обозначение", 100, "w"), ("quantity", "Кол-во", 65, "center"),
                               ("total", "Всего", 65, "center"), ("material", "Материал", 180, "w"),
                               ("size", "Размеры, мм", 125, "center"), ("area", "Площадь, м²", 100, "e"),
                               ("mass", "Масса, кг", 90, "e"), ("bends", "Гибов", 60, "center")], tree=True)
        self.tree.heading("#0", text="Изделие / узел / деталь", anchor="w")
        self.tree.column("#0", width=270, minwidth=220)
        self.tree.bind("<Double-1>", lambda e: self.edit_node(edit=True))
        self.tree.bind("<Delete>", lambda e: self.remove_node())
        self.tree.bind("<<TreeviewSelect>>", lambda e:self._show_node_details())
        self.tree.bind("<Button-3>",self._node_context_menu)
        self.tree_summary = tk.StringVar()
        label(tab, "", textvariable=self.tree_summary).pack(anchor="w", pady=8)
        materials = ttk.Frame(panes, padding=(0, 8, 0, 0))
        panes.add(materials, weight=1)
        heading = ttk.Frame(materials)
        heading.pack(fill='x')
        ttk.Label(heading, text='Материалы и раскрои', style='Title.TLabel').pack(side='left')
        self.composition_laser_edit = button(heading, 'Изменить раскрой…', self.edit_composition_laser)
        label(materials, 'Те же данные, что в лазерной резке. Расход учитывается один раз. Все столбцы — прокрутка вправо.',
              foreground='#64748b').pack(anchor='w', pady=(4, 8))
        self.composition_laser_tree = table(materials, LASER_COLUMNS, height=3)
        self.composition_laser_tree.bind('<Double-1>', lambda e: self.edit_composition_laser())
        self.composition_laser_tree.bind('<<TreeviewSelect>>', lambda e: self.composition_laser_edit.configure(
            state='normal' if self.composition_laser_tree.selection() else 'disabled'))
        self.composition_laser_edit.configure(state='disabled')

    def check_calculation(self):
        order=self.calculate(True)
        if order:
            warnings=order["calculation"]["warnings"]
            messagebox.showinfo("Проверка расчёта", "\n".join(warnings) if warnings else "Расчёт обновлён. Замечаний нет.",parent=self)

    def open_parts_catalog(self):
        if self.flush():
            from part_picker import PartPicker
            return PartPicker(self)

    def visible_sections(self):
        enabled_kinds = {stage["kind"] for stage in self.order["stages"] if stage["enabled"]}
        visible = {"tree", "stages"}
        visible.update(key for key, kinds in self.SECTION_KINDS.items() if enabled_kinds & kinds)
        return visible

    def _node_context_menu(self,event):
        if self.node_menu.winfo_ismapped():
            return self._dismiss_node_menu()
        item=self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            # Keep Tk's popup grab: it routes outside clicks back to the menu
            # so the native menu bindings can dismiss it and restore focus.
            self.node_menu.tk_popup(event.x_root,event.y_root)
        return 'break'

    def _dismiss_node_menu(self, event=None):
        self.tk.call('tk::MenuUnpost', str(self.node_menu))
        return 'break'

    def edit_composition_laser(self):
        selection = self.composition_laser_tree.selection()
        if selection:
            self.edit_laser(True, row_id=selection[0])

    def _show_node_details(self):
        node = self.selected_node()
        if not node:
            self.node_title.set("Выберите позицию")
            self.node_info.set("Выберите изделие или деталь в составе заказа.")
            return
        self.node_title.set(node["name"])
        calculated = next((n for n in self.order.get("calculation", {}).get("tree", []) if n["id"] == node["id"]), node)
        lines = [node.get("code") or "Без обозначения", "", "Количество на узел: " + number(node["quantity"]),
                 "Всего в заказе: " + number(calculated.get("total_quantity", node["quantity"]))]
        if node["kind"] == "part":
            lines += ["", "Материал", node.get("material", {}).get("name", "Не выбран"), "",
                      f"Размеры: {number(node['width'])} × {number(node['height'])} мм",
                      f"Толщина: {number(node['thickness'])} мм", "",
                      f"Гибов на деталь: {number(node['bends'])}",
                      f"Резка: {number(node['laser_minutes'])} мин / деталь",
                      "Покраска: " + ("Да" if node.get("paint", True) else "Нет")]
        else:
            lines += ["", "Изделие / сборочный узел", "Добавляйте детали и вложенные узлы в состав выбранного изделия."]
        self.node_info.set("\n".join(lines))

    def selected_node(self):
        selected = self.tree.selection()
        return next((n for n in self.order["nodes"] if selected and n["id"] == selected[0]), None)

    def _parent_choices(self, excluded=()):
        return {"— Корень заказа —": "", **{f"{n['name']} [{n['id'][:6]}]": n["id"]
                for n in self.order["nodes"] if n["kind"] == "assembly" and n["id"] not in excluded}}

    def edit_node(self, kind="part", edit=False, root=False, container=None, on_close=None):
        selected = self.selected_node()
        if root:
            selected = None
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

        note = "Размеры и нормы — на одну деталь. Материал и цена сохраняются в заказе."
        if container is not None:
            panel = FormPanel(container, fields, save, on_close, note)
            panel.pack(fill='both', expand=True)
            return panel
        return FormDialog(self, "Карточка изделия" if node["kind"] == "assembly" else "Карточка детали", fields, save, note)

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
        ttk.Label(bar, text='Этапы производства', style='Title.TLabel').pack(side='left', padx=(0, 24))
        button(bar, "Настроить этап", self.edit_stage, True)
        button(bar, "Сохранить нормы по умолчанию", self.save_stage_defaults)
        ttk.Label(tab, text='Примечание — ввод в ячейке. Двойной щелчок по операции — нормы и тарифы.',
                  style='Muted.TLabel').pack(anchor='w', pady=(8, 12))
        bottom = ttk.Frame(tab, padding=(0, 10, 0, 0))
        bottom.pack(side='bottom', fill='x')
        ttk.Button(bottom, text='+ Вспомогательный', command=self.add_auxiliary_stage).pack(side='left')
        self.stages_tree = table(tab, [("n", "№", 42, "center"), ("name", "Этап", 180, "w"),
                                      ("title", "Операция", 180, "w"), ("enabled", "Включён", 100, "center"),
                                      ("otk", "ОТК", 65, "center"), ("description", "Примечание", 170, "w"),
                                      ("amount", "Сумма, ₽", 120, "e")])
        self.stages_tree.configure(style='Stages.Treeview')
        self.stage_editor = None
        self.stage_checkboxes = {}
        # Reposition real controls when rows scroll; only visible cells are mapped.
        self._flags_pending = None
        for option in ('yscrollcommand', 'xscrollcommand'):
            original = self.stages_tree.cget(option)
            def scroll(first, last, original=original):
                self.tk.call(original, first, last)
                self._queue_stage_flags()
            self.stages_tree.configure(**{option: scroll})
        self.stages_tree.bind('<Configure>', lambda e: self._queue_stage_flags())
        self.stages_tree.bind('<<TreeviewSelect>>', lambda e: self._queue_stage_flags())
        # Column dragging does not resize the Treeview widget itself. Run after
        # its class binding has changed column widths, also on final release.
        self.stages_tree.bind('<B1-Motion>', lambda e: self._queue_stage_flags(), add='+')
        self.stages_tree.bind('<ButtonRelease-1>', lambda e: self._queue_stage_flags(), add='+')
        self.stages_tree.bind("<Button-1>", self.toggle_stage)
        self.stages_tree.bind("<Double-1>", self.stage_double_click)
        self.stages_tree.bind("<space>", lambda e: self.toggle_selected_stage())

    def _queue_stage_flags(self):
        if self._flags_pending is None:
            self._flags_pending = self.after_idle(self._place_stage_flags)

    def destroy(self):
        for pending in (self.pending, getattr(self, '_flags_pending', None)):
            if pending:
                self.after_cancel(pending)
        super().destroy()

    def _place_stage_flags(self):
        self._flags_pending = None
        selected = self.stages_tree.selection()
        for index, stage in enumerate(self.order['stages'], 1):
            background = '#e8f1fd' if stage['id'] in selected else ('#fafbfd' if index % 2 == 0 else 'white')
            for field in ('enabled', 'otk'):
                widget = self.stage_checkboxes[stage['id'], field]
                box = self.stages_tree.bbox(stage['id'], field)
                if box and box[0] >= 0 and box[0]+box[2] <= self.stages_tree.winfo_width():
                    x, y, width, height = box
                    widget.set(stage[field], background)
                    widget.place(x=x+(width-36)//2, y=y+(height-36)//2, width=36, height=36)
                else:
                    widget.place_forget()
        if self.stage_editor is not None:
            box = self.stages_tree.bbox(self.stage_editor.stage_id, self.stage_editor.field)
            if box:
                x, y, width, height = box
                self.stage_editor.place(x=x+1, y=y+3, width=width-2, height=height-6)
            else:
                self.finish_stage_cell()

    def add_auxiliary_stage(self):
        if not self.flush():
            return
        stage = new_auxiliary_stage()
        self.order['stages'].append(stage)
        self.refresh()
        self.stages_tree.selection_set(stage['id'])
        self.stages_tree.see(stage['id'])
        self.after_idle(lambda: self.edit_stage_cell(stage['id'], 'name'))

    def edit_stage_cell(self, key, field):
        if self.stage_editor is not None:
            if (self.stage_editor.stage_id, self.stage_editor.field) == (key, field):
                return 'break'
            if not self.finish_stage_cell():
                return 'break'
        if not self.flush():
            return 'break'
        stage = next(s for s in self.order['stages'] if s['id'] == key)
        box = self.stages_tree.bbox(key, field)
        if not box:
            return 'break'
        self.stages_tree.selection_set(key)
        editor = ttk.Entry(self.stages_tree)
        editor.stage_id, editor.field = key, field
        value = number(stage['settings']['fixed_cost']) if field == 'amount' else stage[field]
        editor.insert(0, value)
        self.stage_editor = editor
        x, y, width, height = box
        editor.place(x=x+1, y=y+3, width=width-2, height=height-6)
        editor.bind('<Return>', lambda e: self.finish_stage_cell())
        editor.bind('<Escape>', lambda e: self.cancel_stage_cell())
        editor.bind('<FocusOut>', lambda e: self.finish_stage_cell())
        editor.focus_set()
        editor.selection_range(0, 'end')
        return 'break'

    def _apply_stage_cell(self):
        editor = self.stage_editor
        if editor is None:
            return
        value = editor.get().strip()
        stage = next(s for s in self.order['stages'] if s['id'] == editor.stage_id)
        if editor.field == 'amount':
            stage['settings']['fixed_cost'] = numeric(value, 'Сумма')
        else:
            if editor.field == 'name' and not value:
                raise ValueError('Укажите имя этапа')
            stage[editor.field] = value
        self.stage_editor = None
        editor.destroy()

    def finish_stage_cell(self):
        if self.stage_editor is None:
            return True
        try:
            self._apply_stage_cell()
        except ValueError as error:
            self.error_var.set(str(error))
            return False
        return self.flush()

    def cancel_stage_cell(self):
        editor, self.stage_editor = self.stage_editor, None
        if editor is not None:
            editor.destroy()
        self.stages_tree.focus_set()
        return 'break'

    def save_stage_defaults(self):
        if self.flush():
            self.db.set_state("production_stage_defaults", self.order["stages"])
            self.status("Флаги, нормы и тарифы этапов сохранены для новых заказов")
            messagebox.showinfo("Нормы по умолчанию", "Текущие флаги, нормы и тарифы будут применяться к новым заказам. Существующие заказы сохраняют свои настройки.", parent=self)

    def stage_double_click(self, event):
        column = self.stages_tree.identify_column(event.x)
        key = self.stages_tree.identify_row(event.y)
        stage = next((s for s in self.order['stages'] if s['id'] == key), None)
        if column == '#6' or (stage and stage['kind'] == 'auxiliary' and column in ('#2', '#7')):
            return self.toggle_stage(event)
        if key and column not in ("#4", "#5", '#7'):
            self.edit_stage()

    def toggle_selected_stage(self):
        selection = self.stages_tree.selection()
        if selection:
            self._toggle(selection[0], "enabled")
        return "break"

    def toggle_stage(self, event):
        key = self.stages_tree.identify_row(event.y)
        column = self.stages_tree.identify_column(event.x)
        if key:
            stage = next(s for s in self.order['stages'] if s['id'] == key)
            if column == '#6':
                return self.edit_stage_cell(key, 'description')
            if stage['kind'] == 'auxiliary' and column in ('#2', '#7'):
                return self.edit_stage_cell(key, 'name' if column == '#2' else 'amount')
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
        sync = getattr(self.winfo_toplevel(), "_sync_navigation", None)
        if sync:
            sync()

    def edit_stage(self, stage=None):
        if not self.flush():
            return
        if stage is None:
            selection = self.stages_tree.selection()
            if not selection:
                return
            stage = next(s for s in self.order["stages"] if s["id"] == selection[0])
        else:
            stage = next(s for s in self.order['stages'] if s['id'] == stage['id'])
        fields = [("name", "Имя этапа в дереве спецификации", stage["name"], "text"),
                  ("enabled", "Включить", stage["enabled"], "bool"), ("otk", "Есть ОТК", stage["otk"], "bool"),
                  ("description", "Расшифровка", stage["description"], "text")]
        if stage['kind'] == 'auxiliary':
            fields += [('fixed_cost', 'Сумма, ₽', stage['settings']['fixed_cost'], 'number')]
        elif stage["kind"] != "welding":
            fields += [(key, title, stage["settings"][key], "number") for key, title, _ in FIELDS[stage["kind"]] + COMMON_FIELDS]
        def save(values):
            if not values["name"]:
                raise ValueError("Укажите имя этапа")
            if stage["kind"] == "painting" and values["oven_area"] <= 0:
                raise ValueError("Площадь печи должна быть больше нуля")
            for key in ("name", "enabled", "otk", "description"):
                stage[key] = values.pop(key)
            stage["settings"].update(values)
            if stage['kind'] == 'auxiliary':
                stage['title'] = 'Вспомогательный'
            self._load_widgets()
            sync = getattr(self.winfo_toplevel(), "_sync_navigation", None)
            if sync:
                sync()
        FormDialog(self, "Настройка этапа: " + stage["name"], fields, save,
                   "Укажите общую сумму за вспомогательный этап на весь заказ."
                   if stage['kind'] == 'auxiliary' else
                   "Стоимость = время / 60 × тариф + дополнительная стоимость. ОТК учитывается только при включённом флаге."
                   if stage["kind"] != "welding" else "Сварка пока без параметров и расчёта стоимости.")

    def _scroll_form(self, parent):
        canvas = tk.Canvas(parent, bg="white", highlightthickness=0)
        scroll = AutoScrollbar(parent, command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        frame = ttk.Frame(canvas, style="Prod.TFrame")
        window = canvas.create_window(0, 0, anchor="nw", window=frame)
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        wheel_area(canvas, frame)
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
        caption = label(bar, stage["name"], foreground="#2868aa", font=(ui_font(self), 13, 'bold'))
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
                      foreground="#2868aa", font=(ui_font(self), 11)).grid(row=row, columnspan=2, sticky="w", pady=(6, 5))
                row += 1
            var = tk.StringVar(value=stage["settings"].get(key, default))
            settings[key] = var
            label(form, title + ":").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
            ttk.Entry(form, textvariable=var, width=13, justify="right", style="Prod.TEntry").grid(row=row, column=1, sticky="w", pady=4)
            var.trace_add("write", self._changed)
            row += 1
            if stage["kind"] == "painting" and key == "volume_coef":
                self.paint_prep_var = tk.StringVar()
                label(form, "Общее время на этап подготовки (мин):", foreground="#2868aa").grid(row=row, column=0, sticky="w", pady=4)
                ttk.Entry(form, textvariable=self.paint_prep_var, width=13, justify="right", state="readonly").grid(row=row, column=1, sticky="w", pady=4)
                row += 1
        self.setting_vars[stage["id"]] = settings
        result = tk.StringVar()
        self.result_vars[stage["id"]] = result
        label(section, "", textvariable=result, foreground="#2868aa", justify="left").pack(anchor="w", pady=8)
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
        guide = label(tab, 'Загрузите раскрои → проверьте материал, цену и время → сохраните заказ.',
                      wraplength=950, foreground='#64748b')
        guide.pack(anchor='w', pady=(0, 8))
        self.directory_var = tk.StringVar(value=self.order["laser_directory"])
        self.area_only_var = tk.BooleanVar(value=self.order["laser_area_only"])
        self.directory_var.trace_add("write", self._changed)
        self.laser_book = ttk.Notebook(tab, style='Prod.TNotebook')
        self.laser_book.pack(fill='both', expand=True)
        upper, lower, settings_page = [ttk.Frame(self.laser_book, padding=10) for _ in range(3)]
        for page, title in ((upper, 'Раскрои и материалы'), (lower, 'Данные деталей'), (settings_page, 'Нормы и тариф')):
            self.laser_book.add(page, text=title)
        settings_page = self._scroll_form(settings_page)
        lower = self._scroll_form(lower)
        label(settings_page, 'Эти нормы определяют дополнительное время и стоимость резки всего заказа.',
              foreground='#64748b').pack(anchor='w', pady=(0, 12))
        params = ttk.Frame(settings_page, style="Prod.TFrame")
        params.pack(fill="x")
        stage = next(s for s in self.order["stages"] if s["kind"] == "laser")
        settings = {}
        for index, (key, title, default) in enumerate(FIELDS["laser"]):
            row, col = divmod(index, 2)
            label(params, title + ":", wraplength=280).grid(row=row, column=col * 2, sticky="w", padx=(0, 6), pady=3)
            var = tk.StringVar(value=stage["settings"][key])
            settings[key] = var
            ttk.Entry(params, textvariable=var, width=8, justify="right", style="Prod.TEntry").grid(row=row, column=col*2+1, padx=(0, 18))
            var.trace_add("write", self._changed)
        self.setting_vars[stage["id"]] = settings
        tariff = ttk.Frame(settings_page)
        tariff.pack(fill='x', pady=12)
        button(tariff, 'Изменить тариф и ОТК…', lambda: self.edit_stage(next(s for s in self.order['stages'] if s['kind'] == 'laser')))
        label(settings_page, 'Стоимость работ = время / 60 × тариф часа + дополнительная стоимость этапа.\n'
              'Материал = расход × коэффициент × цена единицы. Отходы показаны справочно.\n'
              'Если раскрои загружены, время резки берётся из них, а не повторно из карточек деталей.',
              foreground='#64748b', wraplength=1000, justify='left').pack(anchor='w', pady=8)
        self.laser_totals = tk.StringVar()
        label(settings_page, '', textvariable=self.laser_totals, justify='left').pack(anchor='w', pady=12)
        toolbar = ttk.Frame(upper, style="Prod.TFrame")
        toolbar.pack(fill="x")
        button(toolbar, 'Импорт раскроев…', self.import_files, True)
        button(toolbar, '+ Раскрой вручную', self.edit_laser)
        self.laser_edit_button = button(toolbar, 'Изменить раскрой…', lambda: self.edit_laser(True))
        self.laser_edit_button.configure(state='disabled')
        more = ttk.Menubutton(toolbar, text='Ещё', style='Compact.TMenubutton')
        more.pack(side='left', padx=5)
        self.laser_menu = tk.Menu(more, tearoff=False)
        self.laser_menu.add_command(label='Импортировать всю папку…', command=self.import_directory)
        self.laser_menu.add_command(label='Источники и замечания импорта…', command=self.show_import_sources)
        self.laser_menu.add_separator()
        self.laser_menu.add_command(label='Переместить раскрой выше', command=lambda: self.reorder_row('laser_rows', self.laser_tree, -1))
        self.laser_menu.add_command(label='Переместить раскрой ниже', command=lambda: self.reorder_row('laser_rows', self.laser_tree, 1))
        self.laser_menu.add_command(label='Удалить выбранный раскрой…', command=lambda: self.remove_row('laser_rows', self.laser_tree))
        more.configure(menu=self.laser_menu)
        self.laser_search = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.laser_search, width=18).pack(side='right', padx=(8, 0))
        label(toolbar, 'Поиск:').pack(side='right')
        hint = label(upper, 'Цена — в «Изменить раскрой». Все столбцы — прокрутка вправо.', foreground='#64748b')
        hint.pack(anchor='w', pady=(4, 6))
        self.laser_overview = tk.StringVar()
        label(upper, '', textvariable=self.laser_overview).pack(side='bottom', anchor='w', pady=(8, 0))
        self.laser_search.trace_add("write", lambda *a: self.refresh_laser())
        self.laser_tree = table(upper, LASER_COLUMNS, height=7)
        self.laser_tree.bind('<<TreeviewSelect>>', lambda e: self.laser_edit_button.configure(
            state='normal' if self.laser_tree.selection() else 'disabled'))
        self.laser_tree.bind("<Double-1>", lambda e: self.edit_laser(True))
        comment = ttk.Frame(lower, style="Prod.TFrame")
        comment.pack(fill="x", pady=5)
        label(comment, "Комментарий технолога:").pack(side="left", padx=(0, 10))
        self.comment_var = tk.StringVar(value=self.order["laser_comment"])
        ttk.Entry(comment, textvariable=self.comment_var, width=50, style="Prod.TEntry").pack(side="left", fill='x', expand=True)
        self.comment_var.trace_add("write", self._changed)
        label(lower, 'Данные из файлов → детали заказа', font=(ui_font(self), 12, 'bold')).pack(anchor='w', pady=8)
        label(lower, 'При обычном импорте данные уже перенесены в детали. Этот раздел нужен для ручного исправления связей.\n'
              'Выберите строку, укажите деталь заказа, затем нажмите «Обновить карточки деталей». Повторный перенос заменяет значения.',
              wraplength=1000, foreground='#64748b', justify='left').pack(anchor='w', pady=(0, 8))
        line = ttk.Frame(lower, style="Prod.TFrame")
        line.pack(fill="x")
        self.include_var = tk.BooleanVar(value=self.order["laser_include_filled"])
        ttk.Checkbutton(line, text='Заменять уже заполненное время в деталях', variable=self.include_var, style='Prod.TCheckbutton', command=self._changed).pack(side='left')
        ttk.Checkbutton(line, text='Обновлять только площадь и массу', variable=self.area_only_var, style='Prod.TCheckbutton', command=self._changed).pack(side='left', padx=12)
        actions = ttk.Frame(lower)
        actions.pack(fill='x', pady=6)
        button(actions, 'Связать с деталью…', lambda: self.edit_unmatched(True))
        button(actions, 'Обновить карточки деталей', self.transfer_times, True)
        more = ttk.Menubutton(actions, text='Ещё', style='Compact.TMenubutton')
        more.pack(side='left', padx=5)
        self.mapping_menu = tk.Menu(more, tearoff=False)
        self.mapping_menu.add_command(label='Добавить строку вручную…', command=self.edit_unmatched)
        self.mapping_menu.add_command(label='Удалить строку сопоставления…', command=lambda: self.remove_row('laser_unmatched', self.unmatched_tree))
        more.configure(menu=self.mapping_menu)
        content = ttk.Frame(lower, style="Prod.TFrame")
        content.pack(fill="both", expand=True)
        self.unmatched_tree = table(content, [("name", "Наименование в файле", 170, "w"),
            ("quantity", "Количество", 75, "center"), ("minutes", "Время резки", 85, "e"),
            ("match", "Деталь заказа", 185, "w"), ("area", "Площадь 1 шт., м²", 150, "e"), ("mass", "Масса 1 шт., кг", 130, "e")], height=4)
        self.unmatched_tree.bind("<Double-1>", lambda e: self.edit_unmatched(True))
        def fit_layout(event):
            # On short displays preserve room for rows, not repeated help text.
            compact = event.height < 360
            if compact:
                guide.pack_forget()
                hint.pack_forget()
            elif not guide.winfo_manager():
                guide.pack(anchor='w', pady=(0, 8), before=self.laser_book)
                hint.pack(anchor='w', pady=(4, 6), after=toolbar)
        tab.bind('<Configure>', fit_layout, add='+')

    def import_files(self):
        paths = filedialog.askopenfilenames(parent=self, title="Выберите один или несколько отчётов раскроя",
            initialdir=self.directory_var.get() or None,
            filetypes=[("Отчёты раскроя", "*.xlsx *.pdf *.html *.htm"), ("Все файлы", "*.*")])
        if paths:
            self.directory_var.set(str(Path(paths[0]).parent))
            self.start_import(paths)

    def import_directory(self):
        from cam_import import SUPPORTED
        directory = filedialog.askdirectory(parent=self, title="Папка с отчётами раскроя",
                                            initialdir=self.directory_var.get() or None)
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
        scroll = AutoScrollbar(window, command=box.yview)
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

    def edit_laser(self, edit=False, row_id=None):
        if not self.flush():
            return
        selected = self.laser_tree.selection()
        row_id = row_id or (selected[0] if selected else None)
        old = next((r for r in self.order["laser_rows"] if r['id'] == row_id), None)
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
            if edit:
                for field, flag in (('cut_minutes', 'missing_time'), ('weight', 'missing_mass')):
                    if values[field] != old[field]:
                        row[flag] = False
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
        self._apply_stage_cell()
        order = deepcopy(self.order)
        number_value = self.number_var.get().strip()
        if not number_value:
            raise ValueError("Укажите номер заказа")
        order.update(number=number_value, customer=self.customer_var.get().strip(), name=self.name_var.get().strip(),
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
            self.db.set_state(self.draft_key, {"order": order, "saved_id": self.saved_id})
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
        from costing import cost_document
        financial = cost_document(self.order)["totals"]
        for key,value in (("material",financial["Материалы"]["cost"]),("work",financial["Работы"]["cost"]),("sale",financial["Всего"]["sale"])):
            self.overview_vars[key].set(money(value))
        self._show_node_details()
        self.total_var.set(f"Деталей: {number(c['quantity'])}   ·   Время: {number(c['minutes'])} мин")
        warnings = c["warnings"]
        self.error_var.set((warnings[0] + (f"; ещё замечаний: {len(warnings)-1}" if len(warnings) > 1 else "")) if warnings else "")
        self.tree_summary.set(f"Площадь покраски: {number(c['paint_area'])} м²   ·   Масса: {number(c['mass'])} кг   ·   Гибов: {number(c['bends'])}   ·   Материал: {c['material_basis']}")
        selected = self.stages_tree.selection()
        position = self.stages_tree.yview()[0]
        self.stages_tree.delete(*self.stages_tree.get_children())
        current_ids = {s['id'] for s in self.order['stages']}
        for key, widget in list(self.stage_checkboxes.items()):
            if key[0] not in current_ids:
                widget.destroy()
                del self.stage_checkboxes[key]
        for index, stage in enumerate(self.order["stages"], 1):
            for field in ('enabled', 'otk'):
                if (stage['id'], field) not in self.stage_checkboxes:
                    self.stage_checkboxes[stage['id'], field] = FlagButton(
                        self.stages_tree, lambda key=stage['id'], field=field: self._toggle(key, field))
            self.stages_tree.insert("", "end", iid=stage["id"], values=(index, stage["name"], stage["title"],
                "", "", stage["description"], number(stage['settings']['fixed_cost']) if stage['kind'] == 'auxiliary' else '—'),
                tags=("alternate",) if index % 2 == 0 else ())
        if selected:
            self.stages_tree.selection_set([key for key in selected if self.stages_tree.exists(key)])
        self.stages_tree.yview_moveto(position)
        self._queue_stage_flags()
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
        app = self.winfo_toplevel()
        if getattr(app, 'order_tab', None) is self:
            app._sync_navigation()

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
        laser_stage = next(s for s in self.order["stages"] if s["kind"] == "laser")
        settings = laser_stage["settings"]
        rendered = []
        for index, row in enumerate(self.order["laser_rows"], 1):
            change = row["sheets"] * settings["sheet_change"]
            total = row["cut_minutes"] * (1 + settings["transport_pct"] / 100) + change + row["extra_minutes"]
            values = (index, row["material"], row["characteristic"], number(row["thickness"]), number(row["coef"]),
                      number(row["quantity"]), number(row["waste_pct"]), row["center"],
                      '—' if row.get('missing_time') else number(row["cut_minutes"]),
                      number(row["sheets"]), number(change), '—' if row.get('missing_mass') else number(row["weight"]),
                      number(row["extra_minutes"]), '—' if row.get('missing_time') else number(total),
                      row["blank"], 'Да' if row["customer_material"] else 'Нет')
            rendered.append((row, values, ("alternate",) if index % 2 == 0 else ()))
        for widget in (self.laser_tree, self.composition_laser_tree):
            selected, position = widget.selection(), widget.yview()[0]
            widget.delete(*widget.get_children())
            for row, values, tags in rendered:
                if widget is self.laser_tree and self.laser_search.get().casefold() not in (row['material']+' '+row['center']).casefold():
                    continue
                widget.insert('', 'end', iid=row['id'], values=values, tags=tags)
            widget.selection_set([key for key in selected if widget.exists(key)])
            widget.yview_moveto(position)
        laser = self.order.get('calculation', {}).get('laser', {})
        self.laser_overview.set((
            f"Раскроев: {len(rendered)} · Время этапа: {number(laser.get('total', 0))} мин · "
            f"Материал по раскроям: {money(laser.get('material', 0))}" if rendered else
            'Раскроев пока нет. Импортируйте отчёты или добавьте строку вручную.') +
            (' · Время неполное: есть неизвестные значения.' if any(r.get('missing_time') for r in self.order['laser_rows']) else ''))
        selected, position = self.unmatched_tree.selection(), self.unmatched_tree.yview()[0]
        self.unmatched_tree.delete(*self.unmatched_tree.get_children())
        nodes = {n["id"]: n["name"] for n in self.order["nodes"]}
        for row in self.order["laser_unmatched"]:
            self.unmatched_tree.insert("", "end", iid=row["id"], values=(row["name"], row["quantity"], "—" if row.get("missing_time") else number(row["minutes"]),
                nodes.get(row["node_id"], "—"), "—" if row.get("missing_area") else number(row["area"]), "—" if row.get("missing_mass") else number(row["mass"])))
        self.unmatched_tree.selection_set([key for key in selected if self.unmatched_tree.exists(key)])
        self.unmatched_tree.yview_moveto(position)

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
        duplicate = next((row for row in self.db.list_orders()
                          if row["number"] == order["number"] and row["id"] != self.saved_id), None)
        if duplicate:
            messagebox.showwarning("Номер заказа",
                                   f"Заказ с номером «{order['number']}» уже существует. Укажите другой номер.",
                                   parent=self)
            return
        if self.saved_id:
            self.db.update_order(self.saved_id, order)
        else:
            self.saved_id = self.db.save_order(order)
            self.order["number"] = order["number"]
            self.number_var.set(order["number"])
        self.db.set_state(self.draft_key, {"order": self.order, "saved_id": self.saved_id})
        self.history_refresh()
        self.status(f"Заказ {order['number']} записан в историю")

    def show_document(self, kind):
        order = self.calculate(True)
        if order:
            self.preview(self, order, kind)

    def load_order(self, order, order_id):
        self.cancel_stage_cell()
        self.order = prepare_order_stages(order)
        self.saved_id = order_id
        self._load_widgets()

    def reset(self):
        if not messagebox.askyesno("Новый заказ", "Создать новый заказ? Текущий черновик будет заменён. Записанные заказы останутся в истории.", parent=self):
            return
        self.cancel_stage_cell()
        self.order = new_order(self.db.next_order_number())
        if self.db.get_state("production_stage_defaults"):
            self.order["stages"] = self.db.get_state("production_stage_defaults")
        self.order = prepare_order_stages(self.order)
        self.saved_id = None
        self._load_widgets()

    def refresh_references(self):
        # Existing specifications keep their historical price snapshots.
        pass
