#!/usr/bin/env python3
"""Neue und alte Wanderkarte vergleichen (Prompt 3: „Verschwinden in einer Region auffällig viele Wege (über ca. 2 %),
nicht veröffentlichen, nachfragen“).

Aufruf: vergleich.py <alt/index.json oder -> <neu/index.json> <bericht.md>
Ergebnis: Exit-Code 0 = in Ordnung, 3 = auffällig (nicht freischalten, Bericht lesen).
Region = Block von 1° x 1° (ca. 75 x 110 km); geprüft werden nur Blöcke mit mindestens 300 Elementen.
"""
import json, sys, math
from collections import defaultdict

GRUPPEN = {"sac": "Wege und Steige (T1–T6, Forstwege)", "hike": "markierte Wanderwege", "mtb": "MTB-Routen", "hut": "Hütten und Almen",
           "pka": "Parkplätze", "sperr": "Wege mit Sperren/Einschränkungen", "spt": "Sportanlagen", "guide": "Wegweiser"}
GRENZE = 0.02

def bloecke(idx, key):
    size = idx["cell"][key]; out = defaultdict(int)
    for cid, n in idx["zellen"].get(key, {}).items():
        ix, iy = map(int, cid.split("_"))
        out[(math.floor(ix * size + 1e-9), math.floor(iy * size + 1e-9))] += n
    return out

def main(alt, neu, bericht):
    n = json.load(open(neu))
    z = [f"# Vergleich Wanderkarte {n.get('version')}", ""]
    if alt == "-" or not alt:
        z.append("Erste Version – kein Vergleich möglich.")
        z.append(""); z.append("| Gruppe | Anzahl |"); z.append("|---|---|")
        for k, txt in GRUPPEN.items(): z.append(f"| {txt} | {n['elemente'].get(k, 0)} |")
        open(bericht, "w").write("\n".join(z) + "\n"); print("\n".join(z)); return 0
    a = json.load(open(alt))
    z.append(f"Vorher: Version {a.get('version')} ({a.get('erzeugt')}), jetzt: {n.get('version')} ({n.get('erzeugt')})"); z.append("")
    z.append("| Gruppe | vorher | jetzt | Änderung |"); z.append("|---|---|---|---|")
    for k, txt in GRUPPEN.items():
        va, vn = a["elemente"].get(k, 0), n["elemente"].get(k, 0)
        z.append(f"| {txt} | {va} | {vn} | {vn - va:+d} ({(vn - va) / va * 100 if va else 0:+.1f} %) |")
    auff = []
    for k in ("sac", "hike", "mtb", "hut", "pka"):
        if k not in a.get("cell", {}) or k not in n.get("cell", {}): continue
        ba, bn = bloecke(a, k), bloecke(n, k)
        for b, va in ba.items():
            vn = bn.get(b, 0)
            if va >= 300 and (va - vn) / va > GRENZE:
                auff.append(f"- {GRUPPEN[k]}: Gebiet {b[0]}–{b[0]+1}° O / {b[1]}–{b[1]+1}° N: {va} → {vn} ({(vn - va) / va * 100:.1f} %)")
    z.append("")
    if auff:
        z.append("## AUFFÄLLIG – nicht freigeschaltet"); z.append("In diesen Gebieten fehlen mehr als 2 % der Elemente. Bitte prüfen (z. B. Fehler in OSM, abgeschnittener Download)."); z += auff
    else:
        z.append("Keine Region verliert mehr als 2 %. In Ordnung.")
    open(bericht, "w").write("\n".join(z) + "\n"); print("\n".join(z))
    return 3 if auff else 0

if __name__ == "__main__": sys.exit(main(*sys.argv[1:4]))
