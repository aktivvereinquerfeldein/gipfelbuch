#!/usr/bin/env python3
"""Bergbahnen und Seilbahnen: Betriebszeiten (Prompt 5) – Erstsuche und Abgleich ohne KI.

Gebiet: DACH (Österreich, Deutschland, Schweiz, Liechtenstein) und Südtirol.
Ablauf (siehe .github/workflows/bergbahnen.yml):
  bergbahnen.py grenzen  <grenzen.osm.pbf> <grenzen.json>     Bundesländer/Kantone (4), Bezirke (6), Gemeinden (8)
  bergbahnen.py osm      <grenzen.json> <rel.pbf> <aus.json> <bahnen.pbf>...
                                                             Bahnen (Gondel, Sessel, Pendel, Standseil-, Zahnradbahn,
                                                             Schlepplift), Stationen, Skigebiete, Orte
  bergbahnen.py bestand  <aus.json> <ordner>                 Tal-/Bergstation mit Höhe, Anlagen, Vergleich mit dem
                                                             letzten Lauf -> <ordner>/bahnen.json
  bergbahnen.py quellen  <ordner>                            örtliche Betreiberquellen je Bahn: eigene Webseite der Bahn,
                                                             Station oder des Skigebiets (Kartendaten), Wikidata,
                                                             Mitgliederliste Seilbahnen Schweiz, karte/bb_quellen.txt
  bergbahnen.py lesen    <ordner>                            Betreiberseiten lesen (nur lesen, robots.txt, 1 Anfrage/s
                                                             je Seite), Unterseiten „Betriebszeiten“, „Öffnungszeiten“,
                                                             „Revision“, „Preise“ … ; Fortschritt wird laufend gespeichert
  bergbahnen.py auswerten <ordner>                           Fakten je Bahn (karte/bb_lesen.py), Sicherheitsstufe,
                                                             Prüfliste, Bericht, entscheidungen.json hat Vorrang
  bergbahnen.py db       <ordner>                            Tabelle gb_bergbahnen (braucht SUPABASE_SERVICE_KEY)

Regeln (Marcel, Block A und Prompt 5):
- Nur lesen: nirgends anmelden, keine Formulare, robots.txt, höchstens 1 Anfrage pro Sekunde und Seite.
- Nur Fakten (Daten, Zeiten, Ja/Nein, Preise). Keine Texte oder Bilder übernehmen; die kurzen Zeilen in der Prüfliste
  dienen nur zum Prüfen und landen nie in der App.
- Webseite der Bahn hat Vorrang; Kartendaten (OSM-Öffnungszeiten) zählen nicht als Quelle.
- Sicherheit: Betreiber-/offizielle Seite mit eindeutiger Zuordnung = bestaetigt; unsichere Zuordnung, Widerspruch ohne
  klare neueste Angabe oder veraltet (Saison älter als 2 Jahre) = unklar. Bei Widerspruch gilt die neueste offizielle Angabe.
- Nichts löschen: Verschwundenes = status nicht_mehr_gefunden.
"""
import concurrent.futures as cf, datetime, gzip, hashlib, html, io, json, math, os, re, subprocess, sys, tempfile, threading, time
import urllib.parse, urllib.request, urllib.robotparser
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sammler, bb_lesen as L

HIER = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HIER)
HEUTE = datetime.date.today()
H = HEUTE.isoformat()
UA = sammler.UA
AW = {"cable_car": "seilbahn", "gondola": "gondelbahn", "mixed_lift": "gondelbahn", "chair_lift": "sessellift",
      "drag_lift": "schlepplift", "t-bar": "schlepplift", "j-bar": "schlepplift", "platter": "schlepplift"}
HAUPT = {"seilbahn", "gondelbahn", "sessellift", "standseilbahn", "zahnradbahn"}
LANDNAME = {"AT": "Österreich", "DE": "Deutschland", "CH": "Schweiz", "LI": "Liechtenstein", "IT": "Südtirol"}
# Seiten, die keine Betreiberquelle sind (nur Hinweis oder gar nicht)
NICHT_QUELLE = re.compile(r"(bergfex|outdooractive|komoot|skiresort|skiinfo|snow-?online|wikipedia|wikimedia|facebook|instagram|youtube|google|"
                          r"tripadvisor|booking|holidaycheck|alpenvereinaktiv|openstreetmap|osm\.org|lift-?world|remontees-mecaniques|seilbahn-?news|"
                          r"funimag|seilbahnen\.at|wko\.at|seilbahnen\.org|seilbahnen\.de|liftdatenbank)", re.I)

def lade(p, leer):
    try:
        if p.endswith(".gz"):
            with gzip.open(p, "rt", encoding="utf-8") as f: return json.load(f)
        with open(p, encoding="utf-8") as f: return json.load(f)
    except Exception: return leer
def speichern(p, d, gz=False):
    tmp = p + ".tmp"
    if gz:
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f: json.dump(d, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    else:
        with open(tmp, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=0, sort_keys=True)
    os.replace(tmp, p)
def hav(lon1, lat1, lon2, lat2):
    R = 6371000; p1, p2 = math.radians(lat1), math.radians(lat2); dp = p2 - p1; dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(1, a)))
def domain(u):
    try: n = urllib.parse.urlparse(u if "://" in u else "https://" + u).netloc.lower()
    except Exception: return ""
    n = n.split("@")[-1].split(":")[0]
    return n[4:] if n.startswith("www.") else n
def url_norm(u):
    u = (u or "").strip().split()[0] if (u or "").strip() else ""
    if not u: return ""
    if not re.match(r"^https?://", u, re.I): u = "https://" + u.lstrip("/")
    return u

# ---------------------------------------------------------------- Grenzen
def grenzen(pbf, aus):
    import osmium
    from osmium.geom import WKBFactory
    wkb = WKBFactory(); out = lade(aus, [])
    class Hd(osmium.SimpleHandler):
        def area(s, a):
            t = {x.k: x.v for x in a.tags}
            if t.get("boundary") != "administrative": return
            lv = t.get("admin_level"); iso = t.get("ISO3166-2") or ""
            if lv == "2" and t.get("ISO3166-1") == "LI": lv, iso = "4", "LI"
            if iso == "IT-BZ": lv = "4"
            if lv not in ("4", "6", "8"): return
            if lv == "4" and not (iso[:3] in ("AT-", "CH-", "DE-") or iso in ("IT-BZ", "LI")): return
            try: out.append({"lv": lv, "name": t.get("name:de") or t.get("name"), "iso": iso, "wkb": wkb.create_multipolygon(a)})
            except Exception: pass
    Hd().apply_file(pbf, locations=True, idx="flex_mem")
    json.dump(out, open(aus, "w")); print(f"{len(out)} Grenzen")

class Finder:
    def __init__(s, path):
        from shapely import wkb as W
        from shapely.strtree import STRtree
        s.lv = {}
        for lv in ("4", "6", "8"):
            r = [(x["name"], x["iso"], W.loads(x["wkb"], hex=True)) for x in json.load(open(path)) if x["lv"] == lv]
            s.lv[lv] = (r, STRtree([g for _, _, g in r]) if r else None)
    def __call__(s, lon, lat, lv="4"):
        from shapely.geometry import Point
        r, tree = s.lv[lv]
        if tree is None: return None, None
        p = Point(lon, lat)
        for i in tree.query(p):
            n, iso, g = r[int(i)]
            if g.contains(p): return n, iso
        return None, None
def land_aus(iso):
    if iso == "LI": return "LI"
    if iso == "IT-BZ": return "IT"
    return (iso or "")[:2] or None

# ---------------------------------------------------------------- Kartendaten
TAGS = re.compile(r"^(name|name:de|operator|website|url|contact:website|wikidata|opening_hours|aerialway:.*|ele|access|bicycle|dog|seasonal|"
                  r"railway|rack|aerialway|usage|service|disused.*|abandoned.*|razed.*|construction|lifecycle|ref|description)$")
