"""Shared desktop design tokens inspired by the supplied layouts."""
import tkinter as tk
from tkinter import ttk

BG = '#f5f7fa'
INK = '#263344'
MUTED = '#8290a3'
BLUE = '#2868aa'
LINE = '#e1e7ef'


class AutoScrollbar(ttk.Scrollbar):
    """Only occupy layout space when the attached view can actually scroll."""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._saved_geometry = None

    def set(self, first, last):
        super().set(first, last)
        needed = float(first) > 0.000001 or float(last) < 0.999999
        manager = self.winfo_manager()
        if not needed and manager:
            if manager == 'grid':
                self._saved_geometry = ('grid', self.grid_info(), None)
                self.grid_remove()
            elif manager == 'pack':
                siblings = self.master.pack_slaves()
                index = siblings.index(self)
                next_widget = siblings[index+1] if index+1 < len(siblings) else None
                self._saved_geometry = ('pack', self.pack_info(), next_widget)
                self.pack_forget()
        elif needed and not manager and self._saved_geometry:
            mode, options, next_widget = self._saved_geometry
            options = dict(options)
            if mode == 'grid':
                self.grid(**options)
            else:
                if next_widget is not None and next_widget.winfo_exists() and next_widget.winfo_manager() == 'pack':
                    options['before'] = next_widget
                self.pack(**options)


class IconButton(tk.Canvas):
    """Geometrically centered icons, independent of font baseline metrics."""
    def __init__(self, parent, icon, command, background=BG, **kwargs):
        super().__init__(parent,width=34,height=38,bg=background,highlightthickness=1,highlightbackground=background,highlightcolor=BLUE,takefocus=True,cursor='hand2',**kwargs)
        self.normal = background
        self.icon, self.command = icon, command
        self.bind('<Configure>',self.draw)
        self.bind('<Enter>',lambda e:self.configure(bg='#fbe9e9' if icon=='close' else '#e6effb'))
        self.bind('<Leave>',lambda e:self.configure(bg=self.normal))
        self.bind('<Button-1>',lambda e:command())
        self.bind('<Return>',lambda e:command())
        self.bind('<space>',lambda e:command())
        self.bind('<FocusIn>',lambda e:self.configure(highlightbackground=BLUE))
        self.bind('<FocusOut>',lambda e:self.configure(highlightbackground=self.normal))

    def draw(self,event=None):
        self.delete('all')
        x,y=self.winfo_width()/2,self.winfo_height()/2
        if self.icon=='close':
            self.create_line(x-5,y-5,x+5,y+5,fill='#697b92',width=2,capstyle='round')
            self.create_line(x-5,y+5,x+5,y-5,fill='#697b92',width=2,capstyle='round')
        else:
            self.create_line(x-7,y,x+7,y,fill=BLUE,width=2,capstyle='round')
            self.create_line(x,y-7,x,y+7,fill=BLUE,width=2,capstyle='round')


def wheel_area(canvas, content):
    top=canvas.winfo_toplevel()
    def wheel(event):
        widget=event.widget
        while widget is not None:
            if widget in (canvas,content):
                if canvas.yview() != (0.0,1.0):
                    canvas.yview_scroll(-int(event.delta/120),'units')
                return 'break'
            widget=getattr(widget,'master',None)
    binding=top.bind('<MouseWheel>',wheel,add='+')
    def cleanup(event):
        if event.widget==canvas:
            top.unbind('<MouseWheel>',binding)
    canvas.bind('<Destroy>',cleanup,add='+')


