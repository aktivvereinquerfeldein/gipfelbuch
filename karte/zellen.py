#!/usr/bin/env python3
"""Gipfelbuch-Kartenserver: Zellen-Dateien aus einer OSM-Datei (.osm.pbf) erzeugen.

Die App lädt Wege, Hütten, Parkplätze usw. in Gitter-Zellen (RTDEF/CELL in index.html) und fragt dafür
bisher Overpass. Dieses Skript erzeugt für jede Zelle genau die Antwort, die Overpass liefern würde
(gleiches JSON-Format, gleiche Filter), damit das Kartenbild unverändert bleibt.
Ausgabe: <ziel>/<key>/<ix>_<iy>.json  (+ <ziel>/index.json mit Zählern für den Vergleich, Prompt 3)

Aufruf: python3 zellen.py daten.osm.pbf ausgabe/ [--keys sac,hike,...] [--bbox w,s,e,n]
Benötigt: pip install osmium
"""
import json, math, os, re, sys, gzip, time, argparse
from collections import defaultdict
import osmium

# ---- muss zu CELL in index.html passen ----
CELL = {"pka": .05, "spt": .05, "pnt": .05, "rest": .1, "valley": 1, "range": 1, "park": 1, "area": .2, "hut": .4,
        "hike": .2, "mtb": .2, "sac": .1, "minor": .1, "forest": .2, "guide": .1, "sperr": .1, "t2": .1}

I = re.IGNORECASE
def rx(p, f=0): return re.compile(p, f)
R_HUT_T = rx(r"^(alpine_hut|wilderness_hut)$")
R_HUT_PLACE = rx(r"^(locality|isolated_dwelling|hamlet|farm)$")
R_HUT_PNAME = rx(r"^(Alpe?|Alm|Maisäß|Vorsäß)( |$)|[a-zäöüß]{3}(alpe|alp|alm)$|(^|[ -]|ober|unter|hinter|vorder|inner|mittel)alp(e|ila)?([ -]|$)", I)  # auch Alpila, Gaßneralpe, Oberhutlaalpe (27.10.2026)
R_HUT_BLD = rx(r"^(hut|cabin|farm_auxiliary|barn|yes|house|farm|cowshed|stable|detached|shed)$")  # benannte Alpgebäude ohne place-Punkt
R_HUT_AM = rx(r"^(restaurant|cafe|biergarten)$")
R_HUT_ANAME = rx(r"(alpe?( |$)|^alp |hütte|maisäß)", I)
R_REST_AM = rx(r"^(restaurant|cafe|pub|biergarten)$")
R_PARK_PT = rx(r"Biosph|Naturpark|Nationalpark", I)
R_AREA_NAT = rx(r"^(glacier|bare_rock|massif|gorge)$")
R_AREA_PC = rx(r"^(1|1a|1b|2|3|4|5|97)$")
R_MINOR = rx(r"^(unclassified|residential)$")
R_SAC = rx(r"^(path|footway|bridleway|steps|track|via_ferrata)$")
R_SPT = rx(r"^(pitch|sports_centre|stadium|track|swimming_pool|golf_course|water_park)$")
R_PNT_NAT = rx(r"^(cave_entrance|spring)$")
R_PNT_AM = rx(r"^(hunting_stand|bench)$")
R_PNT_MM = rx(r"^(cross|tower)$")
R_SP_BIKE = rx(r"^(no|dismount)$")
R_SP_NOTE = rx(r"gesperrt|sperre", I)
R_SP_ROUTE = rx(r"^(mtb|bicycle|hiking|foot)$")
# Bergweg-Prüfung für Radabschnitte (osmT2On in index.html)
R_T2_HW1 = rx(r"^(path|footway|bridleway|track|steps)$")
R_T2_SAC = rx(r"^(mountain_hiking|demanding_mountain_hiking|alpine_hiking|demanding_alpine_hiking|difficult_alpine_hiking)$")
R_T2_HW2 = rx(r"^(path|bridleway|track|cycleway)$")
R_T2_HW3 = rx(r"^(path|bridleway|track)$")
R_T2_NAME = rx(r"downhill|bikepark|flow ?trail|freeride", I)

def m(t, k, r): v = t.get(k); return v is not None and r.search(v) is not None

