"""Folder-based reusable parts library and selectable CAM import preview."""
from copy import deepcopy
from queue import Queue, Empty
from threading import Thread
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import sqlite3
from part_catalog import PartCatalog, import_candidates
from production import new_node
from production_ui import FormDialog, table
from reports import number


class PartsManager(ttk.Frame):
    spec = {'title':'Детали'}

    def __init__(self, parent, db, on_change=None):
        super().__init__(parent,padding=12)
        self.catalog = PartCatalog(db)
        self.db = db
        self.importing = False
        bar = ttk.Frame(self)
        bar.pack(fill='x',pady=(0,10))
        for title,callback in (('+ Папка',self.add_folder),('+ Деталь',self.edit_part),('Из файла',self.import_files),
                               ('Изменить',self.edit_selected),('Переместить',self.move_selected),('Удалить',self.delete_selected)):
            ttk.Button(bar,text=title,command=callback,style='Compact.TButton').pack(side='left',padx=(0,5))
        ttk.Button(bar,text='Добавить в заказ',command=self.add_to_order,style='Accent.TButton').pack(side='right')
        search = ttk.Frame(self)
        search.pack(fill='x',pady=(0,10))
        ttk.Label(search,text='Поиск детали').pack(side='left',padx=(0,8))
        self.query = tk.StringVar()
        ttk.Entry(search,textvariable=self.query,width=42).pack(side='left')
        ttk.Label(search,text='Папки содержат шаблоны деталей, не заказы.',style='Muted.TLabel').pack(side='right')
        self.tree = table(self,[('code','Обозначение',130,'w'),('size','Размеры, мм',150,'center'),
                               ('material','Материал',180,'w'),('qty','Кол-во',65,'center')],tree=True)
        self.tree.configure(selectmode='extended')
        self.tree.heading('#0',text='Папка / деталь',anchor='w')
        self.tree.column('#0',width=320,minwidth=200)
        self.tree.bind('<Double-1>',lambda e:self.edit_selected())
        self.tree.bind('<<TreeviewSelect>>',lambda e:self.describe())
        self.info = tk.StringVar()
        ttk.Label(self,textvariable=self.info,style='Muted.TLabel',wraplength=1000,justify='left').pack(fill='x',pady=10)
        self.query.trace_add('write',lambda *a:self.reload())
        self.reload()

    def reload(self):
        selection = self.tree.selection()
        self.folders = {r['id']:r for r in self.catalog.folders()}
        self.parts = {r['id']:r for r in self.catalog.parts()}
        self.tree.delete(*self.tree.get_children())
        self.tree.insert('','end',iid='root',text='Все детали',open=True,tags=('assembly',))
        def folders(parent, iid):
            for folder in self.folders.values():
                if folder['parent_id']==parent:
                    key='f'+str(folder['id'])
                    self.tree.insert(iid,'end',iid=key,text='▸ '+folder['name'],open=True,tags=('assembly',))
                    folders(folder['id'],key)
        folders(None,'root')
        query=self.query.get().strip().casefold()
        for r in self.parts.values():
            p=r['payload']
            if query and query not in (p['name']+' '+p.get('code','')).casefold():
                continue
            self.tree.insert('f'+str(r['folder_id']) if r['folder_id'] else 'root','end',iid='p'+str(r['id']),text=p['name'],
                             values=(p.get('code',''),f"{number(p['width'])} × {number(p['height'])} × {number(p['thickness'])}",p.get('material',{}).get('name','—'),p['quantity']))
        retained=[s for s in selection if self.tree.exists(s)]
        self.tree.selection_set(retained or ['root'])
        self.describe()

    def chosen(self):
        selected=self.tree.selection()
        return selected[0] if selected else 'root'

    def destination(self):
        key=self.chosen()
        return int(key[1:]) if key.startswith('f') else (self.parts[int(key[1:])]['folder_id'] if key.startswith('p') else None)

    def folder_choices(self):
        choices={'Без папки':None}
        def visit(parent,path):
            for f in self.folders.values():
                if f['parent_id']==parent:
                    name=path+f['name']
                    choices[f"{name} [№{f['id']}]"]=f['id']
                    visit(f['id'],name+' / ')
        visit(None,'')
        return choices

    def describe(self):
        key=self.chosen()
        if key.startswith('p') and int(key[1:]) in self.parts:
            p=self.parts[int(key[1:])]['payload']
            self.info.set(f"{p['name']} · масса {number(p.get('mass',0))} кг · резка {number(p.get('laser_minutes',0))} мин\n"+p.get('note',''))
        else:
            self.info.set('Выберите папку для добавления или импорта. Для добавления в заказ выделите детали (Ctrl / Shift).')

    def add_folder(self):
        parent=self.destination()
        def save(v):
            key=self.catalog.save_folder(v['name'],parent)
            self.reload()
            self.tree.selection_set('f'+str(key))
        FormDialog(self,'Новая папка',[('name','Название папки','','text')],save)

    def edit_selected(self):
        key=self.chosen()
        if key.startswith('f'):
            f=self.folders[int(key[1:])]
            def save(v):
                self.catalog.save_folder(v['name'],f['parent_id'],f['id'])
                self.reload()
            FormDialog(self,'Название папки',[('name','Название',f['name'],'text')],save)
        elif key.startswith('p'):
            self.edit_part(True)

    def edit_part(self,edit=False):
        key=self.chosen()
        record=self.parts.get(int(key[1:])) if edit and key.startswith('p') else None
        if edit and not record:
            return
        node=deepcopy(record['payload']) if record else new_node('part')
        folder=self.destination()
        choices=self.folder_choices()
        materials={'Не выбран':{}}
        for m in self.db.list_records('materials',active_only=True):
            materials[f"{m['name']} [№{m['id']}]"]=m
        if node.get('material'):
            materials['Материал из сохранённой детали']=node['material']
        current='Материал из сохранённой детали' if node.get('material') else 'Не выбран'
        fields=[('name','Наименование',node['name'],'text'),('code','Обозначение',node.get('code',''),'text'),
                ('folder','Папка',next(k for k,v in choices.items() if v==folder),list(choices)),
                ('material','Материал',current,list(materials))]
        for key,title in (('quantity','Количество по умолчанию'),('width','Ширина, мм'),('height','Длина, мм'),('thickness','Толщина, мм'),
                          ('area','Площадь покраски одной детали, м²'),('mass','Масса одной детали, кг'),('bends','Гибов'),
                          ('laser_minutes','Резка одной детали, мин'),('filing_minutes','Напилка одной детали, мин')):
            fields.append((key,title,node.get(key,0),'int' if key in ('quantity','bends') else 'number'))
        fields += [('paint','Включать покраску',node.get('paint',True),'bool'),('note','Комментарий / источник',node.get('note',''),'text')]
        def save(v):
            dest=choices[v.pop('folder')]
            material=deepcopy(materials[v.pop('material')])
            node.update(v,material=material)
            ident=self.catalog.save_part(node,dest,record['id'] if record else None)
            self.query.set('')
            self.reload()
            self.tree.selection_set('p'+str(ident))
            self.tree.see('p'+str(ident))
        FormDialog(self,'Деталь справочника',fields,save,'Все параметры относятся к одной детали. Нулевые неизвестные значения проверьте перед расчётом заказа.')

    def move_selected(self):
        key=self.chosen()
        if key=='root': return
        selected=self.tree.selection()
        if len(selected)>1 and any(not k.startswith('p') for k in selected):
            messagebox.showinfo('Перемещение','Выберите одну папку или несколько деталей.',parent=self)
            return
        choices=self.folder_choices()
        def save(v):
            destination=choices[v['folder']]
            if key.startswith('f'):
                f=self.folders[int(key[1:])]
                self.catalog.save_folder(f['name'],destination,f['id'])
            else:
                self.catalog.move_parts([int(k[1:]) for k in selected],destination)
            self.reload()
        FormDialog(self,'Переместить',[('folder','В папку',next(iter(choices)),list(choices))],save)

    def delete_selected(self):
        key=self.chosen()
        if key=='root': return
        if not messagebox.askyesno('Удаление','Удалить выбранный элемент справочника? Детали уже созданных заказов не изменятся.',parent=self): return
        try:
            self.catalog.delete(int(key[1:]),folder=key.startswith('f'))
            self.reload()
        except ValueError as error:
            messagebox.showwarning('Удаление',str(error),parent=self)

    def add_to_order(self):
        records=[self.parts[int(k[1:])] for k in self.tree.selection() if k.startswith('p')]
        if not records:
            messagebox.showinfo('Детали','Выделите одну или несколько деталей.',parent=self)
            return
        self.winfo_toplevel().add_catalog_parts(records)

    def import_files(self):
        if self.importing: return
        paths=filedialog.askopenfilenames(parent=self,title='Импорт деталей в выбранную папку',filetypes=[('Файлы раскроя','*.pdf *.xlsx *.html *.htm'),('Все файлы','*.*')])
        if not paths: return
        folder=self.destination()
        queue=Queue()
        self.importing=True
        self.info.set('Чтение файлов…')
        def worker():
            try:
                from cam_import import read_batch
                batch=read_batch(paths)
                queue.put((batch,import_candidates(batch),None))
            except Exception as error:
                queue.put((None,None,str(error)))
        def poll():
            try: batch,candidates,error=queue.get_nowait()
            except Empty:
                self.after(100,poll)
                return
            self.importing=False
            if error:
                self.info.set(error)
                return
            self.preview_import(batch,candidates,folder)
        Thread(target=worker,daemon=True).start()
        self.after(100,poll)

    def preview_import(self,batch,candidates,folder):
        win=tk.Toplevel(self)
        win.title('Импорт деталей — предварительный просмотр')
        win.geometry('1000x700')
        win.transient(self.winfo_toplevel())
        win.grab_set()
        destination=self.folders.get(folder,{}).get('name','Без папки')
        ttk.Label(win,text=f'Папка: {destination} · найдено деталей: {len(candidates)}',padding=12).pack(fill='x')
        footer=ttk.Frame(win,padding=12)
        footer.pack(side='bottom',fill='x')
        notices=batch['errors']+batch['duplicates']+[d['filename']+': '+w for d in batch['documents'] for w in d['warnings']]
        notices.insert(0,'В справочнике сохраняется шаблон с количеством 1. Неизвестные значения — 0; противоречивая масса BIT не переносится. Повторный импорт пропускает уже добавленные детали, не перезаписывая ручные изменения.')
        log=tk.Text(win,height=7,wrap='word',font=('Segoe UI',9))
        log.pack(side='bottom',fill='x',padx=12,pady=8)
        log.insert('1.0','\n'.join(notices)); log.configure(state='disabled')
        rows=table(win,[('name','Деталь',280,'w'),('size','Размеры',160,'center'),('material','Материал',180,'w'),('time','Резка, мин',90,'e')])
        rows.configure(selectmode='extended')
        for i,(_,p) in enumerate(candidates):
            rows.insert('','end',iid=str(i),values=(p['name'],f"{p['width']} × {p['height']} × {p['thickness']}",p.get('material',{}).get('name',''),number(p['laser_minutes'])))
        rows.selection_set(rows.get_children())
        def save():
            selected=[candidates[int(i)] for i in rows.selection()]
            if not selected:
                messagebox.showinfo('Импорт','Выберите детали для импорта (Ctrl / Shift).',parent=win)
                return
            try:
                added,skipped=self.catalog.import_parts(selected,folder)
            except (ValueError,sqlite3.Error) as error:
                messagebox.showerror('Импорт',str(error),parent=win)
                return
            win.destroy()
            self.reload()
            self.info.set(f'Добавлено: {added}. Уже в справочнике: {skipped}.')
        ttk.Button(footer,text='Добавить выбранные',style='Accent.TButton',command=save).pack(side='right')
        ttk.Button(footer,text='Отмена',command=win.destroy).pack(side='right',padx=8)
        ttk.Button(footer,text='Выделить всё',command=lambda:rows.selection_set(rows.get_children())).pack(side='left')
        win.import_tree=rows
        win.confirm_import=save
        return win