def apply_theme(root):
    s = ttk.Style(root)
    s.theme_use('clam')
    s.configure('.', font=('Segoe UI', 9), foreground=INK, background=BG)
    for name in ('TFrame', 'Prod.TFrame'):
        s.configure(name, background=BG)
    s.configure('Card.TFrame', background='white', bordercolor=LINE, relief='solid', borderwidth=1)
    s.configure('Surface.TFrame', background='white', borderwidth=0)
    for name in ('TLabel', 'Prod.TLabel', 'Field.TLabel'):
        s.configure(name, background=BG, foreground=INK)
    s.configure('Card.TLabel', background='white', foreground=INK)
    s.configure('Muted.TLabel', foreground=MUTED)
    s.configure('Title.TLabel', font=('Segoe UI Semibold', 13))
    s.configure('PageTitle.TLabel', font=('Segoe UI Semibold', 19))
    s.configure('Info.TLabel', background='#edf4fc', foreground=BLUE, padding=10)
    for name in ('TButton', 'Prod.TButton'):
        s.configure(name, background='white', foreground=INK, bordercolor=LINE, lightcolor='white', darkcolor='white', padding=(12,7), relief='flat')
        s.map(name, background=[('active','#edf4fc'),('pressed','#dceafa')])
    for name in ('Accent.TButton', 'ProdAccent.TButton'):
        s.configure(name, background=BLUE, foreground='white', bordercolor=BLUE, padding=(14,7))
        s.map(name, background=[('active','#347cbe'),('pressed','#20568c')], foreground=[('disabled','#cbd6e3'),('!disabled','white')])
    for name in ('Compact.TButton','Compact.TMenubutton'):
        s.configure(name,background='white',foreground=INK,padding=(8,4),width=0,borderwidth=1,bordercolor=LINE,font=('Segoe UI',9))
    for name in ('TEntry','Prod.TEntry','TCombobox','TSpinbox'):
        s.configure(name, fieldbackground='white', bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, padding=(7,5), arrowcolor=MUTED)
        s.map(name, bordercolor=[('focus',BLUE)], fieldbackground=[('readonly','#f8fafc')])
    for name in ('TCheckbutton','Prod.TCheckbutton','TRadiobutton'):
        s.configure(name, background=BG)
        s.map(name, background=[('active',BG)])
    for name in ('Treeview','Prod.Treeview'):
        s.configure(name, background='white', fieldbackground='white', foreground=INK, rowheight=32, borderwidth=0, bordercolor=LINE, font=('Segoe UI',9))
        s.map(name, background=[('selected','#e8f1fd')], foreground=[('selected',BLUE)])
        s.configure(name+'.Heading', background='#f5f8fc', foreground=MUTED, font=('Segoe UI',9), padding=(8,10), borderwidth=0, relief='flat')
        s.map(name+'.Heading', background=[('active','#edf3fa')])
    for name in ('TNotebook','Prod.TNotebook'):
        s.configure(name, background=BG, borderwidth=0, tabmargins=(0,8,0,0), bordercolor=BG, lightcolor=BG, darkcolor=BG)
        s.configure(name+'.Tab', background=BG, foreground=MUTED, padding=(14,10), borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG)
        s.map(name+'.Tab', background=[('selected','white'),('active','#edf3fa')], foreground=[('selected',BLUE)])
    s.layout('Shell.TNotebook.Tab', [])
    s.configure('Shell.TNotebook', background=BG, borderwidth=0, tabmargins=0, bordercolor=BG, lightcolor=BG, darkcolor=BG)
    s.configure('TLabelframe', background=BG, bordercolor=LINE, relief='solid')
    s.configure('TLabelframe.Label', background=BG, foreground=INK, font=('Segoe UI Semibold',10))
    for name in ('Vertical.TScrollbar','Horizontal.TScrollbar'):
        orient='Vertical' if name.startswith('Vertical') else 'Horizontal'
        s.layout(name,[(orient+'.Scrollbar.trough',{'sticky':'ns' if orient=='Vertical' else 'we','children':[(orient+'.Scrollbar.thumb',{'expand':1,'sticky':'nswe'})]})])
        s.configure(name, background='#c3cedb', troughcolor=BG, borderwidth=0, arrowsize=9, width=9, relief='flat',gripcount=0,lightcolor='#c3cedb',darkcolor='#c3cedb',bordercolor=BG)
        s.map(name,background=[('pressed','#7188a3'),('active','#91a5bd')])
    s.configure('TSeparator',background=LINE)
    s.configure('TProgressbar',background=BLUE,troughcolor='#eaf0f7',borderwidth=0)
    root.option_add('*Menu.font',('Segoe UI',10))
    root.option_add('*Menu.background','white')
    root.option_add('*Menu.foreground',INK)
    root.option_add('*Menu.activeBackground','#e8f1fd')
    root.option_add('*Menu.activeForeground',BLUE)
    root.option_add('*Menu.relief','flat')
    root.option_add('*Text.relief','flat')
    root.option_add('*Text.borderWidth',0)
    root.option_add('*Text.highlightThickness',1)
    root.option_add('*Text.highlightBackground',LINE)
    root.option_add('*Text.highlightColor',BLUE)
    root.option_add('*Text.background','white')
    root.option_add('*Text.foreground',INK)
    root.option_add('*Text.selectBackground','#dceafa')
    root.option_add('*Text.selectForeground',INK)


def side_card(parent, width=310):
    """A fixed-width inspector with its own scroll area on short screens."""
    outer = ttk.Frame(parent, style='Card.TFrame', width=width, padding=1)
    outer.pack(side='right', fill='y', padx=(16,0))
    outer.pack_propagate(False)
    canvas = tk.Canvas(outer, bg='white', highlightthickness=0, width=width-16)
    scroll = AutoScrollbar(outer, command=canvas.yview)
    scroll.pack(side='right', fill='y')
    canvas.pack(side='left', fill='both', expand=True)
    canvas.configure(yscrollcommand=scroll.set)
    inner = ttk.Frame(canvas, style='Surface.TFrame', padding=14)
    window = canvas.create_window(0,0,anchor='nw',window=inner)
    canvas.bind('<Configure>', lambda e:canvas.itemconfigure(window,width=e.width))
    inner.bind('<Configure>', lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
    wheel_area(canvas,inner)
    return inner
