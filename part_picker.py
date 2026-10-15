"""Add a catalog copy or a manually entered part without leaving the order."""
import tkinter as tk
from tkinter import ttk

from part_catalog import PartCatalog, copy_to_order
from production import calculate_production, numeric
from production_ui import table
from reports import number
from ui_theme import BG


class PartPicker(tk.Toplevel):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.configure(bg=BG)
        self.title('Добавить деталь')
        self.geometry(f'860x{min(680, self.winfo_screenheight()-120)}')
        self.minsize(720, 480)
        self.transient(owner.winfo_toplevel())
        selected = owner.selected_node()
        self.parent_id = (selected['id'] if selected and selected['kind'] == 'assembly'
                          else selected.get('parent_id', '') if selected else '')
        heading = ttk.Frame(self, padding=(20, 16))
        heading.pack(fill='x')
        ttk.Label(heading, text='Добавить деталь', style='Title.TLabel').pack(anchor='w')
        parent_name = next((n['name'] for n in owner.order['nodes'] if n['id'] == self.parent_id), 'Корень заказа')
        ttk.Label(heading, text=f'В состав: {parent_name}', style='Muted.TLabel').pack(anchor='w', pady=(6, 0))
        self.book = ttk.Notebook(self)
        self.book.pack(fill='both', expand=True, padx=16, pady=(0, 16))
        self.catalog_page = ttk.Frame(self.book, padding=12)
        self.manual_page = ttk.Frame(self.book)
        self.book.add(self.catalog_page, text='Из справочника')
        self.book.add(self.manual_page, text='Вручную')
        self.manual_form = None
        self.book.bind('<<NotebookTabChanged>>', self.change_mode)
        catalog = PartCatalog(owner.db)
        self.folders = catalog.folders()
        self.parts = {r['id']: r for r in catalog.parts()}
        search = ttk.Frame(self.catalog_page)
        search.pack(fill='x', pady=(0, 12))
        ttk.Label(search, text='Поиск').pack(side='left', padx=(0, 10))
        self.query = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.query)
        entry.pack(side='left', fill='x', expand=True)
        footer = ttk.Frame(self.catalog_page)
        footer.pack(side='bottom', fill='x', pady=(12, 0))
        self.error = tk.StringVar()
        ttk.Label(footer, textvariable=self.error, foreground='#b42318', wraplength=700).pack(fill='x')
        actions = ttk.Frame(footer)
        actions.pack(fill='x', pady=(8, 0))
        ttk.Label(actions, text='Количество на узел').pack(side='left', padx=(0, 10))
        self.quantity = tk.StringVar(value='1')
        ttk.Spinbox(actions, from_=1, to=1000000, textvariable=self.quantity, width=7).pack(side='left')
        ttk.Button(actions, text='Добавить', style='Accent.TButton', command=self.add_selected).pack(side='right')
        ttk.Button(actions, text='Отмена', command=self.destroy).pack(side='right', padx=8)
        self.details = tk.StringVar(value='Выберите папку и деталь. Можно искать по названию или артикулу.')
        ttk.Label(footer, textvariable=self.details, style='Muted.TLabel', wraplength=720).pack(fill='x', pady=(10, 0))
        self.tree = table(self.catalog_page, [('code', 'Артикул', 130, 'w'), ('size', 'Размеры, мм', 160, 'center')], tree=True)
        self.tree.heading('#0', text='Папки и детали', anchor='w')
        self.tree.column('#0', width=320, minwidth=200)
        self.tree.bind('<<TreeviewSelect>>', self.describe)
        self.tree.bind('<Double-1>', self.double_click)
        self.query.trace_add('write', lambda *args: self.reload())
        self.reload()
        self.bind('<Escape>', lambda e: self.destroy())
        self.bind('<Control-Return>', lambda e: self.accept())
        self.wait_visibility()
        self.grab_set()
        entry.focus_set()

    def change_mode(self, event=None):
        if self.book.select() == str(self.manual_page) and self.manual_form is None:
            self.manual_form = self.owner.edit_node(container=self.manual_page, on_close=self.destroy)

    def reload(self):
        self.tree.delete(*self.tree.get_children())
        self.tree.insert('', 'end', iid='root', text='Все детали', open=True)
        query = self.query.get().strip().casefold()
        def folders(parent, item):
            for folder in self.folders:
                if folder['parent_id'] == parent:
                    key = f"f{folder['id']}"
                    self.tree.insert(item, 'end', iid=key, text=folder['name'], open=bool(query), tags=('assembly',))
                    folders(folder['id'], key)
        folders(None, 'root')
        count = 0
        for key, record in self.parts.items():
            part = record['payload']
            if query and query not in (part['name']+' '+part.get('code', '')).casefold():
                continue
            parent = f"f{record['folder_id']}" if record['folder_id'] else 'root'
            self.tree.insert(parent, 'end', iid=f'p{key}', text=part['name'],
                             values=(part.get('code', ''), f"{number(part['width'])} × {number(part['height'])}"))
            count += 1
        self.details.set('Выберите папку и деталь.' if count else
                         'Ничего не найдено. Измените поиск или добавьте деталь на вкладке «Вручную».')

    def selected(self):
        selection = self.tree.selection()
        return self.parts.get(int(selection[0][1:])) if selection and selection[0].startswith('p') else None

    def describe(self, event=None):
        record = self.selected()
        if record:
            part = record['payload']
            self.quantity.set(str(part.get('quantity', 1)))
            self.details.set(f"{part['name']} · {part.get('material', {}).get('name', 'Материал не выбран')}\n"
                             f"Толщина {number(part['thickness'])} мм · масса {number(part['mass'])} кг · резка {number(part['laser_minutes'])} мин")
            self.error.set('')

    def double_click(self, event):
        row = self.tree.identify_row(event.y)
        if row.startswith('p'):
            self.tree.selection_set(row)
            self.add_selected()
            return 'break'

    def accept(self):
        if self.book.select() == str(self.manual_page):
            self.manual_form.save()
        else:
            self.add_selected()

    def add_selected(self):
        record = self.selected()
        if not record:
            self.error.set('Выберите деталь внутри папки.')
            return
        try:
            quantity = numeric(self.quantity.get(), 'Количество', positive=True, integer=True)
            node = copy_to_order([record], self.parent_id)[0]
            node['quantity'] = quantity
            candidate = dict(self.owner.order, nodes=[*self.owner.order['nodes'], node])
            self.owner.order = calculate_production(candidate)
        except (ValueError, KeyError) as error:
            self.error.set(str(error))
            return
        self.owner.refresh()
        self.owner.tree.selection_set(node['id'])
        self.owner.tree.see(node['id'])
        self.owner.status(f"Добавлена деталь: {node['name']}")
        self.destroy()
