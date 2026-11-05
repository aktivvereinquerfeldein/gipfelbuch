#!/usr/bin/env python3
"""Reliefbilder wie bergfex aus dem 1-m-Geländemodell Österreich (BEV ALS DTM, v1997).

Warum: Die feinen Rinnen und Strukturen im Fels, die bergfex zeigt, kommen aus einem Laserscan-Geländemodell
(1 m). Die AWS-Höhenkacheln sind dafür zu grob. Das BEV stellt Österreich frei als 1-m-Raster bereit
(Kacheln 50 x 50 km, EPSG:3035, Cloud-optimiertes GeoTIFF, Adresse
https://data.bev.gv.at/download/ALS/DTM/<Stichtag>/ALS_DTM_CRS3035RES50000mN<N>E<E>.tif). GDAL liest davon nur
die nötigen Ausschnitte (Übersichtsstufe passend zur Zielauflösung), es muss nichts ganz heruntergeladen werden.

Ablauf je Zoom-12-Kachel (Block, 2048 x 2048 Bildpunkte in Zoom-15-Auflösung, mit Rand):
  1. Höhen aus dem BEV-Raster in Web-Mercator (wie die Karte) umrechnen (gdalwarp, Mittelwert).
  2. Lücken (außerhalb Österreichs) mit den AWS-Höhenkacheln Zoom 15 füllen (gleiches Raster, passt genau).
  3. Zoom 14, 13, 12 durch Zusammenfassen von 2x2, 4x4, 8x8 Bildpunkten.
  4. Schattierung wie bergfex: Hauptlicht von Nordwest, grauer Schatten, Lichtseiten durchsichtig; je Zoom zerlegen
     in 256-px-WebP-Kacheln (Zoom 15 nur im Gebirge, ab 200 m Höhenunterschied je Kachel).

Aufruf: python3 relief_bev.py <ausgabeordner> <Rechtecke w,s,e,n;…> [Prozesse] [--vorschau datei.png]
"""
import sys, os, io, math, json, time
from multiprocessing import Pool
import numpy as np
from PIL import Image
from osgeo import gdal, osr

gdal.UseExceptions()
for k, v in {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
             "GDAL_HTTP_MULTIRANGE": "YES", "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES", "VSI_CACHE": "TRUE",
             "VSI_CACHE_SIZE": "200000000", "GDAL_CACHEMAX": "512", "GDAL_HTTP_MAX_RETRY": "6",
             "GDAL_HTTP_RETRY_DELAY": "3", "GDAL_HTTP_USERAGENT": "gipfelbuch-relief"}.items():
    gdal.SetConfigOption(k, v)

BEV = os.environ.get("RELIEF_BEV", "https://data.bev.gv.at/download/ALS/DTM/20250915/ALS_DTM_CRS3035RES50000mN{n}E{e}.tif")
AWS = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/15/{x}/{y}.png"
WELT = 40075016.686
R15 = WELT / 2 ** 15 / 256           # Bildpunktgröße Zoom 15 in Web-Mercator-Metern
RAND = 16                            # Rand in Zoom-15-Bildpunkten (Zoom 12: 2)

# Aussehen (wie bergfex: grauer Schatten, Hauptlicht Nordwest)
LICHT = json.loads(os.environ.get("RELIEF_LICHT", "[[315, 0.82], [270, 0.10], [0, 0.08]]"))
ALT = math.radians(float(os.environ.get("RELIEF_ALT", 45)))
UEBERH = {12: 1.6, 13: 1.35, 14: 1.15, 15: 1.0}
DUNKEL = float(os.environ.get("RELIEF_DUNKEL", .62))
NEUTRAL = float(os.environ.get("RELIEF_NEUTRAL", .97))
GAMMA = float(os.environ.get("RELIEF_GAMMA", 1.0))
STEIL = float(os.environ.get("RELIEF_STEIL", .12))
FARBE = tuple(json.loads(os.environ.get("RELIEF_FARBE", "[38, 38, 44]")))
QUALITAET = int(os.environ.get("RELIEF_QUALITAET", 72))
DETAIL = float(os.environ.get("RELIEF_DETAIL", 1.4))    # Rinnen/Kanten hervorheben (wie bergfex auch auf Lichtseiten)
DETAIL_R = float(os.environ.get("RELIEF_DETAIL_R", 5))   # Glättungs-Radius in Bildpunkten


def lon2x(lon, z): return int((lon + 180) / 360 * 2 ** z)
def lat2y(lat, z):
    r = math.radians(lat); return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z)
def merc_x(x, z): return x / 2 ** z * WELT - WELT / 2
def merc_y(y, z): return WELT / 2 - y / 2 ** z * WELT
def tile_lat(y, z):
    n = math.pi - 2 * math.pi * (y + .5) / 2 ** z; return math.degrees(math.atan(math.sinh(n)))


