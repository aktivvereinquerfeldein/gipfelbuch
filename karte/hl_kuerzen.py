#!/usr/bin/env python3
"""Höhenlinien-PMTiles auf Zoom bis <maxzoom> kürzen (höhere Zoomstufen rechnet die App selbst aus dem Höhenmodell).
Aufruf: hl_kuerzen.py <eingabe.pmtiles> <ausgabe.pmtiles> [maxzoom=14]"""
import sys
from pmtiles.reader import Reader, MmapSource, all_tiles
from pmtiles.writer import Writer
from pmtiles.tile import zxy_to_tileid

src, dst = sys.argv[1], sys.argv[2]; maxz = int(sys.argv[3]) if len(sys.argv) > 3 else 14
with open(src, "rb") as f:
    r = Reader(MmapSource(f)); h = dict(r.header()); md = r.metadata()
    tiles = [(zxy_to_tileid(z, x, y), d) for (z, x, y), d in all_tiles(r.get_bytes) if z <= maxz]
tiles.sort(key=lambda t: t[0])
with open(dst, "wb") as f:
    w = Writer(f)
    for tid, d in tiles: w.write_tile(tid, d)
    h["max_zoom"] = min(h.get("max_zoom", maxz), maxz); h["center_zoom"] = min(h.get("center_zoom", maxz), maxz)
    if isinstance(md, dict) and "maxzoom" in md: md["maxzoom"] = maxz
    w.finalize(h, md)
print(f"{src} -> {dst}: {len(tiles)} Kacheln bis Zoom {maxz}")
