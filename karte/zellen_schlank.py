#!/usr/bin/env python3
"""Zellen verschlanken, sobald die Wege als Vektorkacheln (wege.pmtiles) vorliegen:
- hike, mtb, minor, svc, lake: werden nicht mehr gebraucht (App zeichnet sie aus den Vektorkacheln) -> Ordner löschen
- sac: nur Klettersteige behalten (für das Klettersteig-Datenblatt der App), alles andere kommt aus den Vektorkacheln
Aufruf: zellen_schlank.py <zellen-ordner>"""
import sys, os, gzip, json, shutil
d = sys.argv[1]
for k in ("hike", "mtb", "minor", "svc", "lake"):
    p = os.path.join(d, k)
    if os.path.isdir(p): shutil.rmtree(p); print("entfernt:", k)
p = os.path.join(d, "sac"); behalten = geloescht = n = 0
if os.path.isdir(p):
    for f in os.listdir(p):
        if not f.endswith(".json.gz"): continue
        fp = os.path.join(p, f)
        with gzip.open(fp, "rt", encoding="utf-8") as fh: j = json.load(fh)
        els = [e for e in j.get("elements", []) if (e.get("tags") or {}).get("highway") == "via_ferrata" or "via_ferrata_scale" in (e.get("tags") or {})]
        n += len(els)
        if els:
            j["elements"] = els
            with gzip.open(fp, "wt", encoding="utf-8", compresslevel=9) as fh: fh.write(json.dumps(j, separators=(",", ":"), ensure_ascii=False))
            behalten += 1
        else:
            os.remove(fp); geloescht += 1
print(f"sac: {behalten} Zellen mit Klettersteigen ({n} Wege), {geloescht} Zellen entfernt")
