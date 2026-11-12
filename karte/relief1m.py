#!/usr/bin/env python3
"""Reliefbilder aus dem 1-m-Laserscan-Geländemodell des BEV (v1997) – so fein wie bei bergfex.
v2036: dazu Schweiz (swissALTI3D), Bayern (DGM1) und Südtirol (DTM 0,5 m), alle frei und auf 1 m gelesen.

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
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
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


# v2036: weitere freie Laserscan-Modelle (DACH-Alpenraum), alle auf 1 m gelesen. Reihenfolge: BEV zuerst, dann füllen
# die anderen nur Stellen ohne Daten. Je Quelle: (Name, Gebiet w,s,e,n in Grad, Funktion -> (Mosaik, Transform, CRS) oder None)
LADER = 8                     # v2036: Kacheln gleichzeitig laden (Schweiz, Bayern)
CH_STAC = "https://data.geo.admin.ch/api/stac/v0.9/search"
BY_URL = "https://download1.bayernwolke.de/a/dgm/dgm1/{e}_{n}.tif"
BZ_WCS = ("https://geoservices9.civis.bz.it/geoserver/ows?service=WCS&version=2.0.1&request=GetCoverage"
          "&coverageId=p_bz-Elevation__DigitalTerrainModel-0.5m&format=image/tiff&subset=E({w},{e})&subset=N({s},{n})"
          "&scaleFactor=0.5&geotiff:compression=Deflate&geotiff:tiling=true")


def _mosaik(W, S, E, N):
    """Leeres 1-m-Raster (NaN) auf ganze Kilometer gerundet; gibt Raster, Transform und Ursprung zurück."""
    from rasterio.transform import from_origin
    W, S = math.floor(W / 1000) * 1000, math.floor(S / 1000) * 1000
    E, N = math.ceil(E / 1000) * 1000, math.ceil(N / 1000) * 1000
    return np.full((int(N - S), int(E - W)), np.nan, np.float32), from_origin(W, N, 1, 1), W, N


def _einsetzen(mos, W0, N0, arr, left, top, nodata):
    a = arr.astype(np.float32)
    if nodata is not None: a[a == nodata] = np.nan
    a[(a < -500) | (a > 5000)] = np.nan
    r0, c0 = int(round(N0 - top)), int(round(left - W0))
    h, w = a.shape
    rr0, cc0 = max(0, r0), max(0, c0); rr1, cc1 = min(mos.shape[0], r0 + h), min(mos.shape[1], c0 + w)
    if rr1 <= rr0 or cc1 <= cc0: return
    t = mos[rr0:rr1, cc0:cc1]; q = a[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0]
    mos[rr0:rr1, cc0:cc1] = np.where(np.isnan(t), q, t)


def _holen(u, timeout=60):
    for v in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "gipfelbuch-relief"}), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as ex:
            if ex.code == 404: return None
            time.sleep(2 + 3 * v)
        except Exception:
            time.sleep(2 + 3 * v)
    return None


def quelle_ch(w, s, e, n):
    """swissALTI3D (swisstopo, frei): 1-km-Kacheln 0,5 m als COG; gelesen wird die eingebaute 1-m-Stufe. Je Kachel der neueste Stand."""
    import rasterio
    from rasterio.warp import transform_bounds
    lw, ls, le, ln = transform_bounds("EPSG:3857", "EPSG:4326", w, s, e, n)
    neu = {}
    u = f"{CH_STAC}?collections=ch.swisstopo.swissalti3d&bbox={lw},{ls},{le},{ln}&limit=100"
    while u:
        b = _holen(u)
        if not b: break
        d = json.loads(b)
        for f in d.get("features", []):
            _, jahr, kach = f["id"].split("_")
            href = next((a["href"] for a in f["assets"].values() if a["href"].endswith("_0.5_2056_5728.tif")), None)
            if href and (kach not in neu or jahr > neu[kach][0]): neu[kach] = (jahr, href)
        u = next((l["href"] for l in d.get("links", []) if l.get("rel") == "next"), None)
    if not neu: return None
    W, S, E, N = transform_bounds("EPSG:3857", "EPSG:2056", w, s, e, n, densify_pts=21)
    mos, tr, W0, N0 = _mosaik(W, S, E, N)
    def lesen(href):
        for v in range(3):
            try:
                with rasterio.open("/vsicurl/" + href, overview_level=0) as src:      # 1-m-Stufe der 0,5-m-Datei
                    return src.read(1), src.bounds.left, src.bounds.top, src.nodata
            except Exception as ex:
                print("  CH-Fehler", href, ex, flush=True); time.sleep(3 * (v + 1))
    with ThreadPoolExecutor(LADER) as tp:
        for r in tp.map(lesen, [h for _, h in neu.values()]):
            if r: _einsetzen(mos, W0, N0, *r)
    return mos, tr, "EPSG:2056"


_by_fehlt = set()
def quelle_by(w, s, e, n):
    """Bayern DGM1 (LDBV, frei): 1-km-Kacheln 1 m in EPSG:25832, Name <E km>_<N km>.tif."""
    import rasterio
    from rasterio.warp import transform_bounds
    W, S, E, N = transform_bounds("EPSG:3857", "EPSG:25832", w, s, e, n, densify_pts=21)
    mos, tr, W0, N0 = _mosaik(W, S, E, N)
    def lesen(u):
        for v in range(3):
            try:
                with rasterio.open("/vsicurl/" + u) as src:
                    return src.read(1), src.bounds.left, src.bounds.top, src.nodata
            except Exception as ex:
                if "404" in str(ex) or "403" in str(ex) or "not recognized" in str(ex) or "No such file" in str(ex): _by_fehlt.add(u); return None
                print("  BY-Fehler", u, ex, flush=True); time.sleep(3 * (v + 1))
    urls = [BY_URL.format(e=ek, n=nk) for ek in range(int(W0 // 1000), int(math.ceil(E / 1000)))
            for nk in range(int(math.floor(S / 1000)), int(N0 // 1000))]
    da = False
    with ThreadPoolExecutor(LADER) as tp:
        for r in tp.map(lesen, [u for u in urls if u not in _by_fehlt]):
            if r: _einsetzen(mos, W0, N0, *r); da = True
    return (mos, tr, "EPSG:25832") if da else None


def quelle_bz(w, s, e, n):
    """Südtirol DTM 0,5 m (Provinz Bozen, CC0) über den WCS-Dienst, auf 1 m verkleinert, in Stücken zu 3 km."""
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.warp import transform_bounds
    W, S, E, N = transform_bounds("EPSG:3857", "EPSG:25832", w, s, e, n, densify_pts=21)
    mos, tr, W0, N0 = _mosaik(W, S, E, N)
    stuecke = []
    for x0 in range(int(W0), int(W0) + mos.shape[1], 3000):
        for y1 in range(int(N0), int(N0) - mos.shape[0], -3000):
            stuecke.append((x0, max(y1 - 3000, int(N0) - mos.shape[0]), min(x0 + 3000, int(W0) + mos.shape[1]), y1))
    def lesen(st, cov="DigitalTerrainModel-0.5m", sf="0.5"):
        x0, y0, x1, y1 = st
        b = _holen(BZ_WCS.replace("DigitalTerrainModel-0.5m", cov).replace("scaleFactor=0.5", "scaleFactor=" + sf).format(w=x0, e=x1, s=y0, n=y1), timeout=120)
        if not b or b[:4] not in (b"II*\x00", b"MM\x00*"): return None
        try:
            with MemoryFile(b) as mf, mf.open() as src:
                return src.read(1), src.bounds.left, src.bounds.top, src.nodata, src.res[0]
        except Exception as ex:
            print("  BZ-Fehler", st, ex, flush=True)
    da = False
    with ThreadPoolExecutor(4) as tp:      # der Dienst der Provinz – nicht zu viele gleichzeitig
        for r in tp.map(lesen, stuecke):
            if r and abs(r[4] - 1) < .01: _einsetzen(mos, W0, N0, *r[:4]); da = True
    # Das 0,5-m-Modell hat Lücken (z. B. große Teile der Dolomiten) – dort das vollständige 2,5-m-Modell einsetzen
    if np.isnan(mos).any():
        from rasterio.warp import reproject, Resampling
        r = lesen((int(W0) - 10, int(N0) - mos.shape[0] - 10, int(W0) + mos.shape[1] + 10, int(N0) + 10), "DigitalTerrainModel-2.5m", "1")
        if r:
            a = r[0].astype(np.float32); a[(a == r[3]) | (a < -500) | (a > 5000)] = np.nan
            if not np.isnan(a).all():
                from rasterio.transform import from_origin
                grob = np.full(mos.shape, np.nan, np.float32)
                reproject(a, grob, src_transform=from_origin(r[1], r[2], r[4], r[4]), src_crs="EPSG:25832", src_nodata=np.nan,
                          dst_transform=tr, dst_crs="EPSG:25832", dst_nodata=np.nan, resampling=Resampling.cubic, init_dest_nodata=True)
                # 2,5-m-Daten sind in Höhenstufen gespeichert – auf 1 m vergrößert gäbe das Treppen in flachen Hängen: leicht glätten
                ok = ~np.isnan(grob); grob = weich(np.where(ok, grob, np.nanmean(grob)), 2).astype(np.float32); grob[~ok] = np.nan
                mos = np.where(np.isnan(mos), grob, mos); da = True
    return (mos, tr, "EPSG:25832") if da else None


BW_URL = "https://opengeodata.lgl-bw.de/data/dgm/dgm1_32_{e}_{n}_2_bw.zip"
_bw_fehlt = set()


def bw_lesen(e2, n2, faktor=1):
    """v2043: Baden-Württemberg DGM1 (LGL, dl-de/by-2-0): ZIP je 2 × 2 km (Ostwert ungerade, Nordwert gerade, in km) mit vier
    XYZ-Textdateien je 1 km (Punkte an den Pixelmitten). Gibt [(Raster, links, oben)] zurück, bei faktor > 1 gemittelt."""
    import zipfile
    u = BW_URL.format(e=e2, n=n2)
    if u in _bw_fehlt: return []
    b = _holen(u, timeout=180)
    if not b: _bw_fehlt.add(u); return []
    out = []
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        for nm in z.namelist():
            if not nm.endswith(".xyz"): continue
            a = np.fromstring(z.read(nm).decode(), sep=" ", dtype=np.float64).reshape(-1, 3)
            if not len(a): continue
            x0 = math.floor(a[:, 0].min() / 1000) * 1000; y1 = math.ceil(a[:, 1].max() / 1000) * 1000
            g = np.full((1000, 1000), np.nan, np.float32)
            c = np.clip((a[:, 0] - x0).astype(np.int32), 0, 999); r = np.clip((y1 - a[:, 1]).astype(np.int32), 0, 999)
            g[r, c] = a[:, 2]
            if faktor > 1:
                with np.errstate(all="ignore"): g = np.nanmean(g.reshape(1000 // faktor, faktor, 1000 // faktor, faktor), axis=(1, 3)).astype(np.float32)
            out.append((g, x0, y1))
    return out


def bw_zips(W, S, E, N):
    e0 = int(math.floor((W / 1000 - 1) / 2)) * 2 + 1; n0 = int(math.floor(S / 2000)) * 2
    return [(e, n) for e in range(e0, int(math.ceil(E / 1000)) + 1, 2) for n in range(n0, int(math.ceil(N / 1000)) + 1, 2)]


def quelle_bw(w, s, e, n):
    from rasterio.warp import transform_bounds
    W, S, E, N = transform_bounds("EPSG:3857", "EPSG:25832", w, s, e, n, densify_pts=21)
    mos, tr, W0, N0 = _mosaik(W, S, E, N)
    da = False
    with ThreadPoolExecutor(4) as tp:       # Server des LGL schonen
        for teile in tp.map(lambda p: bw_lesen(*p), bw_zips(W, S, E, N)):
            for g, l, t in teile: _einsetzen(mos, W0, N0, g, l, t, None); da = True
    return (mos, tr, "EPSG:25832") if da else None


QUELLEN = [("CH", (5.9, 45.8, 10.6, 47.9), quelle_ch), ("BY", (8.9, 47.2, 13.9, 50.6), quelle_by), ("BZ", (10.3, 46.2, 12.5, 47.1), quelle_bz),
           ("BW", (7.4, 47.5, 10.6, 49.8), quelle_bw)]


def hoehen_block(x12, y12):
    """Höhen für den Zoom-12-Block in Zoom-16-Auflösung (8192 px + Rand), NaN wo keine Laserscan-Daten."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_origin
    px = 512 * 2 ** (BASIS - 12)
    w, s, e, n = tbounds(12, x12, y12)
    res = (e - w) / px
    size = px + 2 * RAND
    dst = np.full((size, size), np.nan, np.float32)
    tr = from_origin(w - RAND * res, n + RAND * res, res, res)
    bw, bs, be, bn = w - RAND * res, s - RAND * res, e + RAND * res, n + RAND * res
    dateien = bev_dateien(bw, bs, be, bn)
    nd = 0
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
    if not np.isnan(dst).all(): nd += 1
    # v2036: weitere Quellen nur, wo noch Lücken sind und der Block in ihrem Gebiet liegt
    lon0, lon1 = w / R * 180, e / R * 180; lat0, lat1 = tile_lat(y12 + .5, 12), tile_lat(y12 - .5, 12)
    for name, (gw, gs, ge, gn), f in QUELLEN:
        if not np.isnan(dst).any(): break
        if lon1 < gw or lon0 > ge or lat1 < gs or lat0 > gn: continue
        try:
            r = f(bw, bs, be, bn)
        except Exception as ex:
            print(f"  {name}-Fehler Block {x12}_{y12}:", ex, flush=True); r = None
        if r is None: continue
        mos, mtr, crs = r
        if np.isnan(mos).all(): continue
        teil = np.full((size, size), np.nan, np.float32)
        reproject(mos, teil, src_transform=mtr, src_crs=crs, src_nodata=np.nan, dst_transform=tr, dst_crs="EPSG:3857",
                  dst_nodata=np.nan, resampling=Resampling.bilinear, init_dest_nodata=True, num_threads=2)
        del mos
        if not np.isnan(teil).all(): nd += 1; print(f"  Block {x12}_{y12}: {name}", flush=True)
        dst = np.where(np.isnan(dst), teil, dst)
    return dst, nd


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
