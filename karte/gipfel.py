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
Zeile:   [Länge, Breite, Name, Höhe|null, Bedeutung 1-3, Art "p"/"s", osm_id, Dominanz km|null, Seite des Namens 0-3]
Seite (v2037, wie bergfex): Der Name steht dort, wo kein Weg gezeichnet ist – 0 unter dem Gipfel (wie bisher), 1 rechts, 2 links,
         3 darüber. Dafür optional: --wege <wege.pbf> (osmium tags-filter auf highway=path,footway,track,…). Ohne Wege immer 0.
"""
import sys, os, json, math, re, datetime
import osmium
import numpy as np

WEG_ARTEN = {"path", "footway", "track", "steps", "bridleway", "via_ferrata", "cycleway", "service", "unclassified", "residential",
             "tertiary", "secondary", "primary", "trunk"}
ZELLE_GRAD = 0.01    # Raster für Wegstücke rund um die Gipfel


def _clip_len(ax, ay, bx, by, x0, y0, x1, y1):
    """Länge des Stücks A-B innerhalb des Rechtecks (Liang-Barsky)."""
    dx, dy = bx - ax, by - ay; t0, t1 = 0.0, 1.0
    for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
        if p == 0:
            if q < 0: return 0.0
            continue
        r = q / p
        if p < 0:
            if r > t1: return 0.0
            if r > t0: t0 = r
        else:
            if r < t0: return 0.0
            if r < t1: t1 = r
    return max(0.0, t1 - t0) * math.hypot(dx, dy)


def namen_seite(lo, la, name, ele, imp, stuecke):
    """Beste Seite für den Namen: 0 unten, 1 rechts, 2 links, 3 oben – die Seite mit den wenigsten Wegen im Namensfeld.
    Gerechnet in Bildpunkten bei Zoom 14 und 15 (Schriftgrößen wie in der App), Wege als Stücke in Metern rund um den Gipfel."""
    if not stuecke: return 0
    gr = {3: 15, 2: 13.5}.get(imp, 12)
    zeilen = 2 if ele is not None else 1
    breite = max(len(name), len(str(ele or ""))) * 0.56 * gr
    hoehe = zeilen * 1.05 * gr
    kos = math.cos(math.radians(la))
    wert = [0.0, 0.0, 0.0, 0.0]
    for z in (14, 15):
        mpx = 156543.03 * kos / 2 ** z      # Meter je Bildpunkt
        g = 0.45 * gr; r = 2                 # Abstand zum Symbol, Rand
        felder = [(-breite / 2 - r, -g - hoehe - r, breite / 2 + r, -g + r),   # unten (Name unter dem Gipfel)
                  (0.7 * gr - r, -hoehe / 2 - r, 0.7 * gr + breite + r, hoehe / 2 + r),  # rechts
                  (-0.7 * gr - breite - r, -hoehe / 2 - r, -0.7 * gr + r, hoehe / 2 + r),  # links
                  (-breite / 2 - r, 0.55 * gr - r, breite / 2 + r, 0.55 * gr + hoehe + r)]  # oben
        for k, (x0, y0, x1, y1) in enumerate(felder):
            x0, y0, x1, y1 = x0 * mpx, y0 * mpx, x1 * mpx, y1 * mpx
            for ax, ay, bx, by in stuecke:
                wert[k] += _clip_len(ax, ay, bx, by, x0, y0, x1, y1) / mpx
    # unten bleibt, solange dort (fast) kein Weg liegt; sonst die erste fast freie Seite in der Reihenfolge rechts, links, oben
    tol = 6
    if wert[0] <= tol: return 0
    m = min(wert)
    for k in (1, 2, 3):
        if wert[k] <= m + tol and wert[0] - wert[k] >= tol: return k
    return 0


class W(osmium.SimpleHandler):
    """Wegstücke nur in Rasterzellen rund um benannte Gipfel sammeln (Meter relativ zur Zellmitte werden später gerechnet)."""
    def __init__(s, zellen):
        super().__init__(); s.z = zellen; s.st = {}

    def way(s, w):
        if w.tags.get("highway") not in WEG_ARTEN: return
        try:
            pts = [(n.location.lon, n.location.lat) for n in w.nodes]
        except osmium.InvalidLocationError:
            return
        for (alo, ala), (blo, bla) in zip(pts, pts[1:]):
            ks = {(math.floor(alo / ZELLE_GRAD), math.floor(ala / ZELLE_GRAD)), (math.floor(blo / ZELLE_GRAD), math.floor(bla / ZELLE_GRAD))}
            for k in ks:
                if k in s.z: s.st.setdefault(k, []).append((alo, ala, blo, bla))


def stuecke_um(st, lo, la, n=1):
    kos = math.cos(math.radians(la)); mx, my = 111320.0 * kos, 110574.0
    cx, cy = math.floor(lo / ZELLE_GRAD), math.floor(la / ZELLE_GRAD); out = set()
    for dx in range(-n, n + 1):
        for dy in range(-n, n + 1):
            for a in st.get((cx + dx, cy + dy), ()): out.add(a)
    return [((alo - lo) * mx, (ala - la) * my, (blo - lo) * mx, (bla - la) * my) for alo, ala, blo, bla in out]

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
    args = sys.argv[1:]; wege = None
    if "--wege" in args:
        i = args.index("--wege"); wege = args[i + 1]; del args[i:i + 2]
    *quellen, aus = args
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

    # v2050: Markantheit wie bergfex – wie hoch ist der Gipfel gegenüber dem höchsten Gipfel im Umkreis von 3 km (rel ≤ 0)
    # plus Abstand zum nächsten höheren Gipfel. pr = rel + 200·log2(1 + Abstand km). Kleine Buckel unter hohen Nachbarn
    # (Spießeck −478, Strahleck −516) kommen in der App später, markante (Falknis +208, Saulakopf +94) haben Vorrang.
    from scipy.spatial import cKDTree
    baum = cKDTree(np.column_stack([x, y]))
    nachbarn = baum.query_ball_point(np.column_stack([x, y]), r=3.0)
    pr = {}
    for k, nb in enumerate(nachbarn):
        rel = float(ele[k] - ele[nb].max())
        pr[gip[k]] = int(round(rel + 200 * math.log2(1 + min(iso.get(gip[k], 0.0), 99.0))))

    # v2037: Seite des Namens (wo kein Weg ist, wie bergfex)
    st = {}
    if wege:
        zellen = set()
        for i in ids:
            lo, la, name, e, art = P[i][:5]
            if art != "p" or not name: continue
            cx, cy = math.floor(lo / ZELLE_GRAD), math.floor(la / ZELLE_GRAD)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1): zellen.add((cx + dx, cy + dy))
        wh = W(zellen); wh.apply_file(wege, locations=True, idx="flex_mem"); st = wh.st
        print("Wegstücke um Gipfel:", sum(len(v) for v in st.values()), "in", len(st), "Zellen")
    n_seite = [0, 0, 0, 0]

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
        seite = 0
        if art == "p" and st:
            seite = namen_seite(lo, la, name, e, imp, stuecke_um(st, lo, la)); n_seite[seite] += 1
        if art == "p" and (seite or i in pr):
            while len(row) < 8: row.append(None)
            row.append(seite or None)
            if i in pr: row.append(pr[i])          # v2050: Feld 9 = Markantheit
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
            "joecher": sum(1 for r in sum(felder.values(), []) if r[5] == "s"), "hoch_km": HOCH_KM, "mittel_km": MITTEL_KM, "kreuz_km": KREUZ_KM, "scharte_m": SCHARTE_M,
            "namen_seite": {"unten": n_seite[0], "rechts": n_seite[1], "links": n_seite[2], "oben": n_seite[3]}}
    with open(os.path.join(aus, "index.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
