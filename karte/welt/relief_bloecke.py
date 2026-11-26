#!/usr/bin/env python3
"""Welt: welche Zoom-12-Blöcke bekommen feines Relief wie Vorarlberg (karte/relief1m.py)?
Nur dort, wo es freie Laserscan-Höhendaten gibt (Mapterhorn-Dateien mit Zoom 15 oder feiner, aus 1–2-m-Modellen der Länder),
und nur im Gebirge: Höhenunterschied im Block mindestens --spanne Meter (aus AWS Terrain Tiles Zoom 8, ca. 16 x 16 Punkte je Block).
Blöcke, die das laufende feine Relief schon hat (karte/relief1m/aktuell.json), werden übersprungen.
Ausgabe: eine Zeile je Block „x_y“. Aufruf: relief_bloecke.py <ausgabe.txt> [--spanne 500] [--ohne aktuell.json] [--nur w,s,e,n]"""
import sys, json, io, math, argparse, urllib.request, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image

ap = argparse.ArgumentParser(); ap.add_argument("aus"); ap.add_argument("--spanne", type=float, default=500)
ap.add_argument("--ohne", default=""); ap.add_argument("--nur", default="")
ap.add_argument("--usa-spanne", type=float, default=800)   # USA: sehr viele Gebirge mit Laserdaten – dort nur die großen (Rockies, Sierra, Cascades …)
a = ap.parse_args()
UA = {"User-Agent": "qfe-active-relief"}
def hole(u, t=60):
    for v in range(5):
        try: return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t).read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
        except Exception: pass
        time.sleep(1 + 2 * v)
    return None
L = json.loads(hole("https://download.mapterhorn.com/download_urls.json"))["items"]
fein = [i for i in L if i["name"].startswith("6-") and i["max_zoom"] >= 15 and i["size"] > 10000]
if a.nur:
    w, s, e, n = map(float, a.nur.split(","))
    fein = [i for i in fein if not (i["max_lon"] < w or i["min_lon"] > e or i["max_lat"] < s or i["min_lat"] > n)]
ohne = set(json.load(open(a.ohne)).get("bloecke", [])) if a.ohne else set()
z8 = [(int(i["name"].split("-")[1]) * 4 + dx, int(i["name"].split("-")[2].split(".")[0]) * 4 + dy) for i in fein for dx in range(4) for dy in range(4)]
print(f"{len(fein)} Mapterhorn-Dateien mit feinen Daten, {len(z8)} Kacheln Zoom 8 prüfen", flush=True)

def pruefe(t):
    x8, y8 = t; b = hole(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/8/{x8}/{y8}.png")
    if not b: return []
    im = np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), np.float32); h = im[..., 0] * 256 + im[..., 1] + im[..., 2] / 256 - 32768
    out = []; lon = (x8 + .5) / 256 * 360 - 180; lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y8 + .5) / 256))))
    sp = a.usa_spanne if (-170 <= lon <= -60 and 18 <= lat <= 72 and not (-142 <= lon <= -52 and lat > 49.5)) else a.spanne
    for j in range(16):
        for i in range(16):
            q = h[j * 16:(j + 1) * 16, i * 16:(i + 1) * 16]
            if q.max() > 0 and q.max() - max(q.min(), 0) >= sp: out.append(f"{x8 * 16 + i}_{y8 * 16 + j}")
    return out

bl = set()
with ThreadPoolExecutor(32) as tp:
    for r in tp.map(pruefe, z8): bl.update(r)
neu = sorted(bl - ohne)
open(a.aus, "w").write("\n".join(neu) + "\n")
print(f"{len(bl)} Gebirgsblöcke mit feinen Daten, davon {len(bl) - len(neu)} schon vorhanden, neu {len(neu)}")
