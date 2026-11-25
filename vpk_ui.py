"""Shared UI pieces for every tab (Milestone 6): palette, ttk theme, confirmation wording, keyboard-shortcut table,
panel-layout and window-geometry rules.

Everything except apply_theme() / install_shortcuts() is plain Python (no tkinter import) and is unit-tested without a display.
Nothing here touches WPK data or any file format.
"""
import re

# ------------------------------------------------------------------------------------------------
# palette (the values the main window already used, named once)
# ------------------------------------------------------------------------------------------------
BG = "#0c1017"
PANEL = "#141b26"
RAISED = "#202a38"
INPUT = "#182230"
BORDER = "#344357"
TEXT = "#e6edf6"
TEXT_SOFT = "#c9d4e3"
MUTED = "#8f9eb2"
ACCENT = "#1f6feb"
SELECT = "#24577b"
SUCCESS = "#3fb950"
WARNING = "#e8710a"
ERROR = "#f85149"
DANGER_BG = "#7d2a2a"

LEVEL_COLOURS = {"info": TEXT_SOFT, "success": SUCCESS, "warning": WARNING, "error": ERROR}

# one meaning for every status colour used in the tabs (old light-theme greys / reds were unreadable on the dark background)
LEGACY_COLOURS = {"#333": TEXT_SOFT, "#555": MUTED, "#777": MUTED, "#b00020": ERROR, "#a00": ERROR,
                  "#1b7f3b": SUCCESS, "#b06000": WARNING, "#b36b00": WARNING}

DIALOG_MIN = (360, 200)            # smallest a dialog may be shrunk to when its own natural size is smaller


# ------------------------------------------------------------------------------------------------
# confirmations: one wording for every destructive question
# ------------------------------------------------------------------------------------------------
def confirm_text(what, details=(), consequence="", undoable=None, limit=12):
    """what = the question ("Delete 3 parts?"); details = bullet facts; consequence = one sentence; undoable True / False / None
    (None = say nothing).  Always the same order: question, facts, consequence, undo line."""
    parts = [what.strip()]
    details = [d for d in details if d]
    if details:
        shown = details[:limit]
        text = "\n".join("• " + d for d in shown)
        if len(details) > limit:
            text += "\n• … and %d more" % (len(details) - limit)
        parts.append(text)
    if consequence:
        parts.append(consequence.strip())
    if undoable is True:
        parts.append("Undo brings it back.")
    elif undoable is False:
        parts.append("This cannot be undone.")
    return "\n\n".join(parts)


def confirm_destructive(messagebox, parent, title, what, details=(), consequence="", undoable=None):
    """ask before something is deleted / replaced / thrown away.  True = go ahead."""
    return bool(messagebox.askyesno(title, confirm_text(what, details, consequence, undoable), icon="warning", parent=parent))


def discard_changes(messagebox, parent, what, dirty=True):
    """the 'unsaved changes' question every tab asks before it opens another file or quits.  True = fine to continue."""
    if not dirty:
        return True
    return confirm_destructive(messagebox, parent, "Unsaved changes", "Throw away the unsaved changes to %s?" % what, undoable=False)


# ------------------------------------------------------------------------------------------------
# status line: ONE look for loading / empty / info / success / warning / error on every page
# ------------------------------------------------------------------------------------------------
LOADING = "#8ecbff"
STATE_COLOURS = {"empty": MUTED, "loading": LOADING, "info": TEXT_SOFT, "success": SUCCESS, "warning": WARNING, "error": ERROR}
STATE_GLYPHS = {"empty": "", "loading": "\u25cc ", "info": "", "success": "\u2714 ", "warning": "\u26a0 ", "error": "\u2716 "}
STATES = tuple(STATE_COLOURS)

_LOADING = ("reading", "looking for", "loading", "scanning", "opening", "baking", "saving", "drawing", "working")
_EMPTY = ("open a ", "open backfire", "open a plain", "choose the", "nothing open", "ready", "no file", "new empty file")
_ERROR = ("cannot", "can't", "could not", "couldn't", "failed", "error", "not a tile", "not a number", "uv layout not available", "unreadable")
_SUCCESS = ("saved", "wrote", "opened", "replaced", "restored", "undone", "scaled", "reset", "texture '", "texture ", "tile ", "sheet replaced",
            "lens covers done", "lamps copied", "cell ", "exported", "animation of", "set #", "new stickers will be", "car settings changed",
            "selected ", "every tile is used", "operation log copied", "unused tile", "new file for", "done")


