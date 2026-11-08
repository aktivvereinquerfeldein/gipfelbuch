#!/usr/bin/env python3
"""Fertige Reliefbilder wie bei bergfex (Stufe 3, v1996).

bergfex legt ab Zoom 12 vorab berechnete Reliefbilder unter die Karte. Hier entsteht dasselbe als durchsichtige
Schicht: Schatten dunkel, Lichtseiten hell, ebenes Gelände ganz durchsichtig – darunter bleiben Wald, Wiesen und
Fels in ihren Farben. Höhenmodell: AWS Terrain Tiles (für Österreich das 10-m-Geländemodell), Zoom 12 bis 15.
Zoom 15 nur für Kacheln mit mindestens 200 m Höhenunterschied (Berge) – spart Speicher, in flachen Lagen reicht Zoom 14.
Hinweis: Die Karte (MapLibre) zeigt 256-px-Kacheln eine Stufe tiefer an – Zoom 15 der Bilder = volle Schärfe bei Kartenzoom 14.

Berechnung je Kachel (256 px) mit 1 Pixel Rand aus den Nachbarkacheln:
  - Licht aus mehreren Richtungen (315° Hauptlicht, dazu 270°, 0°, 225°) – Rinnen und Grate kommen von allen Seiten heraus
  - steile Hänge zusätzlich dunkler (ab ca. 25°), damit Fels und Rinnen Struktur bekommen
  - Ergebnis als WebP mit Durchsichtigkeit: dunkel = Schatten, Lichtseiten durchsichtig (auf hellem Fels sieht man
    nur Schatten; weiße Lichter würden dort verschwinden), ebenes Gelände nur ganz leicht getönt

Aufruf:  python3 relief.py <ausgabeordner> <zooms z.B. 12,13,14> <Rechtecke w,s,e,n;w,s,e,n> [Höhenmodell-Adresse]
Danach:  python3 relief.py --pmtiles <ausgabeordner> <datei.pmtiles>
"""
import sys, os, io, math, json, time, threading
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import numpy as np
from PIL import Image

DEM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
ALT = math.radians(45)
LICHT = [(315, .50), (270, .20), (0, .18), (225, .12)]
UEBERH = {12: 1.9, 13: 1.5, 14: 1.2, 15: 1.1}          # Überhöhung je Zoom (gröbere Stufen brauchen mehr)
DUNKEL, HELL, NEUTRAL = 0.80, 0.0, 0.86                     # Stärke der Schatten / Lichtseiten
FARBE_DUNKEL = (30, 26, 38)
QUALITAET = 70


def lon2x(lon, z): return int((lon + 180) / 360 * 2 ** z)
def lat2y(lat, z):
    r = math.radians(lat); return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z)
def tile_lat(y, z):
    n = math.pi - 2 * math.pi * (y + .5) / 2 ** z; return math.degrees(math.atan(math.sinh(n)))


class Hoehe:
    def __init__(s, url):
        s.url = url; s.c = {}; s.lock = threading.Lock()

    def lade(s, z, x, y):
        k = (z, x, y)
        with s.lock:
            if k in s.c: return s.c[k]
        a = None
        for v in range(4):
            try:
                req = urllib.request.Request(s.url.format(z=z, x=x, y=y), headers={"User-Agent": "gipfelbuch-relief"})
                with urllib.request.urlopen(req, timeout=30) as r: b = r.read()
                im = np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), dtype=np.float32)
                a = im[:, :, 0] * 256 + im[:, :, 1] + im[:, :, 2] / 256 - 32768
                break
            except Exception as e:
                if getattr(e, "code", 0) == 404: break
                time.sleep(1 + v * 2)
        with s.lock: s.c[k] = a
        return a

    def vergiss(s, z, ymin):
        with s.lock:
            for k in [k for k in s.c if k[0] == z and k[2] < ymin]: del s.c[k]


def mosaik(h, z, x, y):
    n = 2 ** z; m = np.zeros((258, 258), np.float32)
    mitte = h.lade(z, x, y)
    if mitte is None: return None
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            a = h.lade(z, (x + dx) % n, min(max(y + dy, 0), n - 1))
            if a is None: a = mitte
            ys = slice(1 + dy * 256, 1 + dy * 256 + 256); xs = slice(1 + dx * 256, 1 + dx * 256 + 256)
            # nur den benötigten Rand übernehmen
            yy0, yy1 = max(ys.start, 0), min(ys.stop, 258); xx0, xx1 = max(xs.start, 0), min(xs.stop, 258)
            m[yy0:yy1, xx0:xx1] = a[yy0 - ys.start:yy1 - ys.start, xx0 - xs.start:xx1 - xs.start]
    return m