# ---- Filter je Ebene (entspricht den Overpass-Abfragen in RTDEF) ----
def keys_node(t):
    k = []
    if (m(t, "tourism", R_HUT_T) or ("name" in t and (m(t, "place", R_HUT_PLACE) or (m(t, "building", R_HUT_BLD) and "amenity" not in t and "tourism" not in t)) and m(t, "name", R_HUT_PNAME) and "verf" not in t["name"])
            or ("name" in t and m(t, "amenity", R_HUT_AM) and m(t, "name", R_HUT_ANAME))): k.append("hut")
    if t.get("information") == "guidepost": k.append("guide")
    if "name" in t and m(t, "amenity", R_REST_AM) and not m(t, "name", R_HUT_ANAME): k.append("rest")
    if t.get("natural") == "valley" and "name" in t: k.append("valley")
    if "name" in t and (m(t, "protection_title", R_PARK_PT) or t.get("boundary") == "national_park"): k.append("park")
    if "name" in t and (m(t, "natural", R_AREA_NAT) or t.get("leisure") == "nature_reserve" or t.get("boundary") == "national_park"
                        or (t.get("boundary") == "protected_area" and m(t, "protect_class", R_AREA_PC))): k.append("area")
    if t.get("amenity") == "parking": k.append("pka")
    if (m(t, "natural", R_PNT_NAT) or t.get("waterway") == "waterfall" or m(t, "amenity", R_PNT_AM)
            or t.get("historic") in ("wayside_shrine", "wayside_cross") or m(t, "man_made", R_PNT_MM)
            or t.get("summit:cross") == "yes" or (t.get("power") == "generator" and t.get("generator:source") == "wind")
            or t.get("ford") == "yes" or t.get("mountain_pass") == "yes" or t.get("tourism") == "picnic_site"): k.append("pnt")
    return k

def sperr_way(t):
    if "highway" not in t: return False
    return ("bicycle:conditional" in t or "access:conditional" in t or "foot:conditional" in t or "mtb:conditional" in t
            or m(t, "bicycle", R_SP_BIKE) or t.get("foot") == "no" or t.get("access") == "no" or m(t, "note", R_SP_NOTE))

def t2_way(t):
    return ((m(t, "highway", R_T2_HW1) and m(t, "sac_scale", R_T2_SAC)) or (m(t, "highway", R_T2_HW2) and "mtb:type" in t)
            or (m(t, "highway", R_T2_HW3) and t.get("foot") == "no") or (m(t, "highway", R_T2_HW3) and m(t, "name", R_T2_NAME)))

def keys_way(t):
    k = []
    if (m(t, "tourism", R_HUT_T) or ("name" in t and (m(t, "place", R_HUT_PLACE) or (m(t, "building", R_HUT_BLD) and "amenity" not in t and "tourism" not in t)) and m(t, "name", R_HUT_PNAME) and "verf" not in t["name"])
            or ("name" in t and m(t, "amenity", R_HUT_AM) and m(t, "name", R_HUT_ANAME))): k.append("hut")
    if "name" in t and (t.get("landuse") == "forest" or t.get("natural") == "wood"): k.append("forest")
    if "name" in t and m(t, "amenity", R_REST_AM) and not m(t, "name", R_HUT_ANAME): k.append("rest")
    if t.get("natural") == "valley" and "name" in t: k.append("valley")
    if "name" in t and (m(t, "protection_title", R_PARK_PT) or t.get("boundary") == "national_park"): k.append("park")
    if "name" in t and (m(t, "natural", R_AREA_NAT) or t.get("leisure") == "nature_reserve" or t.get("boundary") == "national_park"
                        or (t.get("boundary") == "protected_area" and m(t, "protect_class", R_AREA_PC))): k.append("area")
    if m(t, "highway", R_MINOR): k.append("minor")
    if m(t, "highway", R_SAC) or (t.get("highway") == "cycleway" and "mtb:scale" in t) or "via_ferrata_scale" in t: k.append("sac")
    if t.get("amenity") == "parking": k.append("pka")
    if m(t, "leisure", R_SPT): k.append("spt")
    if sperr_way(t): k.append("sperr")
    if t2_way(t): k.append("t2")
    return k

