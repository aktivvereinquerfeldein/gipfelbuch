#!/usr/bin/env python3
"""Gipfelbuch-Kartenserver: Höhenmodell und Hangneigung vorab berechnen.

  hoehen.py liste <umriss.geojson…> --zoom 0-13            Kacheln im Gebiet zählen
  hoehen.py dem <ziel> <umriss.geojson…> --zoom 0-13 [--ohne liste.txt]
        Höhenmodell (Terrarium, gleiche Quelle wie bisher: AWS elevation-tiles-prod) herunterladen
  hoehen.py hang <dem-ordner> <ziel> --zoom 11-14
        Hangneigung (30°/35°/40°/45°, Farben wie in der App) aus dem Höhenmodell berechnen;
        Kacheln ohne steile Stellen werden nicht gespeichert (der Server liefert dann eine leere Kachel)
Benötigt: pip install shapely numpy pillow
"""
import sys, os, io, math, json, argparse, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image
from shapely.geometry import shape, box
from shapely.ops import unary_union
from shapely.prepared import prep

SRC = ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png",
       "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png"]

def lon2x(lon, z): return (lon + 180) / 360 * 2 ** z
def lat2y(lat, z): r = math.radians(lat); return (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z
def x2lon(x, z): return x / 2 ** z * 360 - 180
def y2lat(y, z): return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** z))))

def gebiet(files):
    return unary_union([shape((lambda j: j.get("geometry", j))(json.load(open(f)))) for f in files])

def kacheln(g, z0, z1):
    pg = prep(g); minx, miny, maxx, maxy = g.bounds
    for z in range(z0, z1 + 1):
        for x in range(int(lon2x(minx, z)), int(lon2x(maxx, z)) + 1):
            for y in range(int(lat2y(maxy, z)), int(lat2y(miny, z)) + 1):
                if pg.intersects(box(x2lon(x, z), y2lat(y + 1, z), x2lon(x + 1, z), y2lat(y, z))): yield (z, x, y)

def zr(s): a, b = s.split("-"); return int(a), int(b)

def lade(t, ziel):
    z, x, y = t; p = os.path.join(ziel, str(z), str(x), f"{y}.png")
    if os.path.exists(p): return 0
    os.makedirs(os.path.dirname(p), exist_ok=True)
    for versuch in range(4):
        for u in SRC:
            try:
                with urllib.request.urlopen(u.format(z=z, x=x, y=y), timeout=30) as r: data = r.read()
                with open(p + ".tmp", "wb") as f: f.write(data)
                os.replace(p + ".tmp", p); return len(data)
            except Exception: time.sleep(1 + versuch)
    raise RuntimeError(f"Höhenkachel {z}/{x}/{y} nicht ladbar")

FARBEN = [(45, (142, 58, 154)), (40, (224, 53, 43)), (35, (243, 154, 44)), (30, (245, 231, 75))]

def hang_kachel(png, z, y):
    a = np.asarray(Image.open(io.BytesIO(png) if isinstance(png, bytes) else png).convert("RGB"), dtype=np.float64)
    H = a[:, :, 0] * 256 + a[:, :, 1] + a[:, :, 2] / 256 - 32768
    n = math.pi - 2 * math.pi * (y + .5) / 2 ** z; lat = math.atan(math.sinh(n))
    res = 40075016.686 * math.cos(lat) / 2 ** z / 256
    l = np.concatenate([H[:, :1], H[:, :-1]], axis=1); r = np.concatenate([H[:, 1:], H[:, -1:]], axis=1)
    u = np.concatenate([H[:1, :], H[:-1, :]], axis=0); d = np.concatenate([H[1:, :], H[-1:, :]], axis=0)
    dx = (r - l) / (2 * res); dy = (d - u) / (2 * res)
    deg = np.arctan(np.sqrt(dx * dx + dy * dy)) * 57.2958
    out = np.zeros((256, 256, 4), dtype=np.uint8)
    for grenze, col in reversed(FARBEN):  # von flach nach steil überschreiben
        msk = deg >= grenze; out[msk, 0], out[msk, 1], out[msk, 2], out[msk, 3] = col[0], col[1], col[2], 150
    if not out[:, :, 3].any(): return None
    b = io.BytesIO(); Image.fromarray(out, "RGBA").save(b, "PNG", optimize=True); return b.getvalue()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("args", nargs="*")
    ap.add_argument("--zoom", default="0-13"); ap.add_argument("--ohne", default=None); ap.add_argument("--threads", type=int, default=24)
    a = ap.parse_args(); z0, z1 = zr(a.zoom)
    if a.cmd == "liste":
        g = gebiet(a.args); c = {}
        for z, x, y in kacheln(g, z0, z1): c[z] = c.get(z, 0) + 1
        print(json.dumps(c), "gesamt", sum(c.values()))
    elif a.cmd == "dem":
        ziel, files = a.args[0], a.args[1:]; g = gebiet(files)
        ohne = set(open(a.ohne).read().split()) if a.ohne else set()
        todo = [t for t in kacheln(g, z0, z1) if f"dem/{t[0]}/{t[1]}/{t[2]}.png" not in ohne]
        print("Höhenkacheln zu laden:", len(todo), flush=True); t0 = time.time(); n = 0; b = 0
        with ThreadPoolExecutor(a.threads) as ex:
            for s in ex.map(lambda t: lade(t, ziel), todo):
                n += 1; b += s
                if n % 2000 == 0: print(f"  {n}/{len(todo)} ({b/1e6:.0f} MB, {time.time()-t0:.0f}s)", flush=True)
        print(f"fertig: {n} Kacheln, {b/1e6:.0f} MB neu", flush=True)
    elif a.cmd == "hang":
        dem, ziel = a.args[0], a.args[1]; n = k = 0
        for z in range(z0, z1 + 1):
            zd = os.path.join(dem, str(z))
            if not os.path.isdir(zd): continue
            for xs in os.listdir(zd):
                for ys in os.listdir(os.path.join(zd, xs)):
                    if not ys.endswith(".png"): continue
                    y = int(ys[:-4]); n += 1
                    out = hang_kachel(os.path.join(zd, xs, ys), z, y)
                    if out:
                        p = os.path.join(ziel, str(z), xs, ys); os.makedirs(os.path.dirname(p), exist_ok=True)
                        open(p, "wb").write(out); k += 1
        print(f"Hangneigung: {n} Kacheln geprüft, {k} mit steilen Stellen gespeichert")
    else: raise SystemExit(__doc__)

if __name__ == "__main__": main()
