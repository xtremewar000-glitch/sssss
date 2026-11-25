"""A minimal stand-in for tkinter / tkinter.ttk / PIL.ImageTk so the Models tab can be CONSTRUCTED AND DRIVEN without a display.
It checks wiring (names, callbacks, selection flow, rendering pipeline); it cannot check how anything looks."""
import sys, types


class TclError(Exception):
    pass


class Var:
    def __init__(self, master=None, value=None, **k):
        self._v, self._tr = value, []

    def get(self):
        return self._v

    def set(self, v):
        self._v = v
        for cb in list(self._tr):
            cb("v", "", "write")

    def trace_add(self, mode, cb):
        self._tr.append(cb)


class StringVar(Var):
    def __init__(self, master=None, value="", **k):
        super().__init__(master, value)


class IntVar(Var):
    def __init__(self, master=None, value=0, **k):
        super().__init__(master, value)


class DoubleVar(IntVar):
    pass


class BooleanVar(Var):
    def __init__(self, master=None, value=False, **k):
        super().__init__(master, value)


class W:
    AFTERS = []
    def __init__(self, master=None, **k):
        self.master, self._o, self._b, self.mapped = master, dict(k), {}, True
    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return lambda *a, **k: None
    def configure(self, **k):
        self._o.update(k)
    config = configure
    def cget(self, k):
        return self._o.get(k)
    def __setitem__(self, k, v):
        self._o[k] = v
    def __getitem__(self, k):
        return self._o.get(k)
    def bind(self, ev, cb, add=None):
        self._b[ev] = cb
    def fire(self, ev, **kw):
        e = types.SimpleNamespace(x=0, y=0, state=0, delta=0, num=0, **kw)
        return self._b[ev](e)
    def winfo_children(self):
        return []
    def yview(self, *a):
        return (0.0, 1.0)
    xview = yview
    def bbox(self, *a):
        return (0, 0, 10, 10)
    def winfo_class(self):
        return getattr(self, "klass", "Frame")
    def winfo_width(self):
        return 640
    def winfo_height(self):
        return 360
    def winfo_exists(self):
        return True
    def winfo_ismapped(self):
        return self.mapped
    def after(self, ms, fn=None, *a):
        W.AFTERS.append(fn)
        return len(W.AFTERS)
    def after_cancel(self, j):
        if 0 < j <= len(W.AFTERS):
            W.AFTERS[j - 1] = None
    def grid_remove(self):
        self.mapped = False
    def grid(self, *a, **k):
        self.mapped = True
    def place_forget(self):
        self.mapped = False
    def place(self, **k):
        self.mapped = True
    def clipboard_clear(self):
        self.clip = ""
    def clipboard_append(self, t):
        self.clip = getattr(self, "clip", "") + t


class Canvas(W):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.items = []
    def create_image(self, x, y, **k):
        self.items.append(("image", k.get("tags", "")))
    def create_text(self, x, y, **k):
        self.items.append(("text", k.get("text")))
    def delete(self, what):
        self.items = [] if what == "all" else [i for i in self.items if i[1] != what]


class Text(W):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.buf = ""
    def insert(self, idx, txt, *tags):
        self.buf += txt
    def delete(self, a, b):
        self.buf = ""
    def get(self, a, b=None):
        return self.buf


class Treeview(W):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.nodes, self.order, self._sel, self._focus = {}, [], [], ""
    def insert(self, parent, index, iid=None, **k):
        iid = iid or str(len(self.nodes))
        self.nodes[iid] = dict(parent=parent, **k)
        self.order.append(iid)
        return iid
    def delete(self, *items):
        for i in items:
            for c in self.get_children(i):
                self.delete(c)
            self.nodes.pop(i, None)
            if i in self.order:
                self.order.remove(i)
        self._sel = [s for s in self._sel if s in self.nodes]
    def get_children(self, item=""):
        return tuple(i for i in self.order if self.nodes[i]["parent"] == item)
    def exists(self, i):
        return i in self.nodes
    def selection(self):
        return tuple(self._sel)
    def selection_set(self, *items):
        if len(items) == 1 and isinstance(items[0], (list, tuple)):
            items = items[0]
        self._sel = [i for i in items if i in self.nodes]
        cb = self._b.get("<<TreeviewSelect>>")
        if cb:
            cb(None)
    def selection_remove(self, *items):
        if len(items) == 1 and isinstance(items[0], (list, tuple)):
            items = items[0]
        self._sel = [s for s in self._sel if s not in items]
        cb = self._b.get("<<TreeviewSelect>>")
        if cb:
            cb(None)
    def focus(self, i=None):
        return self._focus
    def item(self, iid, **k):
        return self.nodes[iid]
    def identify_region(self, x, y):
        return "cell"
    def identify_column(self, x):
        return "#1"
    def identify_row(self, y):
        return self._row


