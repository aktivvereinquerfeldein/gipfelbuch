"""Gipfelbuch – feine Höhenkacheln (Terrarium-PNG) für das Relief bei weitem Zoom (v2040).

Die öffentlichen AWS-Höhenkacheln sind nur in Österreich fein (10-m-Modell). In der Schweiz, Bayern und Südtirol
sind sie grob (ca. 30 m), darum wirkte das Relief bei Zoom 9 bis 11 dort verwaschen. Dieses Skript rechnet für
diese Gebiete eigene Höhenkacheln Zoom 12 aus den freien Laserscan-Modellen (dieselben Quellen wie relief1m.py,
aber grob gelesen):
  - Schweiz: swissALTI3D 2-m-Kacheln, eingebaute 4-m-Stufe
  - Bayern: DGM1 1-m-Kacheln (keine Stufen vorhanden), beim Lesen gemittelt
  - Südtirol: Geländemodell 2,5 m über den WCS-Dienst der Provinz, auf 5 m verkleinert geliefert
  - Baden-Württemberg: DGM1 (ZIP mit XYZ-Text je 1 km), beim Lesen auf 4 m gemittelt (v2043)
Wo keine Laserscan-Daten sind, kommt die AWS-Kachel (gleiche Stelle) hinein. Ausgabe <aus>/12/x/y.png im
Terrarium-Format (wie AWS), 256 px, dazu je vier Kacheln Zoom 13 (v2041). Zoom 11 entsteht danach mit
--zoom11 aus je 4 Zoom-12-Kacheln.

Aufruf:  python3 dem_fein.py <aus> <w,s,e,n;…> [teil/anzahl]
         python3 dem_fein.py --zoom11 <aus>
"""
import io, json, math, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from relief1m import tbounds, lon2x, lat2y, tile_lat, _holen, CH_STAC, BY_URL, BZ_WCS, R, bw_lesen, bw_zips

AWS = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
GEBIETE = [("CH", (5.9, 45.8, 10.6, 47.9)), ("BY", (8.9, 47.2, 13.9, 50.6)), ("BZ", (10.3, 46.2, 12.5, 47.1)), ("BW", (7.4, 47.5, 10.6, 49.8))]
_by_fehlt = set()


def terrarium_lesen(b):
    im = np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), dtype=np.float32)
    return im[:, :, 0] * 256 + im[:, :, 1] + im[:, :, 2] / 256 - 32768


def terrarium_schreiben(h, pfad):
    v = np.clip(h + 32768, 0, 65535.99)
    r = np.floor(v / 256); g = np.floor(v - r * 256); b = np.floor((v - r * 256 - g) * 256)
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    Image.fromarray(np.dstack([r, g, b]).astype(np.uint8), "RGB").save(pfad, optimize=True)


def raster(W, S, E, N, res):
    """Leeres Raster in der Quell-Projektion, Rand auf ganze km."""
    from rasterio.transform import from_origin
    W, S = math.floor(W / 1000) * 1000, math.floor(S / 1000) * 1000
    E, N = math.ceil(E / 1000) * 1000, math.ceil(N / 1000) * 1000
    return np.full((int((N - S) / res), int((E - W) / res)), np.nan, np.float32), from_origin(W, N, res, res), W, N


def einsetzen(mos, W0, N0, res, a, left, top, nodata):
    a = a.astype(np.float32)
    if nodata is not None: a[a == nodata] = np.nan
    a[(a < -500) | (a > 5000)] = np.nan
    r0, c0 = int(round((N0 - top) / res)), int(round((left - W0) / res))
    h, w = a.shape
    rr0, cc0 = max(0, r0), max(0, c0); rr1, cc1 = min(mos.shape[0], r0 + h), min(mos.shape[1], c0 + w)
    if rr1 <= rr0 or cc1 <= cc0: return
    t = mos[rr0:rr1, cc0:cc1]
    mos[rr0:rr1, cc0:cc1] = np.where(np.isnan(t), a[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0], t)


