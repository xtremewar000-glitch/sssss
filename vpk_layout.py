"""Blender-style page layout for the tool tabs: a list on the left ("outliner"), the viewport in the middle, collapsible property panels on
the right, with draggable dividers between them.  Nothing here scrolls sideways: the right-hand panel scrolls up / down only, and rows of
buttons wrap onto a second line when the panel is narrow (flowify).

    ws = Workspace(page)                 # page = the tab's frame
    ws.left, ws.center                   # plain frames to build the list / the viewport into
    sec = ws.section("Selected lamps")   # a collapsible panel in the right-hand column
    row = ttk.Frame(sec.body); row.pack(fill="x"); ...pack(side="left") buttons...; ws.flow(row)

Pure helper flow_layout() holds the wrapping maths so it can be tested without a display."""
try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:                       # a machine without Tk (the tests of the pure maths)
    tk = ttk = None

OPEN, CLOSED = "▾", "▸"          # down / right pointing triangles


def flow_layout(sizes, avail, gap_x=0):
    """Wrap boxes left to right.  sizes: [(w, h)] (w includes the padding).  Returns ([(row, col)] per box, widest row).
    A box wider than `avail` gets a row to itself; avail <= 0 means 'do not wrap'."""
    out, row, col, x, widest = [], 0, 0, 0, 0
    for w, _h in sizes:
        if col and avail > 0 and x + w > avail:
            row, col, x = row + 1, 0, 0
        out.append((row, col))
        x += w + gap_x
        col += 1
        widest = max(widest, x)
    return out, widest


def _pad(v):
    """pack_info padx / pady is a number or a pair: (before, after); Tk may hand it over as the text "0 6" """
    if isinstance(v, str) and " " in v.strip():
        v = v.split()
    if isinstance(v, (tuple, list)):
        a = [int(float(str(x))) for x in v]
        return (a[0], a[-1])
    try:
        n = int(float(str(v)))
    except (TypeError, ValueError):
        n = 0
    return (n, n)


class Flow:
    """A row of widgets that was packed side="left", re-laid out with grid so it wraps when it is wider than `limit` gives it.
    Later pack() / pack_forget() calls on the children keep working (they show / hide the child here)."""

    def __init__(self, frame, limit, margin=24):
        self.frame, self.limit, self.margin = frame, limit, margin
        self.kids, self.pads, self.shown = [], {}, {}
        self.key = None
        for k in frame.pack_slaves():
            info = k.pack_info()
            self.kids.append(k)
            self.pads[k] = (_pad(info.get("padx", 0)), _pad(info.get("pady", 0)))
            self.shown[k] = True
        for k in self.kids:
            k.pack_forget()
            k.pack = lambda *a, _k=k, **kw: self.show(_k)
            k.pack_forget = lambda _k=k: self.hide(_k)
        self.layout(force=True)

    def show(self, k):
        self.shown[k] = True
        self.layout(force=True)

    def hide(self, k):
        self.shown[k] = False
        try:
            k.grid_remove()
        except tk.TclError:
            pass
        self.layout(force=True)

    def available(self):
        try:
            w = self.limit.winfo_width() - (self.frame.winfo_rootx() - self.limit.winfo_rootx()) - self.margin
        except tk.TclError:
            return 0
        return w if self.limit.winfo_width() > 60 else 0

    def layout(self, force=False):
        vis = [k for k in self.kids if self.shown.get(k)]
        avail = self.available()
        sizes = []
        for k in vis:
            (l, r), (t, b) = self.pads[k]
            sizes.append((k.winfo_reqwidth() + l + r, k.winfo_reqheight() + t + b))
        pos, _ = flow_layout(sizes, avail)
        key = (tuple(pos), tuple(id(k) for k in vis))
        if key == self.key and not force:
            return
        self.key = key
        for k, (row, col) in zip(vis, pos):
            (l, r), (t, b) = self.pads[k]
            k.grid(row=row, column=col, padx=(l, r), pady=(t, b), sticky="w")


class Section:
    """A collapsible panel: a clickable header and a body frame to build into"""

    def __init__(self, parent, title, opened=True, on_toggle=None):
        self.outer = ttk.Frame(parent)
        self.outer.pack(fill="x", pady=(0, 2))
        self.title, self.opened, self.on_toggle = title, opened, on_toggle
        self.head = ttk.Label(self.outer, text="", cursor="hand2", style=_section_style(), padding=(8, 5))
        self.head.pack(fill="x")
        self.body = ttk.Frame(self.outer, padding=(10, 4, 8, 8))
        self.head.bind("<Button-1>", lambda _e: self.toggle())
        self._paint()

    def _paint(self):
        self.head.configure(text="%s  %s" % (OPEN if self.opened else CLOSED, self.title))
        if self.opened:
            self.body.pack(fill="x")
        else:
            self.body.pack_forget()

    def toggle(self, state=None):
        self.opened = (not self.opened) if state is None else bool(state)
        self._paint()
        if self.on_toggle:
            self.on_toggle(self)


def _section_style():
    """the header look: a slightly lighter bar with bold text (the app's dark theme); the plain label look if styling fails"""
    try:
        ttk.Style().configure("VPK.Section.TLabel", background="#1b2635", foreground="#dbe5f1", font=("TkDefaultFont", 9, "bold"), padding=(9, 7))
        return "VPK.Section.TLabel"
    except Exception:
        return "TLabel"