def keys_rel(t):
    k = []
    if (m(t, "tourism", R_HUT_T) or ("name" in t and (m(t, "place", R_HUT_PLACE) or (m(t, "building", R_HUT_BLD) and "amenity" not in t and "tourism" not in t)) and m(t, "name", R_HUT_PNAME) and "verf" not in t["name"])
            or ("name" in t and m(t, "amenity", R_HUT_AM) and m(t, "name", R_HUT_ANAME))): k.append("hut")
    if "name" in t and (t.get("landuse") == "forest" or t.get("natural") == "wood"): k.append("forest")
    if "name" in t and m(t, "amenity", R_REST_AM) and not m(t, "name", R_HUT_ANAME): k.append("rest")
    if t.get("natural") == "valley" and "name" in t: k.append("valley")
    if t.get("region:type") == "mountain_area" and "name" in t: k.append("range")
    if "name" in t and (m(t, "protection_title", R_PARK_PT) or t.get("boundary") == "national_park"): k.append("park")
    if "name" in t and (m(t, "natural", R_AREA_NAT) or t.get("leisure") == "nature_reserve" or t.get("boundary") == "national_park"
                        or (t.get("boundary") == "protected_area" and m(t, "protect_class", R_AREA_PC))): k.append("area")
    return k

# Ausgabeform je Ebene (wie "out ..." in der Overpass-Abfrage)
#  center: Punkt-Mitte | geom: volle Geometrie | bb: nur Umriss-Box | ids: ohne Tags | notags_nodecoord: Nodes ohne Koordinaten
OUT = {"hut": "center", "forest": "center", "guide": "body", "rest": "center", "valley": "geom", "range": "bb", "park": "bb",
       "area": "bb", "minor": "ids_geom", "hike": "geom", "sac": "geom", "pka": "geom", "spt": "geom", "pnt": "body", "mtb": "ids_geom",
       "sperr": "geom", "t2": "geom"}

def r7(v): return round(v, 7)

class Store:
    def __init__(s, bbox):
        s.cells = defaultdict(list)  # (key, ix, iy) -> [json-str]
        s.bbox = bbox
        s.count = defaultdict(int)
    def inb(s, lon, lat):
        b = s.bbox; return b is None or (b[0] <= lon <= b[2] and b[1] <= lat <= b[3])
    def cells_pt(s, k, lon, lat):
        c = CELL[k]; return {(math.floor(lon / c), math.floor(lat / c))}
    def cells_line(s, k, coords):
        c = CELL[k]; out = set()
        for i in range(len(coords)):
            a = coords[i]; b = coords[i - 1] if i else a
            x0, x1 = sorted((a[0], b[0])); y0, y1 = sorted((a[1], b[1]))
            for ix in range(math.floor(x0 / c), math.floor(x1 / c) + 1):
                for iy in range(math.floor(y0 / c), math.floor(y1 / c) + 1): out.add((ix, iy))
        return out
    def add(s, k, cells, obj):
        js = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        for (ix, iy) in cells:
            if s.bbox is not None:
                c = CELL[k]
                if (ix + 1) * c < s.bbox[0] or ix * c > s.bbox[2] or (iy + 1) * c < s.bbox[1] or iy * c > s.bbox[3]: continue
            s.cells[(k, ix, iy)].append(js)
        s.count[k] += 1

def bounds(coords):
    xs = [c[0] for c in coords]; ys = [c[1] for c in coords]
    return {"minlat": r7(min(ys)), "minlon": r7(min(xs)), "maxlat": r7(max(ys)), "maxlon": r7(max(xs))}

def geomlist(coords): return [{"lat": r7(c[1]), "lon": r7(c[0])} for c in coords]

