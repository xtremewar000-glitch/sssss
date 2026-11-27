import os, sys, time, tempfile
os.environ["HOME"] = tempfile.mkdtemp()
sys.path.insert(0, '.')
import vpk_tool, vpk_ui
def hook(root, app):
    def pump(sec):
        t = time.time()
        while time.time() - t < sec:
            root.update(); time.sleep(0.01)
    pump(1.5)
    # what a real window manager / Aqua may do: a Configure for the toplevel whenever the fonts (so the layout) change
    orig_apply = app.uiscale.apply
    def apply_and_configure(scale):
        r = orig_apply(scale)
        root.after(60, lambda: root.event_generate("<Configure>", width=root.winfo_width(), height=root.winfo_height()))
        return r
    app.uiscale.apply = apply_and_configure
    fits = {"n": 0, "scales": []}
    orig = app.fit_now
    def counting():
        fits["n"] += 1; orig(); fits["scales"].append(round(app.uiscale.current, 2))
    app.fit_now = counting
    app.fit_soon()
    t0 = time.time(); marks = []
    for sec in (2, 4, 6, 8):
        pump(sec - (time.time() - t0)); marks.append((sec, fits["n"]))
    print("fit runs after 2/4/6/8 s:", marks, "| scales seen:", sorted(set(fits["scales"])))
vpk_tool.run_gui(_hook=hook)
