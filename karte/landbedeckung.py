#!/usr/bin/env python3
"""Landbedeckung für weiten Zoom wie bei bergfex (v1999).

Die Grundkarte (OpenFreeMap) hat unter Zoom 7 keine Wald-, Wiesen- und Ackerflächen – die Karte wirkt dort weiß.
bergfex legt für weiten Zoom ein fertiges Bild mit Landbedeckung darunter. Hier entsteht dasselbe aus
ESA WorldCover 2021 (10 m, CC BY 4.0, AWS Open Data), gelesen wird nur die grobe Übersicht (ca. 500 m).

Ausgabe: WebP-Kacheln Zoom 4–8 (256 px), Wasser und ohne Daten durchsichtig.
Aufruf:  python3 landbedeckung.py <ausgabeordner> [w,s,e,n]
"""
import sys, os, math, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image

URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{ns}{lat:02d}{ew}{lon:03d}_Map.tif"
OVR = 5                     # Übersicht 562 px je 3°-Kachel (ca. 0,0053° ≈ 400–600 m)
PX = 562
FARBEN = {                  # WorldCover-Klasse → Farbe (an die Kartenfarben ab Zoom 9 angelehnt)
    10: (122, 165, 102),    # Wald
    95: (122, 165, 102),    # Mangroven
    20: (201, 223, 170),    # Gebüsch
    30: (220, 238, 198),    # Grasland
    40: (230, 236, 202),    # Acker
    50: (217, 215, 210),    # Siedlung
    60: (238, 236, 230),    # kahl / spärlich (Fels, Geröll)
    70: (234, 246, 251),    # Schnee und Eis
    90: (214, 234, 222),    # Feuchtgebiet
    100: (228, 234, 214),   # Moos, Flechten
}
ZMIN, ZMAX = 4, 8
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
if os.path.exists("/root/.ccr/ca-bundle.crt"): os.environ.setdefault("CURL_CA_BUNDLE", "/root/.ccr/ca-bundle.crt")


def lade(lat, lon):
    import rasterio
    u = URL.format(ns="N" if lat >= 0 else "S", lat=abs(lat), ew="E" if lon >= 0 else "W", lon=abs(lon))
    for v in range(3):
        try:
            with rasterio.open("/vsicurl/" + u, overview_level=OVR - 1) as src:
                return src.read(1)
        except Exception as e:
            if "404" in str(e) or "not recognized" in str(e) or "No such file" in str(e) or "does not exist" in str(e): return None
            time.sleep(2 + 3 * v)
    return None


def main():
    aus = sys.argv[1]
    w, s, e, n = map(float, (sys.argv[2] if len(sys.argv) > 2 else "-12,35,42,67").split(","))
    lat0, lat1 = int(math.floor(s / 3) * 3), int(math.floor((n - 1e-9) / 3) * 3)
    lon0, lon1 = int(math.floor(w / 3) * 3), int(math.floor((e - 1e-9) / 3) * 3)
    nlat = (lat1 - lat0) // 3 + 1; nlon = (lon1 - lon0) // 3 + 1
    klasse = np.zeros((nlat * PX, nlon * PX), np.uint8)
    jobs = [(la, lo) for la in range(lat0, lat1 + 1, 3) for lo in range(lon0, lon1 + 1, 3)]
    t0 = time.time()
    with ThreadPoolExecutor(16) as pool:
        for (la, lo), a in zip(jobs, pool.map(lambda j: lade(*j), jobs)):
            if a is None: continue
            a = a[:PX, :PX]
            r = (lat1 - la) * PX // 3; c = (lo - lon0) * PX // 3
            klasse[r:r + a.shape[0], c:c + a.shape[1]] = a
    print(f"{len(jobs)} Kacheln gelesen, {time.time() - t0:.0f} s", flush=True)
    # Farben (RGBA), Wasser/ohne Daten durchsichtig
    lut = np.zeros((256, 4), np.uint8)
    for k, rgb in FARBEN.items(): lut[k] = (*rgb, 255)
    rgba = lut[klasse]; del klasse
    W0, N0 = lon0, lat1 + 3                      # linke obere Ecke der Mosaik-Gitter (Grad)
    res = 3 / PX
    H, Wd = rgba.shape[:2]

    def merc_y(lat): r = math.radians(lat); return (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2

    # Mosaik für Zoom 8 in Kartenprojektion (Zeilen über Breite → Mercator), auf Zoom-ZMIN-Kacheln ausgerichtet
    z = ZMAX; T = 256 * 2 ** z; A = 256 * 2 ** (ZMAX - ZMIN)
    x0 = int((w + 180) / 360 * T) // A * A; x1 = -(-int((e + 180) / 360 * T) // A) * A
    y0 = int(merc_y(n) * T) // A * A; y1 = -(-int(merc_y(s) * T) // A) * A
    xs = (np.arange(x0, x1) + .5) / T * 360 - 180
    ys = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * (np.arange(y0, y1) + .5) / T))))
    ci = ((xs - W0) / res).astype(int); ri = ((N0 - ys) / res).astype(int)
    cok = (ci >= 0) & (ci < Wd); rok = (ri >= 0) & (ri < H)
    m = np.zeros((len(ys), len(xs), 4), np.uint8)
    cc = np.clip(ci, 0, Wd - 1)
    for k in range(0, len(ys), 512):                 # streifenweise (Speicher)
        rr = np.clip(ri[k:k + 512], 0, H - 1)
        blk = rgba[rr][:, cc]
        blk[~rok[k:k + 512]] = 0; blk[:, ~cok] = 0
        m[k:k + 512] = blk
    del rgba
    # vormultipliziert speichern (Mitteln ohne dunkle Ränder)
    for k in range(0, m.shape[0], 512):
        b = m[k:k + 512].astype(np.uint16)
        b[..., :3] = b[..., :3] * b[..., 3:4] // 255
        m[k:k + 512] = b.astype(np.uint8)
    n_img = 0
    for z in range(ZMAX, ZMIN - 1, -1):
        hh, ww = m.shape[:2]
        tx0 = x0 // 256 // 2 ** (ZMAX - z); ty0 = y0 // 256 // 2 ** (ZMAX - z)
        for ty in range(hh // 256):
            for tx in range(ww // 256):
                t = m[ty * 256:(ty + 1) * 256, tx * 256:(tx + 1) * 256]
                al = t[..., 3].astype(np.float32)
                if al.max() < 1: continue
                rgb = np.where(al[..., None] > 0, t[..., :3].astype(np.float32) * 255 / np.maximum(al[..., None], 1), 0)
                img = np.dstack([np.clip(rgb, 0, 255), al]).astype(np.uint8)
                os.makedirs(f"{aus}/{z}/{tx0 + tx}", exist_ok=True)
                Image.fromarray(img, "RGBA").save(f"{aus}/{z}/{tx0 + tx}/{ty0 + ty}.webp", "WEBP", quality=80, alpha_quality=70, method=6)
                n_img += 1
        print(f"Zoom {z} fertig, {n_img} Bilder", flush=True)
        if z > ZMIN:
            m2 = np.zeros((hh // 2, ww // 2, 4), np.uint8)
            for k in range(0, hh, 1024):
                b = m[k:k + 1024].astype(np.uint16)
                m2[k // 2:(k + 1024) // 2] = ((b[0::2, 0::2] + b[1::2, 0::2] + b[0::2, 1::2] + b[1::2, 1::2] + 2) // 4).astype(np.uint8)
            m = m2


if __name__ == "__main__":
    main()
