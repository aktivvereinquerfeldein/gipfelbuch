#!/usr/bin/env python3
"""Höhenkacheln (Zoom 12, Terrarium-PNG) für ein Rechteck vorab auf den Rechner laden (Ablauf „Vorschläge berechnen“).
Aufruf: vorschlaege_dem.py <W,S,E,N> <Ordner>   – mit 0,45° Rand (Startpunkte bis gut 20 km um das Kartenstück). Quelle: eigener Server /dem/, sonst AWS Terrain Tiles.
Bricht ab (Exit 1), wenn mehr als 2 % der Kacheln fehlen – ohne Höhen wären die Startpunkte falsch."""
import concurrent.futures as cf, math, os, sys, time, urllib.request
W, S, E, N = map(float, sys.argv[1].split(",")); D = sys.argv[2]; Z = 12; R = 0.45
QUELLEN = ["https://gipfelbuch.quer-feld-ein.workers.dev/dem/{z}/{x}/{y}.png", "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"]
n = 2 ** Z
tx = lambda lon: int((lon + 180) / 360 * n)
ty = lambda lat: int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
jobs = [(x, y) for x in range(tx(W - R), tx(E + R) + 1) for y in range(ty(N + R), ty(S - R) + 1)]
def hole(xy):
    x, y = xy; f = f"{D}/{Z}/{x}/{y}.png"
    if os.path.exists(f) and os.path.getsize(f) > 100: return True
    os.makedirs(os.path.dirname(f), exist_ok=True)
    for v in range(6):
        for q in QUELLEN:
            try:
                b = urllib.request.urlopen(urllib.request.Request(q.format(z=Z, x=x, y=y), headers={"User-Agent": "qfe-active-vorschlaege"}), timeout=30).read()
                if len(b) > 100 and b[:4] == b"\x89PNG": open(f, "wb").write(b); return True
            except Exception: pass
        time.sleep(2 * (v + 1))
    return False
t0 = time.time()
with cf.ThreadPoolExecutor(16) as ex: ok = list(ex.map(hole, jobs))
fehlt = ok.count(False)
print(f"Höhenkacheln: {len(jobs)}, fehlen {fehlt}, {round(time.time() - t0)} s")
if fehlt > len(jobs) * 0.02: sys.exit(1)
