import os, sys, time, tempfile
os.environ["HOME"] = tempfile.mkdtemp()
sys.path.insert(0, '.')
import tkinter as tk
from tkinter import ttk
import wpk_edit as E
root = tk.Tk(); root.geometry("1500x900")
ui = ttk.Frame(root); ui.pack(fill="both", expand=True)
tab = E.build_tab(ui, root)
def pump(n=30):
    for _ in range(n):
        root.update(); time.sleep(0.02)
pump()
print("gl widget:", tab.gl is not None, "| failed:", tab.gl and tab.gl.failed)
tab.open_path("/mnt/user-data/uploads/maz_SE3P.wpk"); pump(60)
print("info:", tab.gl_info.cget("text"), "| warn:", repr(tab.warn.cget("text")))
print("model tris on GPU:", tab.gl.model.n_tris, "| size", tab.gl.widget.winfo_width(), tab.gl.widget.winfo_height())
shots = []
def shot(name):
    im = tab.gl.screenshot(); im.save("/home/claude/out/tk_%s.png" % name); shots.append(im); return im
shot("open")
class Ev:  # a fake mouse event
    def __init__(s, x, y, state=0, delta=0, num=0): s.x, s.y, s.state, s.delta, s.num = x, y, state, delta, num
w = tab.gl.widget
# drag orbit
y0 = tab.cam.yaw
tab._vp_press(Ev(300, 300)); tab._vp_motion(Ev(360, 320)); tab._vp_motion(Ev(420, 330)); tab._vp_release(Ev(420, 330)); pump(10)
print("orbit: yaw %.1f -> %.1f pitch %.1f" % (y0, tab.cam.yaw, tab.cam.pitch)); shot("orbit")
# wheel zoom, pan
z0 = tab.cam.zoom; tab._vp_wheel(Ev(300, 300, delta=120)); tab._vp_wheel(Ev(300, 300, delta=120)); pump(5)
print("zoom %.2f -> %.2f, upp %.4f" % (z0, tab.cam.zoom, tab._upp))
p0 = list(tab.cam.pan); tab._vp_press_pan(Ev(300, 300)); tab._vp_motion(Ev(340, 310)); tab._vp_release(Ev(340, 310)); pump(5)
print("pan", p0, "->", [round(v, 2) for v in tab.cam.pan]); shot("zoom_pan")
tab.fit_view(); pump(5)
# views radio
tab.view.set(1); tab.view_changed(); pump(5); shot("side")
# click select: click on the car centre
W, H = w.winfo_width(), w.winfo_height()
tab.view.set(2); tab.view_changed(); pump(10)
tab._vp_press(Ev(W // 2, H // 2)); tab._vp_release(Ev(W // 2, H // 2)); pump(10)
print("click centre -> selection:", tab.sel_tags()); shot("selected")
tab._vp_press(Ev(3, 3)); tab._vp_release(Ev(3, 3)); pump(5)
print("click empty -> selection:", tab.sel_tags())
# hide the body from the browser's visibility
tab.set_visible([0], False); pump(10); shot("hidden")
tab.set_visible([0], True); pump(5)
# resize the window
root.geometry("1100x700"); pump(30); print("resized ->", w.winfo_width(), w.winfo_height(), "failed:", tab.gl and tab.gl.failed); shot("resized")
# shading + checkboxes
tab.shading.set("Game: Tokyo, day"); tab._gl_look(); tab.persp_on.set(False); tab._gl_look(); pump(5); shot("ortho_day")
# switch to software and back
tab.gpu_on.set(False); tab._gpu_toggled(); pump(60); print("software mode: gl =", tab.gl, "| canvas mapped:", tab.canvas.winfo_ismapped(), "|", tab.gl_info.cget("text"))
tab.gpu_on.set(True); tab._gpu_toggled(); pump(60); print("back on GPU:", tab.gl is not None, tab.gl and tab.gl.failed)
# forced failure -> automatic fallback
tab.gl._fail("Test failure", "Traceback...\nValueError: boom"); pump(30)
print("after failure: gl =", tab.gl, "| canvas mapped:", tab.canvas.winfo_ismapped(), "|", tab.gl_info.cget("text")[:70])
print("log:", [e["msg"] for e in tab.oplog.entries if e["level"] == "warning"])
# a different car + kit
tab.gpu_on.set(True); tab._gpu_toggled(); pump(40)
tab.open_path("/mnt/user-data/uploads/maz_JCESE_aeroB.wpk"); pump(60); print("aeroB: gl", tab.gl is not None, tab.gl and tab.gl.model and tab.gl.model.n_tris)
shot("aeroB")
print("DONE")
