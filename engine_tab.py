"""Engine sounds: swap or replace the engine / exhaust loops of a car (the app's 5th tab).

The game plays, for every car, 4 ENGINE loops and 4 MUFFLER (exhaust) loops from data/sound/engine/<name>.wav.
They are NOT one per gear: each loop covers an rpm band, and the game fades them into each other as the revs
climb, pitching each one with the rpm.  The car type decides which of the 30 sound SETS it uses (stock and fully
tuned cars use different sets).  So a swap is: put different WAV files under a set's 8 file names.

    python vpk_tool.py engine list  [SOUND_DIR]
    python vpk_tool.py engine copy  SOUND_DIR TARGET_SET FROM_SET     (give a set the sound of another)
    python vpk_tool.py engine put   SOUND_DIR TARGET_SET SLOT FILE.wav   (SLOT = e1..e4 / m1..m4)
    python vpk_tool.py engine restore SOUND_DIR [SET]                  (put the original files back)

Original files are copied to engine/_orig/ the first time one is changed, so everything can be undone.
"""
import os
import shutil
import subprocess
import sys
import unicodedata
import wave

import numpy as np

import engine_data as D
import vpk_ui
import vpk_layout as VL                   # Blender-style list | viewport | panels layout

try:
    import wpk_view
except ImportError:                                       # pragma: no cover
    wpk_view = None
try:
    import backfire_edit
except ImportError:                                       # pragma: no cover
    backfire_edit = None

SLOTS = ["e1", "e2", "e3", "e4", "m1", "m2", "m3", "m4"]
SLOT_LABEL = ["Engine 1 (idle / low)", "Engine 2", "Engine 3", "Engine 4 (high)",
              "Exhaust 1 (idle / low)", "Exhaust 2", "Exhaust 3", "Exhaust 4 (high)"]


class SoundError(Exception):
    pass


# ---------------------------------------------------------------- the tables
def set_names(k):
    s = D.SETS[k]
    return list(s[0]) + list(s[1])


def set_title(k):
    e, m = D.SETS[k][0], D.SETS[k][1]
    return "#%d  %s .. %s  +  %s .. %s" % (k, e[0], e[3], m[0], m[3])


def cars_of(k, tuned):
    table = D.TUNED if tuned else D.STOCK
    out = []
    for t, kk in enumerate(table):
        if kk == k:
            out += D.TYPES.get(t, [])
    return sorted(set(out))


def sharing():
    """file name -> sets that use it (a file shared by two sets changes both)"""
    d = {}
    for k in range(len(D.SETS)):
        for n in set_names(k):
            d.setdefault(n, []).append(k)
    return d


def find_dir(start):
    """the data/sound/engine folder, given it or a folder around / below it"""
    if not start:
        return None
    p = os.path.abspath(start)
    for _ in range(6):
        for rel in ("", "engine", "sound/engine", "data/sound/engine"):
            c = os.path.join(p, rel) if rel else p
            if os.path.isdir(c) and os.path.basename(c) == "engine" and os.path.isfile(os.path.join(c, "rx714.wav")):
                return c
        np_ = os.path.dirname(p)
        if np_ == p:
            break
        p = np_
    return None


