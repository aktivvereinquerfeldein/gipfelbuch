#!/usr/bin/env python3
"""Welt: Gebiete für „Relief weit“ (karte/relief.py, Zoom 0–12) wie bisher Europa/Alpen, jetzt weltweit.
Prüft alle Kacheln Zoom 8 (AWS Terrain Tiles) und schreibt Rechtecke (w,s,e,n je Zeile):
  <land.txt>  Kacheln mit Land über dem Meer (Zoom 6–11 werden dort gerechnet, Meer bleibt leer)
  <berge.txt> Kacheln mit mindestens --spanne Meter Höhenunterschied (dort zusätzlich Zoom 12, wie im Kerngebiet Alpen)
Aufruf: relief_weit_gebiet.py <land.txt> <berge.txt> [--spanne 800] [--nur w,s,e,n]"""
import io, math, argparse, urllib.request, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image
ap = argparse.ArgumentParser(); ap.add_argument("land"); ap.add_argument("berge"); ap.add_argument("--spanne", type=float, default=800); ap.add_argument("--nur", default="")
a = ap.parse_args()
def lon(x): return x / 256 * 360 - 180
def lat(y): return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 256))))
def pruefe(t):
    x, y = t
    for v in range(5):
        try:
            b = urllib.request.urlopen(urllib.request.Request(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/8/{x}/{y}.png", headers={"User-Agent": "qfe-active-relief"}), timeout=60).read(); break
        except urllib.error.HTTPError as e:
            if e.code == 404: return t, None, None
        except Exception: pass
        time.sleep(1 + 2 * v)
    else: return t, None, None
    im = np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), np.float32); h = im[..., 0] * 256 + im[..., 1] + im[..., 2] / 256 - 32768
    return t, float(h.max()), float(h.max() - max(h.min(), 0))
K = [(x, y) for x in range(256) for y in range(256)]
if a.nur:
    w, s, e, n = map(float, a.nur.split(",")); K = [(x, y) for x, y in K if lon(x + 1) > w and lon(x) < e and lat(y + 1) < n and lat(y) > s]
land = []; berge = []
with ThreadPoolExecutor(48) as tp:
    for (x, y), mx, sp in tp.map(pruefe, K):
        if mx is None or mx <= 0: continue
        r = f"{lon(x) + 1e-6:.6f},{lat(y + 1) + 1e-6:.6f},{lon(x + 1) - 1e-6:.6f},{lat(y) - 1e-6:.6f}"
        land.append(r)
        if sp >= a.spanne: berge.append(r)
open(a.land, "w").write("\n".join(land) + "\n"); open(a.berge, "w").write("\n".join(berge) + "\n")
print(f"{len(K)} Kacheln Zoom 8 geprüft: Land {len(land)}, Gebirge {len(berge)}")