class Progressbar(W):
    pass


class Style:
    """records the theme so tests can check every style the app asks for"""
    conf, maps, theme = {}, {}, None
    def configure(self, name, **k):
        Style.conf.setdefault(name, {}).update(k)
    def map(self, name, **k):
        Style.maps.setdefault(name, {}).update(k)
    def theme_use(self, n=None):
        Style.theme = n
    def lookup(self, *a, **k):
        return ""


class Menu(W):
    """records what was added, so tests can read the menu bar"""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.entries = []
    def add_command(self, **k):
        self.entries.append(("command", k))
    def add_cascade(self, **k):
        self.entries.append(("cascade", k))
    def add_radiobutton(self, **k):
        self.entries.append(("radio", k))
    def add_checkbutton(self, **k):
        self.entries.append(("check", k))
    def add_separator(self, **k):
        self.entries.append(("separator", k))


class Tk(W):
    """a root window: screen size, geometry, state, bindings and the option database are recorded"""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.geom, self.st, self.options, self.class_binds, self.destroyed = None, "normal", [], {}, False
    def winfo_screenwidth(self):
        return 1920
    def winfo_screenheight(self):
        return 1080
    def winfo_x(self):
        return 120
    def winfo_y(self):
        return 60
    def geometry(self, g=None):
        if g is not None:
            self.geom = g
    def state(self, s=None):
        if s is not None:
            self.st = s
        return self.st
    def option_add(self, pat, val, *a):
        self.options.append((pat, val))
    def bind_class(self, cls, ev, cb, add=None):
        self.class_binds[(cls, ev)] = cb
    def destroy(self):
        self.destroyed = True


def install():
    tk = types.ModuleType("tkinter")
    ttk = types.ModuleType("tkinter.ttk")
    for n in ("StringVar", "IntVar", "DoubleVar", "BooleanVar", "TclError", "Canvas", "Text"):
        setattr(tk, n, globals()[n])
    for n in ("Toplevel", "Frame", "Label", "Button", "Entry", "Scrollbar", "Listbox", "Checkbutton", "Radiobutton"):
        setattr(tk, n, W)
    tk.Tk, tk.Menu = Tk, Menu
    font = types.ModuleType("tkinter.font")
    class Font(W):
        def copy(self):
            return Font()
    font.nametofont = lambda name: Font()
    font.Font = Font
    tk.font = font
    sys.modules["tkinter.font"] = font
    for n in ("Frame", "Label", "Button", "Entry", "Combobox", "Checkbutton", "Radiobutton", "Scrollbar", "LabelFrame", "PanedWindow",
              "Separator", "Style", "Notebook", "Scale", "Spinbox", "Labelframe"):
        setattr(ttk, n, W)
    ttk.Treeview, ttk.Progressbar, ttk.Style = Treeview, Progressbar, Style
    log = []
    mb = types.ModuleType("tkinter.messagebox")
    for n in ("showinfo", "showerror", "showwarning"):
        setattr(mb, n, lambda *a, _n=n, **k: log.append((_n, a)))
    for n in ("askyesno", "askokcancel", "askyesnocancel"):
        setattr(mb, n, lambda *a, _n=n, **k: (log.append((_n, a)), True)[1])
    fd = types.ModuleType("tkinter.filedialog")
    for n in ("askopenfilename", "asksaveasfilename", "askdirectory"):
        setattr(fd, n, lambda *a, **k: "")
    sd, cc = types.ModuleType("tkinter.simpledialog"), types.ModuleType("tkinter.colorchooser")
    tk.ttk, tk.messagebox, tk.filedialog, tk.simpledialog, tk.colorchooser = ttk, mb, fd, sd, cc
    for name, m in (("tkinter", tk), ("tkinter.ttk", ttk), ("tkinter.messagebox", mb), ("tkinter.filedialog", fd),
                    ("tkinter.simpledialog", sd), ("tkinter.colorchooser", cc)):
        sys.modules[name] = m
    it = types.ModuleType("PIL.ImageTk")
    class PhotoImage:
        def __init__(self, im=None, **k):
            self.im = im
    it.PhotoImage = PhotoImage
    sys.modules["PIL.ImageTk"] = it
    import PIL
    PIL.ImageTk = it
    return log