def classify(text):
    """-> one of STATES, guessed from the wording of an older free-text status message.  A caller that knows better passes the level."""
    t = (text or "").strip().lower()
    if not t:
        return "empty"
    if t.endswith(" first.") or t.endswith(" first") or "first (" in t or t.startswith("select ") or t.startswith("nothing to") \
            or "nothing to change" in t or "no triangle uses" in t or "changed, not saved" in t:
        return "warning"
    if any(t.startswith(k) for k in _ERROR) or " failed" in t and not t.startswith("lamps copied"):
        return "error"
    if "failed" in t:
        return "warning"                                   # "copied to 3 packs; 1 failed": partly done
    if any(t.startswith(k) for k in _LOADING):
        return "loading"
    if any(t.startswith(k) for k in _EMPTY):
        return "empty"
    if any(t.startswith(k) for k in _SUCCESS):
        return "success"
    return "info"


def state_style(level):
    """(colour, glyph) for a state; unknown levels look like info"""
    level = level if level in STATE_COLOURS else "info"
    return STATE_COLOURS[level], STATE_GLYPHS[level]


class StatusLine:
    """Drop-in for the tk.StringVar the tabs used as a status line: .set(text[, level]) / .get() keep working, and the level
    (loading / empty / info / success / warning / error) decides colour, glyph and the busy bar.  With no level the wording is classified.
    Bind it to widgets with bar(); one StatusLine can feed several bars.  Pure logic: Tk is only touched inside bar()."""

    def __init__(self, text="", level=None):
        self.text, self.level = "", "empty"
        self._views = []
        self.set(text, level)

    def set(self, text, level=None):
        text = "" if text is None else str(text)
        self.text = text
        self.level = level if level in STATES else classify(text)
        for v in list(self._views):
            try:
                v(self.text, self.level)
            except Exception:
                self._views.remove(v)

    def get(self):
        return self.text

    def shown(self):
        """the text as drawn: glyph + message"""
        return state_style(self.level)[1] + self.text

    # convenience setters
    def loading(self, text):
        self.set(text, "loading")

    def success(self, text):
        self.set(text, "success")

    def warning(self, text):
        self.set(text, "warning")

    def error(self, text):
        self.set(text, "error")

    def empty(self, text):
        self.set(text, "empty")

    def bar(self, parent, ttk, tk, wraplength=0, **pack):
        """a Frame holding the coloured message and, while loading, a small busy bar; packed with **pack (default left / fill x)"""
        f = ttk.Frame(parent)
        lbl = ttk.Label(f, text=self.shown(), foreground=state_style(self.level)[0], wraplength=wraplength, justify="left")
        lbl.pack(side="left", fill="x", expand=True)
        pb = ttk.Progressbar(f, mode="indeterminate", length=90)

        def view(text, level):
            colour, glyph = state_style(level)
            lbl.configure(text=glyph + text, foreground=colour)
            if level == "loading":
                if not pb.winfo_ismapped():
                    pb.pack(side="right", padx=(8, 0))
                pb.start(60)
            else:
                pb.stop()
                pb.pack_forget()
        self._views.append(view)
        view(self.text, self.level)
        f.pack(**(pack or dict(side="left", fill="x", expand=True)))
        return f


# ------------------------------------------------------------------------------------------------
# keyboard shortcuts: ONE table drives the key bindings, the menu labels and the help window
# ------------------------------------------------------------------------------------------------
# (id, Tk sequence, text shown, what it does, scope, typing_safe)
#   scope: "all" works on every page, "models" only while the Vehicle Models page is in front, "stickers" only on the sticker page
#   typing_safe False = ignored while a text box has the keyboard focus (so Delete / Ctrl+Z still edit text there)
SHORTCUTS = (
    ("open", "<Control-o>", "Ctrl+O", "Open a file", "all", True),
    ("save", "<Control-s>", "Ctrl+S", "Save to game", "all", True),
    ("save_as", "<Control-Shift-S>", "Ctrl+Shift+S", "Save as", "models", True),
    ("undo", "<Control-z>", "Ctrl+Z", "Undo the last edit", "models", False),
    ("delete", "<Delete>", "Delete", "Delete the selected part(s)", "models", False),
    ("search", "<Control-f>", "Ctrl+F", "Search the asset browser", "models", True),
    ("fit", "<Control-0>", "Ctrl+0", "Fit / reset the camera", "models", True),
    ("textures", "<Control-t>", "Ctrl+T", "Textures and materials", "models", True),
    ("export_obj", "<Control-e>", "Ctrl+E", "Export the selected parts as OBJ", "models", True),
    ("toggle_log", "<Control-l>", "Ctrl+L", "Show / hide the operation log", "models", True),
    ("shortcuts", "<F1>", "F1", "Keyboard shortcuts", "all", True),
)
PAGE_KEYS = tuple("<Control-Key-%d>" % i for i in range(1, 10))      # Ctrl+1 .. Ctrl+9 = the nth page of the left rail
TYPING_CLASSES = {"Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox", "Listbox"}