def osm(grenzfile, relpbf, aus, pbfs):
    import osmium
    from osmium.geom import WKBFactory
    from shapely import wkb as W
    wkb = WKBFactory(); fnd = Finder(grenzfile)
    relname = {}
    class R(osmium.SimpleHandler):
        def relation(s, r):
            t = {x.k: x.v for x in r.tags}
            if t.get("route") in ("train", "railway", "funicular", "tram") and (t.get("name") or t.get("ref")):
                for m in r.members:
                    if m.type == "w": relname.setdefault(m.ref, (t.get("name:de") or t.get("name") or t.get("ref"), r.id, t.get("operator"), t.get("website") or t.get("url"), t.get("wikidata")))
    if relpbf and os.path.exists(relpbf): R().apply_file(relpbf)
    bahnen, still, stationen, gebiete, orte = [], [], [], [], []
    class Hd(osmium.SimpleHandler):
        def node(s, n):
            t = {x.k: x.v for x in n.tags}
            if t.get("aerialway") == "station":
                stationen.append({"lon": round(n.location.lon, 6), "lat": round(n.location.lat, 6), "name": t.get("name"), "ele": t.get("ele"),
                                  "web": t.get("website") or t.get("url") or t.get("contact:website"), "op": t.get("operator"), "wd": t.get("wikidata")})
            elif t.get("place") in ("city", "town", "village", "hamlet", "locality", "isolated_dwelling") and t.get("name") and 5.9 <= n.location.lon <= 10.6 and 45.8 <= n.location.lat <= 47.85:
                orte.append((t["name"], round(n.location.lon, 5), round(n.location.lat, 5)))
        def way(s, w):
            t = {x.k: x.v for x in w.tags}
            aw = t.get("aerialway"); rw = t.get("railway")
            art = AW.get(aw)
            if not art and rw == "funicular": art = "standseilbahn"
            if not art and rw in ("rail", "narrow_gauge", "light_rail") and t.get("rack") and t.get("rack") != "no": art = "zahnradbahn"
            weg = any(k.startswith(("disused:aerialway", "abandoned:aerialway", "razed:aerialway")) for k in t) or (aw and (t.get("disused") == "yes" or t.get("abandoned") == "yes"))
            if aw in ("goods", "zip_line", "magic_carpet", "rope_tow", "station", "pylon"): return
            if not art and not weg: return
            try: pts = [(round(nd.lon, 6), round(nd.lat, 6)) for nd in w.nodes if nd.location.valid()]
            except Exception: return
            if len(pts) < 2: return
            m = pts[len(pts) // 2]
            reg, iso = fnd(m[0], m[1], "4")
            if not reg: return
            name = t.get("name:de") or t.get("name") or ""
            rel = relname.get(w.id)
            if art in ("zahnradbahn", "standseilbahn") and rel: name = rel[0] or name
            z = {"typ": "way", "id": w.id, "name": name, "art": art, "aw": aw or rw, "region": reg, "iso": iso, "land": land_aus(iso),
                 "bezirk": fnd(m[0], m[1], "6")[0], "gemeinde": fnd(m[0], m[1], "8")[0], "pts": pts,
                 "tags": {k: v for k, v in t.items() if TAGS.match(k)}}
            if rel: z["rel"] = list(rel)
            if weg and not art: still.append(z)
            elif t.get("access") in ("private", "no") or t.get("aerialway:access") in ("private", "no"): return
            elif t.get("usage") in ("industrial", "military") or t.get("service") == "siding": return
            else: bahnen.append(z)
        def area(s, a):
            t = {x.k: x.v for x in a.tags}
            if not (t.get("landuse") == "winter_sports" or t.get("site") == "piste" or (t.get("leisure") == "sports_centre" and t.get("sport") == "skiing")): return
            web = t.get("website") or t.get("url") or t.get("contact:website")
            if not (web or t.get("operator") or t.get("name") or t.get("wikidata")): return
            try:
                g = W.loads(wkb.create_multipolygon(a), hex=True).simplify(0.0005)
                gebiete.append({"name": t.get("name"), "web": web, "op": t.get("operator"), "wd": t.get("wikidata"), "wkb": g.wkb_hex, "bbox": [round(x, 5) for x in g.bounds]})
            except Exception: pass
    h = Hd()
    for p in pbfs: h.apply_file(p, locations=True, idx="flex_mem")
    json.dump({"bahnen": bahnen, "still": still, "stationen": stationen, "gebiete": gebiete, "orte": orte}, open(aus, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{len(bahnen)} Bahnen/Lifte, {len(still)} stillgelegte, {len(stationen)} Stationen, {len(gebiete)} Skigebiete, {len(orte)} Orte")

# ---------------------------------------------------------------- Höhen (AWS Terrain Tiles, wie das Höhenmodell der App)
def hoehen(punkte, z=12):
    """punkte: [(lon,lat)] -> [m oder None]"""
    from PIL import Image
    def kachel(lon, lat):
        n = 2 ** z; x = (lon + 180) / 360 * n; y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
        return int(x), int(y), int((x % 1) * 256), int((y % 1) * 256)
    gruppen = defaultdict(list)
    for i, (lon, lat) in enumerate(punkte):
        x, y, px, py = kachel(lon, lat); gruppen[(x, y)].append((i, px, py))
    out = [None] * len(punkte)
    def eine(k):
        x, y = k
        for v in range(3):
            try:
                req = urllib.request.Request(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png", headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=40) as r: im = Image.open(io.BytesIO(r.read())).convert("RGB")
                for i, px, py in gruppen[k]:
                    rr, gg, bb = im.getpixel((min(255, px), min(255, py)))
                    out[i] = round(rr * 256 + gg + bb / 256 - 32768)
                return
            except Exception: time.sleep(2 + v * 3)
    with cf.ThreadPoolExecutor(16) as ex: list(ex.map(eine, list(gruppen)))
    return out

# ---------------------------------------------------------------- Bestand
def schluessel(b): return ("r" if b["typ"] == "relation" else "w") + str(b["id"])
def bestand(ausfile, ordner):
    d = json.load(open(ausfile, encoding="utf-8"))
    roh = d["bahnen"]
    # Zahnrad- und Standseilbahnen: Abschnitte gleichen Namens (oder gleicher Strecke) zu einer Bahn zusammenfassen
    einzeln, schienen = [], defaultdict(list)
    for b in roh:
        if b["art"] in ("zahnradbahn", "standseilbahn"):
            if b.get("rel") or b["name"]: schienen[(b["art"], (b.get("rel") or [None, None])[1] or L.norm(b["name"]))].append(b)
        else: einzeln.append(b)
    for (art, k), teile in schienen.items():
        # Teile, die weiter als 3 km auseinander liegen, sind verschiedene Bahnen (gleicher Name, anderer Ort)
        gruppen = []
        for t in sorted(teile, key=lambda x: x["id"]):
            for g in gruppen:
                if min(hav(*p, *q) for p in (t["pts"][0], t["pts"][-1]) for q in g["enden"]) < 3000:
                    g["teile"].append(t); g["enden"] += [t["pts"][0], t["pts"][-1]]; break
            else: gruppen.append({"teile": [t], "enden": [t["pts"][0], t["pts"][-1]]})
        for g in gruppen:
            e = g["enden"]; best = max(((p, q) for p in e for q in e), key=lambda pq: hav(*pq[0], *pq[1]))
            t0 = min(g["teile"], key=lambda x: x["id"]); rel = t0.get("rel")
            z = dict(t0); z["pts"] = [best[0], best[1]]; z["ways"] = [t["id"] for t in g["teile"]]
            if rel and len(gruppen) == 1: z["typ"], z["id"] = "relation", rel[1]
            z["name"] = re.sub(r"^[A-Z]{0,4}\s?\d{1,4}:\s*", "", z["name"] or "")
            tg = dict(t0["tags"])
            if rel:
                if rel[2]: tg.setdefault("operator", rel[2])
                if rel[3]: tg.setdefault("website", rel[3])
                if rel[4]: tg.setdefault("wikidata", rel[4])
            z["tags"] = tg; einzeln.append(z)
    gesehen = set(); bahnen = []
    for b in einzeln:
        k = (b["typ"], b["id"])
        if k not in gesehen: gesehen.add(k); bahnen.append(b)
    # Höhen: Station mit ele in der Nähe, sonst Höhenmodell
    st = d["stationen"]; gitter = defaultdict(list)
    for s in st: gitter[(round(s["lon"], 2), round(s["lat"], 2))].append(s)
    def station_bei(lon, lat, maxm=80):
        best, bd = None, maxm
        for dx in (-0.01, 0, 0.01):
            for dy in (-0.01, 0, 0.01):
                for s in gitter.get((round(lon + dx, 2), round(lat + dy, 2)), []):
                    dd = hav(lon, lat, s["lon"], s["lat"])
                    if dd < bd: best, bd = s, dd
        return best
    punkte = []
    for b in bahnen: punkte += [b["pts"][0], b["pts"][-1]]
    dem = hoehen(punkte)
    def ele_aus(s):
        try: return round(float(re.sub(r"[^\d.]", "", (s or "").split(";")[0].replace(",", "."))))
        except Exception: return None
    for i, b in enumerate(bahnen):
        a, e = b["pts"][0], b["pts"][-1]
        sa, se = station_bei(*a), station_bei(*e)
        ha = ele_aus(sa and sa["ele"]) or dem[2 * i]; he = ele_aus(se and se["ele"]) or dem[2 * i + 1]
        b["hq"] = "Kartendaten/Höhenmodell"
        if ha is not None and he is not None and ha > he: a, e, sa, se, ha, he = e, a, se, sa, he, ha
        b["tal"] = {"lon": a[0], "lat": a[1], "ele": ha, "name": sa and sa["name"], "web": sa and sa["web"], "op": sa and sa["op"], "wd": sa and sa["wd"]}
        b["berg"] = {"lon": e[0], "lat": e[1], "ele": he, "name": se and se["name"], "web": se and se["web"], "op": se and se["op"], "wd": se and se["wd"]}
        b["key"] = schluessel(b)
        b["laenge"] = round(sum(hav(*b["pts"][k - 1], *b["pts"][k]) for k in range(1, len(b["pts"]))))
        b["mitte"] = [round((a[0] + e[0]) / 2, 6), round((a[1] + e[1]) / 2, 6)]
        del b["pts"]
    # Skigebiete: kleinstes Gebiet, das Tal- oder Bergstation enthält
    from shapely import wkb as W
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    geb = [(g, W.loads(g["wkb"], hex=True)) for g in d["gebiete"]]
    tree = STRtree([x[1] for x in geb]) if geb else None
    for b in bahnen:
        b["gebiet"] = None
        if not tree: continue
        best = None
        for p in (Point(b["tal"]["lon"], b["tal"]["lat"]), Point(b["berg"]["lon"], b["berg"]["lat"])):
            for i in tree.query(p):
                g, poly = geb[int(i)]
                if poly.contains(p) and (best is None or poly.area < best[1]): best = (g, poly.area)
        if best: b["gebiet"] = {k: best[0][k] for k in ("name", "web", "op", "wd")}
    # Anlagen (Gruppe zusammenhängender Bahnen): Stationen höchstens 600 m auseinander oder gleiches Skigebiet
    eltern = {b["key"]: b["key"] for b in bahnen}
    def wurzel(k):
        while eltern[k] != k: eltern[k] = eltern[eltern[k]]; k = eltern[k]
        return k
    def verbinde(a, b_): eltern[wurzel(a)] = wurzel(b_)
    g2 = defaultdict(list)
    for b in bahnen:
        for s in (b["tal"], b["berg"]): g2[(round(s["lon"] / 0.01), round(s["lat"] / 0.01))].append((b["key"], s["lon"], s["lat"]))
    for (gx, gy), lst in g2.items():
        nb = [x for dx in (-1, 0, 1) for dy in (-1, 0, 1) for x in g2.get((gx + dx, gy + dy), [])]
        for k1, lo1, la1 in lst:
            for k2, lo2, la2 in nb:
                if k1 < k2 and hav(lo1, la1, lo2, la2) < 600: verbinde(k1, k2)
    nachg = defaultdict(list)
    for b in bahnen:
        if b["gebiet"] and (b["gebiet"]["name"] or b["gebiet"]["web"]): nachg[(b["gebiet"]["name"], b["gebiet"]["web"])].append(b["key"])
    for ks in nachg.values():
        for k in ks[1:]: verbinde(ks[0], k)
    for b in bahnen: b["anlage"] = wurzel(b["key"])
    bahnen.sort(key=lambda b: (b["land"] or "", b["region"] or "", b["bezirk"] or "", b["key"]))
    # Vergleich mit dem letzten Lauf
    alt = {b["key"]: b for b in lade(os.path.join(ordner, "bahnen.json"), [])}
    neu = [b["key"] for b in bahnen if b["key"] not in alt]
    weg = [k for k in alt if k not in {b["key"] for b in bahnen}]
    speichern(os.path.join(ordner, "bahnen.json"), bahnen)
    speichern(os.path.join(ordner, "bestand-aenderung.json"), {"neu": neu, "weg": weg, "alt_anzahl": len(alt), "datum": H,
               "still": [{"key": schluessel(s), "name": s["name"], "region": s["region"], "gemeinde": s["gemeinde"], "lon": s["pts"][0][0], "lat": s["pts"][0][1]} for s in d["still"]]})
    c = Counter((b["land"], b["art"]) for b in bahnen)
    print(f"{len(bahnen)} Bahnen ({sum(1 for b in bahnen if b['art'] in HAUPT)} ohne Schlepplifte), {len({b['anlage'] for b in bahnen})} Anlagen; neu {len(neu) if alt else '-'}, nicht mehr gefunden {len(weg)}")
    print(dict(sorted(c.items())))

# ---------------------------------------------------------------- Quellen
def bb_quellen():
    """karte/bb_quellen.txt: Land;Region;Betreiber;lat,lon;Radius km;Adresse [Adresse …]  (# = Kommentar)"""
    out = []
    p = os.path.join(HIER, "bb_quellen.txt")
    if not os.path.exists(p): return out
    for z in open(p, encoding="utf-8"):
        z = z.strip()
        if not z or z.startswith("#"): continue
        t = [x.strip() for x in z.split(";")]
        if len(t) < 6: continue
        try: la, lo = [float(x) for x in t[3].split(",")]; r = float(t[4] or 5)
        except Exception: continue
        out.append({"land": t[0], "region": t[1], "name": t[2], "lat": la, "lon": lo, "r": r, "urls": [url_norm(u) for u in t[5].split()]})
    return out

def wikidata_webseiten(qids):
    out = {}
    qids = sorted({q for q in qids if q and re.fullmatch(r"Q\d+", q)})
    for i in range(0, len(qids), 50):
        u = "https://www.wikidata.org/w/api.php?action=wbgetentities&props=claims&format=json&ids=" + "|".join(qids[i:i + 50])
        for v in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=60) as r: j = json.load(r)
                for q, e in (j.get("entities") or {}).items():
                    for c in (e.get("claims") or {}).get("P856", []):
                        w = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
                        if w and c.get("rank") != "deprecated": out.setdefault(q, w)
                break
            except Exception: time.sleep(3 + v * 5)
        time.sleep(1)
    return out

def seilbahnen_schweiz():
    """Mitgliederliste von Seilbahnen Schweiz (Verband): Name, Ort, Webseite"""
    try:
        with urllib.request.urlopen(urllib.request.Request("https://www.seilbahnen.org/verband/ordentliche-mitglieder", headers={"User-Agent": UA}), timeout=60) as r:
            s = r.read().decode("utf-8", "replace")
    except Exception as e:
        print("Seilbahnen Schweiz nicht lesbar:", e); return []
    out = []
    # Nuxt-Daten: {"id":..,"name":..,"location":..,"url":..} – Werte stehen als Liste daneben; wir lesen die Dreiergruppen Name, Ort, Adresse
    for m in re.finditer(r'\d+,"([^"]{3,120})",(?:"([^"]{2,60})",)?"(https?://[^"]{6,120})"', s):
        name, ort, url = html.unescape(m.group(1)), m.group(2), m.group(3)
        if re.search(r"(seilbahnen\.org|a4e\.shop|azurewebsites|myswitzerland|atomic\.com|employerswetrust)", url) or url.rstrip("/") in ("https:", "http:"): continue
        out.append({"name": name, "ort": ort, "url": url})
    print(f"Seilbahnen Schweiz: {len(out)} Mitglieder mit Webseite")
    return out

def quellen(ordner, ausfile=None):
    bahnen = lade(os.path.join(ordner, "bahnen.json"), [])
    orte = defaultdict(list)
    op = os.path.join(ordner, "orte-ch.json.gz")
    if ausfile and os.path.exists(ausfile):
        ol = json.load(open(ausfile, encoding="utf-8"))["orte"]; speichern(op, ol, gz=True)
    else: ol = lade(op, [])
    for n, lo, la in ol: orte[L.norm(n)].append((lo, la))
    q = defaultdict(list)          # key -> [{url, art, sicher, woher}]
    def dazu(k, url, art, sicher, woher):
        url = url_norm(url)
        if not url or NICHT_QUELLE.search(domain(url)): return
        if any(domain(x["url"]) == domain(url) for x in q[k]): return
        q[k].append({"url": url, "art": art, "sicher": sicher, "woher": woher})
    wds = set()
    for b in bahnen:
        for wd in (b["tags"].get("wikidata"), b["tal"].get("wd"), b["berg"].get("wd"), (b.get("gebiet") or {}).get("wd")): wds.add(wd)
    wdw = wikidata_webseiten(wds)
    print(f"Wikidata: {len(wdw)} Webseiten")
    nach_anl = defaultdict(list)
    for b in bahnen: nach_anl[b["anlage"]].append(b)
    for b in bahnen:
        t = b["tags"]; k = b["key"]
        for w in (t.get("website"), t.get("url"), t.get("contact:website")): dazu(k, w, "betreiber", True, "Webseite der Bahn (Kartendaten)")
        if t.get("wikidata") in wdw: dazu(k, wdw[t["wikidata"]], "betreiber", True, "Webseite der Bahn (Wikidata)")
        for s in (b["tal"], b["berg"]):
            if s.get("web"): dazu(k, s["web"], "betreiber", True, "Webseite der Station (Kartendaten)")
            if s.get("wd") in wdw: dazu(k, wdw[s["wd"]], "betreiber", True, "Webseite der Station (Wikidata)")
        g = b.get("gebiet") or {}
        if g.get("web"): dazu(k, g["web"], "betreiber", True, "Webseite des Skigebiets (Kartendaten)")
        if g.get("wd") in wdw: dazu(k, wdw[g["wd"]], "betreiber", True, "Webseite des Skigebiets (Wikidata)")
    # Anlage: eine einzige Betreiber-Domain bei den Nachbarbahnen -> gilt für die ganze Anlage (sonst unsicher)
    for an, bs in nach_anl.items():
        doms = Counter(domain(x["url"]) for b in bs for x in q[b["key"]])
        ops = {L.norm(b["tags"].get("operator")) for b in bs if b["tags"].get("operator")}
        if len(doms) == 1:
            d0 = next(iter(doms)); u0 = next(x["url"] for b in bs for x in q[b["key"]] if domain(x["url"]) == d0)
            for b in bs:
                if not q[b["key"]]: dazu(b["key"], u0, "betreiber", len(ops) <= 1, "Webseite der Anlage (Nachbarbahn)")
        elif len(doms) > 1:
            for b in bs:
                if not q[b["key"]]:
                    for d0 in doms:
                        u0 = next(x["url"] for bb in bs for x in q[bb["key"]] if domain(x["url"]) == d0)
                        dazu(b["key"], u0, "betreiber", False, "Webseite einer Nachbarbahn (mehrere Betreiber)")
    # eigene Liste karte/bb_quellen.txt (Betreiber, Tourismusverband, Gemeinde) – im Umkreis
    for e in bb_quellen():
        for b in bahnen:
            if hav(e["lon"], e["lat"], b["mitte"][0], b["mitte"][1]) <= e["r"] * 1000:
                for u in e["urls"]: dazu(b["key"], u, "liste", True, f"Quellenliste: {e['name']}")
    # Seilbahnen Schweiz: Mitglied über Ort + Namensteil zuordnen
    if any(b["land"] in ("CH", "LI") for b in bahnen):
        for m in seilbahnen_schweiz():
            pos = orte.get(L.norm(m["ort"] or ""), [])
            if not pos: continue
            mt = set(L.tokens(m["name"])) | set(L.tokens(domain(m["url"]).split(".")[0].replace("-", " ")))
            kand = [b for b in bahnen if b["land"] in ("CH", "LI") and any(hav(lo, la, b["mitte"][0], b["mitte"][1]) <= 8000 for lo, la in pos)]
            for b in kand:
                bt = set(L.tokens(b["name"])) | set(L.tokens(b["tags"].get("operator") or "")) | set(L.tokens((b["tal"]["name"] or "") + " " + (b["berg"]["name"] or "")))
                sicher = bool(mt & bt) or any(hav(lo, la, b["tal"]["lon"], b["tal"]["lat"]) <= 1500 for lo, la in pos)
                if sicher or len(kand) <= 3: dazu(b["key"], m["url"], "verband", sicher, f"Seilbahnen Schweiz: {m['name']}")
    speichern(os.path.join(ordner, "quellen.json"), q)
    mit = sum(1 for b in bahnen if q.get(b["key"])); mitH = sum(1 for b in bahnen if q.get(b["key"]) and b["art"] in HAUPT)
    print(f"Quellen: {mit} von {len(bahnen)} Bahnen ({mitH} von {sum(1 for b in bahnen if b['art'] in HAUPT)} ohne Schlepplifte), {len({domain(x['url']) for v in q.values() for x in v})} Webseiten")

# ---------------------------------------------------------------- Seiten lesen
KW = re.compile(r"(betriebszeit|oeffnungszeit|öffnungszeit|offnungszeit|fahrzeit|fahrplan|revision|saisonzeit|betrieb|opening|orari|horaire|lift-?status|"
                r"anlagen|bahnen|lifte|sommer|winter|preise|tarif|ticket|bergbahn|seilbahn|gondel|sessel|hund|bike|rad\b|fahrrad|info|service|"
                r"planen|besuch|zeiten|saison|stagione|estate|inverno|prezzi|impianti|funivia|cabinovia|seggiovia|termine|daten)", re.I)
KW_STARK = re.compile(r"(betriebszeit|oeffnungszeit|öffnungszeit|offnungszeit|fahrzeit|fahrplan|revision|saisonzeit|opening|orari|horaire|lift-?status|"
                      r"betriebs?-?(und|&)?-?oeffnung|zeiten|preise|tarif|prezzi|termine)", re.I)
RAUS = re.compile(r"(job|karriere|stellen|impressum|datenschutz|privacy|agb|cookie|shop|warenkorb|cart|login|konto|account|newsletter|presse|news|blog|"
                  r"magazin|webcam|wetter|kontakt|anfrage|buchung|booking|gutschein|hochzeit|feier|seminar|tagung|event|veranstaltung|galerie|gallery|video|"
                  r"immobilien|unterkunft|hotel|zimmer|apartment|wohnung|schneehoehen|schneebericht|lawine|\.(jpg|jpeg|png|gif|webp|svg|zip|mp4|ics|xml)(\?|$))", re.I)
FREMD_SPRACHE = re.compile(r"/(en|it|fr|nl|pl|cs|cz|es|ru|hu|sk|da|sv|ja|zh|ko|sl|hr|ro|pt)(/|$|-)", re.I)

_rob, _zul, _sperre = {}, {}, threading.Lock()
def darf(url):
    u = urllib.parse.urlparse(url); host = u.scheme + "://" + u.netloc
    with _sperre: rp = _rob.get(host, "-")
    if rp == "-":
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(host + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r: rp.parse(r.read(500_000).decode("utf-8", "replace").splitlines())
        except urllib.error.HTTPError as e: rp = None if e.code >= 400 else rp
        except Exception: rp = None
        with _sperre: _rob[host] = rp
    return rp is None or rp.can_fetch(UA, url)
def warten(url):
    h = urllib.parse.urlparse(url).netloc
    with _sperre:
        w = 1.0 - (time.time() - _zul.get(h, 0)); _zul[h] = time.time() + max(0, w)
    if w > 0: time.sleep(w)
def holen(url, maxb=3_000_000, timeout=30):
    """-> (status, content_type, end_url, bytes, last_modified)"""
    if not darf(url): return -1, "", url, b"", None
    warten(url)
    h = {"User-Agent": UA, "Accept-Language": "de,de-AT;q=0.9,en;q=0.3", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.5"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type") or "", r.geturl(), r.read(maxb), r.headers.get("Last-Modified")
    except urllib.error.HTTPError as e: return e.code, "", url, b"", None
    except Exception as e: return 0, str(e)[:80], url, b"", None
def text_aus(raw, ctype, url):
    if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf") or raw[:5] == b"%PDF-":
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(raw); f.flush()
            try: t = subprocess.run(["pdftotext", "-q", f.name, "-"], capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
            except Exception: return "", [], []
        return "PDF", [re.sub(r"\s+", " ", z).strip() for z in t.split("\n") if z.strip()], []
    cs = re.search(r"charset=([\w-]+)", ctype or "", re.I) or re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4000], re.I)
    enc = (cs.group(1).decode() if isinstance(cs.group(1), bytes) else cs.group(1)) if cs else "utf-8"
    try: s = raw.decode(enc, "replace")
    except LookupError: s = raw.decode("utf-8", "replace")
    tm = re.search(r"<title[^>]*>(.*?)</title>", s, re.I | re.S)
    z, links = sammler.text_und_links(s)
    return (html.unescape(tm.group(1)).strip()[:150] if tm else ""), z, links
def lm_datum(lm):
    if not lm: return None
    try: return datetime.datetime.strptime(lm[5:16], "%d %b %Y").date().isoformat()
    except Exception: return None
def score(url, txt=""):
    p = urllib.parse.urlparse(url).path.lower() + " " + (txt or "").lower()
    if RAUS.search(urllib.parse.urlparse(url).path + "?" + (urllib.parse.urlparse(url).query or "")): return -9
    s = 3 * len(KW_STARK.findall(p)) + len(KW.findall(p))
    if FREMD_SPRACHE.search(urllib.parse.urlparse(url).path): s -= 4
    if url.lower().split("?")[0].endswith(".pdf"): s = s - 1 if KW_STARK.search(p) else -9
    return s
def sitemap_urls(root, maxn=4000):
    out, todo, gesehen = [], [], set()
    try:
        st, ct, _, raw, _ = holen(root + "/robots.txt", 300_000)
        todo += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", raw.decode("utf-8", "replace")) if st == 200 else []
    except Exception: pass
    if not todo: todo = [root + "/sitemap.xml", root + "/sitemap_index.xml"]
    n = 0
    while todo and n < 25 and len(out) < maxn:
        u = todo.pop(0)
        if u in gesehen: continue
        gesehen.add(u); n += 1
        st, ct, _, raw, _ = holen(u, 8_000_000)
        if st != 200 or not raw: continue
        if raw[:2] == b"\x1f\x8b":
            try: raw = gzip.decompress(raw)
            except Exception: continue
        s = raw.decode("utf-8", "replace")
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", s)
        if "<sitemapindex" in s:
            # deutsche/allgemeine Teil-Sitemaps zuerst, Bilder/Produkte/News weg
            subs = [x for x in locs if not re.search(r"(image|video|product|news|post|blog|event|job|tag|author|categor)", x, re.I)]
            todo += sorted(subs, key=lambda x: (bool(FREMD_SPRACHE.search(urllib.parse.urlparse(x).path)), len(x)))
        else: out += [html.unescape(x) for x in locs]
    return out[:maxn]

def domain_lesen(dom, start, namen, alt, maxs=26):
    """liest eine Betreiber-Webseite; namen = Bahn-Namen dieser Seite (für die Wahl der Unterseiten). -> dict"""
    erg = {"datum": H, "seiten": [], "fehler": None, "start": start}
    root_url = start[0]
    p = urllib.parse.urlparse(root_url); root = f"{p.scheme}://{p.netloc}"
    namen_t = {t for n in namen for t in L.tokens(n)}
    def sc(u, txt=""):
        s = score(u, txt)
        if s > -5 and namen_t and any(t in L.norm(urllib.parse.urlparse(u).path + " " + (txt or "")) for t in namen_t): s += 2
        return s
    kand = {}
    def kandidat(u, txt=""):
        u = urllib.parse.urljoin(root + "/", html.unescape(u)).split("#")[0]
        if not u.startswith("http") or domain(u) != dom and not domain(u).endswith("." + dom) and not dom.endswith("." + domain(u)): return
        s = sc(u, txt)
        if s >= 1: kand[u] = max(kand.get(u, -99), s)
    gelesen = set()
    def eine(u):
        if u in gelesen: return None
        gelesen.add(u)
        st, ct, end, raw, lm = holen(u)
        if st == -1: return {"url": u, "status": "robots"}
        if st != 200 or not raw: return {"url": u, "status": st or ct}
        if "html" not in ct and "pdf" not in ct and not u.lower().endswith(".pdf") and ct: return {"url": u, "status": "typ " + ct[:30]}
        titel, z, links = text_aus(raw, ct, end)
        hx = hashlib.sha1("\n".join(z).encode()).hexdigest()[:16]
        datz = [bool(L.daten_in(x) or re.search(r"\d{1,2}[:.]\d{2}|\d\s*uhr", x, re.I)) for x in z]
        if any(datz):
            idx = [i for i, v in enumerate(datz) if v]
            a, b_ = max(0, idx[0] - 40), min(len(z), idx[-1] + 25)
            zz = [x[:400] for x in z[a:b_]][:1500]
        else: zz = []
        for l in links: kandidat(l)
        return {"url": end, "titel": titel, "lm": lm_datum(lm), "hash": hx, "zeilen": zz, "status": 200, "links": links}
    # Startseiten
    for u in start[:4] + ([root + "/"] if root + "/" not in start else []):
        r = eine(u)
        if r is None: continue
        if r["status"] != 200:
            if not erg["seiten"]: erg["fehler"] = f"{u}: {r['status']}"
            continue
        r.pop("links", None); erg["seiten"].append(r)
    # Links der Startseite mit Linktext bewerten
    for u in sitemap_urls(root):
        if domain(u) == dom or domain(u).endswith("." + dom): kandidat(u)
    for runde in range(2):
        rest = sorted((u for u in kand if u not in gelesen), key=lambda u: -kand[u])
        for u in rest[:max(0, maxs - len(erg["seiten"]))] if runde == 0 else rest[:6]:
            if len(erg["seiten"]) >= maxs + 6: break
            r = eine(u)
            if r and r["status"] == 200:
                r.pop("links", None); erg["seiten"].append(r)
    if erg["seiten"]: erg["fehler"] = None
    # geändert gegenüber letztem Lauf?
    alth = {s["url"]: s.get("hash") for s in (alt or {}).get("seiten", [])}
    erg["geaendert"] = sorted(s["url"] for s in erg["seiten"] if s.get("zeilen") and alth.get(s["url"]) != s["hash"])
    return erg

def lesen(ordner, nur=None):
    bahnen = {b["key"]: b for b in lade(os.path.join(ordner, "bahnen.json"), [])}
    q = lade(os.path.join(ordner, "quellen.json"), {})
    seitenp = os.path.join(ordner, "seiten.json.gz")
    alles = lade(seitenp, {})
    doms = defaultdict(lambda: {"start": [], "namen": set(), "bahnen": 0})
    for k, lst in q.items():
        b = bahnen.get(k)
        if not b or (nur and b["iso"] not in nur and b["land"] not in nur): continue
        for x in lst:
            d = domain(x["url"]); e = doms[d]
            if x["url"] not in e["start"]: e["start"].append(x["url"])
            if b["name"]: e["namen"].add(b["name"])
            e["bahnen"] += 1 if b["art"] in HAUPT else 0.2
    nur_neu = os.environ.get("BB_NUR_NEU") == "1"
    todo = [d for d in sorted(doms, key=lambda d: -doms[d]["bahnen"]) if (alles.get(d) or {}).get("datum") != H and not (nur_neu and d in alles)]
    print(f"{len(doms)} Webseiten, davon {len(todo)} noch zu lesen")
    fertig, sp = 0, threading.Lock()
    t0 = time.time()
    def arbeit(d):
        nonlocal fertig
        e = doms[d]
        try: r = domain_lesen(d, sorted(e["start"], key=lambda u: (len(urllib.parse.urlparse(u).path) > 1, u)), e["namen"], alles.get(d))
        except Exception as ex: r = {"datum": H, "seiten": [], "fehler": str(ex)[:120], "start": e["start"]}
        with sp:
            alles[d] = r; fertig += 1
            if fertig % 25 == 0:
                speichern(seitenp, alles, gz=True)
                print(f"  {fertig}/{len(todo)} Webseiten gelesen ({round(time.time() - t0)} s)", flush=True)
    with cf.ThreadPoolExecutor(int(os.environ.get("BB_PARALLEL", "16"))) as ex: list(ex.map(arbeit, todo))
    speichern(seitenp, alles, gz=True)
    ok = sum(1 for d in doms if (alles.get(d) or {}).get("seiten"))
    print(f"Webseiten gelesen: {ok} von {len(doms)} lesbar, {sum(len(v.get('seiten', [])) for v in alles.values())} Seiten")

# ---------------------------------------------------------------- Auswerten
def iso(d): return datetime.date.fromisoformat(d) if d else None
def saison_von(p):
    """sommer/winter für Anzeige und Bericht"""
    if p.get("saison") in ("sommer", "winter"): return p["saison"]
    v, b = iso(p.get("von")) or iso(p["bis"]), iso(p["bis"])
    tage = [v + datetime.timedelta(days=k) for k in range(0, (b - v).days + 1, 7)] or [v]
    s_ = sum(1 for t in tage if 6 <= t.month <= 9); w_ = sum(1 for t in tage if t.month in (12, 1, 2, 3))
    return "sommer" if s_ > w_ else "winter" if w_ > s_ else None
def saison_jahr(p):
    v = iso(p.get("von")) or iso(p["bis"])
    return v.year - 1 if saison_von(p) == "winter" and v.month <= 6 else v.year
def perioden_zusammen(fs):
    """gleiche Zeiträume zusammenführen (Zeiten aus Berg- und Talfahrt-Tabelle ergänzen sich)"""
    nach = {}
    for f in fs:
        k = (f.get("von"), f["bis"])
        p = nach.setdefault(k, {"von": f.get("von"), "bis": f["bis"], "typ": f.get("typ") or "", "saison": f.get("saison"), "zeiten": {}, "url": f["url"]})
        for k2, v2 in (f.get("zeiten") or {}).items(): p["zeiten"].setdefault(k2, v2)
        if f.get("tage") and not p.get("tage"): p["tage"] = f["tage"]
        if f.get("typ") and not p["typ"]: p["typ"] = f["typ"]
        if f.get("saison") and not p["saison"]: p["saison"] = f["saison"]
    out = []
    for p in sorted(nach.values(), key=lambda p: (p["von"] or p["bis"], p["bis"])):
        if not p["zeiten"]: del p["zeiten"]
        if not p["saison"]: p["saison"] = saison_von(p)
        if not p["typ"]: del p["typ"]
        out.append(p)
    return out
def zeiten_aus_perioden(per):
    zs = [p["zeiten"] for p in per if p.get("zeiten")]
    if not zs: return None
    erste = sorted({z["erste"] for z in zs if z.get("erste")})
    lb = sorted({z["letzte_berg"] for z in zs if z.get("letzte_berg")})
    lt = sorted({z["letzte_tal"] for z in zs if z.get("letzte_tal")}) or sorted({z["betrieb_bis"] for z in zs if z.get("betrieb_bis")})
    r = {}
    if erste: r["erste"] = erste[-1]
    if lb: r["letzte_berg"] = lb[0]
    if lt: r["letzte_tal"] = lt[0]
    if not any(z.get("letzte_tal") for z in zs) and lt: r["betrieb_bis"] = True
    if len({json.dumps(z, sort_keys=True) for z in zs}) > 1: r["unterschiedlich"] = True
    if r.get("erste") and r.get("letzte_tal") and r["erste"] >= r["letzte_tal"]: return None
    return r or None

def auswerten(ordner):
    bahnen = lade(os.path.join(ordner, "bahnen.json"), [])
    q = lade(os.path.join(ordner, "quellen.json"), {})
    seiten = lade(os.path.join(ordner, "seiten.json.gz"), {})
    ent = lade(os.path.join(ordner, "entscheidungen.json"), {})
    alt = {r["key"]: r for r in lade(os.path.join(ordner, "ergebnis.json"), [])}
    bz = {b["key"]: b for b in bahnen}
    for k_, e_ in ent.items():
        if not k_.startswith("dom:"): continue
        d_ = k_[4:]
        for nm_, ks_ in (e_.get("alias") or {}).items():
            for kk in (ks_ if isinstance(ks_, list) else [ks_]):
                if kk in bz and not any(domain(x["url"]) == d_ for x in q.setdefault(kk, [])):
                    q[kk].append({"url": "https://" + d_ + "/", "art": "liste", "sicher": True, "woher": f"Entscheidung: „{nm_[:40]}“ auf {d_}"})
    dom_bahnen = defaultdict(list)
    for b in bahnen:
        for x in q.get(b["key"], []):
            if b not in dom_bahnen[domain(x["url"])]: dom_bahnen[domain(x["url"])].append(b)
    fakt_dom = {}
    for d, bs in dom_bahnen.items():
        e = seiten.get(d) or {}
        dt = set(L.tokens(d.split(".")[0].replace("-", " "))) | {t for t in L.norm(d.split(".")[0]).split() if len(t) >= 4}
        bobj = []
        # Namen, unter denen die Webseite eine Bahn führt (Entscheidung „alias“ oder automatisch bei fast gleichem Namen)
        regel_d = ent.get("dom:" + d) or {}
        for nm_, ks_ in (regel_d.get("alias") or {}).items():
            for k_ in (ks_ if isinstance(ks_, list) else [ks_]):
                o = L.Bahn(k_, nm_); bobj.append(o)
        for b in bs:
            if not b["name"]: continue
            o = L.Bahn(b["key"], b["name"])
            # Namensteil, der auch im Namen der Webseite steckt (z. B. „Zugspitze“ auf zugspitze.de), erkennt die Bahn nicht
            rest = [t for t in o.toks if not any(t == x or x.startswith(t) for x in dt)]
            if rest != o.toks:
                o.toks = rest; o.ganz = o.ganz if rest else ""
            if o.toks or o.ganz: bobj.append(o)
        # Überschriften „~Name“ einer ersten Lesung mit fast gleichem Namen einer Bahn dieser Seite automatisch zuordnen
        import difflib
        kompakt = {b["key"]: L.norm(b["name"]).replace(" ", "") for b in bs if b["name"]}
        for s_ in e.get("seiten", []):
            if not s_.get("zeilen"): continue
            for k in L.lesen(s_["zeilen"], bobj, s_["url"], s_.get("titel", "")):
                if not k.startswith("~"): continue
                ue = L.norm(k[1:]).replace(" ", "")
                wert = sorted(((difflib.SequenceMatcher(None, ue, v).ratio(), kk) for kk, v in kompakt.items()), reverse=True)
                if wert and wert[0][0] >= 0.88 and (len(wert) == 1 or wert[1][0] < wert[0][0] - 0.05 or kompakt[wert[1][1]].rstrip("i1234") == kompakt[wert[0][1]].rstrip("i1234")):
                    for sc, kk in wert:
                        if sc >= wert[0][0] - 0.05 and kompakt[kk].rstrip("i1234") == kompakt[wert[0][1]].rstrip("i1234"): bobj.append(L.Bahn(kk, k[1:]))
        res = defaultdict(list)
        for s_ in e.get("seiten", []):
            if not s_.get("zeilen"): continue
            r = L.lesen(s_["zeilen"], bobj, s_["url"], s_.get("titel", ""))
            sd = L.stand_datum(s_["zeilen"]) or s_.get("lm")
            stark = bool(KW_STARK.search(urllib.parse.urlparse(s_["url"]).path + " " + s_.get("titel", "")))
            start = len(urllib.parse.urlparse(s_["url"]).path.strip("/")) <= 3
            for k, fs in r.items():
                for f in fs: f["url"] = s_["url"]; f["qd"] = sd; f["stark"] = stark; f["start"] = start; res[k].append(f)
        fakt_dom[d] = res
    # Listen auf Tourismus- und Betreiberseiten („Spieljochbahn, Fügen“): Namen mit allen Bahnen im Umkreis abgleichen,
    # nur eindeutige Treffer (eine Bahn oder ihre Teilstrecken I/II)
    gitter = defaultdict(list)
    for b in bahnen:
        if b["art"] in HAUPT and b["name"]: gitter[(int(b["mitte"][0] * 4), int(b["mitte"][1] * 4))].append(b)
    for d, res in list(fakt_dom.items()):
        ue = [k[1:] for k, fs in res.items() if k.startswith("~") and any(f["art"] == "periode" for f in fs)]
        eigen_b = dom_bahnen[d]
        if not ue or not eigen_b: continue
        mx = sum(b["mitte"][0] for b in eigen_b) / len(eigen_b); my = sum(b["mitte"][1] for b in eigen_b) / len(eigen_b)
        kand = [b for gx in range(int(mx * 4) - 2, int(mx * 4) + 3) for gy in range(int(my * 4) - 2, int(my * 4) + 3) for b in gitter.get((gx, gy), [])
                if hav(mx, my, b["mitte"][0], b["mitte"][1]) <= 30000]
        neu = []
        for h in ue:
            hn = L.norm(re.split(r"[,(|:]", h)[0].replace("*", ""))
            if not hn or len(hn) < 5: continue
            def stamm_tok(n):
                # „Rüfikopfbahn“ passt auch zu „Rüfikopf Seilbahn“: Endung -bahn/-lift weg, wenn genug übrig bleibt
                return [re.sub(r"(seilbahn|sesselbahn|gondelbahn|bahn|lift|express)$", "", t) if len(re.sub(r"(seilbahn|sesselbahn|gondelbahn|bahn|lift|express)$", "", t)) >= 5 else t for t in L.tokens(n)]
            hw = set(hn.split())
            tr = [b for b in kand if L.tokens(b["name"]) and (L.nennt(hn, L.tokens(b["name"]), L.norm(b["name"])) or all(t in hw for t in stamm_tok(b["name"])))]
            if tr and len({L.ohne_nummer(b["name"]) for b in tr}) == 1:
                neu += [(b, h) for b in tr]
        if not neu: continue
        bobj = [L.Bahn(b["key"], b["name"]) for b in eigen_b if b["name"]] + [L.Bahn(b["key"], h) for b, h in neu]
        res = defaultdict(list)
        for s_ in (seiten.get(d) or {}).get("seiten", []):
            if not s_.get("zeilen"): continue
            r = L.lesen(s_["zeilen"], bobj, s_["url"], s_.get("titel", ""))
            sd = L.stand_datum(s_["zeilen"]) or s_.get("lm")
            stark = bool(KW_STARK.search(urllib.parse.urlparse(s_["url"]).path + " " + s_.get("titel", "")))
            start = len(urllib.parse.urlparse(s_["url"]).path.strip("/")) <= 3
            for k, fs in r.items():
                for f in fs: f["url"] = s_["url"]; f["qd"] = sd; f["stark"] = stark; f["start"] = start; res[k].append(f)
        fakt_dom[d] = res
        for b, h in neu:
            if not any(domain(x["url"]) == d for x in q.setdefault(b["key"], [])):
                q[b["key"]].append({"url": "https://" + d + "/", "art": "liste", "sicher": True, "woher": f"Liste auf {d} („{h[:40]}“)"})
                dom_bahnen[d].append(b)
    pruef = defaultdict(list); erg = []; widerspr = []
    unbekannt = defaultdict(dict)
    for d, res in fakt_dom.items():
        for k, fs in res.items():
            if k.startswith("~") and any(f["art"] in ("periode", "zeiten") for f in fs):
                ws = sorted({(f.get("von") or "…") + "–" + f["bis"] for f in fs if f["art"] == "periode"})
                unbekannt[d][k[1:]] = ", ".join(ws[:3]) or "nur Uhrzeiten"
    dom_regel = {k[4:]: v for k, v in ent.items() if k.startswith("dom:")}
    unten = HEUTE - datetime.timedelta(days=400); oben = HEUTE + datetime.timedelta(days=550)
    for b in bahnen:
        k = b["key"]; quellen_b = q.get(k, [])
        fak, quell, sicher_zu = [], [], True
        for x in quellen_b:
            d = domain(x["url"]); fd = fakt_dom.get(d) or {}
            haupt = [bb for bb in dom_bahnen[d] if bb["art"] in HAUPT]
            regel = dom_regel.get(d) or {}
            eigen = list(fd.get(k, []))
            seitenweit = []
            frage = [f for f in fd.get("?", []) if f["art"] in ("periode", "revision", "zeiten", "ganzjaehrig", "ruhetage", "schoenwetter", "rad", "hunde", "preis")]
            if frage:
                einzig = (len(haupt) == 1 and haupt[0]["key"] == k) or (not haupt and len(dom_bahnen[d]) == 1)
                if regel.get("seitenweit") == "alle" or k in (regel.get("seitenweit") or []): seitenweit = frage
                elif einzig:
                    # nur eine Bahn auf dieser Seite: Angaben ohne Abschnitt gelten für sie – aber bei widersprüchlichen Zeiträumen
                    # nur die Seiten „Betriebszeiten/Öffnungszeiten“, sonst Prüfliste
                    per = [f for f in frage if f["art"] == "periode"]
                    spannen = defaultdict(set)
                    for f in per: spannen[(saison_von(f), saison_jahr(f))].add((f["url"], f.get("von"), f["bis"]))
                    def uneinig(fs):
                        sp = defaultdict(set)
                        for f in fs:
                            if f["art"] == "periode" and f.get("typ") != "einzeltag": sp[(saison_von(f), saison_jahr(f))].add((min(g.get("von") or g["bis"] for g in fs if g["url"] == f["url"] and g["art"] == "periode"), f["url"]))
                        return any(len({a for a, _ in v}) > 1 for v in sp.values())
                    def ueberlappend(fs):
                        sp = defaultdict(list)
                        for f in fs:
                            if f["art"] == "periode" and f.get("typ") != "einzeltag" and f.get("von"): sp[(f["url"], saison_von(f), saison_jahr(f))].append((f["von"], f["bis"]))
                        for v in sp.values():
                            v = sorted(set(v))
                            for a_, b_ in zip(v, v[1:]):
                                if b_[0] <= a_[1] and not (a_[0] <= b_[0] and b_[1] <= a_[1]): return True
                        return False
                    if uneinig(frage) or ueberlappend(frage):
                        st_ = [f for f in frage if f["stark"]]
                        if st_ and not uneinig(st_) and not ueberlappend(st_): seitenweit = st_
                        else: pruef["mehrdeutig"].append((b, d, frage))
                    else: seitenweit = frage
                elif b["art"] in HAUPT and len(haupt) > 1 and not regel:
                    pruef["zuordnung:" + d].append(b)
            gebiet = [f for f in fd.get("*", []) if (f["stark"] or f.get("start")) and ((f["art"] == "periode" and saison_von(f) == "winter") or (f["art"] == "zeiten" and f.get("saison") == "winter") or (f["art"] == "revision" and len(haupt) <= 1))]
            # mehrere verschiedene Wintersaisonen auf derselben Seite (z. B. zwei Teilgebiete) = nicht eindeutig
            gs = defaultdict(set)
            for f in gebiet:
                if f["art"] == "periode" and f.get("typ") != "einzeltag": gs[f["url"]].add((f.get("von"), f["bis"]))
            gebiet = [f for f in gebiet if len(gs.get(f["url"], ())) <= 1 or f["art"] != "periode"]
            for f in eigen: f["zu"] = "bahn"
            for f in seitenweit: f["zu"] = "seite"
            for f in gebiet: f["zu"] = "gebiet"
            neu = eigen + seitenweit + gebiet
            if neu:
                fak += neu; quell.append(x)
                if not x["sicher"]: sicher_zu = False
        r = {"key": k, "name": b["name"], "art": b["art"], "land": b["land"], "region": b["region"], "bezirk": b["bezirk"], "gemeinde": b["gemeinde"],
             "quellen": [x["url"] for x in quellen_b]}
        per = [f for f in fak if f["art"] == "periode" and iso(f["bis"]) >= unten and iso(f.get("von") or f["bis"]) <= oben]
        # Widerspruch: gleiche Jahreszeit und gleiches Jahr, verschiedene Seiten mit anderem Beginn/Ende (> 3 Tage)
        for sk in {(saison_von(f), saison_jahr(f)) for f in per if f.get("typ") != "einzeltag" and saison_von(f)}:
            nachurl = defaultdict(list)
            for f in per:
                if f.get("typ") != "einzeltag" and (saison_von(f), saison_jahr(f)) == sk: nachurl[f["url"]].append(f)
            spannen = {u: (min(f.get("von") or f["bis"] for f in fs), max(f["bis"] for f in fs)) for u, fs in nachurl.items()}
            vs = list(spannen.values())
            if len(set(vs)) > 1 and ((iso(max(v[0] for v in vs)) - iso(min(v[0] for v in vs))).days > 3 or (iso(max(v[1] for v in vs)) - iso(min(v[1] for v in vs))).days > 3):
                bevor = sorted(spannen, key=lambda u: (not nachurl[u][0]["stark"], -(iso(next((f["qd"] for f in nachurl[u] if f.get("qd")), None) or "2000-01-01")).toordinal(), u))
                u0 = bevor[0]
                per = [f for f in per if not ((saison_von(f), saison_jahr(f)) == sk and f.get("typ") != "einzeltag" and f["url"] != u0)]
                widerspr.append({"key": k, "name": b["name"], "art": sk[0], "jahr": sk[1], "spannen": spannen, "gewaehlt": u0})
        # überlappende, nicht ineinander liegende Zeiträume auf derselben Seite = vermutlich verschiedene Anlagen -> Prüfliste
        def kreuz(fs):
            sp = defaultdict(set)
            for f in fs:
                if f.get("typ") != "einzeltag" and f.get("von"): sp[f["url"]].add((f["von"], f["bis"]))
            for v in sp.values():
                v = sorted(v)
                for i_, a_ in enumerate(v):
                    for b_ in v[i_ + 1:]:
                        if b_[0] > a_[1]: break
                        if not (b_[1] <= a_[1]) and not (a_[0] == b_[0]): return True
                        if a_[0] == b_[0] and a_[1] != b_[1] and abs((iso(a_[1]) - iso(b_[1])).days) <= 20: return True
            return False
        if per and kreuz(per):
            pruef["mehrdeutig"].append((b, ", ".join(sorted({domain(f["url"]) for f in per})), per))
            per = []
        # kurze Zeiträume ohne Jahreszeit von Seiten, die keine Betriebszeiten-Seite sind (Feste, Aktionen), zählen nicht
        per = [f for f in per if f["stark"] or f.get("saison") or not f.get("von") or (iso(f["bis"]) - iso(f["von"])).days >= 21 or f.get("typ") == "wochenende"]
        perioden = perioden_zusammen(per)
        # allgemeine Zeiten (ohne Datumszeile davor) an Zeiträume derselben Jahreszeit ohne eigene Zeiten hängen
        allg = [f for f in fak if f["art"] == "zeiten"]
        for f in allg:
            zt = {x: f[x] for x in ("erste", "letzte_berg", "letzte_tal", "betrieb_bis", "tage") if f.get(x)}
            for p in perioden:
                if not p.get("zeiten") and (f.get("saison") in (None, p.get("saison"))): p["zeiten"] = dict(zt)
        # frühere Zeiträume (letzter Lauf) behalten, solange sie im Fenster liegen und nichts Neues sie überdeckt – für „Saison vorbei“
        a = alt.get(k) or {}
        if perioden or a.get("perioden"):
            fr = min((p.get("von") or p["bis"] for p in perioden), default="9999")
            for p in a.get("perioden") or []:
                if iso(p["bis"]) >= unten and p["bis"] < fr and p["bis"] < H and not p.get("alt") and p not in perioden: perioden.append(dict(p, alt=True))
            perioden.sort(key=lambda p: (p.get("von") or p["bis"], p["bis"]))
        rev = []
        for f in sorted((f for f in fak if f["art"] == "revision"), key=lambda f: f["von"]):
            p = {"von": f["von"], "bis": f["bis"]}
            if p not in rev and iso(f["bis"]) >= HEUTE - datetime.timedelta(days=200): rev.append(p)
        ganzj = any(f["art"] == "ganzjaehrig" for f in fak)
        if fak and (perioden or rev or ganzj or allg):
            som = [p for p in perioden if p.get("saison") == "sommer" and not p.get("alt")]
            win = [p for p in perioden if p.get("saison") == "winter" and not p.get("alt")]
            sj = max((saison_jahr(p) for p in som if p.get("typ") != "einzeltag"), default=None)
            wj = max((saison_jahr(p) for p in win if p.get("typ") != "einzeltag"), default=None)
            som_j = [p for p in som if sj and saison_jahr(p) == sj] if sj else []
            win_j = [p for p in win if wj and saison_jahr(p) == wj] if wj else []
            if not [p for p in perioden if not p.get("alt")] and not ganzj and b["art"] in HAUPT and (allg or rev):
                pruef["ohne_saison"].append((b, [f for f in fak if f["art"] in ("zeiten", "revision")][:6]))
            rt = sorted({t for f in fak if f["art"] == "ruhetage" for t in f["tage"]}, key=L.WTO.index)
            def jn(art):
                v = {f["wert"] for f in fak if f["art"] == art and f.get("zu") != "gebiet"}
                return v.pop() if len(v) == 1 else None
            preis = next((f for f in fak if f["art"] == "preis" and f.get("zu") != "gebiet"), None)
            urls = sorted({f["url"] for f in fak if f["art"] in ("periode", "zeiten", "revision")}, key=lambda u: (not KW_STARK.search(u), bool(FREMD_SPRACHE.search(urllib.parse.urlparse(u).path)), len(u)))
            qd = max([f["qd"] for f in fak if f.get("qd")], default=None)
            nur_gebiet = all(f.get("zu") == "gebiet" for f in fak if f["art"] == "periode")
            neueste = max((iso(p["bis"]) for p in perioden), default=None)
            veraltet = neueste is not None and neueste < HEUTE - datetime.timedelta(days=730)
            sich = "bestaetigt" if sicher_zu and not veraltet else "unklar"
            r.update({"perioden": perioden, "sommer": som_j, "sommer_jahr": sj, "winter": win_j, "winter_jahr": wj, "revision": rev, "ganzjaehrig": ganzj,
                      "zeiten_sommer": zeiten_aus_perioden(som_j) if som_j else None, "zeiten_winter": zeiten_aus_perioden(win_j) if win_j else None,
                      "zeiten_allgemein": zeiten_aus_perioden([{"zeiten": {x: f[x] for x in ("erste", "letzte_berg", "letzte_tal", "betrieb_bis") if f.get(x)}} for f in allg]) if allg and not perioden else None,
                      "ruhetage": rt, "schoenwetter": any(f["art"] == "schoenwetter" and f.get("zu") != "gebiet" for f in fak),
                      "rad": jn("rad"), "hunde": jn("hunde"), "preis": (preis["wert"] + " " + preis["waehrung"]) if preis else None,
                      "quelle_url": urls[0] if urls else (quell[0]["url"] if quell else None), "quelle": quell[0]["woher"] if quell else None,
                      "quelle_datum": qd, "sicherheit": sich, "zuordnung": "gebiet" if nur_gebiet and perioden else "bahn",
                      "veraltet": bool(veraltet), "recherche": "auto"})
            if not sicher_zu and perioden: pruef["unsicher"].append((b, quell))
        else:
            r.update({"recherche": "offen" if quellen_b else "ohne_quelle", "sicherheit": "unklar"})
        if k in ent and ent[k].get("perioden") and all(iso(p["bis"]) < HEUTE - datetime.timedelta(days=30) for p in ent[k]["perioden"]) and not ent[k].get("dauerhaft"):
            pruef["veraltet"].append((b, []))
        elif k in ent:
            e = ent[k]
            for f in ("perioden", "sommer", "winter", "revision", "ganzjaehrig", "zeiten_sommer", "zeiten_winter", "zeiten_allgemein", "ruhetage", "schoenwetter",
                      "rad", "hunde", "preis", "quelle", "quelle_url", "quelle_datum", "sicherheit", "hinweis", "status"):
                if f in e: r[f] = e[f]
            if "perioden" in e and "sommer" not in e: r["sommer"] = [p for p in e["perioden"] if saison_von(p) == "sommer"]
            if "perioden" in e and "winter" not in e: r["winter"] = [p for p in e["perioden"] if saison_von(p) == "winter"]
            r["recherche"] = "fertig"; r["entscheidung"] = e.get("datum")
        erg.append(r)
    aend = []
    for r in erg:
        a = alt.get(r["key"])
        if not a: continue
        def kern(x): return [(p.get("von"), p["bis"]) for p in (x.get("perioden") or []) if not p.get("alt")]
        diff = [f for f in ("revision", "zeiten_sommer", "zeiten_winter", "ruhetage") if (a.get(f) or None) != (r.get(f) or None)]
        if kern(a) != kern(r): diff.insert(0, "Zeiträume")
        if diff: aend.append((r, diff))
    speichern(os.path.join(ordner, "ergebnis.json"), erg)
    bericht(ordner, bahnen, erg, pruef, widerspr, aend, seiten, q, dom_bahnen, unbekannt)

def bahn_l(b): return f"[{b['name'] or '(ohne Namen)'}](https://www.openstreetmap.org/{'relation' if b['key'][0] == 'r' else 'way'}/{b['key'][1:]})"
def bericht(ordner, bahnen, erg, pruef, widerspr, aend, seiten, q, dom_bahnen, unbekannt=None):
    unbekannt = unbekannt or {}
    bz = {b["key"]: b for b in bahnen}
    best = lade(os.path.join(ordner, "bestand-aenderung.json"), {})
    z = [f"# Bergbahnen – Betriebszeiten, Lauf {H}", "",
         "Erzeugt von karte/bergbahnen.py (ohne KI). Quellen: eigene Webseiten der Bahnen/Skigebiete (Kartendaten, Wikidata), Mitgliederliste Seilbahnen Schweiz, karte/bb_quellen.txt.", "",
         "| Land | Region | Bahnen (ohne Schlepplifte) | mit Quelle | mit Sommerzeiten | mit Winterzeiten | mit Tageszeiten | bestätigt | unklar |",
         "|---|---|---|---|---|---|---|---|---|"]
    st = defaultdict(Counter)
    for r in erg:
        if r["art"] not in HAUPT: continue
        c = st[(r["land"], r["region"])]
        c["n"] += 1; c["q"] += bool(r["quellen"]); c["s"] += bool(r.get("sommer")); c["w"] += bool(r.get("winter"))
        c["t"] += bool(r.get("zeiten_sommer") or r.get("zeiten_winter") or r.get("zeiten_allgemein")); c["b"] += r.get("sicherheit") == "bestaetigt" and bool(r.get("sommer") or r.get("winter") or r.get("ganzjaehrig"))
        c["u"] += r.get("sicherheit") == "unklar" and bool(r.get("sommer") or r.get("winter"))
    tot = Counter()
    for (land, reg), c in sorted(st.items(), key=lambda x: (x[0][0] or "", x[0][1] or "")):
        z.append(f"| {LANDNAME.get(land, land)} | {reg} | {c['n']} | {c['q']} | {c['s']} | {c['w']} | {c['t']} | {c['b']} | {c['u']} |"); tot.update(c)
    z.append(f"| **gesamt** | | {tot['n']} | {tot['q']} | {tot['s']} | {tot['w']} | {tot['t']} | {tot['b']} | {tot['u']} |")
    sl = sum(1 for r in erg if r["art"] == "schlepplift")
    z += ["", f"Schlepplifte (nur Winter): {sl}, davon mit Wintersaison {sum(1 for r in erg if r['art'] == 'schlepplift' and r.get('winter'))}.", ""]
    if best.get("alt_anzahl"):
        z += ["## Bestand gegenüber dem letzten Lauf", "", f"- neu in den Kartendaten: {len(best['neu'])}", f"- nicht mehr gefunden (wird nur markiert): {len(best['weg'])}", ""]
        for k in best["neu"][:150]:
            b = bz.get(k)
            if b and b["art"] in HAUPT: z.append(f"  - neu: {bahn_l(b)} ({b['region']}, {b['gemeinde'] or ''})")
        for k in best["weg"][:150]: z.append(f"  - nicht mehr gefunden: {k}")
    if best.get("still"):
        z += ["", f"Stillgelegt laut Kartendaten (nicht in der Liste): {len(best['still'])}", ""]
    z += ["", "## Geänderte Betriebszeiten", ""]
    z += [f"- {bahn_l(bz[r['key']])} ({r['region']}): {', '.join(d)}" for r, d in aend[:300]] or ["- keine (oder erster Lauf)"]
    z += ["", "## Widersprüche (neueste offizielle Angabe übernommen)", ""]
    for w in widerspr[:200]:
        z.append(f"- {bahn_l(bz[w['key']])} {w['art']} {w['jahr']}: " + "; ".join(f"{v[0]}–{v[1]} ({u})" for u, v in w["spannen"].items()) + f" → übernommen: {w['gewaehlt']}")
    if not widerspr: z.append("- keine")
    lesbar = sum(1 for v in seiten.values() if v.get("seiten")); nicht = [(d, v) for d, v in seiten.items() if not v.get("seiten")]
    z += ["", "## Webseiten", "", f"- gelesen: {lesbar}, nicht lesbar: {len(nicht)}", ""]
    for d, v in sorted(nicht, key=lambda x: -len(dom_bahnen.get(x[0], [])))[:80]:
        n = sum(1 for b in dom_bahnen.get(d, []) if b["art"] in HAUPT)
        if n: z.append(f"  - {d}: {v.get('fehler') or 'kein Text'} ({n} Bahnen)")
    open(os.path.join(ordner, "bericht.md"), "w", encoding="utf-8").write("\n".join(z) + "\n")
    # Prüfliste – kurz, nur Hauptbahnen
    p = [f"# Bergbahnen – Prüfliste {H}", "",
         "Nur ansehen und entscheiden; Ergebnis in daten/bergbahnen/entscheidungen.json (Schlüssel = Bahn-ID, oder dom:<Webseite> für eine ganze Seite).",
         "Beispiele: {\"w123\": {\"sommer\": [{\"von\": \"2027-05-15\", \"bis\": \"2027-10-17\"}], \"zeiten_sommer\": {\"erste\": \"08:30\", \"letzte_tal\": \"17:00\"}, \"quelle_url\": \"…\", \"sicherheit\": \"bestaetigt\", \"datum\": \"…\"}}",
         "{\"dom:golm.at\": {\"seitenweit\": [\"w123\"]}} = Angaben ohne Bahn-Abschnitt auf dieser Seite gelten für diese Bahn(en); \"alle\" = für alle.", ""]
    zu = pruef.get
    seen = set()
    p += ["## A. Seite mit mehreren Bahnen – Angaben ohne Abschnitt", ""]
    for key, bs in sorted(((k, v) for k, v in pruef.items() if k.startswith("zuordnung:")), key=lambda x: -len(x[1]))[:150]:
        d = key.split(":", 1)[1]
        fd = []
        for s in (seiten.get(d) or {}).get("seiten", []):
            if not s.get("zeilen"): continue
            for f in L.lesen(s["zeilen"], [L.Bahn(b["key"], b["name"]) for b in dom_bahnen[d] if b["name"]], s["url"], s.get("titel", "")).get("?", []):
                if f["art"] in ("periode", "zeiten", "revision"): f["url"] = s["url"]; fd.append(f)
        fd = fd[:5]
        if not fd: continue
        p.append(f"- **{d}** – Bahnen: " + ", ".join(f"{b['key']} {b['name']}" for b in bs[:8]))
        for f in fd: p.append(f"  - {f['art']}: {f.get('von') or ''}{'–' + f['bis'] if f.get('bis') else ''} {f.get('erste', '')}{'–' + (f.get('letzte_tal') or f.get('betrieb_bis') or '') if f.get('erste') else ''} · „{f['zeile'][:90]}“ ({f['url']})")
    p += ["", "## A2. Nur eine Bahn auf der Webseite, aber widersprüchliche Zeiträume auf verschiedenen Unterseiten", ""]
    for b, d, fs in (zu("mehrdeutig") or [])[:120]:
        p.append(f"- {b['key']} {bahn_l(b)} ({b['region']}) – {d}")
        for f in [f for f in fs if f["art"] in ("periode", "revision")][:5]: p.append(f"  - {f['art']}: {f.get('von') or '…'}–{f['bis']} · „{f['zeile'][:80]}“ ({f['url']})")
    p += ["", "## A3. Abschnitte mit Bahnnamen, die zu keiner Bahn der Kartendaten passen (Zuordnung über \"alias\")", ""]
    for d, ue in sorted(unbekannt.items(), key=lambda x: -len(x[1]))[:150]:
        bs = [b for b in dom_bahnen[d] if b["art"] in HAUPT]
        p.append(f"- **{d}**: " + "; ".join(f"„{n}“ ({k})" for n, k in sorted(ue.items())[:10]) + " · Bahnen der Karte: " + ", ".join(f"{b['key']} {b['name']}" for b in bs[:14]))
    p += ["", "## B. Zeiten ohne Jahreszeit (z. B. ganzjährig mit Revision?)", ""]
    for b, fs in (zu("ohne_saison") or [])[:120]:
        p.append(f"- {b['key']} {bahn_l(b)} ({b['region']}, {b['gemeinde'] or ''})")
        for f in fs[:4]: p.append(f"  - {f['art']}: {f.get('von') or ''}{'–' + f['bis'] if f.get('bis') else ''} {f.get('erste', '')}{'–' + (f.get('letzte_tal') or f.get('betrieb_bis') or '') if f.get('erste') else ''} · „{f['zeile'][:90]}“ ({f['url']})")
    p += ["", "## C. Unsichere Zuordnung (Webseite nur über Nachbarbahn/Ort gefunden)", ""]
    for b, qs in (zu("unsicher") or [])[:150]:
        p.append(f"- {b['key']} {bahn_l(b)} ({b['region']}): " + ", ".join(f"{x['url']} ({x['woher']})" for x in qs[:2]))
    open(os.path.join(ordner, "pruefliste.md"), "w", encoding="utf-8").write("\n".join(p) + "\n")
    # Ohne Quelle: nur Hauptbahnen, je Anlage
    o = [f"# Bergbahnen ohne Betreiberquelle – Stand {H}", "",
         "Hauptbahnen (ohne Schlepplifte), für die weder Kartendaten noch Wikidata noch die Quellenliste eine Webseite liefern.",
         "Ergänzen in karte/bb_quellen.txt: Land;Region;Betreiber;lat,lon;Radius km;Adresse", ""]
    an = defaultdict(list)
    for b in bahnen:
        if b["art"] in HAUPT and not q.get(b["key"]): an[(b["land"], b["region"], b["anlage"])].append(b)
    for (land, reg, a), bs in sorted(an.items(), key=lambda x: (x[0][0] or "", x[0][1] or "", -len(x[1]))):
        b0 = bs[0]
        o.append(f"- {LANDNAME.get(land, land)} · {reg} · {b0['gemeinde'] or b0['bezirk'] or ''}: " + ", ".join(bahn_l(b) for b in bs[:6]) + (f" (+{len(bs) - 6})" if len(bs) > 6 else "") + f" · {b0['mitte'][1]:.4f},{b0['mitte'][0]:.4f}")
    open(os.path.join(ordner, "ohne-quelle.md"), "w", encoding="utf-8").write("\n".join(o) + "\n")
    print("\n".join(z[:12 + len(st)]))
    print("Prüfliste:", dict(Counter(k.split(':')[0] for k in pruef for _ in (pruef[k] if not k.startswith("zuordnung") else [1]))), "unbekannte Namen:", sum(len(v) for v in unbekannt.values()), "· Widersprüche:", len(widerspr), "· geändert:", len(aend))

# ---------------------------------------------------------------- Datenbank
SB_URL = os.environ.get("SB_URL", "https://vehrlrjegxlhxibrxqxm.supabase.co")
def sb(method, path, body=None, prefer=None):
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(SB_URL + "/rest/v1/" + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    for v in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500: print("Datenbank-Fehler:", e.code, e.read()[:300]); raise
            time.sleep(5 + v * 10)
        except Exception: time.sleep(5 + v * 10)
    raise RuntimeError("Datenbank nicht erreichbar")
def tag_text(t): return {"mo": "Mo", "di": "Di", "mi": "Mi", "do": "Do", "fr": "Fr", "sa": "Sa", "so": "So"}[t]
def db(ordner):
    if not os.environ.get("SUPABASE_SERVICE_KEY"): print("Kein SUPABASE_SERVICE_KEY – Datenbank bleibt unverändert"); return
    bahnen = {b["key"]: b for b in lade(os.path.join(ordner, "bahnen.json"), [])}
    erg = lade(os.path.join(ordner, "ergebnis.json"), [])
    rows, jetzt = [], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for r in erg:
        b = bahnen[r["key"]]; t, e = b["tal"], b["berg"]
        def betrieb(per, jahr, zeiten):
            if not per: return None
            return {"von": min(p.get("von") or p["bis"] for p in per), "bis": max(p["bis"] for p in per), "jahr": jahr, "perioden": per, "zeiten": zeiten}
        zs = r.get("zeiten_sommer") or {}
        zz = zs if zs else (r.get("zeiten_winter") or {})
        bes = []
        if r.get("schoenwetter"): bes.append("nur bei Schönwetter")
        if r.get("sommer") and all(p.get("typ") == "wochenende" for p in r["sommer"]): bes.append("Sommer nur an Wochenenden")
        if r.get("ganzjaehrig"): bes.append("ganzjährig (außer Revision)")
        rows.append({"osm_typ": "relation" if r["key"][0] == "r" else "way", "osm_id": int(r["key"][1:]), "name": b["name"] or ({"seilbahn": "Seilbahn", "gondelbahn": "Gondelbahn", "sessellift": "Sessellift", "standseilbahn": "Standseilbahn", "zahnradbahn": "Zahnradbahn", "schlepplift": "Schlepplift"}.get(b["art"], "Bahn") + (f" ({b['gemeinde']})" if b.get("gemeinde") else "")), "art": b["art"],
                     "betreiber": b["tags"].get("operator") or (b.get("gebiet") or {}).get("op"),
                     "geom": f"SRID=4326;LINESTRING({t['lon']} {t['lat']},{e['lon']} {e['lat']})",
                     "tal_name": t["name"], "tal_geom": f"SRID=4326;POINT({t['lon']} {t['lat']})", "tal_hoehe": t["ele"],
                     "berg_name": e["name"], "berg_geom": f"SRID=4326;POINT({e['lon']} {e['lat']})", "berg_hoehe": e["ele"],
                     "lat": b["mitte"][1], "lng": b["mitte"][0], "tal_lat": t["lat"], "tal_lng": t["lon"], "berg_lat": e["lat"], "berg_lng": e["lon"],
                     "land": b["land"], "region": b["region"], "bezirk": b["bezirk"], "gemeinde": b["gemeinde"],
                     "sommerbetrieb": betrieb(r.get("sommer"), r.get("sommer_jahr"), r.get("zeiten_sommer")),
                     "winterbetrieb": betrieb(r.get("winter"), r.get("winter_jahr"), r.get("zeiten_winter")),
                     "revision": "; ".join(f"{p['von']} bis {p['bis']}" for p in r.get("revision") or []) or None,
                     "erste_bergfahrt": zz.get("erste"), "letzte_bergfahrt": zz.get("letzte_berg"), "letzte_talfahrt": zz.get("letzte_tal"),
                     "ruhetage": ", ".join(tag_text(x) for x in r.get("ruhetage") or []) or None, "besonderheiten": "; ".join(bes) or None,
                     "rad": r.get("rad"), "hunde": r.get("hunde"), "preis": r.get("preis"),
                     "webseite": (r.get("quellen") or [None])[0], "hinweise": r.get("hinweis"),
                     "quelle": r.get("quelle") or ("OpenStreetMap (nur Bestand)"), "quelle_url": r.get("quelle_url"), "quelle_datum": r.get("quelle_datum"),
                     "pruef_datum": H, "sicherheit": r.get("sicherheit") or "unklar", "status": r.get("status") or "aktiv", "geaendert": jetzt,
                     "daten": {"lauf": "bergbahnen", "recherche": r.get("recherche"), "zuordnung": r.get("zuordnung"), "revision": r.get("revision") or [],
                               "perioden": [{k2: v2 for k2, v2 in p.items() if k2 != "url"} for p in (r.get("perioden") or [])], "zeiten_allgemein": r.get("zeiten_allgemein"),
                               "ganzjaehrig": r.get("ganzjaehrig") or False, "ruhetage": r.get("ruhetage") or [], "veraltet": r.get("veraltet") or False,
                               "quellen": r.get("quellen"), "anlage": b["anlage"], "ways": b.get("ways"), "hoehe_quelle": b.get("hq"),
                               "osm_oeffnungszeiten": b["tags"].get("opening_hours"), "entscheidung": r.get("entscheidung"), "zugang": b["tags"].get("access")}})
    eindeutig = {}
    for x in rows: eindeutig[(x["osm_typ"], x["osm_id"])] = x
    rows = list(eindeutig.values())
    for i in range(0, len(rows), 400):
        sb("POST", "gb_bergbahnen?on_conflict=osm_typ,osm_id", rows[i:i + 400], "resolution=merge-duplicates,return=minimal")
    keys = {(x["osm_typ"], x["osm_id"]) for x in rows}
    vorh = json.loads(sb("GET", "gb_bergbahnen?select=osm_typ,osm_id&status=eq.aktiv&limit=100000"))
    weg = [x for x in vorh if (x["osm_typ"], x["osm_id"]) not in keys]
    for typ in ("way", "relation"):
        ids = [str(x["osm_id"]) for x in weg if x["osm_typ"] == typ]
        for i in range(0, len(ids), 100):
            sb("PATCH", f"gb_bergbahnen?osm_typ=eq.{typ}&osm_id=in.({','.join(ids[i:i + 100])})", {"status": "nicht_mehr_gefunden", "pruef_datum": H}, "return=minimal")
    mit = sum(1 for r in erg if r.get("sommer") or r.get("winter") or r.get("ganzjaehrig"))
    sb("POST", "gb_abgleich_laeufe", {"gruppe": "bergbahnen", "region": "Österreich, Deutschland, Schweiz, Liechtenstein, Südtirol", "gestartet": H,
       "beendet": jetzt, "fortschritt": 100, "status": "fertig", "neu": len((lade(os.path.join(ordner, "bestand-aenderung.json"), {}) or {}).get("neu") or []),
       "geaendert": mit, "nicht_gefunden": len(weg), "bericht": "daten/bergbahnen/bericht.md",
       "daten": {"bahnen": len(rows), "mit_zeiten": mit}}, "return=minimal")
    print(f"Datenbank: {len(rows)} Bahnen eingetragen ({mit} mit Betriebszeiten), {len(weg)} als nicht mehr gefunden markiert")

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    c = a[0]
    if c == "grenzen": grenzen(a[1], a[2])
    elif c == "osm": osm(a[1], a[2], a[3], a[4:])
    elif c == "bestand": bestand(a[1], a[2])
    elif c == "quellen": quellen(a[1], a[2] if len(a) > 2 else None)
    elif c == "lesen": lesen(a[1], set(x for x in (os.environ.get("BB_NUR") or "").split(",") if x) or None)
    elif c == "auswerten": auswerten(a[1])
    elif c == "db": db(a[1])
    else: print(__doc__); sys.exit(1)