def ch(w, s, e, n):
    import rasterio
    from rasterio.warp import transform_bounds
    lw, ls, le, ln = transform_bounds("EPSG:3857", "EPSG:4326", w, s, e, n)
    neu = {}; u = f"{CH_STAC}?collections=ch.swisstopo.swissalti3d&bbox={lw},{ls},{le},{ln}&limit=100"
    while u:
        b = _holen(u)
        if not b: break
        d = json.loads(b)
        for f in d.get("features", []):
            _, jahr, kach = f["id"].split("_")
            href = next((a["href"] for a in f["assets"].values() if a["href"].endswith("_2_2056_5728.tif")), None)
            if href and (kach not in neu or jahr > neu[kach][0]): neu[kach] = (jahr, href)
        u = next((l["href"] for l in d.get("links", []) if l.get("rel") == "next"), None)
    if not neu: return None
    W, S, E, N = transform_bounds("EPSG:3857", "EPSG:2056", w, s, e, n, densify_pts=21)
    mos, tr, W0, N0 = raster(W, S, E, N, 4)
    def lesen(href):
        for v in range(3):
            try:
                with rasterio.open("/vsicurl/" + href, overview_level=0) as src:      # 4-m-Stufe der 2-m-Datei
                    return src.read(1), src.bounds.left, src.bounds.top, src.nodata
            except Exception as ex:
                print("  CH-Fehler", href, ex, flush=True); time.sleep(3 * (v + 1))
    with ThreadPoolExecutor(8) as tp:
        for r in tp.map(lesen, [h for _, h in neu.values()]):
            if r: einsetzen(mos, W0, N0, 4, *r)
    return mos, tr, "EPSG:2056"