class VScroll:
    """A column that scrolls up / down only: the inner frame is always exactly as wide as the column, so nothing needs a sideways scroll bar"""

    def __init__(self, parent):
        self.outer = ttk.Frame(parent)
        self.canvas = tk.Canvas(self.outer, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self.outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas)
        self.win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._relayout)
        self.canvas.bind("<Configure>", self._relayout)
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):          # one handler per column; it checks the pointer is over its own column
            self.canvas.bind_all(ev, self._wheel, add="+")

    def _relayout(self, _e=None):
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 2:
            return
        rh = self.body.winfo_reqheight()
        need = rh > ch
        if need != bool(self.bar.winfo_manager()):
            if need:
                self.bar.pack(side="right", fill="y", before=self.canvas)
            else:
                self.bar.pack_forget()
        self.canvas.itemconfigure(self.win, width=cw, height=max(rh, ch))
        self.canvas.configure(scrollregion=(0, 0, cw, max(rh, ch)))
        if not need:
            self.canvas.yview_moveto(0)

    def _wheel(self, e):
        try:
            under = self.canvas.winfo_containing(e.x_root, e.y_root)
            if under is None or not str(under).startswith(str(self.outer)):
                return                                    # the pointer is somewhere else: not ours
            if under.winfo_class() in ("Listbox", "Treeview", "Text", "TCombobox", "Canvas") and under is not self.canvas:
                return                                    # a list or picture inside the column scrolls itself
        except (tk.TclError, KeyError, AttributeError):
            return
        n = getattr(e, "num", 0)
        d = -1 if (n == 4 or getattr(e, "delta", 0) > 0) else 1
        if self.bar.winfo_manager():
            self.canvas.yview_scroll(3 * d, "units")
        return "break"


class Workspace:
    """list | viewport | properties, with draggable dividers.  Fill ws.left and ws.center yourself; add panels with ws.section()."""

    def __init__(self, parent, left_w=300, right_w=400, min_left=180, min_right=300):
        self.root = ttk.Frame(parent)
        self.root.pack(fill="both", expand=True)
        self.pw = ttk.PanedWindow(self.root, orient="horizontal")
        self.pw.pack(fill="both", expand=True)
        self.left = ttk.Frame(self.pw, padding=(8, 6, 4, 6))
        self.center = ttk.Frame(self.pw, padding=(4, 6))
        self.props = VScroll(self.pw)
        self.pw.add(self.left, weight=0)
        self.pw.add(self.center, weight=1)
        self.pw.add(self.props.outer, weight=0)
        self.sizes = (left_w, right_w)
        self.min_left, self.min_right = min_left, min_right
        self._placed = False
        self._flows = []
        self.pw.bind("<Configure>", self._place)
        self.props.body.bind("<Configure>", lambda _e: self.reflow(), add="+")
        self.props.canvas.bind("<Configure>", lambda _e: self.reflow(), add="+")
        self.left.bind("<Configure>", lambda _e: self.reflow(), add="+")
        self.center.bind("<Configure>", lambda _e: self.reflow(), add="+")

    def _place(self, e=None):
        """set the dividers once the window has a size; after that the user owns them"""
        if self._placed:
            return
        total = self.pw.winfo_width()
        if total < 400:
            return
        self._placed = True
        left, right = self.sizes
        left = max(self.min_left, min(left, total // 3))
        right = max(self.min_right, min(right, total // 2, total - left - 260))      # the middle keeps at least 260 px
        try:
            self.pw.sashpos(0, left)
            self.pw.sashpos(1, total - right)
        except tk.TclError:
            self._placed = False

    def section(self, title, opened=True):
        return Section(self.props.body, title, opened, on_toggle=lambda _s: self.reflow())

    def flow(self, frame, where=None):
        """make a row of side="left" widgets wrap inside the properties column (or inside `where`, e.g. ws.left / ws.center); call it after
        packing the row's children"""
        if len(frame.pack_slaves() or ()) > 1:
            self._flows.append(Flow(frame, where or self.props.body))
        return frame

    def reflow(self):
        for f in self._flows:
            try:
                f.layout()
            except tk.TclError:
                pass


class Slot:
    """The panels of a module that lives inside another page's panes (Backfire / Lamps inside Vehicle Models): the page hands over a
    left frame, a bar frame under the viewport (`center`) and an up / down scrolling column (`props`, a VScroll).  Same section() / flow()
    calls as Workspace."""

    def __init__(self, left, props, center):
        self.left, self.props, self.center = left, props, center
        self._flows = []
        props.body.bind("<Configure>", lambda _e: self.reflow(), add="+")
        props.canvas.bind("<Configure>", lambda _e: self.reflow(), add="+")
        left.bind("<Configure>", lambda _e: self.reflow(), add="+")
        center.bind("<Configure>", lambda _e: self.reflow(), add="+")

    def section(self, title, opened=True):
        return Section(self.props.body, title, opened, on_toggle=lambda _s: self.reflow())

    flow = Workspace.flow
    reflow = Workspace.reflow
