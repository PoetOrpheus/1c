"""Real geometry checks for auto scrollbars, used by packaged smoke tests."""
import tkinter as tk
from tkinter import ttk
from ui_theme import AutoScrollbar, IconButton, wheel_area


def check_stage_resize(root, tab):
    root.select_stage('stages')
    root.update()
    tree = tab.stages_tree
    key = tab.order['stages'][0]['id']
    for field in ('enabled', 'otk'):
        x, y, width, height = tree.bbox(key, field)
        original = tree.column(field, 'width')
        # Exercise the Treeview's native column drag, not just our callback.
        tree.event_generate('<ButtonPress-1>', x=x+width, y=10)
        tree.event_generate('<B1-Motion>', x=x+width+60, y=10)
        root.update()
        assert tree.column(field, 'width') > original
        for flag_field in ('enabled', 'otk'):
            box = tree.bbox(key, flag_field)
            flag = tab.stage_checkboxes[key, flag_field]
            assert abs(flag.winfo_x()+18 - (box[0]+box[2]/2)) <= 1
        tree.event_generate('<ButtonRelease-1>', x=x+width+60, y=10)
        tree.column(field, width=original)
        tab._queue_stage_flags()
        root.update()


def check_context_menu(root, tab):
    from types import SimpleNamespace
    root.select_stage('tree')
    root.update()
    key = tab.tree.get_children()[0]
    x, y, width, height = tab.tree.bbox(key)
    event = SimpleNamespace(x=x+20, y=y+height//2,
                            x_root=tab.tree.winfo_rootx()+x+20,
                            y_root=tab.tree.winfo_rooty()+y+height//2)
    tab._node_context_menu(event)
    root.update()
    assert tab.node_menu.winfo_ismapped()
    tab.node_menu.event_generate('<Button-3>', x=5, y=5)
    root.update()
    assert not tab.node_menu.winfo_ismapped()
    assert root.grab_current() is None
    tab._node_context_menu(event)
    root.update()
    # A grabbed popup receives the outside click after a Leave event.
    tab.node_menu.event_generate('<Leave>', x=-20, y=-20)
    tab.node_menu.event_generate('<ButtonPress-1>', x=-20, y=-20)
    tab.node_menu.event_generate('<ButtonRelease-1>', x=-20, y=-20)
    root.update()
    assert not tab.node_menu.winfo_ismapped()
    assert root.grab_current() is None
    tab._node_context_menu(event)
    root.update()
    tab.node_menu.focus_force()  # Also works on the isolated display without a window manager.
    root.update()
    tab.node_menu.event_generate('<Escape>')
    root.update()
    assert not tab.node_menu.winfo_ismapped()


def check_laser_actions(root, tab):
    from copy import deepcopy
    from unittest.mock import patch
    from production_ui import FormDialog
    original = deepcopy(tab.order)
    try:
        tab.edit_laser()
        dialog = next(w for w in tab.winfo_children() if isinstance(w, FormDialog))
        dialog.variables['material'].set('Временный раскрой')
        dialog.variables['waste_pct'].set('101')
        dialog.save()
        assert dialog.error.get()
        dialog.variables['waste_pct'].set('0')
        dialog.save()
        added = tab.order['laser_rows'][-1]['id']
        tab.laser_tree.selection_set(added)
        tab.laser_menu.invoke(3)  # move up
        assert tab.order['laser_rows'][0]['id'] == added
        tab.laser_menu.invoke(4)  # move down
        assert tab.order['laser_rows'][-1]['id'] == added
        with patch('production_ui.messagebox.askyesno', return_value=False):
            tab.laser_menu.invoke(5)
        assert tab.laser_tree.exists(added)
        with patch('production_ui.messagebox.askyesno', return_value=True):
            tab.laser_menu.invoke(5)
        assert not tab.laser_tree.exists(added) and not tab.composition_laser_tree.exists(added)

        tab.mapping_menu.invoke(0)
        dialog = next(w for w in tab.winfo_children() if isinstance(w, FormDialog))
        node = next(n for n in tab.order['nodes'] if n['kind'] == 'part')
        choices = next(field[3] for field in dialog.panel.fields if field[0] == 'match')
        dialog.variables['match'].set(next(choice for choice in choices if node['id'][:6] in choice))
        for key, value in (('name', 'Ручное сопоставление'), ('quantity', '2'), ('minutes', '8'), ('area', '1'), ('mass', '2')):
            dialog.variables[key].set(value)
        dialog.save()
        tab.include_var.set(True)
        tab.area_only_var.set(False)
        with patch('production_ui.messagebox.showinfo'):
            tab.transfer_times()
            tab.transfer_times()
        updated = next(n for n in tab.order['nodes'] if n['id'] == node['id'])
        assert updated['laser_minutes'] == 4 and updated['mass'] == 2 and updated['area'] == 1
        row_id = tab.unmatched_tree.get_children()[0]
        tab.unmatched_tree.selection_set(row_id)
        tab.edit_unmatched(True)
        dialog = next(w for w in tab.winfo_children() if isinstance(w, FormDialog))
        dialog.variables['minutes'].set('10')
        dialog.save()
        assert tab.order['laser_unmatched'][0]['minutes'] == 10
        with patch('production_ui.messagebox.askyesno', return_value=True):
            tab.mapping_menu.invoke(1)
        assert not tab.unmatched_tree.get_children()
    finally:
        tab.load_order(original, tab.saved_id)
        root.update()


def check_scrolling(root):
    win=tk.Toplevel(root)
    win.geometry('600x320+10+10')
    win.rowconfigure(0,weight=1)
    win.columnconfigure(0,weight=1)
    tree=ttk.Treeview(win,columns=('value',),show='headings')
    tree.column('value',width=150,stretch=False)
    vertical=AutoScrollbar(win,command=tree.yview)
    horizontal=AutoScrollbar(win,orient='horizontal',command=tree.xview)
    tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
    tree.grid(row=0,column=0,sticky='nsew')
    vertical.grid(row=0,column=1,sticky='ns')
    horizontal.grid(row=1,column=0,sticky='ew')
    root.update()
    assert not vertical.winfo_manager() and not horizontal.winfo_manager()
    for n in range(60): tree.insert('','end',values=(n,))
    tree.column('value',width=1000)
    root.update()
    assert vertical.winfo_manager()=='grid' and horizontal.winfo_manager()=='grid'
    tree.yview_moveto(1);tree.xview_moveto(1)
    tree.delete(*tree.get_children())
    tree.column('value',width=150)
    root.update()
    assert not vertical.winfo_manager() and not horizontal.winfo_manager()
    win.destroy()

    win=tk.Toplevel(root)
    win.geometry('500x300+10+10')
    canvas=tk.Canvas(win,highlightthickness=0)
    scroll=AutoScrollbar(win,command=canvas.yview)
    scroll.pack(side='right',fill='y')
    canvas.pack(fill='both',expand=True)
    canvas.configure(yscrollcommand=scroll.set,scrollregion=(0,0,200,50))
    root.update()
    assert not scroll.winfo_manager()
    for height in (800,50,900,40):
        canvas.configure(scrollregion=(0,0,200,height))
        root.update()
        assert bool(scroll.winfo_manager()) == (height>300)
        if height>300:
            assert win.pack_slaves()[0] is scroll
    canvas.configure(scrollregion=(0,0,200,800))
    root.update()
    win.geometry('500x950')
    root.update()
    assert not scroll.winfo_manager()
    win.destroy()

    # Linux sends wheel movement as buttons 4/5 instead of MouseWheel.
    win=tk.Toplevel(root)
    win.geometry('400x240+10+10')
    canvas=tk.Canvas(win,highlightthickness=0)
    canvas.pack(fill='both',expand=True)
    content=ttk.Frame(canvas)
    canvas.create_window(0,0,anchor='nw',window=content)
    for n in range(80):
        ttk.Label(content,text=str(n)).pack()
    content.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
    wheel_area(canvas,content)
    root.update()
    before=canvas.yview()[0]
    content.event_generate('<Button-5>')
    root.update()
    assert canvas.yview()[0]>before
    win.destroy()

    # Tab labels, close controls and the add control share an identical center.
    root.update()
    centers=[]
    for widget in root.tab_strip.winfo_children():
        targets=[widget] if isinstance(widget,IconButton) else widget.winfo_children()
        for target in targets:
            centers.append(target.winfo_rooty()+target.winfo_height()/2)
    assert centers and max(centers)-min(centers)<=1,centers
    original=root.order_tab
    original_box=root.order_tab_boxes[original]
    extras=[root.new_order_tab() for _ in range(7)]
    root.update()
    assert root.order_tab_boxes[original] is original_box
    assert root.tab_canvas.xview()[1]-root.tab_canvas.xview()[0]<1
    active=root.order_tab_boxes[root.order_tab]
    assert active.winfo_x()>=root.tab_canvas.canvasx(0)-1
    assert active.winfo_x()+active.winfo_width()<=root.tab_canvas.canvasx(root.tab_canvas.winfo_width())+1
    for tab in extras:
        root.close_order_tab(tab,confirm=False)
    root.select_tab(original)
    root.update()
    assert root.order_tab_boxes[original] is original_box
    assert root.tab_canvas.xview()==(0.0,1.0)