def by(w, s, e, n):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import transform_bounds
    W, S, E, N = transform_bounds("EPSG:3857", "EPSG:25832", w, s, e, n, densify_pts=21)
    mos, tr, W0, N0 = raster(W, S, E, N, 4)
    def lesen(u):
        for v in range(3):
            try:
                with rasterio.open("/vsicurl/" + u) as src:      # 1 m, beim Lesen auf 4 m gemittelt
                    a = src.read(1, out_shape=(src.height // 4, src.width // 4), resampling=Resampling.average)
                    return a, src.bounds.left, src.bounds.top, src.nodata
            except Exception as ex:
                if any(k in str(ex) for k in ("404", "403", "not recognized", "No such file")): _by_fehlt.add(u); return None
                print("  BY-Fehler", u, ex, flush=True); time.sleep(3 * (v + 1))
    urls = [BY_URL.format(e=ek, n=nk) for ek in range(int(W0 // 1000), int(math.ceil(E / 1000)))
            for nk in range(int(math.floor(S / 1000)), int(N0 // 1000))]
    da = False
    with ThreadPoolExecutor(8) as tp:
        for r in tp.map(lesen, [u for u in urls if u not in _by_fehlt]):
            if r: einsetzen(mos, W0, N0, 4, *r); da = True
    return (mos, tr, "EPSG:25832") if da else None


def bz(w, s, e, n):
    from rasterio.io import MemoryFile
    from rasterio.warp import transform_bounds
    W, S, E, N = transform_bounds("EPSG:3857", "EPSG:25832", w, s, e, n, densify_pts=21)
    mos, tr, W0, N0 = raster(W, S, E, N, 5)
    u = (BZ_WCS.replace("DigitalTerrainModel-0.5m", "DigitalTerrainModel-2.5m").replace("scaleFactor=0.5", "scaleFactor=0.5")
         .format(w=int(W0), e=int(W0) + mos.shape[1] * 5, s=int(N0) - mos.shape[0] * 5, n=int(N0)))
    b = _holen(u, timeout=180)
    if not b or b[:4] not in (b"II*\x00", b"MM\x00*"): return None
    with MemoryFile(b) as mf, mf.open() as src:
        if abs(src.res[0] - 5) > .05: return None
        einsetzen(mos, W0, N0, 5, src.read(1), src.bounds.left, src.bounds.top, src.nodata)
    return mos, tr, "EPSG:25832"


def bw(w, s, e, n):
    """v2043: Baden-Württemberg DGM1 (ZIP mit XYZ je 1 km), beim Lesen auf 4 m gemittelt."""
    from rasterio.warp import transform_bounds
    W, S, E, N = transform_bounds("EPSG:3857", "EPSG:25832", w, s, e, n, densify_pts=21)
    mos, tr, W0, N0 = raster(W, S, E, N, 4)
    da = False
    with ThreadPoolExecutor(4) as tp:
        for teile in tp.map(lambda p: bw_lesen(*p, faktor=4), bw_zips(W, S, E, N)):
            for g, l, t in teile: einsetzen(mos, W0, N0, 4, g, l, t, None); da = True
    return (mos, tr, "EPSG:25832") if da else None


QUELLEN = {"CH": ch, "BY": by, "BZ": bz, "BW": bw}


def kachel(aus, z, x, y):
    """v2041: rechnet die Kachel in doppelter Auflösung (512 px = vier Kacheln Zoom z+1) und schreibt beide Stufen –
    die Karte lädt bei Zoom ~10,3 schon Reliefbilder Zoom 12, und die werden aus Höhen Zoom 13 gerechnet."""
    from rasterio.warp import reproject, Resampling
    from rasterio.transform import from_origin
    P = 512
    w, s, e, n = tbounds(z, x, y); res = (e - w) / P
    tr = from_origin(w, n, res, res)
    h = np.full((P, P), np.nan, np.float32); namen = []
    lon0, lon1 = w / R * 180, e / R * 180; lat0, lat1 = tile_lat(y + .5, z), tile_lat(y - .5, z)
    for name, (gw, gs, ge, gn) in GEBIETE:
        if not np.isnan(h).any(): break
        if lon1 < gw or lon0 > ge or lat1 < gs or lat0 > gn: continue
        try:
            r = QUELLEN[name](w - 2 * res, s - 2 * res, e + 2 * res, n + 2 * res)
        except Exception as ex:
            print(f"  {name}-Fehler {z}/{x}/{y}:", ex, flush=True); r = None
        if r is None: continue
        mos, mtr, crs = r
        if np.isnan(mos).all(): continue
        t = np.full((P, P), np.nan, np.float32)
        reproject(mos, t, src_transform=mtr, src_crs=crs, src_nodata=np.nan, dst_transform=tr, dst_crs="EPSG:3857",
                  dst_nodata=np.nan, resampling=Resampling.average, init_dest_nodata=True)
        if not np.isnan(t).all(): namen.append(name)
        h = np.where(np.isnan(h), t, h)
    if not namen: return None          # keine Laserscan-Daten: AWS bleibt, keine eigene Kachel
    if np.isnan(h).any():              # Lücken aus den vier AWS-Kacheln Zoom z+1
        for dx in (0, 1):
            for dy in (0, 1):
                b = _holen(AWS.format(z=z + 1, x=2 * x + dx, y=2 * y + dy))
                a = terrarium_lesen(b) if b else np.zeros((256, 256), np.float32)
                v = h[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256]
                h[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256] = np.where(np.isnan(v), a, v)
    for dx in (0, 1):
        for dy in (0, 1):
            terrarium_schreiben(h[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256], f"{aus}/{z + 1}/{2 * x + dx}/{2 * y + dy}.png")
    terrarium_schreiben(h.reshape(256, 2, 256, 2).mean(axis=(1, 3)), f"{aus}/{z}/{x}/{y}.png")
    return namen


def zoom11(aus):
    """Zoom 11 aus je 4 Zoom-12-Kacheln (fehlende Viertel aus AWS Zoom 12)."""
    eltern = set()
    for x in os.listdir(f"{aus}/12"):
        for f in os.listdir(f"{aus}/12/{x}"): eltern.add((int(x) // 2, int(f.split(".")[0]) // 2))
    def eine(p):
        X, Y = p; g = np.zeros((512, 512), np.float32)
        for dx in (0, 1):
            for dy in (0, 1):
                f = f"{aus}/12/{2 * X + dx}/{2 * Y + dy}.png"
                b = open(f, "rb").read() if os.path.exists(f) else _holen(AWS.format(z=12, x=2 * X + dx, y=2 * Y + dy))
                g[dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256] = terrarium_lesen(b) if b else 0
        terrarium_schreiben(g.reshape(256, 2, 256, 2).mean(axis=(1, 3)), f"{aus}/11/{X}/{Y}.png")
    with ThreadPoolExecutor(16) as tp: list(tp.map(eine, sorted(eltern)))
    print(len(eltern), "Kacheln Zoom 11")


if __name__ == "__main__":
    if sys.argv[1] == "--zoom11":
        zoom11(sys.argv[2]); sys.exit()
    aus, re_ = sys.argv[1], sys.argv[2]
    # Teil "t/n" (Spalten x % n == t), optional ":k/m" zusätzlich Zeilen y % m == k (für sehr langsame Spalten)
    tspec = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else "0/1"
    tx, _, ty = tspec.partition(":")
    teil, anzahl = map(int, tx.split("/"))
    yteil, yanz = map(int, ty.split("/")) if ty else (0, 1)
    k = set()
    for r in re_.split(";"):
        w, s, e, n = map(float, r.split(","))
        for x in range(lon2x(w, 12), lon2x(e, 12) + 1):
            if x % anzahl != teil: continue
            for y in range(lat2y(n, 12), lat2y(s, 12) + 1):
                if y % yanz == yteil: k.add((x, y))
    k = sorted(k); t0 = time.time(); ok = 0
    print(len(k), "Kacheln Zoom 12", flush=True)
    for i, (x, y) in enumerate(k):
        t = time.time(); r = kachel(aus, 12, x, y)
        if r: ok += 1
        print(f"12/{x}/{y}: {','.join(r) if r else 'nur AWS'} {time.time() - t:.0f} s  ({i + 1}/{len(k)})", flush=True)
    print(f"fertig: {ok} eigene Kacheln, {time.time() - t0:.0f} s")
