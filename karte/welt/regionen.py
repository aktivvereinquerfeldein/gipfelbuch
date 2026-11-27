#!/usr/bin/env python3
"""Welt in Regionen: Liste der OSM-Ausschnitte (Geofabrik), mit denen die Wege und Kartenstücke wie in Österreich gerechnet werden.
Jede Region ist höchstens ca. MAX_GB groß (sonst ihre Unterregionen), damit zellen.py und wegekarte.mjs sie wie Österreich schaffen.
Überlappungen an den Grenzen sind gewollt (vollständige Wege an der Grenze); doppelte Objekte entfernt später das Zusammenfügen.
Ausgabe: regionen.tsv mit Name<TAB>Adresse<TAB>Bytes, größte zuerst.
Aufruf: regionen.py <ausgabe.tsv> [--max-gb 1.2] [--nur id1,id2]"""
import json, sys, time, argparse, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("aus"); ap.add_argument("--max-gb", type=float, default=1.2); ap.add_argument("--nur", default="")
a = ap.parse_args()
UA = {"User-Agent": "qfe-active-karte/1.0"}
idx = json.load(urllib.request.urlopen(urllib.request.Request("https://download.geofabrik.de/index-v1-nogeom.json", headers=UA), timeout=120))
R = {f["properties"]["id"]: f["properties"] for f in idx["features"]}
kinder = {}
for i, p in R.items(): kinder.setdefault(p.get("parent"), []).append(i)
# Sammelregionen, die nur andere Regionen doppeln (nicht rechnen)
GRUPPEN = {"alps", "britain-and-ireland", "dach", "us-midwest", "us-northeast", "us-pacific", "us-south", "us-west", "gcc-states"}

def groesse(i):
    u = R[i].get("urls", {}).get("pbf")
    if not u: return 0
    for v in range(4):
        try:
            r = urllib.request.urlopen(urllib.request.Request(u, method="HEAD", headers=UA), timeout=60)
            return int(r.headers.get("Content-Length") or 0)
        except Exception:
            time.sleep(3 * (v + 1))
    return 0

aus = []
def gehe(i):
    if i in GRUPPEN: return
    k = [c for c in kinder.get(i, []) if c not in GRUPPEN]
    b = groesse(i); time.sleep(0.2)
    if k and (b == 0 or b > a.max_gb * 1e9):
        for c in k: gehe(c)
    elif b:
        aus.append((i, R[i]["urls"]["pbf"], b))
    else:
        print("ohne Datei:", i, file=sys.stderr)

if a.nur:
    for i in a.nur.split(","): aus.append((i, R[i]["urls"]["pbf"], groesse(i)))
else:
    for top in [i for i, p in R.items() if not p.get("parent")]: gehe(top)
aus.sort(key=lambda t: -t[2])
with open(a.aus, "w") as f:
    # Name ohne „/“ (Geofabrik-IDs wie „us/texas“), sonst fehlen Ordner für Download und Protokoll
    for i, u, b in aus: f.write(f"{i.replace('/', '-')}\t{u}\t{b}\n")
print(f"{len(aus)} Regionen, zusammen {sum(t[2] for t in aus)/1e9:.1f} GB, größte {aus[0][0]} {aus[0][2]/1e9:.2f} GB")
