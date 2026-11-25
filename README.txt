VPK tool 2.4 - change the stickers (vinyls) of a Re3DX+ car
==========================================================

Start:  Python 3.9+ (python.org, tick "tcl/tk").  Then double-click tools/vpk_tool/run_vpk_tool.bat  (Mac/Linux: run_vpk_tool.sh).
        The first start installs numpy and pillow for you.  Tip: drop a car's .vpk onto run_vpk_tool.bat to open it.

Four steps (the bar at the top of the window shows where you are)
  1  Open car .vpk     pick a file from the game's vinyl folder, e.g. maz_FD3S.vpk.
                       The file already holds the car's texture and all its stickers - nothing to type.
                       The "Car" box jumps to any other .vpk in the same folder.
  2  Click a sticker   The grid is the in-game sticker menu. Each row of ten is one design in its ten colours:
                         #1-10 Line 1   #11-20 Line 2   #21-30 Fire 1   #31-40 Fire 2   #41 Namco   #42 Pac-Man
                         #43 Works colours   #48-57 Line 3   #58-67 Fire 3   #68-77 Tribal
                       Arrow keys move, Enter = Replace.
  3  Replace image     PNG or TGA with transparency, any size (made 512 x 512, or
                       1024 / 2048: see "Sharper stickers" below). It goes on every body of the car.
                       Colour...  recolours a one-colour sticker (lines, fires, tribals) instantly.
                       Export PNG saves the sticker you are looking at, to use as a template.
                       Undo puts that sticker back.   An orange dot marks stickers you changed.
  4  Save to game      Writes the .vpk you opened. The first time, the old file is kept as <car>.vpk.bak.
                       (Save as... writes a copy elsewhere.)

See it on the car (live preview)   View > Show on the car...   or the button under the sticker
  Pick the car's .wpk (data/cars/<car>.wpk). It is found by itself when it sits next to the .vpk or in a "cars" folder
  around it; otherwise you pick it once. A window shows the car with the selected sticker blended on, and it follows
  every Replace image / Colour / Undo / Import at once.
  - Views: front 3/4, side, rear 3/4, rear, front, top (drawn once when the car loads; the bar shows progress).
  - "With your changes / Original sticker / No sticker" flips between before and after.
  - "Paint" picks the car's paint (the pack keeps a copy of the body texture per paint, p1: p2: ...), and "Texture" shows
    which pack texture the sticker is painted on (found from the name in the .vpk, e.g. fc3s_body01_white).
    "Flip upside down" is only there in case a car's texture is stored the other way.
  - Aero kit / Wing / Bonnet / Mirrors / Wheels (under the picture): a car pack holds every optional part at once, so
    they used to draw on top of each other. Now each box shows ONE choice: None, A, B, C ...
      Aero kit   None = the stock car, A / B / C = loads maz_FC3S_aeroA / B / C.wpk (only the ones that exist next to it)
      Wing       starts on None;  A, B, C, D are the car's wings in file order
      Bonnet     A, B, C, D = the car's bonnets in file order (usually stock, vented, carbon, carbon vented)
      Mirrors    A / B      Wheels   A / B = the two wheel sets in the pack
    Your choices stay when you switch kit or car. The picture is redrawn each time (about a second).
  - Show UV layout (main window, under the sticker picture, and in "Big view…"): draws the car's real panel borders in red
    over the sticker, as a layer you switch on and off. Nothing is written into the picture, so you never have to replace the
    image with a template. It follows the selected body (Normal / Aero A / B / C). Double-click the picture, or press
    "Big view…", for a 768 px view to line things up.
  - Turn the car by hand (car preview): drag the picture with the mouse (the "Free" view), roll the wheel to move the
    camera closer / further. The slider and Reset still work. While dragging you see a quick half-size frame, the full
    picture follows when you let go.
  - Lighting (car preview): Flat = no shading, Soft = the normal look, Shadow = one sun from the upper left with dark
    sides, to judge how a sticker looks on the shaded car. Changing it is instant.
  - UV on the car (car preview): Borders draws the real panel borders on the car's surface, Checker paints a 16 x 16
    chequer over the whole texture so you can see stretching and scale. "strength" sets how strong. It updates live when you
    replace an image, and the main window's Show UV layout box switches it on and off together with the sticker view.
  - UV layer on the sticker (main window): Strength and Lines (Thin / Normal / Thick) next to Show UV layout.
  - Export UV of ALL cars… (main window button / View menu): pick the folder with the cars' .wpk files (sub-folders are
    included) and an output folder, and it writes one UV template PNG per car and per aero kit (<car>_uv.png) plus an
    index.txt, in the background with a progress bar and a Stop button. A car that cannot be read is listed in index.txt
    and does not stop the rest. Command line:  python vpk_tool.py uvall CARS_DIR OUT_DIR 2048
  - Fit a picture to the UV… (main window button / View menu): for art that is only roughly laid out like the car.
    Pick the picture, click it on a feature (the roof circle's middle, a door panel ...) to put a pin there, then drag the
    pin to where that feature belongs on the red panel borders. The picture bends between the pins, live. Right-click a
    pin to remove it, "Show original art" compares, "Keep the edges fixed" stops the borders from drifting.
    "Apply to the sticker" writes it into the sticker (it can be applied again after more tweaking); "Save PNG…" saves it.
    A few pins on the main features (roof, bonnet, doors, boot) is usually enough.
  - Big view: mouse wheel zooms at the cursor (up to 32x, real pixels shown from 3x), drag moves, double-click or "Whole"
    shows everything. The UV lines are vectors, so they stay sharp at any zoom.
  - Faster car preview: small triangles are drawn in bulk, so turning the car with the mouse is smooth (a drag frame
    takes about 0.05 s instead of 0.2 s), and recent drag frames are re-used.
  - Smoother zoom and drag: every change (wheel, slider, dragging, parts) shows a small quick picture at once and the full
    picture replaces it a moment later; the old picture stays on screen meanwhile instead of going blank. Only the view
    you are looking at is drawn, the others when you switch to them. The quick pictures adapt to your computer's speed.
  - Faster opening: the sticker grid appears at once and the pictures fill in a few at a time.
  - Camera distance (car preview): slider from close to far, starts a bit back (85%) so the car has room around it.
    Let go of the slider to redraw. Reset puts it back. (The preview is a flat orthographic view, so this is the
    zoom; it has no perspective field of view.)
  - The right-hand panel of the main window now scrolls (scroll bar, or the mouse wheel over it), so nothing is cut off
    on a short screen.
  - Export UV template… (in the car preview): writes the car's exact UV layout as a 2048 px PNG, for the parts you picked.
  - The UV templates (this one, Export UV of ALL cars, and the command line) show the car's OWN texture where it is not
    body paint: lamps, plate, badges, grille, glass, trim - instead of coloured shapes - so painting over the template
    does not turn them into blobs. Body panels stay coloured islands with their numbers. How it knows: the car pack keeps
    the body texture once per paint colour (the stock one and p1: p2: ...); what is identical in every paint is not paint.
    A car with a single paint has nothing to compare, so there anything that is not the texture's dominant colour is
    shown (a baked-in livery then shows too). Plain colours only: --plain on the command line (uv / uvall).
    Each island (hood, roof, sides, bumpers ...) has its own colour and number. It is drawn the same way up as the PNGs
    from Export PNG, so what you paint on it lands 1:1 on the car. Paint on a layer above it, hide or delete the template
    layer, export a 512 px PNG and use Replace image.
    Command line:  python vpk_tool.py uv nis_BNR32.wpk bnr32_uv.png
                   python vpk_tool.py uvcheck nis_BNR32.wpk my_sticker.png check.png   (red = real panel borders over your art)
  - It is a simple software picture: no game lighting or reflections, and only the one selected sticker is shown.
    Aero bodies use their own .wpk (Choose car .wpk...); it borrows the stock pack's textures by name, so keep
    maz_FC3S.wpk in the same folder as maz_FC3S_aeroA.wpk.

Some stickers have more than one picture
  - Tribal, Line 3, Fire 3 and Pac-Man are drawn per body (normal / aero A, B, C): a "Body" box appears.
    Tick "Change all bodies together" to give them all the same picture.
  - #43 Works colours has a version per paint (works 1 = black, works 2 = white) and a carbon-bonnet one:
    a "Version" box appears.

Editing many at once (File menu)
  Export all PNGs   writes 021_Fire-1.png, 042_Pac-Man_aeroB.png ... (one file per picture, no duplicates).
  Import PNGs       reads a folder back. Keep the number at the start of the file name. Pictures you did not
                    change are skipped, so only your edits are applied.
  Names:  021_Fire-1.png = all bodies,  ..._aeroA.png / _aeroB / _aeroC / _normal = that body,
          ..._works2.png = works index 2,  ..._carbon.png = carbon-bonnet version.

Decals on the glass, lamps and plate   (File > Car textures: glass, lamps, plate...)
  The window glass, the lamps and the plate are not on the page the stickers go on. Each is its own texture inside the
  car's .wpk (the glass is <car>_body02_cola01, 256 x 256 with its own transparency; the same picture also holds the
  badges: Mazda, Roadster, MX-5 ...). A picture painted into one of them is on the car for good, for everybody - not a
  selectable sticker. Use it for a windscreen banner, a sun strip, a tinted lower band.
  1  File > Car textures... lists the textures of the open car's .wpk (or asks for one): size, format, what uses it.
     The glass is selected for you.
  2  Export PNG...   The glass panes use only a small part of the picture (an NCEC windscreen is about 9 x 20 texels
     at 256 x 256), so export at 1024 or 2048 (the "Export at" box): the pane then has 37 x 82 or 75 x 165 pixels.
     UV guide PNG... draws the UV borders over the texture so you can see where each pane is. Where does it show?
     draws the car twice: bottom row, every spot that uses this texture in pink (for the glass: windscreen, rear
     window, door glass, the small quarter panes - and a few badge spots, Mazda / Roadster / MX-5 / fuel cap); top row,
     a position pattern in its place: a pane the pattern varies over (red across, green up, blue checks) can carry a
     decal; a pane of one flat colour maps to a single spot and cannot.
  3  Paint on the PNG in an image editor (upright, keep it square and the size you exported). Keep the tint square
     (the dark area the glass is mapped into) in mind: its colour and transparency ARE the glass.
  4  Import edited PNG...   Choose a folder: the car's own folder replaces the files (each kept once as .bak), another
     folder keeps the originals and gets a complete set. The texture is made the size of your picture (a power of two up
     to 2048; the file grows by about 2 MB at 1024 and 8 MB at 2048 for the glass) and the preview shows it at once.
  What is kept: every other byte of the .wpk is unchanged (checked, byte for byte, before anything is written); a pixel
  you did not touch comes back exactly as it was. Only RGB565, RGBA4444 and RGBA5551 textures can be edited (ASTC
  ones are refused). Gradients in a 4-bit texture band: "Dither on import" smooths them. A texture without alpha
  (RGB565) ignores your picture's transparency.
  Aero kits: they borrow the stock car's textures, so the stock edit covers them. A kit that has its own copy of the
  glass gets the same picture only if its copy is the same one (that is how 9 kits were); a kit with its own, different
  version (gem_964_RSR_aeroA) is left alone and listed - open that kit's .wpk to edit it.
  The game: nothing to change in the game, the texture is read like any other (on phones the half-size texture mode
  halves a 2048 texture again).
  Command line:  textures CAR.wpk   |   texexport CAR.wpk NAME OUT.png [SIZE]   |   texguide CAR.wpk NAME OUT.png [SIZE]
                 texprobe CAR.wpk NAME OUT.png   |   teximport CAR.wpk NAME IMAGE OUT [--size N] [--dither] [--car]
                 (--car: OUT is a folder and the aero kits are done too). NAME is a number, a name or a name without .dds.

