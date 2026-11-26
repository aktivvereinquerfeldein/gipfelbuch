#!/usr/bin/env python3
"""Welt in Regionen: Rohdaten einer Region mit Schlüssel versehen (Zeile = Schlüssel<TAB>GeoJSON, gzip).
Wege: Schlüssel = Art + OSM-Nummer + Form (gleiche Linie aus zwei Nachbarregionen zählt nur einmal).
Lifte und Gebäudenamen: gleiche Auswahl wie in karte-aktualisieren.yml, Schlüssel = OSM-Nummer.
Aufruf: schluessel.py <regionsordner>"""
import sys, os, json, gzip, hashlib
d = sys.argv[1]
def h(s): return hashlib.md5(s.encode()).hexdigest()[:16]

def wege(name):
    with open(os.path.join(d, name + ".geojsonl"), encoding="utf-8") as f, gzip.open(os.path.join(d, name + ".tsv.gz"), "wt", encoding="utf-8", compresslevel=3) as o:
        for z in f:
            z = z.strip()
            if not z: continue
            j = json.loads(z); p = j.get("properties", {})
            k = f"{p.get('k','')}|{p.get('oid',0)}|{h(json.dumps(j.get('geometry'), separators=(',', ':')))}"
            o.write(k + "\t" + z + "\n")
for n in ("wege-niedrig", "wege-hoch"): wege(n)

with open(os.path.join(d, "lifte.geojsonl"), encoding="utf-8") as f, gzip.open(os.path.join(d, "lifte.tsv.gz"), "wt", encoding="utf-8", compresslevel=3) as o:
    for z in f:
        z = z.strip().lstrip(chr(30))
        if not z: continue
        j = json.loads(z); p = j.get("properties", {})
        if p.get("aerialway") in (None, "pylon", "station", "yes") or p.get("disused") or p.get("access") in ("no", "private"): continue
        j["properties"] = {"aerialway": p["aerialway"], **({"name": p["name"]} if p.get("name") else {})}
        o.write(f"l{p.get('@id')}\t" + json.dumps(j, ensure_ascii=False) + "\n")

WEG = ('tourism', 'amenity', 'shop', 'leisure', 'office', 'craft', 'railway', 'public_transport', 'healthcare')
with open(os.path.join(d, "geb.geojsonl"), encoding="utf-8") as f, gzip.open(os.path.join(d, "geb.tsv.gz"), "wt", encoding="utf-8", compresslevel=3) as o:
    for z in f:
        z = z.strip().lstrip(chr(30))
        if not z: continue
        j = json.loads(z); p = j.get("properties", {}); nm = p.get("name:de") or p.get("name")
        if not nm or any(p.get(k) for k in WEG): continue
        g = j["geometry"]; ring = (g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0])[:-1]
        if not ring: continue
        x = sum(c[0] for c in ring) / len(ring); y = sum(c[1] for c in ring) / len(ring)
        o.write(f"g{p.get('@type','')}{p.get('@id')}\t" + json.dumps({"type": "Feature", "properties": {"name": nm}, "geometry": {"type": "Point", "coordinates": [round(x, 6), round(y, 6)]}}, ensure_ascii=False) + "\n")
