#!/usr/bin/env python3
"""Reliefbilder aus dem 1-m-Laserscan-Geländemodell des BEV (v1997) – so fein wie bei bergfex.

Quelle: BEV „ALS DTM Höhenraster 1m“ (ganz Österreich, CC BY 4.0, 55 Kacheln zu 50 km in EPSG:3035, als COG
mit Übersichten). Gelesen wird die 2-m-Übersicht direkt über das Netz (nur die nötigen Ausschnitte).
Außerhalb Österreichs (Grenzstreifen) wird mit den AWS Terrain Tiles aufgefüllt.

Arbeitet in Blöcken = Kacheln von Zoom 12. Je Block: Höhen in Kartenprojektion für Zoom 15 (512 px je Kachel,
also ca. 2,4 m je Bildpunkt wie bei bergfex), daraus durch Mitteln Zoom 14, 13, 12. Ausgabe 512-px-WebP mit
Durchsichtigkeit (Schatten dunkel, Lichtseiten leicht weiß), in der Karte als tileSize 256 eingebunden.

Stil wie bergfex: ein Hauptlicht aus Nordwest (315°, 45° hoch) – Südost-Hänge breit grau, Nordhänge hell mit
dunklen Rinnen; steile Hänge etwas dunkler.

Aufruf:  python3 relief1m.py <ausgabeordner> <w,s,e,n;…> [parallel]
         schreibt zusätzlich <ausgabeordner>/bloecke.json (Liste „x_y“ der Zoom-12-Blöcke mit BEV-Daten)
"""
import sys, os, io, math, json, time, urllib.request
from concurrent.futures import ProcessPoolExecutor
import numpy as np
from PIL import Image

BEV = "https://data.bev.gv.at/download/ALS/DTM/20240915/ALS_DTM_CRS3035RES50000mN{n}E{e}.tif"
AWS = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
R = 6378137 * math.pi
RAND = 8                      # Rand in Zoom-15-Bildpunkten (reicht für 3 x Mitteln + 1 px)
UEBERH = {12: 1.6, 13: 1.35, 14: 1.15, 15: 1.0}
NEUTRAL, DUNKEL, HELL = 0.70, 0.76, 0.50
SCHATTEN = (40, 38, 44)
QUALITAET = 70

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "3")
os.environ.setdefault("GDAL_CACHEMAX", "1024")
if os.path.exists("/root/.ccr/ca-bundle.crt"): os.environ.setdefault("CURL_CA_BUNDLE", "/root/.ccr/ca-bundle.crt")


def lon2x(lon, z): return int((lon + 180) / 360 * 2 ** z)
def lat2y(lat, z):
    r = math.radians(lat); return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z)
def tbounds(z, x, y):
    s = 2 * R / 2 ** z; return (-R + x * s, R - (y + 1) * s, -R + (x + 1) * s, R - y * s)
def tile_lat(y, z):
    n = math.pi - 2 * math.pi * (y + .5) / 2 ** z; return math.degrees(math.atan(math.sinh(n)))