Sharper stickers (1024 / 2048)   needs the game build that reads VPK2 (v10.7 or later)
  Edit > Size of new stickers: 512 x 512 (the default, the arcade's own size), 1024 or 2048 keeps more of a sticker's detail in the game; an all-512 file stays a VPK1 that every build reads. New and replaced stickers are made at that size; the stickers you do not touch stay as
  they are (a file can mix sizes). The game grows the car's texture to the sticker's size by itself when it blends,
  so the car's .wpk needs no change, and the live preview does the same.
  Saving writes a VPK1 while every picture is 512 x 512 (every game build reads it) and a VPK2 as soon as one is
  bigger (the status bar says which). An older build cannot read a VPK2: the car then shows no sticker.
  Costs in the game: the first time a car is drawn with a bigger sticker the blend takes longer (about 4 x at 1024,
  16 x at 2048; the log line "vinyl N on pack: made, X ms" shows it) and its texture is 2 / 8 MB (about a third
  more with mipmaps; the game's half-size texture mode on phones halves it again). Art made at the size you pick
  keeps its detail; a 512 picture scaled up only gets smoother. The car's own paint is not sharper than 512 either.
  Files made by tool 2.2 (a size in the kind byte) are still read and are saved as VPK2.

Other things
  Edit > Add a sticker number   give the car a number it does not have (made on all four bodies).
  Edit > Car settings           the texture name and works table read from the file (rarely needed).
  File > New empty car file     start a car file from nothing (you type the texture name once).
  File > New car file from a car .wpk   for an add-on / imported car that has no .vpk yet (the mt6, a car you converted from FBX ...): pick the car's
                       .wpk and the tool reads the body texture names itself (the picture most of the body is drawn with, e.g. NC1_aero0_0; the aero kits
                       from <car>_aeroA / B / C.wpk next to it).  Then Edit > Add a sticker number… for each sticker and Save as <car>.vpk in the game's
                       vinyl folder (same name as the car's .wpk).  Command line:  python vpk_tool.py newvpk CAR.wpk OUT.vpk [STICKER_FOLDER]
                       (a folder of PNGs named by sticker number: 01_line.png = sticker 1, 21_fire.png = sticker 21 ...; each goes on every body).
                       The stickers are painted in the car's OWN UV layout, so make them on its template, which needs no .vpk: View > Export UV
                       template from a car .wpk… (or, after New car file from a car .wpk, View > Export UV template; or  python vpk_tool.py uv CAR.wpk OUT.png 2048), or "Fit a picture to the UV…".  A stock car's 77 designs cannot be reused as they are:
                       they are drawn for that car's panels.
  Edit > Pictures that are not square   stretch (default) or pad with transparency.

Command line (no window)
  python vpk_tool.py info CAR.vpk
  python vpk_tool.py replace CAR.vpk 21 my.png [OUT.vpk] [--size 512|1024|2048]   swap one sticker on all its bodies (default 512)
  python vpk_tool.py unpack CAR.vpk DIR   /   pack PROJECT.json OUT.vpk      (the older project.json way)
  build_exe.bat makes a single vpk_tool.exe that needs no Python.

Small screens   The Models, Backfire flame and Wheels tabs (and the Exhaust outlets window) scroll: bars appear when the window is too small, the mouse wheel scrolls up / down and
  Shift + wheel sideways (over a picture or a list the wheel keeps doing its own job). On a big screen nothing changes.

The Backfire flame tab   (the 3rd tab: the exhaust flame, backfire_edit.py)
  The flame is shared by every car: backfire.wpk (3 pictures + 17 flame meshes) and backfire.bfx (17 flame animations + the exhaust OUTLETS of every car body) in the
  game's data folder. Open backfire.wpk; backfire.bfx beside it is opened too.
  Exhaust outlets...   Flames on an imported / edited car in the wrong place? The game draws one flame at every MUFFLER1 outlet of the car's key (cars/<car> or cars/<car>:aero_a|b|c)
                       and those outlets are the STOCK car's. This window lists every car key, shows the outlets (red = drawn, grey MUFFLER2 = not drawn by the game) over the car's body
                       as the Models tab's own textured 3D render, from behind and from the side, zoomed on the exhaust (untick Zoom for the whole car); cyan = the outlets.
                       The render takes ~3 s per car (a wireframe is shown until it is ready; the window stays usable) and has no wing, so the pipes are visible. The body follows the car you pick in the list: it is loaded from the Cars folder (the Models tab's folder, or "Cars folder...") as <car>.wpk / <car>_aeroA.wpk;
                       a key with no such file shows no body (never another car's), and "Open car .wpk..." loads one by hand, and lets you double-click x / y / z, Move all, Mirror L -> R / R -> L,
                       or Copy from another car. Click a row and drag the x / y / z SLIDERS under the table: the red dots move live in both views
                       ("Keep left / right mirrored" moves the other side with it, x flipped; each slider spans +-6 around the outlet's value, click another row to re-centre). Car space: 10 units = 1 m, +Z = front, +Y = up, right side of the car = negative x. Line the red dots up with the new tail pipes, Save to game.
                       Only cars that already have outlets in the .bfx can be edited (the file keeps its size); the keys are the pack names, so keep the car file names.
  Preview     the selected flame's triangles from the side (its length) or the front (its width), and on the right the texture it uses with its UV lines.
  Textures    Export PNG / Replace with PNG: the picture keeps its size and 16-bit format (256x512, 128x128 ...); a picture of another size is stretched.
  Shape       Width x / Length x for the selected flames or all 17 (Reset selected goes back to the opened shape).
  Animation   "Edit animation...": a flame's 17 frames, each a 4x4 matrix (16 numbers as stored; the diagonal is its size and grows ~0 -> ~2.5, m12..m14 is an offset).
  Save to game writes both files and keeps a .bak of each once. An unchanged pair is written back byte for byte (python backfire_edit.py selftest backfire.wpk).
  Checked: the game's own backfire.c / backfire_wpk.c (bfx_load, bfx_wpk_validate) accept your pair and the same pair after outlet, animation, shape and texture edits.
  NOT checked: seeing it on the phone. The game drops the whole effect if either file is invalid, so keep the .bak.
  Command line:  python vpk_tool.py backfire info backfire.wpk [backfire.bfx]   |   selftest backfire.wpk
                 python vpk_tool.py backfire outlets backfire.bfx [cars/maz_FD3S]   |   setoutlet backfire.bfx cars/maz_FD3S MF_NULL_R1 X Y Z [OUT.bfx]

The Wheels tab   (the 4th tab: the wheel packs, wheels_tab.py)
  Choose the game's wheels folder (data/wheels; remembered). Two kinds of pack:
    <name>_<n>.wpk   in-race wheels: part 0x100 = left wheel, 0x101 = right wheel (the left one turned 180 degrees about the up axis)
    <model>_high.wpk the preview model of the GET screen (one part)
  Browse     filter the list, pick a pack: the wheel is drawn (View: side, 3/4, front ...; Wheel: left / right / both), with its triangle count and textures.
  Textures   Export PNG / Replace from PNG (keeps the old picture's size and 16-bit format), Swap red / blue, Colour... (hue, saturation, brightness; alpha kept),
             Fill with one colour (for the tiny colour swatches of the _high packs). Nothing is written until Save.
  Custom wheel   "Export wheel as OBJ" (0.5 m wheel, textures as PNG beside it) and "Replace with an OBJ...":
             the model is turned so its axle runs along X (Auto = its thinnest direction; pick X / Y / Z if the guess is wrong), scaled to the old wheel's diameter and put on the old hub.
             A race pack gets the left wheel from your OBJ and the right wheel is built from it (or replace one side only). Outer face the wrong way round: Rotate 180.
             Inside-out: tick Flip faces. Textures: pictures named by the OBJ's .mtl (map_Kd) are added to the pack (plain lit, like the stock wheels without shine; tick "keep the old wheel's shine" to reuse the old shader and gloss map, which only fits the old UVs); or pick one of the pack's textures, or a PNG, for the whole wheel.
             Keep race wheels light (stock: a few hundred to a few thousand triangles; the game draws four per car).
  Save keeps the original once as <file>.bak; Undo goes back step by step.
  Command line:  python vpk_tool.py wheels list FOLDER   |   wheels swap PACK.wpk MODEL.obj [OUT.wpk] [--axis x|y|z] [--scale N] [--rot 0|90|180|270] [--flip-faces] [--no-fit] [--side both|left|right] [--png TEXTURE.png] [--keep-shine]
  Shine (from the game's render.c): stock race wheels use the car-blend shader (a gloss map looked up by the wheel's UVs, so it cannot fit a new model), plain, or a weighted env reflection.
  NOT checked: seeing it in the game (the pack passes the same validation as the Models tab's saves).

Good to know
  - A sticker is drawn for ONE car's body UVs. Use the art made for that car (its sticker TGAs); the 128 x 128
    menu thumbnails only upscale to something blurry.
  - Format and blend: tools/stage/pack_vinyl.py (the packer), src/app.c vinyl_tex and src/render.c wpk_texture_over.
  - The texture editor is wpk_tex.py (file layout, pixel formats and the checks are described at its top).
  - Tests: python3 tools/test_vpk_tool.py   and   python3 tests/test_vinyl_loader_host.py (runs the game's own
    .vpk reading and page growth, extracted from app.c / render.c, on files the tool wrote; needs cc and zlib).
  - Not checked: loading a saved .vpk in the Android app itself.

The Models (WPK) tab   (the 2nd tab: swap a car's 3D parts for your own model - merged from the arcade build, unchanged)
  Folders and dropdowns (Models tab): set the "Cars folder" (where the .wpk files are) and the "Arcade folder" (the playerCar folder with the PLC_<car> folders) once with
  Browse...; both are remembered (in ~/.vpk_tool_settings.json) together with the last car you had open, which opens again at start-up. The Car dropdown lists
  every car in the Cars folder (picking another car keeps the same kit when it has it), the Kit dropdown switches between Base and aero A / B / C. In Import arcade
  car and Part from arcade the arcade car is a dropdown too, pre-set to the car that matches the open file (names are matched, e.g. R35 = BNR35).

Dark smudges on edited or new geometry in the game: the .ao map was baked for the old shape. Save, then Bake AO.

Bake AO  (Models tab, bottom bar; or  python ao_bake.py CAR.wpk)
  The game darkens a car's body texture with <pack>.ao (baked ambient occlusion in the body texture's UV layout). After a body swap the old .ao no longer fits,
  so bake a new one: press Bake AO… (nothing to set up: the tool carries the game's own bake_ao.c, and on Windows a ready-built bake_ao_win.exe, so a copy of the .exe works for someone with no game source and no compiler. "Game source" is optional: if set, that folder's own bake_ao.c is used instead) (a few seconds; the .ao is written beside
  the .wpk, an old one is kept once as .ao.bak). Mac / Linux need a C compiler once (cc / gcc / clang); Windows needs none, or else a compiler / WSL with gcc (Ubuntu: sudo apt update && sudo apt install -y gcc), which it
  uses by itself when the native compile fails. The baker is compiled once into ~/.vpk_tool/ao. On macOS: install Apple's Command Line Tools once (Terminal: xcode-select --install); the tool compiles the baker itself after that. By hand in WSL:  gcc -O2 -o bake_ao tools/stage/bake_ao.c -lm  then
  ./bake_ao /mnt/c/.../CAR.wpk /mnt/c/.../CAR.ao 256 96
  The preview does not show AO; the game does.

Planned (not built yet): a 3rd tab, "Rebuild pack from arcade". A whole pack made from the arcade data the way Re3DX+'s own packer does it: every part converted, new body
  textures, paint variants and AO. It would render perfectly, but its UV layout is the arcade's, so existing vinyl stickers would not line up, and it needs more work per car.

Checking files with the game's own loader  (loader_check.py)
  python loader_check.py PATH/TO/Re3DXPlus-v10_7 FILE.wpk [more.wpk ...]   (needs a C compiler)
  Builds a small program from the real wpk_open in your Re3DX+ src/render.c and runs it on the files: header, tables, file ranges, vertex pools, chunk ranges,
  paint pairing. All 214 of your .wpk files and 400 imported files (kit N + last kit of every car with an arcade model) are accepted. It cannot draw: the
  Android app itself is still the real test.
  From reading render.c: an aero pack borrows every texture it holds no data for from the stock pack by name (wpk_share), so keep Base and aero files together
  and do not rename textures; a <pack>.ao beside a .wpk is the baked ambient occlusion in that body texture's UV layout.

Import arcade car  (Models tab > "Import arcade car..."; or  python vpk_tool.py plc CAR.wpk PLC_FOLDER KIT [OUT.wpk])
  Takes the original game's car (data/playerCar/PLC_<car>/ with PLC_<car>.mdl and .tex) and puts the body of one of its aero kits
  into the .wpk you have open: the stock car (Base) or its aero kit A, B or C. The arcade has more kits than the app: N (standard),
  A, B, C, D ... K, so any of them can go into any of the four files.
  How the body is textured: triangle by triangle, where the new body has the same surface as the .wpk's own body (same place) the .wpk's own UVs and material are kept,
  so the grille mesh, indicators, lens detail and plate look as they did. A triangle the car does not have (another kit's bumper, skirts, vents ...) gets a plain paint
  texel of that car (plain dark plastic if it faces down), so no lamp or badge picture lands on it and it is not left black. The arcade's extra decals are left out.
  Windows use the tint of the car's own windows. Vertex colours come from the arcade data. Vinyl stickers and any <pack>.ao ambient-occlusion file belong to the
  .wpk's own UV layout: they fit the surfaces it kept, not the new parts of another kit.
  The kit's own bonnet takes the place of Bonnet A (tick it off in the dialog to keep the car's). Lamp covers are clear glass over the car's own lens picture.
  What comes over: the kit's body shell, windows (tinted like the car's own), headlights, boot lid, number plates (the baked plate picture of
  that car's body texture), exhaust. What stays as the .wpk had it: wheels, lamps, mirrors, wings, bonnets, textures (the body is painted with the car's own
  body texture, so paint colours and vinyl keep working). Rebuild shadow and "interior" are on by default (the interior is what makes the windows look dark instead of see-through; untick it to save triangles).
  Want an arcade kit as this file's whole car (e.g. aero kit A <- arcade kit E)? Use Import arcade car..., not Part from arcade.
  Part from arcade...  (select one part in the list first, not the Body): replaces that part group (a bonnet 294-297, wing 290-293, mirror 288-289, lamp 272-276 ...)
  with one arcade part of your choice from the same car (bonnet_A, rearspoiler_E, mirror_N ...). Which arcade part belongs to which slot differs from car to
  car, so you pick it; the preview shows the result. Wheels are the same arcade wheels the .wpk already has.
  The preview follows the dialog: pick a kit (or tick a box) and the car on the right changes at once; the view buttons still work while the
  dialog is open; Cancel puts the car back, Import keeps it. "Part from arcade" previews the same way.
  Undo steps back; Save writes the .wpk and keeps a .bak.  Read and rebuilt without errors for all 124 arcade cars that have aero kits
  (a car whose arcade folder holds a stand-in model of another car gets a warning).
  Which cars: with your 214-file set, 51 of the 55 cars have an arcade model and import cleanly into their Base and aero A/B/C files
  (400 test imports of kit N and the last kit, no errors; the imported body matches each car's own body size to within 1 unit). Pick the
  PLC_<car> folder, or just the playerCar folder: the tool finds the car's own folder (names that differ are mapped, e.g. R35 = BNR35,
  GG3P_school = GG3PS, UCF10_taxi = UCF10T).
  Not possible yet: 964 38RS, 964 RSR, 997 AVA and SELEN have no arcade folder in the data (the folders R964 / R997 hold a BMW Z4
  stand-in, and the tool warns if you pick one). Some cars have only kit N (R35), others N + A-K.
  NOT checked: loading an imported .wpk in the Android app. Number plates: 47 of 51 cars show their plate picture, 2 get a plain white plate, and
  the two JZS161 cars get none (their plate part has no name). Lamp parts from the arcade are drawn as glow and are the least tested.

Models (WPK) tab - swap a car's 3D parts for your own model   (2nd tab of the window; the sticker tab is unchanged)
  Every aero kit (<car>_aeroA / B / C.wpk) is a complete car in its own file: body, bumpers, lamps, wing ... The tab opens one
  .wpk, lists its parts, exports them as OBJ, and puts your own OBJ back in place of any part.
  1  Open car .wpk      data/cars/<car>.wpk or a kit. "Kit" jumps between the stock car and its aero kits (same folder).
  2  Pick a part        Body (0), wheels (256-259), lamps (272-276), mirrors (288-289), wings (290-293), bonnets (294-297),
                        spare tyres (298-301), shadow volume (65535). Show / hide and "Show only the selected part" are preview only.
  3  Export OBJ…        the selected parts (or all) as OBJ + MTL + textures (PNG) in <name>_tex. Metres, +Z = front, +Y = up.
                        Objects are named t<part>_b<n>; materials <texture>__<kind><reflection>, e.g. fc3s_body01_white__paint80.
  4  Replace with OBJ…  your model goes in place of the selected part (or of the parts named t<part>_… in the OBJ).
                        Scale 10 (OBJ in metres), Rotate (front must be +Z), Flip faces, Flip texture V (on for Blender OBJ).
                        Each material is matched to one of the car's textures by name, or you pick the texture from a list.
  5  Save to game       writes the .wpk you opened; the old file is kept once as <file>.wpk.bak.   Undo steps back.
  Material kinds (the word after __):  paint (reflective car paint; paint95 = reflection %), flat (plain lit: interior, plastic),
    glass (see-through window), add (glowing, lamps), metal (reflective, no paint). A material named only like a texture copies the
    look the car already gives that texture.
  Textures...           add or replace a picture in the pack (made a power of two, at most 512). To use your own picture, add it, then
                        name the material after it (e.g. mywing.tga__paint80 or just mywing.tga).
  Rebuild shadow        makes the car's shadow volume from the new body's footprint (do it after replacing the Body).
  Good to know
  - Use the car's own UV layout when you want the car's paint on your model: Export OBJ shows it. Otherwise give your model its own texture.
  - An aero kit does not hold its own textures (they are borrowed from the stock .wpk by name): keep both files in one folder, and do not
    rename the stock pack's textures.
  - Limits: 65 535 vertices per piece (split bigger meshes), no animation. Wheels stay in the wheel parts (they spin and steer), centred on the hub.
  - Command line:  python vpk_tool.py wpk info CAR.wpk
                   python vpk_tool.py wpk export CAR.wpk OUT.obj [--tags 0,290] [--scale 0.1]
                   python vpk_tool.py wpk replace CAR.wpk 290 MODEL.obj [OUT.wpk] [--scale 10] [--rot 180] [--flip-faces] [--shadow]
                                                  [--texture NAME=FILE.png]
                   python wpk_edit.py selftest CARS_DIR    (reads every .wpk and writes it back: checks the writer)
  - Checked on the 107 packs of cars.zip (all read and rewrite with identical content) and in the software preview.
    NOT checked: loading an edited .wpk in the Android app itself. Try one small change first (a wing) before a whole body.

Good to know
  - A sticker is drawn for ONE car's body UVs. Use the art made for that car (its sticker TGAs); the 128 x 128
    menu thumbnails only upscale to something blurry.
  - Format and blend: Re3DX+ tools/stage/pack_vinyl.py and ports/android/src/app.c (vinyl_tex).
  - Not checked: loading a saved .vpk in the Android app itself.


Clear windows   (Models tab, "Clear windows" button; or  python vpk_tool.py wpk windows CAR.wpk [OUT.wpk])
  For a newly imported car whose windows are solid black.  A car built from one texture atlas has its window panes as plain body paint,
  sitting on a dark spot of the atlas; a stock car keeps them in their own glass batch (alpha blended), which is what makes glass see-through.
  The button moves the panes into a glass batch (the car's own glass material, e.g. its headlight lens).  A triangle is moved only when it is
  on a flat dark texel, on the outside of the car (the seats and dash behind a pane are not touched), above the middle of the body (bumper and
  sill trim stay black) not out at the side mirrors (nothing wider than 72 % of the body's half width), and part of a group of 40+ triangles (wipers, thin trim stay black); the rim rows of a pane that failed those tests are then added if they touch it and face the same way.  Undo puts them back.
  The glass gets a small grey texture ('window_tint', about 44 % opaque, like a stock car's glass texture) so the windows look tinted instead of
  open.  The button opens a small window with two sliders, Darkness (0 % clear ... 100 % solid) and Tone (black ... light grey); the car
  preview follows them live, OK keeps it, Cancel puts the car back, or tick "Use the car's own glass texture".  Finding the panes takes a few
  seconds when the window opens; moving the sliders afterwards is instant.
  The third slider, Mirror cut-off (default 72 %), keeps the side mirrors black: nothing wider than that share of the body's half width becomes
  glass.  Lower it if a mirror or its mount still goes see-through on some car, raise it if the outer edge of a side window stays black.
  The only slow step is the first look at the car when the window opens (a few seconds, the window stays alive); all three sliders are instant after that.
  Command line:  python vpk_tool.py wpk windows CAR.wpk OUT.wpk --dark 44 --tone 35 --inner 72   (--clear = the car's own, almost clear, glass texture).
  It is a guess from the shape of the car: run it on newly imported cars, check the preview, and Undo if it caught something it should not.
  On a stock car it still finds a few dark trim pieces (about 270 triangles on the BNR32), so use it on imported cars only.

Delete textures   (Models tab > Textures… > Delete; or  python vpk_tool.py wpk deltex CAR.wpk NAME_OR_NUMBER ... [--out OUT.wpk])
  Select one or more textures (Ctrl / Shift click) and press Delete.  The list marks the ones no part uses with "(unused)" as a guide, but
  you pick what goes: nothing is deleted automatically (the game may look some pictures up by name, like a number plate or team livery).
  The window says how many batches draw with each selected texture before it deletes anything.  A part that used a deleted picture keeps its
  shape and turns flat colour (deleting a car's reflection or specular map takes the shine off the parts that used it); everything else is
  renumbered, so nothing else moves.  Undo brings it all back.

Add part   (Models tab, "Add part..." under the part list; or  python vpk_tool.py wpk addpart CAR.wpk NAME --from TAG [OUT.wpk])
  Gives the car one more part, e.g. a second bonnet, a fourth wing or a boot lid, ready to receive your own model.
  1  Select a part of the same kind (Bonnet A) - the box suggests the next name (bonnet_B) - or just type a name:
       bonnet_B  wing_C  mirror_B  boot_A  exhaust_B  rollcage_A  roofitem_3   (a space or - works too; rearspoiler_E and hood_B are understood)
       or a tag number: 0x127 / 295.   The line under the box shows the tag it becomes; red text says what is wrong.
  2  Start from: a copy of any part of the car (it lies exactly on its original, so it starts hidden in the preview - Show / hide), or empty.
  3  Your model (optional): pick an OBJ or FBX in the dialog.  After Add, its Replace window opens for the new part (scale, rotation,
     textures) so the part is added and loaded in one go.  Command line:  python vpk_tool.py wpk addpart CAR.wpk wing_A --model WING.fbx OUT.wpk
  4  Add.  Without a model: select the new part and use Replace with OBJ... to give it your model.  Undo removes it.
  The name only picks the slot.  A .wpk stores a NUMBER per part (the tag), never a name: every name in this tool is worked out from that
  number.  So a free-text name cannot be saved in the file, and bonnet_B simply means tag 0x127, exactly what the part list calls "Bonnet B".
  Older tags have fixed letters (Bonnet A-D = 0x126-0x129, Wing A-D = 0x122-0x125, Mirrors A-B = 0x120-0x121), so a missing slot can be filled
  (the NCEC has no Wing A: wing_A puts one at 0x122) and a slot that is in use is refused.  Stock cars normally have all of them, so there is
  nothing free: the next step is the other tag scheme (bonnets 0x180-0x188, wings 0x190-0x19e, mirrors 0x1a0-0x1a2 ...), and the tool will not
  mix schemes behind your back - type the tag number (0x180) when you want that.  A car that only has newer tags counts from its first one
  (stock bonnet 0x180 = A, bonnet_B = 0x181).  The preview letters the variants by the order the car really has, so with a gap its A, B ...
  can differ from the list's.
  A group the car has none of yet (no wing / mirrors on a newly imported car) follows the tag scheme of the car's other parts: wing_A is 0x190
  on a car whose bonnet is 0x180, and 0x122 on a car with old-scheme parts.
  A NEW CUSTOM CAR (Convert FBX car to WPK / Rebuild from arcade): the converter keeps the DONOR car's whole part table and only empties
  it, so Wing A-D, Bonnet A-D, Mirrors A-B, the lamp parts and the tyre-only slots are already in the new car's list, with 0 triangles
  for what the FBX did not have.  Those need no Add part: select "Bonnet B (295)" and use Replace with OBJ...  (typing bonnet_B in Add part
  then says "already in the car, but empty" and the same advice).  Add part is for a slot the donor does not have at all - a boot lid, a roll
  cage, roof items, a fifth bonnet - so pick a donor that already has the parts you need.  The FBX converter itself only fills Wing / Bonnet A-D.
  An EMPTY part cannot be saved: the tool asks you to fill it (Replace with OBJ...) or delete it first.  From the command line a part is
  always a copy (--from), then  wpk replace CAR.wpk TAG MODEL.obj  swaps in your model.
  NOT checked: that the Android app offers an added bonnet / wing / mirror as a choice in its menus - the tags are the ones the stock
  packs use (Bonnet A-D = 0x126-0x129, 0x180-0x188 ...), but whether the game reads a given slot is up to the game.

FBX support (Models tab / wpk_edit.py)
  Export:  Export... now offers "Autodesk FBX (binary)" next to OBJ (or:  python vpk_tool.py wpk export CAR.wpk OUT.fbx).
           Metres, +Y up, +Z front; textures go to OUT_tex/ as PNG and are linked from the FBX.
  Import:  Replace with... accepts .fbx as well as .obj (or:  python vpk_tool.py wpk replace CAR.wpk TAG MODEL.fbx).
           Binary FBX 7.x (Unity / Blender / Maya).  ASCII FBX is refused - re-save it as Binary.
           Every mesh+material becomes one batch.  Material names are matched to the car's textures the same way as for OBJ.
           Parent transforms (position / rotation / scale of the objects) are baked in.  Use --scale if the FBX is not in metres.
  Code:    fbx_io.py (pure Python + numpy, no FBX SDK).

FBX -> WPK (fbx2wpk.py)
  Turns an arcade-style car FBX (the DS_cars/CarXXXXXX folders: FBX + PNG textures) into a Re3DX+ car pack.
    python fbx2wpk.py CAR_FOLDER DONOR.wpk OUT.wpk [--name NAME] [--preview OUT.png] [--flip-faces]
    python fbx2wpk.py --all DS_CARS_DIR DONOR.wpk OUT_DIR           (every car; one that fails is listed and skipped)
  DONOR is any stock car .wpk (e.g. maz_FD3S.wpk): it gives the pack layout, env/spec maps and shader presets; its own
  geometry and unused textures are dropped.  Body = PartsBody + normal-aero set, Wheel_FL/FR/RL/RR -> wheels, PartsWing_nn -> Wing A-D.
  Not converted yet: aero kits A/B/C (separate _aeroX.wpk), lamps, mirrors and bonnets as separate parts (they stay in the body),
  and the game's own car table entry.

Convert FBX car to WPK (Models tab button)
  "Convert FBX car to WPK..." asks for the car folder (or a folder with many cars), a donor .wpk and where to save.
  One car: saved and opened.  Many cars: one CarXXXXXX_<name>.wpk each, with progress / Stop; cars already in the folder are skipped.
  Part rules: body = the stock variant of every part group under Chassis (lowest-numbered aero/bumper, N of N/A/B/C), wheels = Wheel_FL/FR/RL/RR,
  wings = Wing_00..03 -> Wing A-D.  Window materials become glass.  Shadow and alpha meshes are skipped.
  Cars with no wheel meshes (pacman, taiko drum) get a guessed hub height and no wheels.

Aero kits, bonnets, wings (fbx2wpk.py)
  Aero kits A/B/C: the 2nd, 3rd and 4th variant of every group of alternatives in the FBX (Parts0Aero_01..03, FrontBumper_01.., BodyKit_01..)
  are written as CAR_aeroA.wpk / _aeroB / _aeroC (full packs, like stock kit packs).  --no-kits or the checkbox in the dialog skips them.
  Bonnets: bonnet meshes become Bonnet A; Bonnets/Bonnet_00.. folders become Bonnet A-D.  Wings: Wing_00..03 -> Wing A-D.
  Not separable in the FBX data (so they stay in the body): mirrors, lamps, tyre sets, brakes, driver.

Replace with... an FBX car (Models tab)
  Picking a .fbx now opens a simpler dialog: the selected part or the whole car, and the aero kit (Stock / A / B / C).
  Only the stock variant of each part group is used; shadow, paint-overlay and effect meshes are skipped.  The PNG textures next to the FBX
  are added to the car, the new body texture becomes body01 with its p1..p5 copies.  "Whole car" can also clear the old car's lamps,
  mirrors and wing/bonnet options so nothing of it is left.  (.obj files still use the old dialog.)

Mirrors, tyre-only slots (fbx2wpk.py)
  Mirrors: the pair of small mirror-image pieces at the door tops is moved out of the body into Mirror A (found on ~75% of cars).
  Tyres: the rubber ("Gom" mesh, or the outer ring of a one-piece wheel) is also written to the tyre-only slots 0x12a..0x12d.
  Brakes, lamps and driver stay as they were (see the notes above).

UV unwrapping (uvgen.py)
  A model without UVs gets them automatically when you import it (Replace with...) or convert it: box-projection pieces, equal texel
  density, packed into the 0..1 square.  Standalone:  python uvgen.py in.obj out.obj   (writes an OBJ with the new UVs).

Livery painter (livery.py, "Livery..." button on the Models tab)
  Recolours the body (keeps the baked shading) and adds patterns placed by the car's 3D shape: centre stripes, side band, two-tone roof,
  fade towards the rear, number roundels on the doors.  All body textures (body, bonnet, kits) and the p1..p5 paint copies are repainted.
  Command line:  python livery.py CAR.wpk OUT.wpk --color #d01818 --stripes #ffffff --side #ffffff --roof #101010 --number 86 --png tex.png

Glass (fbx2wpk.py)
  The game tints glass through a small texture (stock cars: body02_colA01).  Converted cars used to have none, so windows rendered white.
  Now every converted car gets a dark window tint texture (<name>_glass.dds) and a pale see-through one for lamp covers (<name>_glass_lamp.dds).
  Packs converted with an older version still have white windows: convert them again.

Lamps (fbx2wpk.py)
  The FBX's additive glow meshes (lightADD) used to sit inside the body, so the lamps could never switch off.  Now, like stock cars:
    0x112  rear lamp glow (tail lamps)           0x113  the same, a touch bigger and brighter, plus one flare quad per side
    0x114  everything else (headlamps, fog, turn signal, plate light)
  The flare uses the game's own car_brakelamp_glow texture.  Which of these the game switches on braking / lights is not known to me:
  this copies the stock layout, it has not been tried in the game.  Lamp covers (glass) stay in the body as pale see-through glass.

Paint colours (the in-game colour choice)
  A stock car keeps its body texture in the default colour plus five copies, p1:NAME .. p5:NAME, one per paint the game offers
  (the stock RX-7: blue, red, white, silver, black).  Converted cars used to have five identical copies, so changing the colour did nothing.
  Now fbx2wpk makes the five paints from the car's own texture (baked shading, lamps, badges and trim kept) for the body, mirrors,
  wing A and bonnet A.  Other wing / bonnet options keep their own texture (each extra paint texture costs 2.6 MB).
  Change the colours or fix an old pack:  "Paint colours..." button, or  python livery.py CAR.wpk OUT.wpk --palette #1f4aa8,#c4200f,#f2f2f0,#9aa0a2,#17181a
  fbx2wpk.py --no-palette skips it.  Not tried in the game: that the game picks p1..p5 for its paint choices is how the stock cars are built.


Engine sound tab (new)
  The 5th tab swaps a car's engine / exhaust sounds.  Each car plays 4 engine + 4 exhaust loops (data/sound/engine/*.wav) that
  fade into each other by RPM (not by gear).  The car type picks one of 30 sound sets (stock and fully tuned cars differ).
  1  Choose the game's data/sound/engine folder (the one with rx714.wav).  It is remembered.
  2  Type a car in "Find a car" (RX-7 finds the full-width RX－7) and click its set.
  3  Replace... one loop with your own WAV (any rate, mono or stereo, 8/16/24/32-bit PCM; saved as mono 16-bit), or
     "Give this set the sound of" another car's set -> Copy.  Play listens to a loop.
     "Make it loop smoothly" cross-fades the end into the start; "Match the stock loudness" levels it like the original.
  4  Restore this set / Restore ALL puts the originals back (they are kept in engine/_orig the first time).
  Some files are shared between sets (e.g. rx714-717 are used by the stock AND the tuned RX-7); the tab warns you.
  Command line: python vpk_tool.py engine list | copy DIR TARGET FROM | put DIR SET e1..m4 FILE.wav | restore DIR [SET]

Gloss (Models tab, "Gloss..." button)
  Changes the shine of a car that is already built, no model is re-imported.  Select parts first to limit it, or select none
  for the whole car.  Each material (texture) of the car is a row: what it is now, a Look (keep / Glossy paint / Metal / Glass /
  Flat) and Gloss % (0 matte .. 100 mirror; stock paint is about 20, only used by Glossy paint).  "All body paint -> glossy"
  turns the body paint (and flat materials called body / paint) into glossy paint in one click.  The picture follows live; Apply
  keeps it (Undo works), Cancel puts the car back.  The car needs its reflection map (the 'alfa' texture): stock cars have it.
  In the Replace dialog the same Look / Gloss % choices are there for a new model's materials.

Reflection map for custom models
  Paint reflects through the car pack's own 'alfa' texture (plus a specular map), not through your OBJ/FBX.  Importing into a stock
  car's .wpk (Replace with OBJ...) or converting an FBX with a stock donor already has it.  If a car has none, Gloss... offers to copy
  both maps from any stock car's .wpk (Undo removes them).  Converting an FBX no longer drops the maps when no material uses them yet.
Rebuild from arcade (arc2wpk.py, "Rebuild from arcade..." button)
  Builds whole car packs from the arcade game's own car data (playerCar/PLC_xxx: .mdl + .tex, and the game's script data: color, position, tire).
    python arc2wpk.py PLC_FOLDER DONOR.wpk OUT.wpk --game NF_DIR [--kit A]
    python arc2wpk.py --all PLAYERCAR_DIR DONOR.wpk OUT_DIR --game NF_DIR [--stock STOCK_WPK_DIR] [--no-kits]
  Body, windows, interior, plates and exhaust come from the tool's importer; on top of that:
    mirrors (mirror_N, mirror_A), bonnets A-D (bonnet_N/A/B/C), wings A-D (rearspoiler_a..d), lamps (run -> 0x112, stop -> 0x113, light -> 0x114),
    wheels at the game's hub positions with the car's rim picture and wheel size, the donor's tyre fitted to the tyre model,
    aero kits A/B/C as OUT_aeroA/B/C.wpk, and the five in-game paints from the game's DefaultColor list for that car.
  Stock-style look: flat paint area + vertex-colour shading; the kit atlas supplies grille / lamp / plate / badge detail on the nose and tail only.
  Donor: the car's own stock pack when --stock has one, else any stock pack (the RX-7 was used).
  Not decoded: the arcade's own paint shader (colour masks, baked shading), side detail, carbon pads (dark).  Which wings / bonnets go in which slot
  and which lamp part the game switches when are my defaults.  Not tried in the game.

Headlamps (arc2wpk.py)
  Lamp glow shapes whose picture is in the kit atlas (no separate hash texture) used to be drawn as plain additive white.  They now use the atlas
  lamp pictures, so headlamps / tail lamps show their real lamp detail and glow properly at night.

Headlamp placement (arc2wpk.py)
  The arcade's lamp_light glow shapes are positioned by the game's own light positions, so on some cars (2000GT, S660 ...) they did not sit on the
  real headlamp.  The converter now finds the headlamp lens pieces of the body and keeps only the glow shapes that sit on a lens; a lens without one
  gets its own glow quad (the game's glow texture).  Cars where no lens piece is found keep the arcade's glow shapes as they were.


Headlights (v3)
---------------
Models tab -> "Fix headlights" (or: python wpk_edit.py headlights CAR.wpk [OUT.wpk]; also runs by itself after an FBX car convert).
A solid dark lens cover hides the bulbs in the game, and the bulbs live only in the lamps-ON part (0x111), which the game draws
in a race, not in the garage/menus (those draw 0x110).  The fix turns the cover into clear glass and copies the bulbs to 0x110.
The new "Lights" box under the preview shows what the game draws: garage/menus, race, or race braking.

Part groups in the preview (patch)
----------------------------------
Packs hold every wing, bonnet, mirror, boot lid, exhaust and roll cage variant at once, each as its own part, so the preview
used to draw them on top of each other. The preview now knows both tag schemes (old 0x120-0x129, new 0x130-0x246):
  Wing       0x122-0x125, 0x190-0x19e        (none shown until you pick one)
  Bonnet     0x126-0x129, 0x180-0x188        (first one the pack has)
  Mirrors    0x120-0x121, 0x1a0-0x1a2
  Boot       0x1b0-0x1b1
  Exhaust    0x130-0x131
  Roll cage  0x132, 0x1c0-0x1c8              (none shown until you pick one)
  Roof item  0x201-0x246                     (none shown until you pick one)
A, B, C ... follow that order among the variants the pack really has. The Models tab hides the same parts at start.
These groups were worked out from the shapes and positions of the parts in 727 packs, not from game data: if one is wrong, tell me.

Lamps tab (headlights and tail lights)
--------------------------------------
Move, resize, turn, flip, mirror, split, delete, recolour and ADD the lamps of a car (lamp_edit.py; the "Lamps" tab).
Every connected piece of the lamp parts is one lamp in the list:
  0x110 garage / menus   0x111 race, lamps on   0x112 race, tail glow   0x113 braking, stop glow   (lens = clear glass in the body)
Pick lamps in the list (they turn blue in the picture), then type a new position / size or nudge with the buttons.
 - "Edit both sides" repeats each change mirrored on the other side of the car.
 - "Also the matching piece in the linked part" repeats it in the other part the lamp lives in (tail glow <-> stop glow,
   garage <-> lamps on); one strip for both sides is cut so only this lamp's part moves.
 - Colour / Glow %: tint or dim lamps; "Flat colour" gives any colour but loses the texture's detail.
 - Add a lamp: kind, parts, facing, shape (rectangle / round / ring), colour, size, position; Count + Step = a row (LED bar);
   Both sides = mirror image; Snap to body = just outside the skin; Tilt to the surface = lies flat on the slope (a plane
   is fitted over the lamp's footprint). New lamps use a soft glow texture (lamp_glow_*) added to the pack.
 - Lens covers: "Show what would turn to glass" previews (pink) what the headlight glass fix would change, with the
   black threshold and reach adjustable, so black trim next to a lamp can be left out; then "Make it clear glass".
 - Copy lamps to other kits: lamp parts + textures into the car's other packs (.bak kept).
A tail lamp must be in BOTH 0x112 and 0x113 to show racing and braking; a headlamp needs 0x110 as well to show in the garage.
A part holds at most 65535 vertices; the game's own limit on lamps is not known. Checked in the tool's preview, not in the game.
Command line:  python vpk_tool.py lamps list|selftest CAR.wpk   |   lamps apply SRC.wpk DST.wpk ...   |   lamps kits SRC.wpk


Meters tab (gauge pictures)
---------------------------
Edit the dial faces, needles and small dials of a PLAIN meters file: meters-v2.bin (WMS2, 6 zones per car) or the older
meters.bin (WMT1, 2 zones: day / night). meters_edit.py, the "Meters" tab. Reading a file and writing it back gives the identical bytes.
  WMS2: "WMS2", version 1, cars, 6 zones, 9 parts, tiles | cars x 6 x 9 u16 tile numbers (0xFFFF = empty) | tiles x (w, h, offset) | pixels
  WMT1: "WMT1", cars, tiles | cars x 2 x 9 u16 | tiles x (w, h, offset) | pixels            (pixels: w x h x 4 bytes R G B A, no compression)
 - Sheet view: each zone is a strip of pictures; click one, Export / Replace the tile or the whole sheet as PNG. Tiles are shared
   between cars: replacing one asks whether to change it everywhere or only for this car (a copy is added).
 - As in game view: the meters drawn the way the engine draws them (every part centred on its fixed position of the 640 x 480
   screen, needles turned by the km/h / rev / boost sliders, glow parts added, "Shift lamp lit"); click a part to select it.
   Positions and needle ranges come from the engine's tables (meter_tables.py, made by gen_meter_tables.py from meterdb.h /
   meterdb_v2.h; run it again if the engine's tables change).
 - Engine check: the engine's own reader (meter_pack.h) ignores a file it dislikes. The tab applies its rules: 55 cars, 1-8192 tiles,
   tiles 1-4096 px, the first N parts of each car and zone filled (N from the engine's table) and the rest empty, nothing after the
   last tile. Saving a file that fails asks first. It also warns when a shift lamp tile was changed (see below).
 - Shift lamp: the racing style's (zones 2 and 3, part 4) lamp is drawn by the engine at (491, 283) and lit only while its tile is the
   original blank one (tiles 12 and 17). Paint the lamp in a replacement face at (62, 75) of the 256 x 256 tile and leave those tiles alone.
 - Name tiles from sprite folder: points at the game's data/sprite and tells you which PNG each tile is (exact pixel match).
Command line:  python vpk_tool.py meters info|check|sheet|tile|preview|selftest FILE.bin

Models tab: engine view
-----------------------
The Re3DX+ engine draws only these part numbers of a car: 0-2 (ground, short, long range), 0x100-0x103 wheels, 0x110-0x114 lamps,
0x120-0x129 dress-up parts (2 mirrors, 4 wings, 4 bonnets: one of each is chosen), 0x12a-0x12d custom tyres, 0x141-0x17f high brake lamps,
0xffff shadow. Any other number is a "named object" a car never turns on. The part list marks such numbers with *, and the checkbox
"Show only what the Re3DX+ engine draws" shows the car as that engine would. (Packs with bonnets at 0x180 and mirrors at 0x1a0 lose them there.)
"Fix headlights" opens a live dialog like Clear windows: Darkness and Tone (how dark the lens glass is), Reach (how far around a lamp
the cover is searched) and Lens black (how dark a paint pixel counts as lens). The preview follows the sliders; Cancel puts the car back.
(Older note: it used to look first and ask, with the number of triangles it would turn into glass.

Lamps tab
---------
Also lists the engine's high-mount / wing brake lamps (0x141-0x17f; shown only while braking, for the wings whose bit is set in the number).

Encrypted game files (WMDC)
---------------------------
aura.bin, meters.bin, mt6meter.bin, mt6xmeter.bin and the .txt files next to them are WMDC containers: a 32-byte header, then the
data in 1 MiB chunks each followed by a 16-byte tag. They are encrypted (strong cipher, key not known to this tool), so it cannot open
or write them; wmdc.py only reads the header. The Re3DX+ engine reads the plain meters.bin / meters-v2.bin instead, which is what the
Meters tab edits. Nothing in the engine source reads an aura file.


Part numbers the engine draws (parts marked * in the Models list are NOT drawn)
-------------------------------------------------------------------------------
Wings 0x122-0x125 (A-D), bonnets 0x126-0x129 (A-D), mirrors 0x120-0x121. Numbers like 0x180 / 0x190 stay invisible in the game.
Add part... now uses the drawn numbers by default (bonnet_B = 0x127, wing_A = 0x122).
Models tab -> "Move to part number..." renumbers the selected part (geometry and textures stay); pick a drawn slot from the list
or type 0x127 / wing_B. python wpk_edit.py movepart CAR.wpk FROM_TAG TO [OUT.wpk]

Aero kits: each kit is its own file
-----------------------------------
name.wpk (Normal), name_aeroA.wpk, name_aeroB.wpk, name_aeroC.wpk. In the stock cars every kit file is a full copy of the car: only the
body (0), some lamps (0x110-0x114) and the kit's wing (0x122) differ. A part you add belongs only to the file you added it to.
Models tab -> "Copy to other kits..." copies the selected parts (same numbers, with their textures and paint copies) into the car's
other kit files beside this one (tick which). python wpk_edit.py kitcopy CAR.wpk 0x122,0x126 [--to base,A,B,C]

Thin white lines along panel edges after painting (fixed): the paint colour only covered the texels inside the triangles, so the light
anti-aliased edge row of each panel stayed white / grey and showed as a dashed white outline in the game. The paint now also covers
that edge row (3 texels around each panel). Run Paint colours... (or the livery painter) again on a car that has the lines.

Paint colours / livery now only paint real body paint
-----------------------------------------------------
A black soft top, the badges / emblems sheet, the number plate, the lamp sheet and the exhaust used to be recoloured too (the roof of an
imported S2000 turned body blue). When the body is glossy paint (shader 1), a texture drawn with the flat shader (0) is left alone unless
its name says body / paint; names with plate, light, lamp, exhaust, rubber, tire are skipped; a dark texture with almost no paint-like
texels is skipped unless it is the main body texture. Re-run Paint colours... on a car that already got its roof painted: the roof's old
p1:..p5: copies stay in the file, so delete them in Textures... (or start again from the unpainted file).

Black grilles / intakes no longer turn body colour
--------------------------------------------------
A bumper texture that is mostly light but has a black grille used to be repainted whole (every used texel got the paint colour), so the
grille turned blue. A texture is now repainted whole only when almost none of it is paint-like (a really dark / black car); otherwise
only the light paint texels are recoloured and black stays black.
A wing (or any part) that should keep its own look instead of taking the body colour: Gloss... -> select it -> Flat (no shine).

A wing that is a texture of its own, in body colour
---------------------------------------------------
Set the wing to Glossy paint (Gloss...), then run Paint colours...: a small wing / bonnet texture (up to 256 x 256) in any wing or bonnet
slot now gets its p1..p5 copies too. Only its light paint-like areas change colour; black end plates and struts stay black.

UI milestone tests (no display needed)
  python tests/test_browser.py   python tests/test_ui_faketk.py   python tests/test_ui_texdlg.py   python tests/test_ui_m6.py   python tests/test_status_states.py
  VPK_REAL_CARS=<folder of car .wpk> python tests/test_texmgr.py
  python tests/regress_ops.py <vpk_tool folder> <folder of car .wpk>    (run for two versions and diff the output)
See UI_REDESIGN_NOTES.txt for what each milestone changed and what is NOT validated.

GPU viewport (Models tab)
  The 3D viewport of the Models (WPK) tab draws with OpenGL on your graphics card (moderngl + pyopengltk, installed by
  run_vpk_tool.bat on the first start; or: pip install -r requirements-gpu.txt). Orbit, pan and zoom run at full speed and
  full sharpness, and the car is shaded the way the game shades it (the same car.cg / car_blend.cg paint, glass and lamp
  programs, the game's lights, and the baked AO of <car>.ao beside the .wpk).
  Under the picture: GPU viewport (switch it off for the old software viewport), Shading (the game's car-select / Tokyo / Hakone
  lights, a headlight that follows the camera, or flat), Reflections, Baked AO, Perspective.
  If OpenGL 3.3 or the libraries are missing, or anything fails while drawing, the tool goes back to the software viewport
  by itself and says why under the picture and in the log. Force it with the environment variable VPK_VIEWPORT=soft.
  Check a machine:   python gl_viewport.py info        Draw a picture without a window:   python gl_viewport.py shot CAR.wpk out.png

GPU view in the sticker preview (View > Show on the car...)
  The car preview window draws the same way (OpenGL, the game's shading), so turning, zooming and every sticker change are instant:
  drag = turn the car, wheel = camera distance, the sticker, paint, UV Borders / Checker, Night, brake / other lamps, aero kit and
  part choices all work as before. The Lighting box then lists the game's lights (car select, Tokyo, Hakone, headlight, flat);
  "Perspective" and "GPU view" are under the camera slider. "GPU view" off, or any problem with OpenGL, gives the old software
  picture (Flat / Soft / Shadow). The Night bloom (glow around lit lamps) is only in the software view; the GPU view shows the lamps lit
  by the game's own additive pass.

Building for macOS
  A Windows .exe cannot run on a Mac and cannot be converted: build on a Mac (sh build_mac.sh -> dist/vpk_tool.app and dist/vpk_tool-mac.zip),
  or let GitHub build it: copy github_build_workflow.yml to .github/workflows/build.yml in your repo, set WORKDIR in it, and run it from the
  Actions tab (Apple Silicon and Intel builds + the Windows .exe).  The Mac app is unsigned (right-click > Open the first time) and uses the
  GPU viewport too, drawn off screen and copied into the window (see below).  Bake AO needs Apple's Command Line Tools once (xcode-select --install).
  build_exe.bat now also bundles the assets folder (the shift-lamp check of the meters tab needs it).

GPU viewport on macOS (and anywhere pyopengltk is missing)
  Tk on a Mac cannot host an OpenGL surface, so there the GPU draws into a window-less OpenGL context (moderngl, Apple's OpenGL 4.1; the
  shaders need 3.3), each picture is read back (a few MB) and shown in the window.  Same picture, orbit / pan / zoom / picking / outline /
  screenshot as on Windows; a few milliseconds slower per frame.  Pictures are drawn at the window's size in points, so on a Retina screen
  they are not 2x sharp.  If OpenGL cannot start the tool falls back to the software viewport and says why in the operation log.
  Check a Mac:  python3 gl_viewport.py info   (prints "viewport : offscreen" and the GPU).   VPK_GL_MODE=offscreen tries this path on Windows.
