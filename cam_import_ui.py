"""Preview, source selection and explicit part mapping before committing CAM data."""
from copy import deepcopy
from pathlib import Path
import tkinter as tk
import sqlite3
from tkinter import ttk, messagebox
from ui_theme import AutoScrollbar, ui_font

from cam_import import aggregate_parts, apply_import, default_mapping
from production import numeric, calculate_production
from production_ui import FormDialog, label, button, table
from reports import number, money


def shown(value):
    return "—" if value is None else number(value)


class ImportPreview(tk.Toplevel):
    def __init__(self, owner, batch):
        super().__init__(owner)
        self.owner, self.batch = owner, batch
        self.documents = batch["documents"]
        self.title("Импорт раскроев — проверка данных")
        self.geometry("1250x820")
        self.minsize(1060, 650)
        self.transient(owner.winfo_toplevel())
        self.configure(bg="white")
        previous = {d["key"] for d in owner.order.get("cam_imports", [])}
        self.existing = previous
        self.included = {d["key"]: d["key"] not in previous or owner.area_only_var.get() for d in self.documents}
        self.configs = {d["key"]: dict(basis="full", center=d["center"], price=0, material=None,
                                      cut_minutes=d["cut_minutes"], stock_mass=d["stock_mass"]) for d in self.documents}
        self.mappings = {key: default_mapping(owner.order, part) for key, part in aggregate_parts(self.documents).items()}
        self.replace_var = tk.BooleanVar(value=False)
        self.quantity_var = tk.BooleanVar(value=False)
        self.mass_var = tk.BooleanVar(value=False)
        self.geometry_var = tk.BooleanVar(value=True)
        self.area_only_var = tk.BooleanVar(value=owner.area_only_var.get())
        self.sides_var = tk.StringVar(value="Две стороны")
        self.parent_choices = {"— Новый узел в корне заказа —": "", **{
            f"{n['name']} [{n['id'][:6]}]": n["id"] for n in owner.order["nodes"] if n["kind"] == "assembly"}}
        selected = owner.selected_node()
        parent_id = selected["id"] if selected and selected["kind"] == "assembly" else ""
        self.parent_var = tk.StringVar(value=next(k for k,v in self.parent_choices.items() if v == parent_id))
        self.group_var = tk.StringVar(value="Импорт раскроев")
        self._build()
        self.render()
        self.grab_set()

    def _build(self):
        top = ttk.Frame(self, style="Prod.TFrame", padding=10)
        top.pack(fill="x")
        label(top, 'Импорт раскроев', font=(ui_font(self), 15, 'bold')).pack(anchor='w')
        label(top, '1. Выберите раскрои и укажите цены.  2. Проверьте, какие детали попадут в заказ.  3. Просмотрите замечания.',
              wraplength=1100, foreground='#64748b').pack(anchor='w', pady=5)
        label(top, 'До нажатия «Добавить выбранное в заказ» ничего не изменится. Одинаковые PDF и XLSX не добавляются дважды.',
              wraplength=1100, foreground='#64748b').pack(anchor='w')
        self.summary_var = tk.StringVar()
        footer = ttk.Frame(self, style="Prod.TFrame", padding=10)
        footer.pack(side="bottom", fill="x")
        label(footer, "", textvariable=self.summary_var, wraplength=1150).pack(anchor="w", pady=(0, 5))
        button(footer, "Добавить выбранное в заказ", self.apply, True)
        button(footer, "Отмена", self.destroy)
        book = ttk.Notebook(self, style="Prod.TNotebook")
        book.pack(fill="both", expand=True, padx=10, pady=5)
        self.book = book
        docs_tab, parts_tab, info_tab, options = [ttk.Frame(book, style='Prod.TFrame', padding=8) for _ in range(4)]
        book.add(docs_tab, text='1. Раскрои и цены')
        book.add(parts_tab, text='2. Детали заказа')
        book.add(info_tab, text='3. Замечания')
        book.add(options, text='Параметры обновления')
        options = self.owner._scroll_form(options)
        label(options, 'Для первого импорта обычно менять ничего не нужно.',
              font=(ui_font(self), 12, 'bold')).pack(anchor='w', pady=(4, 12))
        for title, variable, note in (
            ('Заменить выбранные ранее загруженные раскрои', self.replace_var,
             'Для повторной загрузки исправленного файла. Отметьте нужный раскрой на первой вкладке; без этого флага повторы пропускаются.'),
            ('Только площадь и массу', self.area_only_var,
             'Только обновить связанные детали. Новые раскрои и детали не создаются, время и количество не меняются.'),
            ('Обновить количество существующих деталей', self.quantity_var,
             'Разрешить заменить вручную заданное количество в уже существующих деталях заказа.'),
            ('Обновлять известные размеры и толщину существующих деталей', self.geometry_var,
             'Перенести размеры из файла. Отсутствующие значения не затирают заполненные поля.'),
            ('Переносить массу BIT, несмотря на расхождение итогов', self.mass_var,
             'В образцах BIT массы деталей расходятся с итогом файла. Включайте только после проверки исходных данных.'),
        ):
            ttk.Checkbutton(options, text=title, variable=variable, style='Prod.TCheckbutton', command=self.render).pack(anchor='w', pady=(10, 2))
            label(options, note, wraplength=960, foreground='#64748b').pack(anchor='w', padx=28)
        label(docs_tab, 'Цен в файлах нет: откройте «Материал и цена…». «—» означает неизвестное значение, а не ноль.',
              foreground='#64748b', wraplength=1050).pack(anchor='w', pady=(0, 8))
        toolbar = ttk.Frame(docs_tab, style="Prod.TFrame")
        toolbar.pack(fill="x")
        button(toolbar, "Выбрать все новые", self.select_new)
        button(toolbar, "Снять выбор", self.deselect_all)
        button(toolbar, 'Материал и цена…', self.edit_document, True)
        self.docs_tree = table(docs_tab, [("include", "Добавить", 80, "center"), ("file", "Файл / программа", 210, "w"),
            ("material", "Материал", 135, "w"), ("thickness", "Толщина, мм", 115, "center"), ("count", "Листов / труб", 120, "center"),
            ("parts", "Деталей", 70, "center"), ("minutes", "Резка, мин", 90, "e"), ("price", "Цена ед.", 100, "e"),
            ("status", "Статус", 150, "w")])
        self.docs_tree.bind("<Button-1>", self.toggle)
        self.docs_tree.bind("<Double-1>", lambda e: self.edit_document() if self.docs_tree.identify_column(e.x) != "#1" else None)
        target = ttk.Frame(parts_tab, style="Prod.TFrame")
        target.pack(fill="x")
        label(target, "Добавлять детали в:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Combobox(target, textvariable=self.parent_var, values=list(self.parent_choices), state="readonly", width=42).grid(row=0, column=1, sticky="w")
        label(target, "Имя нового узла:").grid(row=0, column=2, sticky="w", padx=(14, 6))
        ttk.Entry(target, textvariable=self.group_var, width=30).grid(row=0, column=3, sticky="w")
        label(target, "Площадь покраски:").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Combobox(target, textvariable=self.sides_var, values=["Две стороны", "Одна сторона"], state="readonly", width=20).grid(row=1, column=1, sticky="w")
        label(parts_tab, '«Создать деталь» — появится новая позиция в составе заказа. Имя существующей детали — обновится её карточка.\n'
              'Автоподбор уже выполнен. Если связь неверна, выберите строку и нажмите «Куда добавить деталь…».',
              wraplength=1050, foreground='#64748b', justify='left').pack(anchor='w', pady=(4, 8))
        toolbar = ttk.Frame(parts_tab, style="Prod.TFrame")
        toolbar.pack(fill="x")
        button(toolbar, 'Куда добавить деталь…', self.edit_mapping)
        button(toolbar, 'Повторить автоподбор', self.auto_match)
        self.parts_tree = table(parts_tab, [("name", "Деталь / обозначение", 240, "w"), ("q", "Всего, шт.", 75, "center"),
            ("size", "Размеры, мм", 130, "center"), ("area", "Плоская площадь, м²/шт.", 155, "e"),
            ("mass", "Масса из файла, кг/шт.", 145, "e"), ("minutes", "Резка, мин/шт.", 110, "e"),
            ("mapping", "Действие / позиция заказа", 230, "w")])
        self.parts_tree.bind("<Double-1>", lambda e: self.edit_mapping())
        text = tk.Text(info_tab, wrap="word", font=("Segoe UI", 10), padx=10, pady=10)
        scroll = AutoScrollbar(info_tab, command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)
        text.pack(fill="both", expand=True)
        lines = ["Все файлы читаются локально. Исходные файлы не изменяются.",
                 "Секунды переводятся в минуты, площадь мм² — в м². Для покраски отдельно выбирается одна или две стороны; кромки не добавляются.",
                 "Полный лист / труба — расход по количеству заготовок. Минимальная заготовка — доля площади листа или использованной длины трубы.",
                 "Если масса или время отсутствуют, итог будет неполным: заполните их вручную после импорта.", ""]
        lines += ["ПОВТОРЫ", *self.batch["duplicates"], "", "ОШИБКИ ЧТЕНИЯ", *(self.batch["errors"] or ["Нет"])]
        for doc in self.documents:
            lines += ["", doc["filename"], doc["source"], f"Формат: {doc['family']}; программа: {doc['program']}", *doc["warnings"], "Исходные поля:"]
            lines += [f"  {k}: {v}" for k,v in doc["raw_summary"].items()]
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")

    def selected_documents(self):
        return [d for d in self.documents if self.included[d["key"]]]

    def render(self):
        selection = self.docs_tree.selection()
        self.docs_tree.delete(*self.docs_tree.get_children())
        for doc in self.documents:
            key = doc["key"]
            config = self.configs[key]
            status = "Уже загружен" if key in self.existing else "Новый"
            if doc["warnings"]:
                status += " · есть замечания"
            if key in self.existing and self.replace_var.get():
                status = "Будет заменён" if self.included[key] else "Не выбран"
            self.docs_tree.insert("", "end", iid=key, values=("☑" if self.included[key] else "☐", doc["filename"],
                (config["material"] or {}).get("name", doc["material"]), shown(doc["thickness"]), doc["stock_count"],
                sum(p["quantity"] for p in doc["parts"]), shown(config["cut_minutes"]), money(config["price"]), status))
        if selection:
            self.docs_tree.selection_set(selection)
        self.parts = aggregate_parts(self.selected_documents())
        self.parts_tree.delete(*self.parts_tree.get_children())
        names = {n["id"]: n["name"] for n in self.owner.order["nodes"]}
        for key, part in self.parts.items():
            mapped = self.mappings[key]
            mapping = {"new": "Создать деталь", "unmatched": "Только таблица; нужно сопоставить"}.get(mapped, names.get(mapped, "Позиция отсутствует"))
            size = shown(part["width"]) + (" × " + shown(part["height"]) if part["height"] is not None else " (длина)")
            self.parts_tree.insert("", "end", iid=key, values=(part["name"], part["quantity"], size, shown(part["flat_area"]),
                                                              shown(part["mass"]), shown(part["minutes"]), mapping))
        docs = self.selected_documents()
        unpriced = sum(not self.configs[d['key']]['price'] for d in docs)
        self.summary_var.set(f"Выбрано раскроев: {len(docs)}; позиций деталей: {len(self.parts)}; деталей всего: {sum(p['quantity'] for p in self.parts.values())}. "
                             f"Повторов файлов: {len(self.batch['duplicates'])}; ошибок чтения: {len(self.batch['errors'])}.\n" +
                             ('Режим: только площадь и масса — раскрои не добавятся.' if self.area_only_var.get() else
                              f'Без цены: {unpriced}. Их материал будет учтён по нулевой стоимости.' if unpriced else
                              'Цены выбранных раскроев заполнены.' if docs else 'Отметьте раскрои в первом столбце.') +
                             (' Ранее загруженные выбранные раскрои будут заменены.' if self.replace_var.get() else ''))

    def toggle(self, event):
        key = self.docs_tree.identify_row(event.y)
        if key and self.docs_tree.identify_column(event.x) == "#1":
            self.included[key] = not self.included[key]
            self.render()
            return "break"

    def select_new(self):
        self.included = {d["key"]: d["key"] not in self.existing or self.replace_var.get() or self.area_only_var.get() for d in self.documents}
        self.render()

    def deselect_all(self):
        self.included = {d["key"]: False for d in self.documents}
        self.render()

    def edit_document(self):
        selection = self.docs_tree.selection()
        if not selection:
            return
        doc = next(d for d in self.documents if d["key"] == selection[0])
        config = self.configs[doc["key"]]
        choices = {"— Данные из файла, цена вручную —": None, **{
            f"{r['name']} · {number(r['thickness'])} мм [№{r['id']}]": r for r in self.owner.db.list_records("materials", active_only=True)}}
        current = next((k for k,v in choices.items() if v == config["material"]), next(iter(choices)))
        fields = [("material", "Материал из справочника", current, list(choices)),
                  ("price", "Цена целого листа / трубы, ₽", config["price"], "number"),
                  ("basis", "Расход материала", "Полная заготовка" if config["basis"] == "full" else "Минимальная заготовка", ["Полная заготовка", "Минимальная заготовка"]),
                  ("center", "Рабочий центр", config["center"], "text"),
                  ("cut_minutes", "Общее время резки, мин (пусто = неизвестно)", "" if config["cut_minutes"] is None else config["cut_minutes"], "text"),
                  ("stock_mass", "Масса целого листа / трубы, кг (пусто = неизвестно)", "" if config["stock_mass"] is None else config["stock_mass"], "text")]
        def save(values):
            ref = choices[values["material"]]
            if ref and doc["thickness"] is not None and abs(ref["thickness"] - doc["thickness"]) > .001:
                raise ValueError("Толщина выбранного материала не совпадает с файлом")
            if ref and doc["stock_kind"] == "sheet":
                source_dims = sorted((doc["width"], doc["height"]))
                ref_dims = sorted((ref["width"], ref["height"]))
                if any(abs(a-b) > .01 for a,b in zip(source_dims, ref_dims)):
                    raise ValueError("Размер листа в справочнике не совпадает с раскроем. Добавьте материал нужного формата или введите цену вручную.")
            config.update(material=deepcopy(ref), price=ref["price_sheet"] if ref else values["price"],
                          basis="full" if values["basis"] == "Полная заготовка" else "blank", center=values["center"],
                          cut_minutes=numeric(values["cut_minutes"], "Время резки") if values["cut_minutes"] else None,
                          stock_mass=numeric(values["stock_mass"], "Масса заготовки") if values["stock_mass"] else None)
            self.render()
        FormDialog(self, "Параметры импорта: " + doc["filename"], fields, save,
                   "При выборе материала из справочника используется его цена. Исходные значения файла остаются в журнале.")

    def edit_mapping(self):
        selection = self.parts_tree.selection()
        if not selection:
            return
        key = selection[0]
        choices = {"Создать новую деталь": "new", "Только таблица сопоставления": "unmatched", **{
            f"{n['code']} {n['name']} [{n['id'][:6]}]": n["id"] for n in self.owner.order["nodes"] if n["kind"] == "part"}}
        current = next((k for k,v in choices.items() if v == self.mappings[key]), "Только таблица сопоставления")
        def save(values):
            self.mappings[key] = choices[values["mapping"]]
            self.render()
        FormDialog(self, self.parts[key]["name"], [("mapping", "Действие", current, list(choices))], save,
                   "При сопоставлении обновляются известные площадь, масса и время. Количество существующей детали меняется только при включённом флаге.")

    def auto_match(self):
        if not messagebox.askyesno('Повторить автоподбор',
                                  'Заново подобрать связи для всех деталей в списке? Ручной выбор связей будет заменён.', parent=self):
            return
        for key, part in self.parts.items():
            self.mappings[key] = default_mapping(self.owner.order, part)
        self.render()

    def apply(self):
        docs = deepcopy(self.selected_documents())
        if not docs:
            messagebox.showinfo("Импорт", "Выберите хотя бы один раскрой.", parent=self)
            return
        if not self.owner.flush():
            messagebox.showerror("Импорт", "Исправьте параметры заказа перед импортом.", parent=self)
            return
        try:
            order, stats = apply_import(self.owner.order, docs, configs=self.configs, mappings=self.mappings,
                parent_id=self.parent_choices[self.parent_var.get()], group_name=self.group_var.get(),
                paint_sides=2 if self.sides_var.get() == "Две стороны" else 1, update_quantities=self.quantity_var.get(),
                replace=self.replace_var.get(), area_only=self.area_only_var.get(), accept_conflicting_mass=self.mass_var.get(),
                update_geometry=self.geometry_var.get())
            calculated = calculate_production(order)
            # One atomic database write before replacing the visible order.
            self.owner.db.set_state(self.owner.draft_key, {"order": calculated, "saved_id": self.owner.saved_id})
            self.owner.order = calculated
            self.owner._load_widgets()
        except (ValueError, KeyError, OSError, sqlite3.Error) as error:
            messagebox.showerror("Импорт не применён", str(error), parent=self)
            return
        self.owner.status(f"Импортировано раскроев: {stats['imported']}; создано деталей: {stats['created']}; сопоставлено: {stats['matched']}; пропущено повторов: {len(stats['skipped'])}")
        self.destroy()
