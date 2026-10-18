#!/usr/bin/env python3
"""Gipfelbuch-Kartenserver: Gebiet (Österreich + Südtirol, später weitere Länder) als Umriss bestimmen.

  gebiet.py admin <datei.osm.pbf> <name-regex> <admin_level> <ausgabe.geojson>
      Umriss einer Verwaltungsgrenze aus OSM (z. B. Südtirol: "^(Südtirol|Alto Adige|Bozen)" 6)
  gebiet.py poly <datei.poly> <ausgabe.geojson>
      Geofabrik-.poly-Datei in GeoJSON umwandeln
  gebiet.py abdeckung <ausgabe.json> <umriss1.geojson> [umriss2.geojson …]
      Vereinigung aller Umrisse -> Abdeckung für den Worker:
        gebiet: um ca. 1 km verkleinerter, vereinfachter Umriss (für Kartenkacheln)
        zellen: je Zellgröße die Zellen, die ganz im Gebiet liegen (Zeilen iy -> [[ix_von, ix_bis], …])
Benötigt: pip install shapely osmium
"""
import sys, json, math
from shapely.geometry import shape, mapping, Polygon, MultiPolygon, box
from shapely.ops import unary_union

CELLSIZES = [0.05, 0.1, 0.2, 0.4, 1]

def read_poly(path):
    rings, cur, name, holes = [], None, None, []
    polys = []
    with open(path) as f:
        lines = [l.strip() for l in f]
    i = 1
    while i < len(lines):
        l = lines[i]
        if l == "END": break
        hole = l.startswith("!"); pts = []; i += 1
        while lines[i] != "END":
            x, y = map(float, lines[i].split()[:2]); pts.append((x, y)); i += 1
        i += 1
        if hole and polys: polys[-1] = Polygon(polys[-1].exterior.coords, list(polys[-1].interiors) + [pts])
        else: polys.append(Polygon(pts))
    return unary_union(polys)

def admin(pbf, name_rx, level):
    import re, osmium
    from shapely import wkb
    rx = re.compile(name_rx)
    fab = osmium.geom.WKBFactory()
    found = []
    class H(osmium.SimpleHandler):
        def area(self, a):
            t = a.tags
            if a.from_way(): return
            if t.get("boundary") != "administrative" or t.get("admin_level") != str(level): return
            if not any(rx.search(x.v) for x in t if x.k.startswith("name")): return
            found.append(wkb.loads(fab.create_multipolygon(a), hex=True))
    H().apply_file(pbf, locations=True)
    if not found: raise SystemExit("Grenze nicht gefunden: " + name_rx)
    return unary_union(found)

def runs(geom, size):
    """Zellen, die ganz im Gebiet liegen."""
    minx, miny, maxx, maxy = geom.bounds
    out = {}
    from shapely.prepared import prep
    pg = prep(geom)
    for iy in range(math.floor(miny / size), math.floor(maxy / size) + 1):
        row = []; start = None
        for ix in range(math.floor(minx / size), math.floor(maxx / size) + 2):
            inside = pg.contains(box(ix * size, iy * size, (ix + 1) * size, (iy + 1) * size))
            if inside and start is None: start = ix
            if not inside and start is not None: row.append([start, ix - 1]); start = None
        if row: out[str(iy)] = row
    return out

def abdeckung(out, files):
    g = unary_union([shape(json.load(open(f))["geometry"] if "geometry" in json.load(open(f)) else json.load(open(f))) for f in files])
    # ca. 1 km nach innen (in Grad, grob für 47° N), dann vereinfachen
    shrunk = g.buffer(-0.012).simplify(0.004, preserve_topology=True)
    polys = [shrunk] if isinstance(shrunk, Polygon) else list(getattr(shrunk, "geoms", []))
    gebiet = [[[[round(x, 5), round(y, 5)] for x, y in p.exterior.coords]] + [[[round(x, 5), round(y, 5)] for x, y in h.coords] for h in p.interiors] for p in polys if p.area > 1e-5]
    res = {"gebiet": gebiet, "zellen": {str(s): runs(g, s) for s in CELLSIZES}, "bounds": [round(v, 4) for v in g.bounds]}
    json.dump(res, open(out, "w"), separators=(",", ":"))
    n = {k: sum(b - a + 1 for r in v.values() for a, b in r) for k, v in res["zellen"].items()}
    print("Abdeckung:", out, "Teilflächen", len(gebiet), "Zellen ganz im Gebiet:", n)

def save(geom, out):
    json.dump({"type": "Feature", "properties": {}, "geometry": mapping(geom)}, open(out, "w"))
    print("gespeichert:", out, "Fläche (Grad²)", round(geom.area, 4))

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "admin": save(admin(sys.argv[2], sys.argv[3], int(sys.argv[4])), sys.argv[5])
    elif cmd == "poly": save(read_poly(sys.argv[2]), sys.argv[3])
    elif cmd == "abdeckung": abdeckung(sys.argv[2], sys.argv[3:])
    else: raise SystemExit(__doc__)
