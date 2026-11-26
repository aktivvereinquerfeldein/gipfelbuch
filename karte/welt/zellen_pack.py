#!/usr/bin/env python3
"""Welt in Regionen: Kartenstücke (Hütten, Parkplätze, Sperren …) aller Regionen zusammenfügen und je Ebene in EINE Datei packen.
Weltweit wären es Millionen Einzeldateien (jedes Hochladen kostet bei R2 je Datei), darum je Ebene ein PMTiles-Archiv:
Zelle (ix, iy) der Größe c -> Kachel z = ceil(log2(360/c)), x = ix + 180/c, y = iy + 90/c. Inhalt unverändert (gleiches
Overpass-JSON wie karte/<Version>/zellen/<ebene>/<ix>_<iy>.json.gz). Zellen an Regionsgrenzen kommen aus mehreren Regionen:
gleiche Objekte (Typ + Nummer) nur einmal, das vollständigere (längere) bleibt.
Ausgabe: <aus>/zellen-<ebene>.pmtiles und <aus>/zellen-index.json (Anzahl je Zelle wie zellen/index.json).
Aufruf: zellen_pack.py <regionen-ordner r/> <aus> [prozesse]"""
import sys, os, gzip, json, math, re
from multiprocessing import Pool
from pmtiles.writer import Writer
from pmtiles.tile import zxy_to_tileid, TileType, Compression

R, AUS = sys.argv[1], sys.argv[2]; P = int(sys.argv[3]) if len(sys.argv) > 3 else 8
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from zellen import CELL
REG = sorted(r for r in os.listdir(R) if os.path.isfile(os.path.join(R, r, "fertig")))
ZN = re.compile(r"^(-?\d+)_(-?\d+)\.json\.gz$")

def geo(c):
    nx, ny = round(360 / c), round(180 / c); z = math.ceil(math.log2(max(nx, ny))); return z, nx // 2, ny // 2

def packe(k):
    c = CELL[k]; z, ox, oy = geo(c); zellen = {}
    for r in REG:
        d = os.path.join(R, r, "zellen", k)
        if not os.path.isdir(d): continue
        for f in os.listdir(d):
            m = ZN.match(f)
            if m: zellen.setdefault((int(m[1]), int(m[2])), []).append(os.path.join(d, f))
    if not zellen: return k, 0, {}
    ein = []
    for (ix, iy), fs in zellen.items(): ein.append((zxy_to_tileid(z, ix + ox, iy + oy), ix, iy, fs))
    ein.sort()
    idx = {}; doppelt = 0
    with open(os.path.join(AUS, f"zellen-{k}.pmtiles"), "wb") as fo:
        w = Writer(fo)
        for tid, ix, iy, fs in ein:
            if len(fs) == 1:
                with open(fs[0], "rb") as fh: data = fh.read()
                n = len(json.loads(gzip.decompress(data))["elements"])
            else:
                el = {}
                for p in fs:
                    with gzip.open(p, "rt", encoding="utf-8") as fh:
                        for e in json.load(fh)["elements"]:
                            s = json.dumps(e, ensure_ascii=False, separators=(",", ":")); key = (e.get("type"), e.get("id"))
                            if key in el: doppelt += 1
                            if key not in el or len(s) > len(el[key]): el[key] = s
                body = '{"version":0.6,"generator":"gipfelbuch-zellen","elements":[' + ",".join(el.values()) + "]}"
                data = gzip.compress(body.encode("utf-8"), 9); n = len(el)
            w.write_tile(tid, data); idx[f"{ix}_{iy}"] = n
        w.finalize({"tile_type": TileType.UNKNOWN, "tile_compression": Compression.GZIP, "min_zoom": z, "max_zoom": z,
                    "min_lon_e7": -1800000000, "min_lat_e7": -850000000, "max_lon_e7": 1800000000, "max_lat_e7": 850000000,
                    "center_zoom": z, "center_lon_e7": 0, "center_lat_e7": 0},
                   {"name": f"qfe-active Kartenstücke {k}", "cell": c, "z": z, "ox": ox, "oy": oy})
    return k, doppelt, idx

if __name__ == "__main__":
    os.makedirs(AUS, exist_ok=True)
    keys = sorted({k for r in REG for k in (os.listdir(os.path.join(R, r, "zellen")) if os.path.isdir(os.path.join(R, r, "zellen")) else []) if k in CELL})
    out = {"cell": {k: CELL[k] for k in keys}, "pack": {}, "zellen": {}, "regionen": len(REG)}
    with Pool(P) as pool:
        for k, doppelt, idx in pool.imap_unordered(packe, keys):
            z, ox, oy = geo(CELL[k]); out["pack"][k] = {"z": z, "ox": ox, "oy": oy}; out["zellen"][k] = idx
            print(f"{k}: {len(idx)} Zellen, {sum(idx.values())} Objekte, {doppelt} doppelte entfernt", flush=True)
    json.dump(out, open(os.path.join(AUS, "zellen-index.json"), "w"), separators=(",", ":"))