_vorhanden = {}
def bev_dateien(w, s, e, n):
    """BEV-Kacheln (EPSG:3035), die das Rechteck (EPSG:3857) berühren."""
    from rasterio.warp import transform_bounds
    W, S, E, N = transform_bounds("EPSG:3857", "EPSG:3035", w, s, e, n, densify_pts=21)
    out = []
    for ee in range(int(W // 50000) * 50000, int(E // 50000) * 50000 + 1, 50000):
        for nn in range(int(S // 50000) * 50000, int(N // 50000) * 50000 + 1, 50000):
            u = BEV.format(n=nn, e=ee)
            if u not in _vorhanden:
                try:
                    req = urllib.request.Request(u, method="HEAD"); urllib.request.urlopen(req, timeout=30); _vorhanden[u] = True
                except Exception:
                    _vorhanden[u] = False
            if _vorhanden[u]: out.append(u)
    return out


def aws_fuellen(arr, z15, x15, y15, px):
    """NaN-Stellen mit AWS-Höhen (Zoom 14, bilinear) auffüllen."""
    if not np.isnan(arr).any(): return arr
    zz = 14; f = 2 ** (15 - zz)
    # Bereich in Zoom-14-Bildpunkten
    m = RAND / 512 * f  # Rand in z14-Kacheln … einfacher: über Weltkoordinaten
    w, s, e, n = tbounds(z15, x15, y15)
    res = (e - w) / px
    W, N = w - RAND * res, n + RAND * res
    size = arr.shape[0]
    s14 = 2 * R / 2 ** zz / 256
    gx0 = (W + R) / s14; gy0 = (R - N) / s14
    gx1 = gx0 + size * res / s14; gy1 = gy0 + size * res / s14
    tx0, ty0, tx1, ty1 = int(gx0 // 256), int(gy0 // 256), int(gx1 // 256), int(gy1 // 256)
    mos = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            for v in range(4):
                try:
                    with urllib.request.urlopen(AWS.format(z=zz, x=tx, y=ty), timeout=30) as r: b = r.read()
                    im = np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), dtype=np.float32)
                    mos[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = im[:, :, 0] * 256 + im[:, :, 1] + im[:, :, 2] / 256 - 32768
                    break
                except Exception:
                    time.sleep(1 + v)
    box = (gx0 - tx0 * 256, gy0 - ty0 * 256, gx1 - tx0 * 256, gy1 - ty0 * 256)
    up = np.asarray(Image.fromarray(mos, "F").resize((size, size), Image.BILINEAR, box=box), np.float32)
    return np.where(np.isnan(arr), up, arr)


def hoehen_block(x12, y12):
    """Höhen für den Zoom-12-Block in Zoom-15-Auflösung (4096 px + Rand), NaN wo keine BEV-Daten."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_origin
    px = 4096
    w, s, e, n = tbounds(12, x12, y12)
    res = (e - w) / px
    size = px + 2 * RAND
    dst = np.full((size, size), np.nan, np.float32)
    tr = from_origin(w - RAND * res, n + RAND * res, res, res)
    dateien = bev_dateien(w - RAND * res, s - RAND * res, e + RAND * res, n + RAND * res)
    for u in dateien:
        teil = np.full((size, size), np.nan, np.float32)
        for v in range(3):
            try:
                with rasterio.open("/vsicurl/" + u, overview_level=0) as src:
                    reproject(rasterio.band(src, 1), teil, src_nodata=src.nodata, dst_transform=tr, dst_crs="EPSG:3857",
                              dst_nodata=np.nan, resampling=Resampling.bilinear, init_dest_nodata=True, num_threads=2)
                break
            except Exception as ex:
                print("  BEV-Fehler", u, ex, flush=True); time.sleep(5 * (v + 1))
        dst = np.where(np.isnan(dst), teil, dst)
    dst[(dst < -500) | (dst > 5000)] = np.nan
    return dst, len(dateien)


def mitteln(a):
    h, w = a.shape; return a.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))


def schatten(m, z, y_lat):
    res = 2 * R / 2 ** z / 512 * math.cos(math.radians(y_lat))
    p = m * UEBERH[z]
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, f = p[1:-1, :-2], p[1:-1, 2:]
    g, h, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res)
    dzdy = ((g + 2 * h + i) - (a + 2 * b + c)) / (8 * res)
    hang = np.arctan(np.hypot(dzdx, dzdy)); asp = np.arctan2(dzdy, -dzdx)
    A = math.radians(45); azr = math.radians(360 - 315 + 90)
    s = np.clip(math.sin(A) * np.cos(hang) + math.cos(A) * np.sin(hang) * np.cos(azr - asp), 0, 1)
    q = m
    gx = ((q[:-2, 2:] + 2 * q[1:-1, 2:] + q[2:, 2:]) - (q[:-2, :-2] + 2 * q[1:-1, :-2] + q[2:, :-2])) / (8 * res)
    gy = ((q[2:, :-2] + 2 * q[2:, 1:-1] + q[2:, 2:]) - (q[:-2, :-2] + 2 * q[:-2, 1:-1] + q[:-2, 2:])) / (8 * res)
    grad = np.degrees(np.arctan(np.hypot(gx, gy)))
    s = s * (1 - np.clip((grad - 30) / 45, 0, 1) * 0.25)
    dunkel = np.clip((NEUTRAL - s) / NEUTRAL, 0, 1) ** 0.85 * DUNKEL
    hell = np.clip((s - NEUTRAL) / (1 - NEUTRAL), 0, 1) * HELL
    out = np.zeros(dunkel.shape + (4,), np.uint8)
    ist_d = dunkel >= hell
    for k in range(3): out[..., k] = np.where(ist_d, SCHATTEN[k], 255)
    out[..., 3] = (np.where(ist_d, dunkel, hell) * 255).astype(np.uint8)
    return out


def block(args):
    aus, x12, y12 = args
    t0 = time.time()
    m15, nd = hoehen_block(x12, y12)
    if nd == 0 or np.isnan(m15).all(): return None
    anteil = float(np.isnan(m15).mean())
    m15 = aws_fuellen(m15, 12, x12, y12, 4096)
    m15 = np.nan_to_num(m15, nan=float(np.nanmean(m15)))
    stufen = {15: m15}
    a = m15
    for z in (14, 13, 12):
        a = mitteln(a); stufen[z] = a
    n = 0
    lat = tile_lat(y12, 12)
    for z, arr in stufen.items():
        k = 2 ** (z - 12); r = RAND // (2 ** (15 - z))      # Rand in Bildpunkten dieser Stufe
        inner = arr[r - 1: r - 1 + k * 512 + 2, r - 1: r - 1 + k * 512 + 2]
        img = schatten(inner, z, lat)
        for j in range(k):
            for i in range(k):
                t = img[j * 512:(j + 1) * 512, i * 512:(i + 1) * 512]
                if t[..., 3].max() < 8: continue
                x, y = x12 * k + i, y12 * k + j
                os.makedirs(f"{aus}/{z}/{x}", exist_ok=True)
                Image.fromarray(t, "RGBA").save(f"{aus}/{z}/{x}/{y}.webp", "WEBP", quality=QUALITAET, alpha_quality=60, method=4)
                n += 1
    print(f"Block {x12}_{y12}: {n} Bilder, ohne BEV {anteil:.0%}, {time.time() - t0:.0f} s", flush=True)
    return f"{x12}_{y12}"


def main():
    aus, re_ = sys.argv[1], sys.argv[2]
    par = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    bl = set()
    for r in re_.split(";"):
        w, s, e, n = map(float, r.split(","))
        for x in range(lon2x(w, 12), lon2x(e, 12) + 1):
            for y in range(lat2y(n, 12), lat2y(s, 12) + 1): bl.add((x, y))
    print(len(bl), "Blöcke", flush=True)
    os.makedirs(aus, exist_ok=True)
    fertig = []
    with ProcessPoolExecutor(par) as pool:
        for r in pool.map(block, [(aus, x, y) for x, y in sorted(bl)]):
            if r: fertig.append(r)
    with open(f"{aus}/bloecke.json", "w") as f: json.dump(sorted(fertig), f)
    print(len(fertig), "Blöcke mit BEV-Daten")


if __name__ == "__main__":
    main()
