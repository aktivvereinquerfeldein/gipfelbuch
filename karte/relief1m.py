#!/usr/bin/env python3
"""Reliefbilder aus dem 1-m-Laserscan-Geländemodell des BEV (v1997) – so fein wie bei bergfex.

Quelle: BEV „ALS DTM Höhenraster 1m“ (ganz Österreich, CC BY 4.0, 55 Kacheln zu 50 km in EPSG:3035, als COG
mit Übersichten). Gelesen wird die 2-m-Übersicht direkt über das Netz (nur die nötigen Ausschnitte).
Außerhalb Österreichs (Grenzstreifen) wird mit den AWS Terrain Tiles aufgefüllt.

Arbeitet in Blöcken = Kacheln von Zoom 12. Je Block: Höhen in Kartenprojektion für Zoom 16 (512 px je Kachel,
also ca. 1,2 m je Bildpunkt, v2034), daraus durch Mitteln Zoom 15, 14, 13, 12. Ausgabe 512-px-WebP mit
Durchsichtigkeit (nur Schatten, seit v1998), in der Karte als tileSize 256 eingebunden.

Stil wie bergfex: ein Hauptlicht aus Nordwest (315°, 45° hoch) – Südost-Hänge breit grau, Nordhänge hell mit
dunklen Rinnen; steile Hänge etwas dunkler.

Aufruf:  python3 relief1m.py <ausgabeordner> <w,s,e,n;…> [parallel] [teil/anzahl]
         teil/anzahl (v2035): nur jede anzahl-te Spalte von Zoom-12-Blöcken ab Spalte teil – so kann ganz Österreich
         auf mehrere Läufe gleichzeitig verteilt werden (je Teil eine eigene PMTiles-Datei)
         schreibt zusätzlich <ausgabeordner>/bloecke.json (Liste „x_y“ der Zoom-12-Blöcke mit BEV-Daten)
"""
import sys, os, io, math, json, time, urllib.request
from concurrent.futures import ProcessPoolExecutor
import numpy as np
from PIL import Image

BEV = "https://data.bev.gv.at/download/ALS/DTM/20240915/ALS_DTM_CRS3035RES50000mN{n}E{e}.tif"
AWS = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
R = 6378137 * math.pi
BASIS = 16                    # v2034: feinste Stufe Zoom 16 aus dem vollen 1-m-Modell (vorher Zoom 15 aus der 2-m-Übersicht) – so fein wie bergfex
RAND = 16                     # Rand in Bildpunkten der feinsten Stufe (reicht für 4 x Mitteln + 1 px)
SCHARF = {16: .8, 15: .8, 14: .5, 13: .3, 12: 0}    # v2034: Kanten nachschärfen (Unscharfmaske auf den Schatten, mittlere Helligkeit bleibt gleich)
ALPHA_Q = {16: 65, 15: 65}    # sonst 50
UEBERH = {12: 1.3, 13: 1.18, 14: 1.08, 15: 1.0, 16: 1.0}     # v1998: weniger Überhöhung bei weitem Zoom (war zu dunkel)
NEUTRAL, DUNKEL, HELL = 0.70, 0.80, 0.0            # v1998: nur Schatten; v2017: DUNKEL 0,8 (mehrere Lichter machen weicher)
LICHT = [(315, .55), (270, .2), (0, .15), (225, .1)]   # v2017
HANG = 0.32                                         # v2017: Grundton steiler Hänge
SCHATTEN = (40, 38, 44)
QUALITAET = 70
# v2030: Außerhalb der BEV-Daten (Grenzstreifen, mit AWS aufgefüllt) sahen die Bilder anders aus als die gröberen
# Reliefbilder daneben (/karte/relief) – an der Blockgrenze entstand eine gerade Kante (z. B. Himmelschrofen/Oberstdorf).
# Jetzt werden dort die gröberen Bilder selbst eingesetzt und zum BEV-Gebiet hin weich überblendet (UEBERGANG Bildpunkte).
GROB = "https://gipfelbuch.quer-feld-ein.workers.dev/karte/relief/{z}/{x}/{y}.webp?b=2&roh=1"
GROB_MAXZOOM = 14            # wie in der Karte (source relief maxzoom 14)
UEBERGANG = 24

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
    px = 512 * 2 ** (BASIS - 12)
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
                with rasterio.open("/vsicurl/" + u) as src:      # volle Auflösung (1 m)
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
    # v2017: Licht aus mehreren Richtungen wie bergfex (Hauptlicht Nordwest, dazu West, Nord, Südwest) und weicher Verlauf –
    # vorher nur ein Licht mit harter Schwelle: Hänge kippten je nach Ausrichtung schlagartig von Grau auf Weiß („abgeschnitten“)
    A = math.radians(45); s = np.zeros_like(hang)
    for az, w in LICHT:
        azr = math.radians(360 - az + 90)
        s += w * np.clip(math.sin(A) * np.cos(hang) + math.cos(A) * np.sin(hang) * np.cos(azr - asp), 0, 1)
    eben = math.sin(A)
    dunkel = np.clip((eben - s) / eben, 0, 1) ** 1.1 * DUNKEL          # Schatten nach Ausrichtung, ohne Knick
    dunkel = np.maximum(dunkel, np.clip(np.sin(hang) - .25, 0, 1) * HANG)  # jeder steile Hang etwas grau (weicher Grundton)
    out = np.zeros(dunkel.shape + (4,), np.uint8)
    for k in range(3): out[..., k] = SCHATTEN[k]             # gleiche Farbe überall: Farbkanäle kosten fast nichts
    out[..., 3] = (dunkel * 255).astype(np.uint8)
    return out