def relief(m, z, y):
    res = 40075016.686 * math.cos(math.radians(tile_lat(y, z))) / 2 ** z / 256
    ex = UEBERH.get(z, 1.2)
    p = m * ex
    # Horn-Verfahren (wie gdaldem)
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, f = p[1:-1, :-2], p[1:-1, 2:]
    g, hh, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res)
    dzdy = ((g + 2 * hh + i) - (a + 2 * b + c)) / (8 * res)
    hang = np.arctan(np.hypot(dzdx, dzdy))
    aspekt = np.arctan2(dzdy, -dzdx)
    s = np.zeros_like(hang)
    for az, w in LICHT:
        azr = math.radians(360 - az + 90)
        s += w * (math.sin(ALT) * np.cos(hang) + math.cos(ALT) * np.sin(hang) * np.cos(azr - aspekt))
    s = np.clip(s, 0, 1)
    # steile Hänge (echte Neigung ohne Überhöhung) etwas dunkler – Fels und Rinnen bekommen Struktur
    q = m
    dzdx0 = ((q[:-2, 2:] + 2 * q[1:-1, 2:] + q[2:, 2:]) - (q[:-2, :-2] + 2 * q[1:-1, :-2] + q[2:, :-2])) / (8 * res)
    dzdy0 = ((q[2:, :-2] + 2 * q[2:, 1:-1] + q[2:, 2:]) - (q[:-2, :-2] + 2 * q[:-2, 1:-1] + q[:-2, 2:])) / (8 * res)
    grad = np.degrees(np.arctan(np.hypot(dzdx0, dzdy0)))
    s = s * (1 - np.clip((grad - 25) / 40, 0, 1) * 0.30)
    eben = NEUTRAL                                  # etwas heller als ebenes Gelände: auch helle Felsflächen zeigen Rinnen
    dunkel = np.clip((eben - s) / eben, 0, 1) ** 0.9 * DUNKEL
    hell = np.clip((s - eben) / (1 - eben), 0, 1) * HELL
    out = np.zeros((256, 256, 4), np.uint8)
    ist_d = dunkel >= hell
    out[..., 0] = np.where(ist_d, FARBE_DUNKEL[0], 255)
    out[..., 1] = np.where(ist_d, FARBE_DUNKEL[1], 255)
    out[..., 2] = np.where(ist_d, FARBE_DUNKEL[2], 252)
    out[..., 3] = (np.where(ist_d, dunkel, hell) * 255).astype(np.uint8)
    return out


def rechne(aus, zooms, rechtecke, url):
    h = Hoehe(url); n_ok = 0; t0 = time.time()
    for z in zooms:
        kacheln = set()
        for w, s_, e, n in rechtecke:
            for x in range(lon2x(w, z), lon2x(e, z) + 1):
                for y in range(lat2y(n, z), lat2y(s_, z) + 1): kacheln.add((x, y))
        reihen = {}
        for x, y in kacheln: reihen.setdefault(y, []).append(x)
        print(f"Zoom {z}: {len(kacheln)} Kacheln", flush=True)
        with ThreadPoolExecutor(32) as pool:
            for y in sorted(reihen):
                xs = sorted(reihen[y])
                # Höhen der Reihe und der Nachbarreihen parallel holen
                list(pool.map(lambda a: h.lade(z, a[0], a[1]), [(x + dx, y + dy) for x in xs for dx in (-1, 0, 1) for dy in (-1, 0, 1)]))
                def eine(x):
                    m = mosaik(h, z, x, y)
                    if m is None: return 0
                    spanne = float(m.max() - m.min())
                    if spanne < 3: return 0                         # flach (See, Ebene): keine Kachel nötig
                    if z >= 15 and spanne < 200: return 0           # Zoom 15 nur im Gebirge
                    img = Image.fromarray(relief(m, z, y), "RGBA")
                    os.makedirs(f"{aus}/{z}/{x}", exist_ok=True)
                    img.save(f"{aus}/{z}/{x}/{y}.webp", "WEBP", quality=QUALITAET, method=4)
                    return 1
                n_ok += sum(pool.map(eine, xs))
                h.vergiss(z, y - 1)
        print(f"  fertig bis Zoom {z}: {n_ok} Bilder, {time.time() - t0:.0f} s", flush=True)
    return n_ok


def packen(aus, ziel):
    from pmtiles.writer import Writer
    from pmtiles.tile import zxy_to_tileid, TileType, Compression
    liste = []
    for z in os.listdir(aus):
        if not z.isdigit(): continue
        for x in os.listdir(f"{aus}/{z}"):
            for f in os.listdir(f"{aus}/{z}/{x}"):
                y = int(f.split(".")[0]); liste.append((zxy_to_tileid(int(z), int(x), y), int(z), int(x), y))
    liste.sort()
    zs = [t[1] for t in liste]
    with open(ziel, "wb") as fo:
        wr = Writer(fo)
        for tid, z, x, y in liste:
            with open(f"{aus}/{z}/{x}/{y}.webp", "rb") as fi: wr.write_tile(tid, fi.read())
        wr.finalize({"tile_type": TileType.WEBP, "tile_compression": Compression.NONE, "min_zoom": min(zs), "max_zoom": max(zs),
                     "min_lon_e7": int(-180e7), "min_lat_e7": int(-85e7), "max_lon_e7": int(180e7), "max_lat_e7": int(85e7),
                     "center_zoom": 12, "center_lon_e7": int(10e7), "center_lat_e7": int(47e7)},
                    {"name": "gipfelbuch-relief", "format": "webp", "attribution": "Gelände: Mapzen/AWS Terrain Tiles"})
    print(f"{len(liste)} Kacheln in {ziel}")


if __name__ == "__main__":
    if sys.argv[1] == "--pmtiles":
        packen(sys.argv[2], sys.argv[3])
    else:
        aus, zooms, re_ = sys.argv[1], [int(v) for v in sys.argv[2].split(",")], sys.argv[3]
        url = sys.argv[4] if len(sys.argv) > 4 else DEM
        rechtecke = [tuple(map(float, r.split(","))) for r in re_.split(";")]
        print(rechne(aus, zooms, rechtecke, url))
