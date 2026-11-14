#!/usr/bin/env python3
"""Aufträge für „Vorschläge berechnen“: je Region und Teil ein Rechner. Aufruf: vorschlaege_plan.py <Regionen> <Teile> [Test-Rechteck]
Schreibt auftraege=<JSON> (für die Matrix des Ablaufs)."""
import json, math, sys
# Rechtecke W,S,E,N (wie karte/fotos_ki.py REGIONEN, auf 0,1° gerundet nach außen)
REGIONEN = {
    "Vorarlberg": (9.5, 46.8, 10.3, 47.6), "Tirol": (10.0, 46.6, 13.0, 47.8), "Salzburg": (12.0, 46.9, 14.0, 48.1),
    "Kärnten": (12.6, 46.3, 15.1, 47.2), "Südtirol": (10.3, 46.2, 12.5, 47.1), "Steiermark": (13.5, 46.6, 16.2, 47.9),
    "Oberösterreich": (12.7, 47.4, 15.0, 48.8), "Niederösterreich": (14.4, 47.4, 17.1, 49.1),
}
def kacheln(w, s, e, n, rand=0.25):
    """5°-Kacheln des Routenrechners (mit Rand für Startpunkte außerhalb des Rechtecks)"""
    out = set()
    for x in range(math.floor((w - rand) / 5) * 5, math.floor((e + rand) / 5) * 5 + 1, 5):
        for y in range(math.floor((s - rand) / 5) * 5, math.floor((n + rand) / 5) * 5 + 1, 5):
            out.add(f"{'E' if x >= 0 else 'W'}{abs(x)}_{'N' if y >= 0 else 'S'}{abs(y)}")
    return " ".join(sorted(out))
def main():
    regionen, teile, box = sys.argv[1], max(1, min(20, int(sys.argv[2] or 4))), (sys.argv[3] if len(sys.argv) > 3 else "").strip()
    auf = []
    if box:
        w, s, e, n = map(float, box.split(","))
        auf.append({"region": "Test", "box": box, "teil": 0, "von": 1, "kacheln": kacheln(w, s, e, n), "probe": f"{(w+e)/2:.4f},{(s+n)/2:.4f}|{(w+e)/2+0.01:.4f},{(s+n)/2+0.01:.4f}"})
    else:
        for r in [x.strip() for x in regionen.split(",") if x.strip()]:
            if r not in REGIONEN: sys.exit("unbekannte Region: " + r)
            w, s, e, n = REGIONEN[r]
            for t in range(teile):
                auf.append({"region": r, "box": f"{w},{s},{e},{n}", "teil": t, "von": teile, "kacheln": kacheln(w, s, e, n),
                            "probe": f"{(w+e)/2:.4f},{(s+n)/2:.4f}|{(w+e)/2+0.01:.4f},{(s+n)/2+0.01:.4f}"})
    print("auftraege=" + json.dumps(auf, ensure_ascii=False))
if __name__ == "__main__": main()
