#!/usr/bin/env python3
"""Lage der Seiten mit Hütten-Erwähnungen: GPX-Track der Tour oder Koordinate der Seite.
So wird geprüft, dass die Seite wirklich von DIESER Hütte spricht (zwei Sattelalpen in Vorarlberg, Allerweltsnamen).
Aufruf: lage.py <treffer.json> <lage.json>"""
import os, sys, re, json, concurrent.futures as cf
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import sammler, quellen

R_GPX = re.compile(r"""href=["']([^"']+\.gpx)["']""", re.I)
R_PT = re.compile(r"""<(?:trkpt|rtept|wpt)[^>]*?lat=["']([\d.]+)["'][^>]*?lon=["']([\d.]+)["']|<(?:trkpt|rtept|wpt)[^>]*?lon=["']([\d.]+)["'][^>]*?lat=["']([\d.]+)["']""")


def seite(u):
    raw, err = sammler.holen(u)
    if err or not raw: return u, None
    r = {"punkt": None, "track": []}
    m = quellen.R_LATLON.search(raw) or quellen.R_LONLAT.search(raw)
    if m:
        a, b = (float(m.group(1)), float(m.group(2))) if m.re is quellen.R_LATLON else (float(m.group(2)), float(m.group(1)))
        if 45 <= a <= 55.5 and 5 <= b <= 17.5: r["punkt"] = [round(b, 5), round(a, 5)]
    g = R_GPX.search(raw)
    if g:
        graw, gerr = sammler.holen(sammler.urllib.parse.urljoin(u, sammler.html.unescape(g.group(1))))
        if graw:
            pts = []
            for p in R_PT.finditer(graw):
                lat, lon = (p.group(1), p.group(2)) if p.group(1) else (p.group(4), p.group(3))
                pts.append([round(float(lon), 5), round(float(lat), 5)])
            r["track"] = pts[::3] if len(pts) > 3000 else pts
    return u, r


if __name__ == "__main__":
    t = json.load(open(sys.argv[1]))
    urls = sorted({s["url"] for l in t.values() for s in l})
    alt = json.load(open(sys.argv[2])) if os.path.exists(sys.argv[2]) else {}
    neu = [u for u in urls if u not in alt]
    print(len(urls), "Seiten,", len(neu), "neu zu lesen", flush=True)
    with cf.ThreadPoolExecutor(6) as ex:
        for i, (u, r) in enumerate(ex.map(seite, neu)):
            alt[u] = r
            if i % 100 == 0: print(i, flush=True)
    json.dump(alt, open(sys.argv[2], "w"))
    print("mit Track:", sum(1 for r in alt.values() if r and r["track"]), "mit Punkt:", sum(1 for r in alt.values() if r and r["punkt"]))
