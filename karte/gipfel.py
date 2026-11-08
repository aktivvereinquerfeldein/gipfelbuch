#!/usr/bin/env python3
"""Gipfel und Jöcher mit Wichtigkeit wie bei bergfex (v1994).

bergfex teilt Gipfel nach Bedeutung ein (hoch / mittel / niedrig). Ein Abgleich mit den bergfex-Kacheln
(Vorarlberg, 05.11.2026) hat gezeigt: die Bedeutung hängt vor allem davon ab, wie weit der nächste höhere
Gipfel entfernt ist (Dominanz): hoch ab ca. 5 km. „Mittel“ passt am besten mit: Dominanz ab 2 km, oder Gipfelkreuz/
Gipfelbuch und Dominanz ab 0,5 km, oder eingetragene Schartenhöhe (prominence) ab 100 m. Jöcher: mittel nur bei mountain_pass=yes.

Aufruf:  python3 gipfel.py <gipfel.pbf> [<weitere.pbf> ...] <ausgabeordner>
Eingabe: OSM-Dateien, vorher mit osmium auf Gipfel/Jöcher gefiltert
         (osmium tags-filter X.pbf n/natural=peak,volcano,saddle,hill n/mountain_pass=yes).
Ausgabe: <ausgabeordner>/w.json      alle Gipfel mit Bedeutung hoch (ganzes Gebiet, für weiten Zoom)
         <ausgabeordner>/m_<ix>_<iy>.json  Gipfel mittel/hoch und wichtige Jöcher je 1°-Feld (ab Zoom 9,5)
         <ausgabeordner>/<ix>_<iy>.json  alle benannten Gipfel und Jöcher je 1°-Feld (ix = floor(Länge), iy = floor(Breite), ab Zoom 10,5)
         <ausgabeordner>/index.json  Liste der Felder, Stand, Anzahl
Zeile:   [Länge, Breite, Name, Höhe|null, Bedeutung 1-3, Art "p"/"s", osm_id, (bei hoher Bedeutung) Dominanz km]
"""
import sys, os, json, math, re, datetime
import osmium
import numpy as np

HOCH_KM, MITTEL_KM, KREUZ_KM, SCHARTE_M = 5.0, 2.0, 0.5, 100


def hoehe(v):
    if not v: return None
    m = re.match(r"^\s*(-?\d+(?:[.,]\d+)?)", v)
    if not m: return None
    try:
        h = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    if "ft" in v: h *= 0.3048
    return round(h) if -500 < h < 9000 else None


class H(osmium.SimpleHandler):
    def __init__(s):
        super().__init__(); s.p = {}

    def node(s, n):
        t = n.tags; nat = t.get("natural"); mp = t.get("mountain_pass") == "yes"
        if nat in ("peak", "volcano", "hill"): art = "p"
        elif nat == "saddle" or mp: art = "s"
        else: return
        name = t.get("name:de") or t.get("name") or ""
        kreuz = t.get("summit:cross") == "yes" or bool(t.get("summit:register"))
        s.p[n.id] = (n.location.lon, n.location.lat, name.strip(), hoehe(t.get("ele")), art, nat,
                     kreuz, mp, hoehe(t.get("prominence")))


def main():
    *quellen, aus = sys.argv[1:]
    h = H()
    for q in quellen:
        h.apply_file(q, locations=False)
    ids = list(h.p.keys()); P = h.p
    os.makedirs(aus, exist_ok=True)

    # Dominanz: Abstand zum nächsten höheren Gipfel (alle Gipfel mit Höhe, auch ohne Namen; Hügel zählen mit)
    gip = [i for i in ids if P[i][4] == "p" and P[i][3] is not None]
    lon = np.array([P[i][0] for i in gip]); lat = np.array([P[i][1] for i in gip]); ele = np.array([P[i][3] for i in gip], float)
    x = lon * 111.32 * np.cos(np.radians(lat)); y = lat * 111.32
    order = np.argsort(-ele)
    iso = {}
    # nach Höhe absteigend: für jeden Gipfel die schon gesehenen (höheren) prüfen – mit einem Raster für Geschwindigkeit
    zelle = 2.0; raster = {}
    for k in order:
        gx, gy = int(x[k] // zelle), int(y[k] // zelle); best = 1e9; r = 0
        while True:
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r: continue
                    for j in raster.get((gx + dx, gy + dy), ()):
                        if ele[j] > ele[k]:
                            d = math.hypot(x[j] - x[k], y[j] - y[k])
                            if d < best: best = d
            if best <= r * zelle or r * zelle > 60: break
            r += 1
        iso[gip[k]] = best if best < 1e9 else 99.0
        raster.setdefault((gx, gy), []).append(k)

    felder = {}; wichtig = []; n_imp = {1: 0, 2: 0, 3: 0}
    for i in ids:
        lo, la, name, e, art, nat, kreuz, mp, sch = P[i]
        if not name: continue
        if art == "p":
            if nat == "hill" and e is None: continue
            d = iso.get(i, 0.0)
            imp = 3 if d >= HOCH_KM else 2 if (d >= MITTEL_KM or (kreuz and d >= KREUZ_KM) or (sch or 0) >= SCHARTE_M) else 1
        else:
            imp = 2 if mp else 1
        row = [round(lo, 5), round(la, 5), name, e, imp, art, i]
        if art == "p" and imp >= 3: row.append(round(min(d, 99.0), 1))   # v2002: Dominanz in km für den weiten Zoom (wie bergfex nur die markantesten)
        felder.setdefault(f"{math.floor(lo)}_{math.floor(la)}", []).append(row)
        if imp >= 3 and art == "p": wichtig.append(row)
        if art == "p": n_imp[imp] += 1

    for k, rows in felder.items():
        rows.sort(key=lambda r: (-r[4], -(r[3] or 0)))
        with open(os.path.join(aus, k + ".json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
        with open(os.path.join(aus, "m_" + k + ".json"), "w", encoding="utf-8") as f:
            json.dump([r for r in rows if r[4] >= 2], f, ensure_ascii=False, separators=(",", ":"))
    wichtig.sort(key=lambda r: (-r[4], -(r[3] or 0)))
    with open(os.path.join(aus, "w.json"), "w", encoding="utf-8") as f:
        json.dump(wichtig, f, ensure_ascii=False, separators=(",", ":"))
    info = {"stand": datetime.date.today().isoformat(), "felder": sorted(felder), "gipfel": n_imp,
            "joecher": sum(1 for r in sum(felder.values(), []) if r[5] == "s"), "hoch_km": HOCH_KM, "mittel_km": MITTEL_KM, "kreuz_km": KREUZ_KM, "scharte_m": SCHARTE_M}
    with open(os.path.join(aus, "index.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