# ---------------------------------------------------------------- wav
def read_wav(path):
    """-> (rate, mono float32 array in -1..1)"""
    try:
        with wave.open(path, "rb") as w:
            ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except (wave.Error, EOFError, OSError) as ex:
        raise SoundError("%s is not a WAV file the tool can read (%s). Save it as PCM 16-bit WAV." % (os.path.basename(path), ex))
    if sw == 1:
        a = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 2:
        a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | b[:, 1] << 8 | b[:, 2] << 16
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        a = v.astype(np.float32) / (1 << 23)
    elif sw == 4:
        a = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    else:
        raise SoundError("unsupported sample size in " + os.path.basename(path))
    if ch > 1:
        a = a[: len(a) // ch * ch].reshape(-1, ch).mean(axis=1)
    if len(a) < 64:
        raise SoundError("%s is too short to be an engine loop." % os.path.basename(path))
    return rate, a


def write_wav(path, rate, a):
    pcm = (np.clip(a, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())


def make_loop(a, rate, fade_ms):
    """cross-fade the end into the start so the loop has no click"""
    n = int(rate * fade_ms / 1000.0)
    n = min(n, len(a) // 3)
    if n < 8:
        return a
    t = np.linspace(0.0, 1.0, n, dtype=np.float32)
    body = a[:-n].copy()
    body[:n] = a[:n] * t + a[-n:] * (1.0 - t)
    return body


def rms(a):
    return float(np.sqrt(np.mean(a * a))) + 1e-9


def info(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getframerate(), w.getnframes() / float(w.getframerate()), w.getsampwidth() * 8
    except (wave.Error, OSError, EOFError, ZeroDivisionError):
        return None


# ---------------------------------------------------------------- change files
def _orig_dir(d):
    return os.path.join(d, "_orig")


def backup(d, name):
    src = os.path.join(d, name + ".wav")
    dst = os.path.join(_orig_dir(d), name + ".wav")
    if os.path.isfile(src) and not os.path.exists(dst):
        os.makedirs(_orig_dir(d), exist_ok=True)
        shutil.copy2(src, dst)


def is_changed(d, name):
    return os.path.isfile(os.path.join(_orig_dir(d), name + ".wav"))


def put_file(d, name, wavpath, fade_ms=30.0, match_loudness=True, make_loop_=True):
    """convert wavpath to mono 16-bit and write it as engine/<name>.wav"""
    rate, a = read_wav(wavpath)
    if make_loop_ and fade_ms > 0:
        a = make_loop(a, rate, fade_ms)
    ref = os.path.join(_orig_dir(d), name + ".wav")
    if not os.path.isfile(ref):
        ref = os.path.join(d, name + ".wav")
    if match_loudness and os.path.isfile(ref):
        try:
            _, o = read_wav(ref)
            g = rms(o) / rms(a)
            peak = float(np.max(np.abs(a))) * g
            if peak > 0.98:
                g *= 0.98 / peak
            a = a * g
        except SoundError:
            pass
    else:
        pk = float(np.max(np.abs(a)))
        if pk > 0.98:
            a = a * (0.98 / pk)
    backup(d, name)
    write_wav(os.path.join(d, name + ".wav"), rate, a)


def copy_set(d, target, source):
    """give set `target` the sound of set `source` (a plain copy of its 8 loops)"""
    for tn, sn in zip(set_names(target), set_names(source)):
        if tn == sn:
            continue
        src = os.path.join(_orig_dir(d), sn + ".wav")       # the stock sound, even if the source set was changed
        if not os.path.isfile(src):
            src = os.path.join(d, sn + ".wav")
        if not os.path.isfile(src):
            raise SoundError("missing " + sn + ".wav")
        backup(d, tn)
        shutil.copyfile(src, os.path.join(d, tn + ".wav"))


def restore(d, names=None):
    od = _orig_dir(d)
    n = 0
    if not os.path.isdir(od):
        return 0
    for f in os.listdir(od):
        if f.lower().endswith(".wav") and (names is None or f[:-4] in names):
            shutil.copyfile(os.path.join(od, f), os.path.join(d, f))
            os.remove(os.path.join(od, f))
            n += 1
    try:
        os.rmdir(od)
    except OSError:
        pass
    return n


def play(path):
    """play a WAV with whatever the system has (never raises)"""
    try:
        if sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        elif sys.platform == "darwin":
            subprocess.Popen(["afplay", path])
        else:
            for cmd in ("paplay", "aplay", "play"):
                if shutil.which(cmd):
                    subprocess.Popen([cmd, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
    except Exception:                                      # pragma: no cover
        pass


def band_text(b):
    r0, r1, base, r2, r3 = b
    if r0 == r1 == 0 and r2 >= 9000:
        return "all revs"
    if r0 >= 9000 and r3 >= 9000:
        return "not used"
    return "full %d-%d rpm (fades %d..%d)" % (r1, r2, r0, r3)


# ---------------------------------------------------------------- command line
def cli(argv):
    if not argv or argv[0] == "list":
        d = find_dir(argv[1]) if len(argv) > 1 else None
        for k in range(len(D.SETS)):
            print(set_title(k))
            for tuned in (False, True):
                c = cars_of(k, tuned)
                if c:
                    print("     %s: %s" % ("tuned" if tuned else "stock", "; ".join(c)))
        if d:
            print("folder:", d)
        return 0
    d = find_dir(argv[1]) if len(argv) > 1 else None
    if not d:
        raise SoundError("Give the data/sound/engine folder (the one holding rx714.wav).")
    if argv[0] == "copy" and len(argv) == 4:
        copy_set(d, int(argv[2]), int(argv[3]))
        print("set #%s now sounds like set #%s" % (argv[2], argv[3]))
    elif argv[0] == "put" and len(argv) == 5:
        k, slot = int(argv[2]), argv[3].lower()
        if slot not in SLOTS:
            raise SoundError("SLOT is one of " + ", ".join(SLOTS))
        put_file(d, set_names(k)[SLOTS.index(slot)], argv[4])
        print("replaced", set_names(k)[SLOTS.index(slot)])
    elif argv[0] == "restore":
        names = set_names(int(argv[2])) if len(argv) > 2 else None
        print("restored %d file(s)" % restore(d, names))
    else:
        print(__doc__)
        return 1
    return 0


# ---------------------------------------------------------------- the tab
def build_tab(tab_parent, root, app=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    parent = tab_parent                     # no page scrolling: the panels scroll themselves (vpk_layout)
    prefs = wpk_view.load_prefs() if wpk_view else {}
    share = sharing()

    class Tab:
        def __init__(self):
            self.dir = find_dir(prefs.get("engine_dir", ""))
            self.k = 0
            self.status = vpk_ui.StatusLine("Choose the game's sound/engine folder.")
            self.fade = tk.StringVar(value="30")
            self.loop = tk.BooleanVar(value=True)
            self.match = tk.BooleanVar(value=True)
            self.filter = tk.StringVar()
            self.rows = []
            self.build()
            if self.dir:
                self.set_dir(self.dir)

        # ---- layout
        def build(self):
            # Blender-style: toolbar on top, status at the bottom, between them list | the loops | options (vpk_layout)
            top = ttk.Frame(parent, padding=(12, 10, 12, 4))
            top.pack(fill="x")
            ttk.Button(top, text="Choose sound/engine folder…", command=self.pick_dir).pack(side="left")
            self.dirlabel = ttk.Label(top, text="", foreground="#8f9eb2")
            self.dirlabel.pack(side="left", padx=8)
            ttk.Label(parent, text="Each car plays 4 engine loops + 4 exhaust loops that fade into each other as the revs climb "
                      "(it is by rpm, not by gear).  Pick a sound set, then replace its loops or copy another car's sound.",
                      wraplength=900, justify="left", foreground="#8f9eb2").pack(fill="x", padx=12, pady=(0, 4))
            self.status.bar(parent, ttk, tk, wraplength=900, fill="x", side="bottom", padx=12, pady=4)
            ttk.Separator(parent).pack(fill="x", side="bottom")
            ws = self.ws = VL.Workspace(parent, left_w=290, right_w=380)
            left = ws.left
            ttk.Label(left, text="Find a car:").pack(anchor="w")
            e = ttk.Entry(left, textvariable=self.filter, width=30)
            e.pack(fill="x")
            self.filter.trace_add("write", lambda *_: self.fill_list())
            lf = ttk.Frame(left)
            lf.pack(fill="both", expand=True, pady=(6, 0))
            self.lb = tk.Listbox(lf, width=30, height=22, exportselection=False)
            sb = ttk.Scrollbar(lf, orient="vertical", command=self.lb.yview)
            self.lb.configure(yscrollcommand=sb.set)
            self.lb.pack(side="left", fill="both", expand=True)
            sb.pack(side="left", fill="y")
            self.lb.bind("<<ListboxSelect>>", lambda _e: self.pick_set())
            main = ws.center
            self.used = ttk.Label(main, text="", wraplength=440, justify="left")
            self.used.pack(anchor="w")
            self.grid = ttk.Frame(main)
            self.grid.pack(anchor="w", pady=(8, 0), fill="x")
            self.warn = ttk.Label(main, text="", wraplength=440, justify="left", foreground="#f85149")
            self.warn.pack(anchor="w", pady=(8, 0))

            opt_sec = ws.section("Loops").body
            opt = ttk.Frame(opt_sec)
            opt.pack(fill="x")
            ttk.Checkbutton(opt, text="Make it loop smoothly, cross-fade", variable=self.loop).pack(side="left")
            ttk.Entry(opt, textvariable=self.fade, width=4).pack(side="left", padx=4)
            ttk.Label(opt, text="ms").pack(side="left")
            ws.flow(opt)
            op2 = ttk.Frame(opt_sec)
            op2.pack(fill="x", pady=(4, 0))
            ttk.Checkbutton(op2, text="Match the stock loudness", variable=self.match).pack(side="left")
            cp_sec = ws.section("Copy a sound").body
            ttk.Label(cp_sec, text="Give this set the sound of:").pack(anchor="w")
            self.srcvar = tk.StringVar()
            self.src = ttk.Combobox(cp_sec, textvariable=self.srcvar, state="readonly", width=30,
                                    values=[set_title(i) for i in range(len(D.SETS))])
            self.src.pack(fill="x", pady=4)
            ttk.Button(cp_sec, text="Copy", command=self.copy_from).pack(anchor="w")
            bt_sec = ws.section("Restore").body
            bt = ttk.Frame(bt_sec)
            bt.pack(fill="x")
            ttk.Button(bt, text="Restore this set", command=lambda: self.do_restore(True)).pack(side="left")
            ttk.Button(bt, text="Restore ALL sounds", command=lambda: self.do_restore(False)).pack(side="left", padx=8)
            ws.flow(bt)
            self.fill_list()

        # ---- list
        def visible(self):
            f = unicodedata.normalize("NFKC", self.filter.get()).strip().lower()      # RX-7 finds the arcade's full-width RX－7
            out = []
            for k in range(len(D.SETS)):
                hay = unicodedata.normalize("NFKC", set_title(k) + " " + " ".join(cars_of(k, False) + cars_of(k, True))).lower()
                if not f or f in hay:
                    out.append(k)
            return out

        def fill_list(self):
            self.vis = self.visible()
            self.lb.delete(0, "end")
            for k in self.vis:
                cs = cars_of(k, False) or cars_of(k, True)
                tail = (cs[0] + (" +%d" % (len(cs) - 1) if len(cs) > 1 else "")) if cs else "(no car uses it)"
                self.lb.insert("end", "#%d  %s  |  %s" % (k, D.SETS[k][0][0] + ".." + D.SETS[k][0][3][-2:], tail))
            if self.k in self.vis:
                self.lb.selection_set(self.vis.index(self.k))
            elif self.vis:
                self.lb.selection_set(0)
                self.k = self.vis[0]
            self.show()

        def pick_set(self):
            sel = self.lb.curselection()
            if sel:
                self.k = self.vis[sel[0]]
                self.show()

        # ---- folder
        def pick_dir(self):
            p = filedialog.askdirectory(title="Pick the game's data/sound/engine folder (the one with rx714.wav)",
                                        initialdir=os.path.dirname(self.dir) if self.dir else None)
            if not p:
                return
            d = find_dir(p)
            if not d:
                messagebox.showerror("Engine sounds", "rx714.wav is not in there. Pick the folder data/sound/engine.")
                return
            self.set_dir(d)

        def set_dir(self, d):
            self.dir = d
            if wpk_view:
                wpk_view.save_prefs(engine_dir=d)
            self.dirlabel.configure(text=d)
            self.status.set("Ready.")
            self.show()

        # ---- one set
        def show(self):
            k = self.k
            st, tu = cars_of(k, False), cars_of(k, True)
            self.used.configure(text="Stock cars: %s\nFully tuned cars: %s" % ("; ".join(st) or "-", "; ".join(tu) or "-"))
            for c in self.grid.winfo_children():
                c.destroy()
            names = set_names(k)
            shared = set()
            for i, n in enumerate(names):
                bands = D.SETS[k][2] if i < 4 else D.SETS[k][3]
                r = 2 * i
                ttk.Label(self.grid, text=SLOT_LABEL[i]).grid(row=r, column=0, sticky="w")
                ttk.Label(self.grid, text=n + ".wav", font=("TkFixedFont", 9)).grid(row=r, column=1, sticky="w", padx=8)
                ttk.Label(self.grid, text=band_text(bands[i % 4]).replace(" rpm (fades", " (fades"), foreground="#8f9eb2", font=("TkDefaultFont", 8),
                          wraplength=360, justify="left").grid(row=r + 1, column=0, columnspan=5, sticky="w", pady=(0, 4))
                text = ""
                if self.dir:
                    inf = info(os.path.join(self.dir, n + ".wav"))
                    text = "%.2fs" % inf[1] if inf else "missing!"
                    if is_changed(self.dir, n):
                        text += " ●"
                ttk.Label(self.grid, text=text, foreground="#e8710a").grid(row=r, column=2, sticky="w", padx=4)
                state = "normal" if self.dir else "disabled"
                ttk.Button(self.grid, text="Play", width=5, state=state, command=lambda n=n: self.play(n)).grid(row=r, column=3, padx=2)
                ttk.Button(self.grid, text="Replace…", state=state, command=lambda i=i: self.replace(i)).grid(row=r, column=4, padx=2)
                for o in share[n]:
                    if o != k:
                        shared.add(o)
            if shared:
                self.warn.configure(text="Some of these files are also used by set(s) %s, so changing them changes those cars too."
                                    % ", ".join("#%d" % o for o in sorted(shared)))
            else:
                self.warn.configure(text="")

        def play(self, name):
            play(os.path.join(self.dir, name + ".wav"))

        def fade_ms(self):
            try:
                return max(0.0, min(300.0, float(self.fade.get())))
            except ValueError:
                return 30.0

        def replace(self, i):
            p = filedialog.askopenfilename(title="Your %s loop (WAV)" % SLOT_LABEL[i].lower(),
                                           filetypes=[("WAV audio", "*.wav"), ("All files", "*.*")])
            if not p:
                return
            try:
                put_file(self.dir, set_names(self.k)[i], p, self.fade_ms(), self.match.get(), self.loop.get())
            except (SoundError, OSError) as ex:
                messagebox.showerror("Engine sounds", str(ex))
                return
            self.status.set("Replaced %s.wav" % set_names(self.k)[i])
            self.show()

        def copy_from(self):
            if not self.dir:
                messagebox.showinfo("Engine sounds", "Choose the sound/engine folder first.")
                return
            s = self.srcvar.get()
            if not s:
                return
            src = int(s.split()[0].lstrip("#"))
            try:
                copy_set(self.dir, self.k, src)
            except (SoundError, OSError) as ex:
                messagebox.showerror("Engine sounds", str(ex))
                return
            self.status.set("Set #%d now sounds like set #%d" % (self.k, src))
            self.show()

        def do_restore(self, only_this):
            if not self.dir:
                return
            n = restore(self.dir, set_names(self.k) if only_this else None)
            self.status.set("Restored %d file(s)." % n)
            self.show()

    return Tab()


if __name__ == "__main__":
    try:
        sys.exit(cli(sys.argv[1:]))
    except SoundError as ex:
        print("error:", ex)
        sys.exit(2)
