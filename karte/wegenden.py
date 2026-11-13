"""Enden von Forststraßen (highway=track) und was dort anschließt – für die Ableger-Regel der Wegekarte (v2052).

Aufruf: python3 wegenden.py gebiet.pbf wegenden.txt
Ausgabe je Zeile: lonE5 latE5 durch weg-ids (Komma)
  - lonE5/latE5: Koordinate des Endpunkts (wie die Schlüssel in wegekarte.mjs, gerundet auf 1e-5 Grad)
  - durch: 1, wenn ein Weg (jede Straße oder jeder Weg) dort nicht endet, sondern durchläuft
  - weg-ids: alle Wege (highway=*), die diesen Punkt als Ende haben
Ein Ende mit nur der eigenen Weg-Id und durch=0 ist frei (Sackgasse)."""
import sys, osmium

pbf, aus = sys.argv[1], sys.argv[2]

class Enden(osmium.SimpleHandler):
    def __init__(s): super().__init__(); s.ende = {}
    def way(s, w):
        if w.tags.get("highway") != "track" or len(w.nodes) < 2: return
        for n in (w.nodes[0], w.nodes[-1]):
            if n.location.valid(): s.ende[n.ref] = (round(n.location.lon * 1e5), round(n.location.lat * 1e5))

class Anschluss(osmium.SimpleHandler):
    def __init__(s, ende): super().__init__(); s.ende = ende; s.ids = {}; s.durch = set()
    def way(s, w):
        if "highway" not in w.tags: return
        nd = w.nodes; L = len(nd)
        if L < 2: return
        a, b = nd[0].ref, nd[L - 1].ref
        for i in range(L):
            r = nd[i].ref
            if r not in s.ende: continue
            if r == a or r == b: s.ids.setdefault(r, set()).add(w.id)
            else: s.durch.add(r)

e = Enden(); e.apply_file(pbf, locations=True, idx="flex_mem")
print(len(e.ende), "Enden von Forststraßen", flush=True)
a = Anschluss(e.ende); a.apply_file(pbf)
with open(aus, "w") as o:
    for r, (x, y) in e.ende.items():
        o.write(f"{x} {y} {1 if r in a.durch else 0} {','.join(map(str, sorted(a.ids.get(r, ()))))}\n")
print("geschrieben:", aus)