def shortcut(sid):
    return next(s for s in SHORTCUTS if s[0] == sid)


def accel(sid):
    return shortcut(sid)[2]


def menu_label(sid, text):
    """label for tk.Menu.add_command: ('Undo', accelerator='Ctrl+Z') pair"""
    return dict(label=text, accelerator=accel(sid))


def help_rows(page_names=()):
    """[(keys, meaning, where)] for the shortcuts window"""
    where = {"all": "everywhere", "models": "Vehicle Models page", "stickers": "Livery & Stickers page"}
    rows = [(s[2], s[3], where[s[4]]) for s in SHORTCUTS]
    for i, n in enumerate(page_names[:9]):
        rows.append(("Ctrl+%d" % (i + 1), "Go to %s" % n, "everywhere"))
    return rows


def typing_in_text(widget):
    """True when `widget` is a place where Delete / Ctrl+Z edit text"""
    try:
        return widget is not None and widget.winfo_class() in TYPING_CLASSES
    except Exception:
        return False


def make_dispatch(handlers, current_scope, typing=typing_in_text):
    """-> function(sid, event) that runs handlers[sid]() when the shortcut applies, returns "break" if it ran.
    handlers: {sid: callable}; current_scope(): "models" | "stickers" | other page name."""
    def run(sid, event=None):
        sc = shortcut(sid)
        if sc[4] != "all" and current_scope() != sc[4]:
            return None
        if not sc[5] and typing(getattr(event, "widget", None)):
            return None
        fn = handlers.get(sid)
        if fn is None:
            return None
        fn()
        return "break"
    return run


def install_shortcuts(root, dispatch, go_page, page_count):
    """bind every table entry (and Ctrl+1..9) on the root window"""
    for sid, seq, *_ in SHORTCUTS:
        root.bind(seq, lambda e, s=sid: dispatch(s, e))
    for i, seq in enumerate(PAGE_KEYS[:page_count]):
        root.bind(seq, lambda e, i=i: (go_page(i), "break")[1])


# ------------------------------------------------------------------------------------------------
# panel layout and window geometry
# ------------------------------------------------------------------------------------------------
MIN_PANES = (220, 360, 240)        # asset browser, viewport, properties


def clamp_panes(total, left, right, mins=MIN_PANES):
    """widths of the left and right panes (their sash positions are left and total - right), kept inside sane limits.
    A saved layout from a bigger screen can never squeeze the viewport away or push a pane off the window."""
    ml, mc, mr = mins
    try:
        left, right = int(left), int(right)
    except (TypeError, ValueError):
        left, right = 300, 300
    total = int(total)
    if total < ml + mc + mr:                          # window smaller than the three minimums: share it in proportion
        s = float(ml + mc + mr)
        return int(total * ml / s), int(total * mr / s)
    left = max(ml, min(left, total - mc - mr))
    right = max(mr, min(right, total - mc - left))
    return left, right


def parse_layout(d, total):
    """(left, right, log_open) from the saved dict (any junk -> defaults), clamped for a window `total` pixels wide"""
    d = d if isinstance(d, dict) else {}
    left, right = clamp_panes(total, d.get("left", 300), d.get("right", 300))
    return left, right, bool(d.get("log", True))


def fit_geometry(saved, screen_w, screen_h, min_w=980, min_h=620, margin=40):
    """'WxH' or 'WxH+X+Y' for the window from its saved {'width','height','x','y'}, or None when nothing usable is saved.
    The window is moved back on screen if the saved place no longer fits (another monitor, lower resolution)."""
    if not isinstance(saved, dict):
        return None
    try:
        w, h = int(saved.get("width", 0)), int(saved.get("height", 0))
    except (TypeError, ValueError):
        return None
    if w < min_w or h < min_h:
        return None
    w, h = min(w, screen_w), min(h, screen_h)
    if "x" not in saved or "y" not in saved:
        return "%dx%d" % (w, h)
    try:
        x, y = int(saved["x"]), int(saved["y"])
    except (TypeError, ValueError):
        return "%dx%d" % (w, h)
    x = max(0, min(x, screen_w - min(w, screen_w) ))
    y = max(0, min(y, screen_h - min(h, screen_h)))
    if x > screen_w - margin or y > screen_h - margin:
        return "%dx%d" % (w, h)
    return "%dx%d+%d+%d" % (w, h, x, y)


