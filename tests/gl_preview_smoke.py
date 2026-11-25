import os, sys, time, tempfile
os.environ["HOME"] = tempfile.mkdtemp()
sys.path.insert(0, '.')
import tkinter as tk
import numpy as np
from PIL import Image, ImageDraw
import wpk_view as V
root = tk.Tk(); root.withdraw()
pw = V.PreviewWindow(root, lambda: None)
pw.top.geometry("1000x900")
def pump(n=30):
    for _ in range(n):
        root.update(); time.sleep(0.02)
pump(5)
print("gl:", pw.gl is not None, "| note:", pw.gpu_note.cget("text")[:60])
pw.set_car("/mnt/user-data/uploads/maz_SE3P.wpk"); pump(80)
print("model loaded:", pw.model is not None, "| tex:", pw.v_tex.get(), "| paints:", pw.cb_paint.cget("values"), "| msg:", repr(pw.msg.get()))
print("note:", pw.gpu_note.cget("text"))
def shot(name):
    im = pw.gl.screenshot(); im.save("/home/claude/out/pv_%s.png" % name); return np.asarray(im).astype(int)
a = shot("base")
# a sticker: red stripes on the body texture
st = Image.new("RGBA", (512, 512), (0, 0, 0, 0)); d = ImageDraw.Draw(st)
for x in range(0, 512, 64): d.rectangle([x, 0, x + 24, 512], fill=(255, 230, 0, 255))
pw.set_sticker(st, None, os.path.splitext(pw.v_tex.get())[0]); pump(20)
b = shot("sticker"); print("sticker changes the picture:", int((np.abs(a - b).sum(2) > 40).sum()), "px")
pw.v_modetxt.set("No sticker"); pw.v_mode.set("none"); pw.show(); pump(10)
c = shot("nosticker"); print("'No sticker' == base:", bool((np.abs(c - a).sum() == 0)))
pw.v_mode.set("edited"); pw.v_modetxt.set("With your changes")
pw.v_uvcar.set("Borders"); pw.show(); pump(10); e = shot("uvborders"); print("UV borders change picture:", int((np.abs(e - b).sum(2) > 40).sum()))
pw.v_uvcar.set("Off")
# camera: views, drag, zoom
pw.v_view.set(1); pw.show(); pump(5); shot("side")
class Ev:
    def __init__(s, x, y, num=0, delta=0): s.x, s.y, s.num, s.delta = x, y, num, delta
y0 = list(pw.free); pw.drag_start(Ev(200, 200)); pw.drag_move(Ev(300, 230)); pw.drag_end(Ev(300, 230)); pump(10)
print("free:", y0, "->", [round(v, 1) for v in pw.free], "view =", pw.v_view.get() == V.FREE); shot("free")
z0 = pw.v_zoom.get(); pw.wheel_zoom(Ev(1, 1, delta=120)); pw.wheel_zoom(Ev(1, 1, delta=120)); pump(5); print("zoom", z0, "->", round(pw.v_zoom.get(), 3))
pw.reset_zoom(); pump(5)
# night, lamps
pw.v_view.set(2); pw.show(); pump(5); day = shot("day")
pw.v_night.set(True); pw.show(); pump(5); night = shot("night"); print("night darker:", day.mean() > night.mean())
pw.v_brake.set(False); pw.show(); pump(5); nb = shot("night_nobrake"); print("brake lamps off changes picture:", int((np.abs(nb - night).sum(2) > 30).sum()), "px")
pw.v_night.set(False); pw.v_brake.set(True)
# parts + kit
pw.set_car("/mnt/user-data/uploads/maz_SE3P_aeroB.wpk"); pump(80); print("kit B:", pw.model is not None, pw.v_kit.get()); shot("kitB")
pw.v_part["Wing"].set("A") if "A" in pw.cb_part["Wing"].cget("values") else None; pw.parts_changed(); pump(10)
# screenshot via the dialog path (no dialog): just call gl.screenshot
print("screenshot size:", pw.gl.screenshot().size)
# software toggle + fallback
pw.v_gpu.set(False); pw._gpu_toggled(); pump(60); print("software:", pw.gl is None, "| gbufs:", list(pw.gbufs)[:3], "| note:", pw.gpu_note.cget("text"))
pw.v_gpu.set(True); pw._gpu_toggled(); pump(40); print("GPU again:", pw.gl is not None)
pw.gl._fail("Test failure", "Traceback\nValueError: boom"); pump(80); print("after forced failure: gl", pw.gl, "| note:", pw.gpu_note.cget("text")[:60], "| gbufs", list(pw.gbufs)[:3])
pw.close(); print("closed")
