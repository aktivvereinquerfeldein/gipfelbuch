#!/usr/bin/env python3
"""Ordner z/x/y.(pbf|png) in eine PMTiles-Datei packen (eine Datei = schnelles Hochladen, wenige Speicher-Zugriffe).
Aufruf: packen.py <ordner> <ausgabe.pmtiles> mvt|png   (pbf-Dateien müssen bereits gzip-komprimiert sein)
Benötigt: pip install pmtiles
"""
import os, sys, json
from pmtiles.writer import Writer
from pmtiles.tile import zxy_to_tileid, TileType, Compression

def main(src, out, typ):
    ext = ".pbf" if typ == "mvt" else ".png"
    items = []
    for z in os.listdir(src):
        if not z.isdigit(): continue
        for x in os.listdir(os.path.join(src, z)):
            for f in os.listdir(os.path.join(src, z, x)):
                if f.endswith(ext): items.append((zxy_to_tileid(int(z), int(x), int(f[:-len(ext)])), os.path.join(src, z, x, f), int(z)))
    items.sort()
    if not items: raise SystemExit("keine Kacheln")
    zs = [i[2] for i in items]
    with open(out, "wb") as fo:
        w = Writer(fo)
        for tid, p, _ in items:
            with open(p, "rb") as fi: w.write_tile(tid, fi.read())
        w.finalize({"tile_type": TileType.MVT if typ == "mvt" else TileType.PNG,
                    "tile_compression": Compression.GZIP if typ == "mvt" else Compression.NONE,
                    "min_zoom": min(zs), "max_zoom": max(zs),
                    "min_lon_e7": -1800000000, "min_lat_e7": -850000000, "max_lon_e7": 1800000000, "max_lat_e7": 850000000,
                    "center_zoom": min(zs), "center_lon_e7": 0, "center_lat_e7": 0},
                   {"name": os.path.basename(out), "attribution": "Gipfelbuch"})
    print(f"{out}: {len(items)} Kacheln, {os.path.getsize(out)/1e6:.1f} MB")

if __name__ == "__main__": main(*sys.argv[1:4])