def looks_maximised(w, h, screen_w, screen_h, slack_w=24, slack_h=110):
    """True when a window covers the screen's width and (apart from a taskbar / title bar) its height; a last-resort guess for window
    managers that do not report a maximised window"""
    return w >= screen_w - slack_w and h >= screen_h - slack_h


def window_state(root, tcl_error=Exception):
    """'zoomed' when the main window is maximised, otherwise Tk's own state ('normal', 'iconic', 'withdrawn' ...).
    Windows / macOS report 'zoomed' themselves.  On Linux (X11) Tk reports 'normal' for a maximised window, so the
    '-zoomed' attribute is asked next, and then the window's size against the screen."""
    st = root.state()
    if st != "normal":
        return st
    try:
        if int(root.attributes("-zoomed")):
            return "zoomed"
    except (tcl_error, ValueError, TypeError):
        pass                                           # no such attribute here (Windows / macOS report 'zoomed' above)
    try:
        if looks_maximised(root.winfo_width(), root.winfo_height(), root.winfo_screenwidth(), root.winfo_screenheight()):
            return "zoomed"
    except tcl_error:
        pass
    return st


def set_maximised(root, on=True, tcl_error=Exception):
    """maximise / restore the window on every platform; True if one of the ways worked"""
    for fn in (lambda: root.state("zoomed" if on else "normal"), lambda: root.attributes("-zoomed", bool(on))):
        try:
            fn()
            return True
        except tcl_error:
            continue
    return False


def dialog_min(req_w, req_h, screen_w, screen_h):
    """minimum size for a dialog = its natural size (so no control can be squeezed out), never more than 85% of the screen"""
    return (max(DIALOG_MIN[0], min(req_w, int(screen_w * 0.85))), max(DIALOG_MIN[1], min(req_h, int(screen_h * 0.85))))


def recolour(value):
    """old light-theme colour -> the dark theme's equivalent (unchanged if it is not one of the old ones)"""
    return LEGACY_COLOURS.get(str(value).lower(), value)


