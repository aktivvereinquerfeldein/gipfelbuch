#!/usr/bin/env python3
"""Aufträge für „Vorschläge berechnen“ (ab 15.11.2026): Kartenstücke (0,1°) je Region, zusammenhängend in Pakete zu je <je_rechner>
Kartenstücken aufgeteilt (ein Rechner je Paket). Überlappende Regionen werden nur einmal gerechnet (die zuerst genannte gewinnt).
Aufruf: vorschlaege_plan.py <Regionen> <je_rechner> [Test-Rechteck]  →  auftraege=<JSON>"""
import json, math, sys
Z = 0.1
REGIONEN = {   # Rechtecke W,S,E,N
    "Vorarlberg": (9.5, 46.8, 10.3, 47.6), "Tirol": (10.0, 46.6, 13.0, 47.8), "Salzburg": (12.0, 46.9, 14.0, 48.1),
    "Kärnten": (12.6, 46.3, 15.1, 47.2), "Südtirol": (10.3, 46.2, 12.5, 47.1), "Steiermark": (13.5, 46.6, 16.2, 47.9),
    "Oberösterreich": (12.7, 47.4, 15.0, 48.8), "Niederösterreich": (14.4, 47.4, 17.1, 49.1),
    "Wien": (16.1, 48.1, 16.6, 48.4), "Burgenland": (16.0, 46.8, 17.2, 48.2),
}
def zellen(w, s, e, n):
    return [(x, y) for x in range(math.floor(w / Z + 1e-9), math.floor(e / Z - 1e-9) + 1) for y in range(math.floor(s / Z + 1e-9), math.floor(n / Z - 1e-9) + 1)]
def kacheln(w, s, e, n, rand=0.35):
    out = set()
    for x in range(math.floor((w - rand) / 5) * 5, math.floor((e + rand) / 5) * 5 + 1, 5):
        for y in range(math.floor((s - rand) / 5) * 5, math.floor((n + rand) / 5) * 5 + 1, 5):
            out.add(f"{'E' if x >= 0 else 'W'}{abs(x)}_{'N' if y >= 0 else 'S'}{abs(y)}")
    return " ".join(sorted(out))
def auftrag(region, teil, von, zs):
    w, s = min(x for x, _ in zs) * Z, min(y for _, y in zs) * Z
    e, n = (max(x for x, _ in zs) + 1) * Z, (max(y for _, y in zs) + 1) * Z
    box = f"{w:.1f},{s:.1f},{e:.1f},{n:.1f}"
    return {"region": region, "teil": teil, "von": von, "box": box, "zellen": ",".join(f"{x}_{y}" for x, y in zs),
            "kacheln": kacheln(w, s, e, n), "probe": f"{(w+e)/2:.4f},{(s+n)/2:.4f}|{(w+e)/2+0.01:.4f},{(s+n)/2+0.01:.4f}"}
def main():
    regionen = [r.strip() for r in sys.argv[1].split(",") if r.strip()]
    je = max(5, min(200, int(sys.argv[2] or 35))); box = (sys.argv[3] if len(sys.argv) > 3 else "").strip()
    auf = []
    if box:
        zs = zellen(*map(float, box.split(","))); auf.append(auftrag("Test", 0, 1, zs))
    else:
        schon = set()
        for r in regionen:
            if r not in REGIONEN: sys.exit("unbekannte Region: " + r)
            zs = [z for z in zellen(*REGIONEN[r]) if z not in schon]; schon.update(zs)
            if not zs: continue
            zs.sort(); pakete = [zs[i:i + je] for i in range(0, len(zs), je)]
            for i, p in enumerate(pakete): auf.append(auftrag(r, i, len(pakete), p))
    if len(auf) > 250: sys.exit(f"zu viele Aufträge ({len(auf)}), je_rechner erhöhen")
    print("auftraege=" + json.dumps(auf, ensure_ascii=False, separators=(",", ":")))
    print("regionen=" + ",".join(dict.fromkeys(a["region"] for a in auf)))   # nur Regionen mit eigenen Kartenstücken (Wien steckt z. B. in Niederösterreich)
    print(f"{len(auf)} Aufträge, {sum(len(a['zellen'].split(',')) for a in auf)} Kartenstücke", file=sys.stderr)
if __name__ == "__main__": main()