def weich(a, r):
    """Kastenfilter (zweimal) mit Radius r – weicher Übergang der BEV-Maske."""
    for _ in range(2):
        for ax in (0, 1):
            pad = [(0, 0), (0, 0)]; pad[ax] = (r + 1, r)
            c = np.cumsum(np.pad(a, pad, mode="edge"), axis=ax, dtype=np.float64)
            a = ((c[2 * r + 1:] - c[:-2 * r - 1]) if ax == 0 else (c[:, 2 * r + 1:] - c[:, :-2 * r - 1])) / (2 * r + 1)
    return a.astype(np.float32)


_grob = {}
def grob_kachel(z, x, y):
    """Gröberes Reliefbild so, wie die Karte es bei Zoom z zeigt (über Zoom 14 vergrößert), als 512-px-RGBA."""
    zc = min(z, GROB_MAXZOOM); k = 2 ** (z - zc); xc, yc = x // k, y // k
    key = (zc, xc, yc)
    if key not in _grob:
        im = None
        for v in range(4):
            try:
                req = urllib.request.Request(GROB.format(z=zc, x=xc, y=yc), headers={"User-Agent": "gipfelbuch-relief1m"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    b = r.read(); im = Image.open(io.BytesIO(b)).convert("RGBA") if r.status == 200 and b else None
                break
            except Exception:
                time.sleep(1 + v)
        _grob[key] = im
    im = _grob[key]
    if im is None: return np.zeros((512, 512, 4), np.uint8)
    w = im.width / k; box = ((x - xc * k) * w, (y - yc * k) * w, (x - xc * k + 1) * w, (y - yc * k + 1) * w)
    return np.asarray(im.resize((512, 512), Image.BILINEAR, box=box), np.uint8)


def mischen(fein, grob, m):
    """fein dort, wo m = 1 (BEV), grob wo m = 0; Mischung über die Deckkraft (vormultipliziert)."""
    m = m[..., None]; af = fein[..., 3:4] / 255.0; ag = grob[..., 3:4] / 255.0
    a = af * m + ag * (1 - m)
    rgb = (fein[..., :3] * af * m + grob[..., :3] * ag * (1 - m)) / np.maximum(a, 1e-6)
    out = np.zeros_like(fein); out[..., :3] = np.clip(rgb, 0, 255); out[..., 3:4] = np.clip(a * 255 + .5, 0, 255)
    return out


def block(args):
    aus, x12, y12 = args
    t0 = time.time()
    m15, nd = hoehen_block(x12, y12)
    if nd == 0 or np.isnan(m15).all(): return None
    anteil = float(np.isnan(m15).mean())
    bev = (~np.isnan(m15)).astype(np.float32)          # v2030: 1 = BEV-Daten, 0 = aufgefüllt
    m15 = aws_fuellen(m15, 12, x12, y12, 512 * 2 ** (BASIS - 12))
    m15 = np.nan_to_num(m15, nan=float(np.nanmean(m15)))
    stufen = {BASIS: m15}; masken = {BASIS: bev}
    a = m15; b = bev
    for z in range(BASIS - 1, 11, -1):
        a = mitteln(a); stufen[z] = a; b = mitteln(b); masken[z] = b
    n = 0
    lat = tile_lat(y12, 12)
    for z, arr in stufen.items():
        k = 2 ** (z - 12); r = RAND // (2 ** (BASIS - z))      # Rand in Bildpunkten dieser Stufe
        # in Streifen von 4 Kachelreihen rechnen (Zoom 16 sonst mehrere GB Zwischenspeicher)
        img = np.zeros((k * 512, k * 512, 4), np.uint8)
        for j0 in range(0, k, 4):
            j1 = min(k, j0 + 4)
            img[j0 * 512:j1 * 512] = schatten(arr[r - 1 + j0 * 512: r - 1 + j1 * 512 + 2, r - 1: r - 1 + k * 512 + 2], z, lat)
        sf = SCHARF.get(z, 0)
        if sf:
            al = img[..., 3].astype(np.float32); img[..., 3] = np.clip(al + sf * (al - weich(al, 3 if z >= 16 else 2)), 0, 255).astype(np.uint8); del al
        mk = None
        if anteil > 0:   # v2030: Übergang Maske – an der BEV-Grenze 0, nach UEBERGANG Bildpunkten ins BEV-Gebiet hinein 1
            mm = masken[z][r: r + k * 512, r: r + k * 512]
            ue = max(4, int(UEBERGANG * 2.0 ** (z - 15)))       # v2033: Übergang in Metern gleich breit (ca. 60 m) – bei Zoom 12 waren es sonst 450 m, Berge an der Grenze wurden weich
            mk = np.clip(weich(mm, ue // 2) * 2 - 1, 0, 1)
        for j in range(k):
            for i in range(k):
                t = img[j * 512:(j + 1) * 512, i * 512:(i + 1) * 512]
                if mk is not None:
                    tm = mk[j * 512:(j + 1) * 512, i * 512:(i + 1) * 512]
                    if tm.min() < 1: t = mischen(t, grob_kachel(z, x12 * k + i, y12 * k + j), tm)
                if t[..., 3].max() < 8: continue
                x, y = x12 * k + i, y12 * k + j
                os.makedirs(f"{aus}/{z}/{x}", exist_ok=True)
                Image.fromarray(t, "RGBA").save(f"{aus}/{z}/{x}/{y}.webp", "WEBP", quality=QUALITAET, alpha_quality=ALPHA_Q.get(z, 50), method=4)
                n += 1
    print(f"Block {x12}_{y12}: {n} Bilder, ohne BEV {anteil:.0%}, {time.time() - t0:.0f} s", flush=True)
    return f"{x12}_{y12}"


def main():
    aus, re_ = sys.argv[1], sys.argv[2]
    par = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    teil, anzahl = (map(int, sys.argv[4].split("/")) if len(sys.argv) > 4 and sys.argv[4] else (0, 1))
    bl = set()
    for r in re_.split(";"):
        w, s, e, n = map(float, r.split(","))
        for x in range(lon2x(w, 12), lon2x(e, 12) + 1):
            if x % anzahl != teil: continue
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