# ------------------------------------------------------------------------------------------------
# theme (Tk)
# ------------------------------------------------------------------------------------------------
def apply_theme(root, ttk, tk):
    """The dark workshop theme for every ttk widget, plus dark backgrounds for plain Toplevel dialogs and combobox lists.
    Each style call is separate, so one unsupported option cannot drop the rest."""
    style = ttk.Style()
    for name in ("clam",):
        try:
            style.theme_use(name)
        except tk.TclError:
            pass

    def cfg(name, **kw):
        try:
            style.configure(name, **kw)
        except tk.TclError:
            pass

    def mp(name, **kw):
        try:
            style.map(name, **kw)
        except tk.TclError:
            pass

    def opt(pattern, value):
        try:
            root.option_add(pattern, value)
        except tk.TclError:
            pass

    try:
        root.configure(background=BG)
    except tk.TclError:
        pass
    cfg("TFrame", background=BG)
    cfg("TLabel", background=BG, foreground=TEXT)
    cfg("TButton", background=RAISED, foreground=TEXT, bordercolor=BORDER, padding=(10, 6), focuscolor="#8ecbff")
    mp("TButton", background=[("active", "#2c3c50"), ("disabled", "#171e29")], foreground=[("disabled", "#718096")])
    # button hierarchy: Primary (the one main action of a page), default (secondary), Danger (deletes / overwrites)
    cfg("VPK.Primary.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT, padding=(12, 6))
    mp("VPK.Primary.TButton", background=[("active", "#388bfd"), ("disabled", "#17243a")], foreground=[("disabled", "#718096")])
    cfg("VPK.Danger.TButton", background=DANGER_BG, foreground="#ffffff", bordercolor="#a03a3a", padding=(10, 6))
    mp("VPK.Danger.TButton", background=[("active", "#a03a3a"), ("disabled", "#2a1c1f")], foreground=[("disabled", "#718096")])
    cfg("VPK.Nav.TButton", background="#111925", foreground="#a9b7c9", anchor="w", padding=(14, 10), borderwidth=0)
    mp("VPK.Nav.TButton", background=[("active", "#1c2c3e")], foreground=[("active", "#ffffff")])
    cfg("VPK.NavActive.TButton", background="#1d3b54", foreground="#ffffff", anchor="w", padding=(14, 10), borderwidth=0,
        font=("TkDefaultFont", 9, "bold"))
    mp("VPK.NavActive.TButton", background=[("active", "#254b69")])
    cfg("Toolbutton", background=RAISED, foreground=TEXT_SOFT, bordercolor=BORDER, padding=(6, 4), focuscolor="#8ecbff")
    mp("Toolbutton", background=[("selected", SELECT), ("active", "#2c3c50"), ("disabled", "#171e29")],
       foreground=[("selected", "#ffffff"), ("disabled", "#718096")])
    cfg("TNotebook", background=BG, borderwidth=0)
    cfg("TNotebook.Tab", background="#182230", foreground="#a9b7c9", padding=(14, 8))
    mp("TNotebook.Tab", background=[("selected", "#1d3b54"), ("active", "#26384d")], foreground=[("selected", "#ffffff")])
    cfg("TLabelframe", background=PANEL, bordercolor=BORDER)
    cfg("TLabelframe.Label", background=PANEL, foreground="#8ecbff")
    cfg("Treeview", background="#151e2a", fieldbackground="#151e2a", foreground=TEXT, rowheight=25, bordercolor=BORDER)
    cfg("Treeview.Heading", background=RAISED, foreground="#dce6f2", padding=6)
    mp("Treeview", background=[("selected", SELECT)], foreground=[("selected", "#ffffff")])
    cfg("TCombobox", fieldbackground=INPUT, background=RAISED, foreground=TEXT, arrowcolor="#8ecbff", padding=5)
    mp("TCombobox", fieldbackground=[("readonly", INPUT), ("disabled", "#111925")], foreground=[("disabled", "#718096")])
    cfg("TEntry", fieldbackground=INPUT, foreground=TEXT, padding=5)
    mp("TEntry", fieldbackground=[("disabled", "#111925")], foreground=[("disabled", "#718096")])
    cfg("TSpinbox", fieldbackground=INPUT, foreground=TEXT, background=RAISED, arrowcolor="#8ecbff", padding=4)
    cfg("TScrollbar", background=RAISED, troughcolor="#111925", bordercolor="#111925", arrowcolor="#8ea3ba")
    cfg("TCheckbutton", background=BG, foreground="#dce6f2")
    cfg("TRadiobutton", background=BG, foreground="#dce6f2")
    mp("TCheckbutton", foreground=[("disabled", "#718096")])
    mp("TRadiobutton", foreground=[("disabled", "#718096")])
    cfg("TSeparator", background=BORDER)
    cfg("TPanedwindow", background=BG)
    cfg("Sash", sashthickness=6, gripcount=0, background=BORDER)
    cfg("TProgressbar", background=ACCENT, troughcolor="#111925", bordercolor=BORDER)
    cfg("TScale", background=BG, troughcolor="#111925")
    cfg("TMenubutton", background=RAISED, foreground=TEXT, bordercolor=BORDER, padding=(8, 5))
    # plain tk widgets that ttk does not style: dialogs and the lists inside comboboxes
    opt("*Toplevel.background", BG)
    opt("*TCombobox*Listbox.background", INPUT)
    opt("*TCombobox*Listbox.foreground", TEXT)
    opt("*TCombobox*Listbox.selectBackground", SELECT)
    opt("*TCombobox*Listbox.selectForeground", "#ffffff")
    return style


def install_dialog_minsize(root, tk):
    """every dialog (Toplevel) gets, when first shown, a minimum size equal to its natural size, so shrinking it can never hide controls"""
    def on_map(e):
        w = e.widget
        try:
            if w.winfo_toplevel() is not w or w is root:
                return
            if tuple(w.minsize()) not in ((1, 1), (0, 0)):
                return                                   # the dialog already sets its own
            w.minsize(*dialog_min(w.winfo_reqwidth(), w.winfo_reqheight(), w.winfo_screenwidth(), w.winfo_screenheight()))
        except (tk.TclError, AttributeError):
            pass
    root.bind_class("Toplevel", "<Map>", on_map, add="+")