def bev_kacheln(rechtecke):
    """Namen der BEV-Kacheln (50-km-Raster in EPSG:3035), die die Rechtecke berühren."""
    s4326 = osr.SpatialReference(); s4326.ImportFromEPSG(4326); s4326.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    s3035 = osr.SpatialReference(); s3035.ImportFromEPSG(3035); s3035.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(s4326, s3035)
    namen = set()
    for w, s, e, n in rechtecke:
        pts = [tr.TransformPoint(lo, la) for lo in np.linspace(w, e, 12) for la in np.linspace(s, n, 12)]
        E = [p[0] for p in pts]; N = [p[1] for p in pts]
        for ee in range(int(min(E) // 50000) * 50000, int(max(E)) + 1, 50000):
            for nn in range(int(min(N) // 50000) * 50000, int(max(N)) + 1, 50000):
                namen.add((nn, ee))
    da = []
    for nn, ee in sorted(namen):
        u = "/vsicurl/" + BEV.format(n=nn, e=ee)
        try:
            gdal.Open(u); da.append(u)
        except Exception:
            pass
    return da


def aws_mosaik(x0, y0, n, rand):
    """AWS-Höhen Zoom 15 für einen Bereich (Kachel x0,y0 bis +n) mit Rand in Bildpunkten."""
    import urllib.request
    gross = n * 256 + 2 * rand
    m = np.full((gross, gross), np.nan, np.float32)
    k0x, k0y = x0 - 1, y0 - 1
    def hole(tx, ty):
        for v in range(4):
            try:
                req = urllib.request.Request(AWS.format(x=tx, y=ty), headers={"User-Agent": "gipfelbuch-relief"})
                with urllib.request.urlopen(req, timeout=30) as r: b = r.read()
                a = np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), dtype=np.float32)
                return a[:, :, 0] * 256 + a[:, :, 1] + a[:, :, 2] / 256 - 32768
            except Exception as e:
                if getattr(e, "code", 0) == 404: return None
                time.sleep(1 + 2 * v)
        return None
    from concurrent.futures import ThreadPoolExecutor
    jobs = [(tx, ty) for tx in range(k0x, x0 + n + 1) for ty in range(k0y, y0 + n + 1)]
    with ThreadPoolExecutor(32) as p:
        for (tx, ty), a in zip(jobs, p.map(lambda j: hole(*j), jobs)):
            if a is None: continue
            ox = (tx - x0) * 256 + rand; oy = (ty - y0) * 256 + rand
            xs0, ys0 = max(ox, 0), max(oy, 0); xs1, ys1 = min(ox + 256, gross), min(oy + 256, gross)
            if xs1 <= xs0 or ys1 <= ys0: continue
            m[ys0:ys1, xs0:xs1] = a[ys0 - oy:ys1 - oy, xs0 - ox:xs1 - ox]
    return m


def box(a, r, achse):
    """Gleitender Mittelwert (Breite 2r+1) entlang einer Achse, Rand wiederholt."""
    r = int(r); pad = [(0, 0), (0, 0)]; pad[achse] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=achse, dtype=np.float64)
    if achse == 0: return ((c[2 * r + 1:] - c[:-2 * r - 1]) / (2 * r + 1)).astype(np.float32)
    return ((c[:, 2 * r + 1:] - c[:, :-2 * r - 1]) / (2 * r + 1)).astype(np.float32)


def weich(a, sigma):
    """Annähernd Gauß-Weichzeichnung (drei Kastenfilter), ohne zusätzliche Bibliothek."""
    r = max(1, int(round(sigma * 0.85)))
    for _ in range(3): a = box(box(a, r, 0), r, 1)
    return a


def schatten(p, res, z):
    """p: Höhen mit 1 Bildpunkt Rand; Ergebnis RGBA ohne Rand."""
    p = p * UEBERH.get(z, 1.0)
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, f = p[1:-1, :-2], p[1:-1, 2:]
    g, hh, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8 * res)
    dzdy = ((g + 2 * hh + i) - (a + 2 * b + c)) / (8 * res)
    hang = np.arctan(np.hypot(dzdx, dzdy)); aspekt = np.arctan2(dzdy, -dzdx)
    s = np.zeros_like(hang)
    for az, w in LICHT:
        azr = math.radians(360 - az + 90)
        s += w * (math.sin(ALT) * np.cos(hang) + math.cos(ALT) * np.sin(hang) * np.cos(azr - aspekt))
    # Feinstruktur: Unterschied zur geglätteten Schattierung verstärken – Rinnen werden auch auf Lichtseiten sichtbar
    if DETAIL > 0:
        s = s + DETAIL * (s - weich(s, DETAIL_R))
    s = np.clip(s, 0, 1)
    grad = np.degrees(hang) / max(UEBERH.get(z, 1.0), 1e-6)
    s = s * (1 - np.clip((grad - 25) / 40, 0, 1) * STEIL)
    dunkel = np.clip((NEUTRAL - s) / NEUTRAL, 0, 1) ** GAMMA * DUNKEL
    out = np.empty(dunkel.shape + (4,), np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = FARBE
    out[..., 3] = (dunkel * 255).astype(np.uint8)
    return out


QUELLEN = []


def block(arg):
    """Eine Zoom-12-Kachel mit allen Zoom-13/14/15-Kacheln darin."""
    x12, y12, aus = arg
    t0 = time.time()
    n = 8; x15, y15 = x12 * 8, y12 * 8          # eine Zoom-12-Kachel = 8 x 8 Zoom-15-Kacheln
    gross = n * 256 + 2 * RAND
    xmin = merc_x(x15, 15) - RAND * R15; ymax = merc_y(y15, 15) + RAND * R15
    xmax = xmin + gross * R15; ymin = ymax - gross * R15
    m = None
    if QUELLEN:
        try:
            ds = gdal.Warp("", QUELLEN, format="MEM", dstSRS="EPSG:3857", outputBounds=(xmin, ymin, xmax, ymax),
                           width=gross, height=gross, resampleAlg="average", srcNodata=-9999, dstNodata=-9999, outputType=gdal.GDT_Float32,
                           multithread=True)
            m = ds.GetRasterBand(1).ReadAsArray().astype(np.float32); ds = None
            m[m <= -9000] = np.nan
        except Exception as e:
            print("Warp-Fehler", x12, y12, e, flush=True); m = None
    if m is None or np.isnan(m).mean() > 0.0:
        frei = None if m is None else np.isnan(m)
        if m is None or frei.any():
            a = aws_mosaik(x15, y15, n, RAND)
            m = a if m is None else np.where(frei, a, m)
    if m is None or np.isnan(m).all(): return (x12, y12, 0, time.time() - t0)
    m = np.where(np.isnan(m), np.nanmean(m), m)
    zahl = 0
    stufen = {15: m}
    for z, f in ((14, 2), (13, 4), (12, 8)):
        stufen[z] = m.reshape(gross // f, f, gross // f, f).mean(axis=(1, 3))
    for z, a in stufen.items():
        f = 2 ** (15 - z); r = RAND // f; k = 256
        res = R15 * f * math.cos(math.radians(tile_lat(y12 * 2 ** (z - 12) + 2 ** (z - 12) / 2, z)))
        bild = schatten(a[r - 1:a.shape[0] - r + 1, r - 1:a.shape[1] - r + 1], res, z)
        anz = 2 ** (z - 12)
        for i in range(anz):
            for j in range(anz):
                hoeh = a[r + j * k:r + (j + 1) * k, r + i * k:r + (i + 1) * k]
                spanne = float(hoeh.max() - hoeh.min())
                if spanne < 3: continue
                if z == 15 and spanne < 200: continue
                x, y = x12 * anz + i, y12 * anz + j
                os.makedirs(f"{aus}/{z}/{x}", exist_ok=True)
                Image.fromarray(bild[j * k:(j + 1) * k, i * k:(i + 1) * k], "RGBA").save(
                    f"{aus}/{z}/{x}/{y}.webp", "WEBP", quality=QUALITAET, method=4)
                zahl += 1
    return (x12, y12, zahl, time.time() - t0)


def init(q):
    global QUELLEN; QUELLEN = q


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    aus, re_ = args[0], args[1]
    prozesse = int(args[2]) if len(args) > 2 else os.cpu_count()
    rechtecke = [tuple(map(float, r.split(","))) for r in re_.split(";")]
    quellen = bev_kacheln(rechtecke)
    print(f"BEV-Kacheln: {len(quellen)}", flush=True)
    # Die Kacheln direkt übergeben (keine VRT): so nutzt GDAL die eingebauten Übersichtsstufen der COG-Dateien
    # und liest nur grob aufgelöste Daten statt des vollen 1-m-Rasters.
    bloecke = sorted({(x, y) for w, s, e, n in rechtecke for x in range(lon2x(w, 12), lon2x(e, 12) + 1)
                      for y in range(lat2y(n, 12), lat2y(s, 12) + 1)})
    print(f"Blöcke (Zoom 12): {len(bloecke)}", flush=True)
    t0 = time.time(); summe = 0
    with Pool(prozesse, initializer=init, initargs=(quellen,)) as pool:
        for k, (x, y, zahl, dt) in enumerate(pool.imap_unordered(block, [(x, y, aus) for x, y in bloecke])):
            summe += zahl
            if k % 10 == 0 or k == len(bloecke) - 1:
                print(f"{k + 1}/{len(bloecke)} Blöcke, {summe} Bilder, {time.time() - t0:.0f} s (Block {x},{y}: {dt:.0f} s)", flush=True)
    if "--vorschau" in sys.argv:
        ziel = sys.argv[sys.argv.index("--vorschau") + 1]
        vorschau(aus, ziel)


def vorschau(aus, ziel, z=15):
    d = f"{aus}/{z}"
    xs = sorted(int(x) for x in os.listdir(d)); ys = sorted({int(f.split(".")[0]) for x in xs for f in os.listdir(f"{d}/{x}")})
    xs, ys = xs[:10], ys[:8]
    W = Image.new("RGBA", (256 * len(xs), 256 * len(ys)), (244, 242, 238, 255))
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            p = f"{d}/{x}/{y}.webp"
            if os.path.exists(p): W.alpha_composite(Image.open(p).convert("RGBA"), (256 * i, 256 * j))
    W.convert("RGB").save(ziel)


if __name__ == "__main__":
    main()