# ---------- Durchgang 1: Relationen sammeln ----------
class RelPass(osmium.SimpleHandler):
    def __init__(s):
        super().__init__()
        s.rels = {}            # id -> (keys, tags, members[(type,ref,role)])
        s.hike_ways = set(); s.mtb_ways = set()
        s.sperr_rels = {}      # route-Relationen mit url/website (für Quellenangaben der Sperren-Prüfung)
        s.need_ways = set(); s.need_nodes = set()
    def relation(s, r):
        t = {x.k: x.v for x in r.tags}
        route = t.get("route")
        mem = [(x.type, x.ref, x.role) for x in r.members]
        if route == "hiking":
            s.hike_ways.update(ref for (ty, ref, _) in mem if ty == "w")
        if route == "mtb":
            s.mtb_ways.update(ref for (ty, ref, _) in mem if ty == "w")
        if route and R_SP_ROUTE.search(route) and (t.get("url") or t.get("website")):
            s.sperr_rels[r.id] = (t, [ref for (ty, ref, _) in mem if ty == "w"])
            s.need_ways.update(ref for (ty, ref, _) in mem if ty == "w")
        k = keys_rel(t)
        if k:
            s.rels[r.id] = (k, t, mem)
            for (ty, ref, _) in mem:
                if ty == "w": s.need_ways.add(ref)
                elif ty == "n": s.need_nodes.add(ref)

# ---------- Durchgang 2: Nodes und Wege ----------
class MainPass(osmium.SimpleHandler):
    def __init__(s, st, rp, keys):
        super().__init__()
        s.st = st; s.rp = rp; s.keys = keys
        s.wgeo = {}   # Geometrien der Relations-Mitglieder
        s.ngeo = {}
    def node(s, n):
        if n.id in s.rp.need_nodes: s.ngeo[n.id] = (n.location.lon, n.location.lat)
        if not n.tags: return
        t = {x.k: x.v for x in n.tags}
        ks = [k for k in keys_node(t) if k in s.keys]
        if not ks: return
        lon, lat = n.location.lon, n.location.lat
        if not s.st.inb(lon, lat): return
        for k in ks:
            o = OUT[k]
            if o == "tags": obj = {"type": "node", "id": n.id, "tags": t}            # "out tags": ohne Koordinaten (wie Overpass)
            else: obj = {"type": "node", "id": n.id, "lat": r7(lat), "lon": r7(lon), "tags": t}
            s.st.add(k, s.st.cells_pt(k, lon, lat), obj)
    def way(s, w):
        need = w.id in s.rp.need_ways
        hike = w.id in s.rp.hike_ways and "hike" in s.keys
        mtb = w.id in s.rp.mtb_ways and "mtb" in s.keys
        t = {x.k: x.v for x in w.tags} if w.tags else {}
        ks = [k for k in keys_way(t) if k in s.keys] if t else []
        if not (ks or need or hike or mtb): return
        try: coords = [(nd.lon, nd.lat) for nd in w.nodes if nd.location.valid()]
        except osmium.InvalidLocationError: coords = []
        if not coords: return
        if need: s.wgeo[w.id] = coords
        if s.st.bbox is not None and not any(s.st.inb(*c) for c in coords): return
        if hike: ks.append("hike")
        if mtb: ks.append("mtb")
        for k in ks:
            o = OUT[k]
            if o == "center":
                b = bounds(coords); obj = {"type": "way", "id": w.id, "center": {"lat": r7((b["minlat"] + b["maxlat"]) / 2), "lon": r7((b["minlon"] + b["maxlon"]) / 2)}, "tags": t}
            elif o == "bb": obj = {"type": "way", "id": w.id, "bounds": bounds(coords), "tags": t}
            elif o == "ids_geom": obj = {"type": "way", "id": w.id, "bounds": bounds(coords), "geometry": geomlist(coords)}
            else: obj = {"type": "way", "id": w.id, "bounds": bounds(coords), "geometry": geomlist(coords), "tags": t}
            s.st.add(k, s.st.cells_line(k, coords), obj)

