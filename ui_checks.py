"""Real geometry checks for auto scrollbars, used by packaged smoke tests."""
import tkinter as tk
from tkinter import ttk
from ui_theme import AutoScrollbar, IconButton


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

    # Tab labels, close controls and the add control share an identical center.
    root.update()
    centers=[]
    for widget in root.tab_strip.winfo_children():
        targets=[widget] if isinstance(widget,IconButton) else widget.winfo_children()
        for target in targets:
            centers.append(target.winfo_rooty()+target.winfo_height()/2)
    assert centers and max(centers)-min(centers)<=1,centers
    original=root.order_tab
    extras=[root.new_order_tab() for _ in range(7)]
    root.update()
    assert root.tab_canvas.xview()[1]-root.tab_canvas.xview()[0]<1
    active=root.order_tab_boxes[root.order_tab]
    assert active.winfo_x()>=root.tab_canvas.canvasx(0)-1
    assert active.winfo_x()+active.winfo_width()<=root.tab_canvas.canvasx(root.tab_canvas.winfo_width())+1
    for tab in extras:
        root.close_order_tab(tab,confirm=False)
    root.select_tab(original)
    root.update()
    assert root.tab_canvas.xview()==(0.0,1.0)