def finish_relations(st, rp, mp, keys):
    for rid, (ks, t, mem) in rp.rels.items():
        ks = [k for k in ks if k in keys]
        if not ks: continue
        allc = []; mobj = []
        for (ty, ref, role) in mem:
            if ty == "w" and ref in mp.wgeo:
                g = mp.wgeo[ref]; allc.extend(g); mobj.append({"type": "way", "ref": ref, "role": role, "geometry": geomlist(g)})
            elif ty == "n" and ref in mp.ngeo:
                p = mp.ngeo[ref]; allc.append(p); mobj.append({"type": "node", "ref": ref, "role": role, "lat": r7(p[1]), "lon": r7(p[0])})
        if not allc: continue
        if st.bbox is not None and not any(st.inb(*c) for c in allc): continue
        b = bounds(allc)
        for k in ks:
            o = OUT[k]
            if o == "center": obj = {"type": "relation", "id": rid, "center": {"lat": r7((b["minlat"] + b["maxlat"]) / 2), "lon": r7((b["minlon"] + b["maxlon"]) / 2)}, "tags": t}
            elif o == "bb": obj = {"type": "relation", "id": rid, "bounds": b, "tags": t}
            else: obj = {"type": "relation", "id": rid, "bounds": b, "members": mobj, "tags": t}
            cells = set()
            for mo in mobj:
                if mo["type"] == "way": cells |= st.cells_line(k, [(g["lon"], g["lat"]) for g in mo["geometry"]])
                else: cells |= st.cells_pt(k, mo["lon"], mo["lat"])
            st.add(k, cells, obj)
    # Routen mit Webseite für die Quellen der Sperren-Prüfung: je Zelle vereinfachte Mitglieds-Geometrie ("g")
    if "sperr" in keys:
        for rid, (t, wids) in rp.sperr_rels.items():
            percell = defaultdict(list)
            for wid in wids:
                g = mp.wgeo.get(wid)
                if not g: continue
                sg = simplify(g, 0.00005)
                for c in st.cells_line("sperr", g): percell[c].append([[round(x, 5), round(y, 5)] for x, y in sg])
            keep = {k: v for k, v in t.items() if k in ("route", "name", "ref", "url", "website", "network", "operator")}
            for c, lines in percell.items():
                st.add("sperr", {c}, {"type": "relation", "id": rid, "members": [], "tags": keep, "g": lines})

def simplify(pts, tol):
    if len(pts) < 3: return pts
    a, b = pts[0], pts[-1]; dx, dy = b[0] - a[0], b[1] - a[1]; L = math.hypot(dx, dy)
    best, bi = -1, 0
    for i in range(1, len(pts) - 1):
        p = pts[i]
        d = abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / L if L else math.hypot(p[0] - a[0], p[1] - a[1])
        if d > best: best, bi = d, i
    if best <= tol: return [a, b]
    return simplify(pts[:bi + 1], tol)[:-1] + simplify(pts[bi:], tol)

def write(st, out, version):
    os.makedirs(out, exist_ok=True)
    idx = {"version": version, "erzeugt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cell": CELL, "zellen": {}, "elemente": dict(st.count)}
    for (k, ix, iy), items in st.cells.items():
        d = os.path.join(out, k); os.makedirs(d, exist_ok=True)
        body = '{"version":0.6,"generator":"gipfelbuch-zellen","elements":[' + ",".join(items) + "]}"
        with gzip.open(os.path.join(d, f"{ix}_{iy}.json.gz"), "wt", encoding="utf-8", compresslevel=9) as f: f.write(body)
        idx["zellen"].setdefault(k, {})[f"{ix}_{iy}"] = len(items)
    with open(os.path.join(out, "index.json"), "w") as f: json.dump(idx, f, separators=(",", ":"))
    return idx

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf"); ap.add_argument("out")
    ap.add_argument("--keys", default=",".join(CELL.keys()))
    ap.add_argument("--bbox", default=None, help="w,s,e,n – nur Elemente in diesem Rechteck")
    ap.add_argument("--version", default=time.strftime("%Y-%m"))
    a = ap.parse_args()
    keys = set(a.keys.split(","))
    bbox = [float(x) for x in a.bbox.split(",")] if a.bbox else None
    t0 = time.time()
    rp = RelPass(); rp.apply_file(a.pbf)
    print(f"Relationen: {len(rp.rels)} gefiltert, Wanderwege-Mitglieder {len(rp.hike_ways)}, MTB {len(rp.mtb_ways)} ({time.time()-t0:.0f}s)", flush=True)
    st = Store(bbox)
    mp = MainPass(st, rp, keys)
    mp.apply_file(a.pbf, locations=True, idx="flex_mem")
    finish_relations(st, rp, mp, keys)
    idx = write(st, a.out, a.version)
    print(f"Fertig in {time.time()-t0:.0f}s: " + ", ".join(f"{k} {v}" for k, v in sorted(idx['elemente'].items())) + f"; Zellen-Dateien {len(st.cells)}", flush=True)

if __name__ == "__main__":
    main()
