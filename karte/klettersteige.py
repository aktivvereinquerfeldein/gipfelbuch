#!/usr/bin/env python3
"""Klettersteige und Topos abgleichen (Prompt 4) – ohne KI, Claude liest danach nur die Prüfliste.

Gebiet: Österreich, Südtirol, Schweiz, Liechtenstein (weitere Länder: in .github/workflows/klettersteige.yml ergänzen).

Ablauf (siehe .github/workflows/klettersteige.yml):
  klettersteige.py grenzen  <grenzen.osm.pbf> <grenzen.json>        Bundesländer/Kantone (4), Bezirke (6), Gemeinden (8)
  klettersteige.py osm      <grenzen.json> <aus.jsonl> <vf.osm.pbf>... alle benannten Klettersteig-Wege (highway=via_ferrata)
  klettersteige.py zusammen <aus.jsonl> <steige.json>              Wege gleichen Namens zu Steigen zusammenfassen
  klettersteige.py vf       <ordner>                                via-ferrata.de lesen (Sitemap, nur geänderte Seiten)
  klettersteige.py quellen  <ordner>                                Zusatzquellen aus karte/ks_quellen.txt (Topo-Kandidaten)
  klettersteige.py abgleich <steige.json> <ordner>                  vergleichen, Topos zuordnen, Links prüfen, Berichte
  klettersteige.py db       <ordner>                                Tabelle gb_klettersteige (braucht SUPABASE_SERVICE_KEY)

Regeln (Marcel, Block A und Prompt 4):
- Nur lesen: robots.txt, höchstens 1 Anfrage pro Sekunde und Seite, keine Anmeldung, keine Formulare.
- Nur Fakten und Links (Schwierigkeit, Höhenmeter, Dauer, Status, Topo-Adresse). Keine Texte oder Bilder kopieren.
- Topo nur übernehmen, wenn es wirklich zu genau diesem Steig gehört. Feste Zuordnungen bleiben, solange der Link lebt.
  Unsichere Funde kommen auf die Prüfliste (daten/klettersteige/pruefliste.md); Claude bestätigt dort und schreibt
  das Ergebnis nach daten/klettersteige/entscheidungen.json (hat Vorrang vor allem anderen).
- bergsteigen.com nie verwenden (kostenpflichtig). Suchmaschinen-Vorschaubilder gelten als „unklar“.
- Nichts löschen: Verschwundenes = status nicht_mehr_gefunden.
- Fortschritt wird laufend in daten/klettersteige/ gespeichert, ein abgebrochener Lauf macht dort weiter.
"""
import json, os, re, sys, time, math, html, urllib.request, urllib.parse
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sammler   # holen-Regeln (User-Agent, robots.txt), text_und_links()

HIER = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HIER)
HEUTE = time.strftime("%Y-%m-%d")
GEBIET = (5.8, 45.6, 17.3, 49.1)   # grobes Rechteck AT + CH + LI + Südtirol
VERBOTEN = re.compile(r"bergsteigen[._-]com", re.I)
SCHWACH = re.compile(r"(bing\.net|bing\.com/th|googleusercontent|gstatic|strava\.com|facebook|fbcdn|instagram|pinterest)", re.I)

# ---------------------------------------------------------------- Namen (gleich wie in index.html)
BS_STOP = {"klettersteig", "klettersteige", "steig", "via", "ferrata", "ferrate", "sentiero", "attrezzato", "der", "die", "das", "am", "im",
           "de", "la", "di", "del", "della", "dei", "delle", "mit", "und", "von", "zur", "zum", "uebungsklettersteig", "uebungsklettersteige",
           "kopie", "variante", "route", "weg"}
def _ascii(n):
    import unicodedata
    n = str(n or "").lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return "".join(c for c in unicodedata.normalize("NFD", n) if unicodedata.category(c) != "Mn")
def name_key(n): return re.sub(r"[^a-z0-9]+", " ", _ascii(n)).strip()
def norm_tok(n): return [w for w in re.split(r"[^a-z0-9]+", _ascii(n)) if w and len(w) >= 3 and w not in BS_STOP]
def hav(lon1, lat1, lon2, lat2):
    R = 6371000; p1, p2 = math.radians(lat1), math.radians(lat2); dp = p2 - p1; dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
def dmin(punkte, lon, lat): return min((hav(p[0], p[1], lon, lat) for p in punkte), default=1e9)

# OSM via_ferrata_scale 0–6  ->  Buchstaben (wie im Datenblatt der App)
SKALA = {"0": "A", "1": "A", "2": "B", "3": "C", "4": "D", "5": "E", "6": "F"}
GRADE = ["A", "A/B", "B", "B/C", "C", "C/D", "D", "D/E", "E", "E/F", "F"]
def grad_ord(g): return GRADE.index(g) if g in GRADE else -1
def grad_norm(g):
    g = (g or "").upper().replace(" ", "").replace("–", "-").replace("+", "")
    m = re.match(r"^([A-F])(?:[/-]([A-F]))?$", g)
    if not m: return None
    return m.group(1) + ("/" + m.group(2) if m.group(2) and m.group(2) != m.group(1) else "")

def lade(p, leer):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return leer
def speichern(p, d):
    tmp = p + ".tmp"; json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True); os.replace(tmp, p)

# ---------------------------------------------------------------- Abrufen (nur lesen, robots.txt, 1 Anfrage/s je Seite)
_rob, _zul = {}, {}
def darf(url):
    import urllib.robotparser
    u = urllib.parse.urlparse(url); host = u.scheme + "://" + u.netloc
    if host not in _rob:
        rp = urllib.robotparser.RobotFileParser(); rp.set_url(host + "/robots.txt")
        try:
            req = urllib.request.Request(host + "/robots.txt", headers={"User-Agent": sammler.UA})
            with urllib.request.urlopen(req, timeout=15) as r: rp.parse(r.read(500_000).decode("utf-8", "replace").splitlines())
        except urllib.error.HTTPError as e:
            rp = None if e.code >= 400 else rp      # keine robots.txt = erlaubt
        except Exception: rp = None
        _rob[host] = rp
    rp = _rob[host]
    return rp is None or rp.can_fetch(sammler.UA, url)
def warten(url):
    h = urllib.parse.urlparse(url).netloc
    w = 1.0 - (time.time() - _zul.get(h, 0))
    if w > 0: time.sleep(w)
    _zul[h] = time.time()
def holen(url, maxb=4_000_000, timeout=30, rng=False):
    """gibt (status, content_type, end_url, bytes) – status 0 = Netzfehler, -1 = robots.txt verbietet"""
    if not darf(url): return -1, "", url, b""
    warten(url)
    h = {"User-Agent": sammler.UA, "Accept-Language": "de,en;q=0.5"}
    if rng: h["Range"] = "bytes=0-4095"
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type") or "", r.geturl(), r.read(maxb)
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type") or "" if e.headers else "", url, b""
    except Exception as e:
        return 0, str(e)[:80], url, b""

# ---------------------------------------------------------------- Grenzen
def grenzen(pbf, aus):
    import osmium
    from osmium.geom import WKBFactory
    wkb = WKBFactory(); out = lade(aus, [])
    class H(osmium.SimpleHandler):
        def area(s, a):
            t = {x.k: x.v for x in a.tags}
            if t.get("boundary") != "administrative": return
            lv = t.get("admin_level"); iso = t.get("ISO3166-2") or ""
            if lv == "2" and t.get("ISO3166-1") == "LI": lv, iso = "4", "LI"
            if iso == "IT-BZ": lv = "4"   # Südtirol ist in OSM eine Provinz (admin_level 6)
            if lv not in ("4", "6", "8"): return
            if lv == "4" and not (iso[:3] in ("AT-", "CH-") or iso in ("IT-BZ", "LI")): return
            try: out.append({"lv": lv, "name": t.get("name:de") or t.get("name"), "iso": iso, "wkb": wkb.create_multipolygon(a)})
            except Exception: pass
    H().apply_file(pbf, locations=True, idx="flex_mem")
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

# ---------------------------------------------------------------- OSM-Bestand
def osm(grenzfile, aus, pbfs):
    import osmium
    fnd = Finder(grenzfile)
    relname = {}
    class R(osmium.SimpleHandler):
        def relation(s, r):
            t = {x.k: x.v for x in r.tags}
            if t.get("route") == "via_ferrata" or t.get("climbing") == "via_ferrata" or t.get("highway") == "via_ferrata":
                for m in r.members:
                    if m.type == "w" and t.get("name"): relname.setdefault(m.ref, (t["name"], r.id, t.get("via_ferrata_scale", "")))
    n = 0
    with open(aus, "w", encoding="utf-8") as f:
        class W(osmium.SimpleHandler):
            def way(s, w):
                nonlocal n
                t = {x.k: x.v for x in w.tags}
                if not (t.get("highway") == "via_ferrata" or "via_ferrata_scale" in t or any(k.endswith(":highway") and v == "via_ferrata" for k, v in t.items())): return
                name = t.get("name") or (relname.get(w.id) or ("",))[0]
                if not name: return
                try: pts = [(round(nd.lon, 6), round(nd.lat, 6)) for nd in w.nodes if nd.location.valid()]
                except Exception: return
                if not pts: return
                m = pts[len(pts) // 2]
                reg, iso = fnd(m[0], m[1], "4")
                if not reg: return
                bez = fnd(m[0], m[1], "6")[0]; gem = fnd(m[0], m[1], "8")[0]
                lang = sum(hav(*pts[i - 1], *pts[i]) for i in range(1, len(pts)))
                flags = {k: v for k, v in t.items() if re.match(r"^(access|foot|disused|abandoned|razed|demolished|construction|opening_date|check_date|note|fixme|operator|website|url|wikidata|via_ferrata_scale|highway|lifecycle)", k)}
                if w.id in relname and not t.get("via_ferrata_scale") and relname[w.id][2]: flags["via_ferrata_scale"] = relname[w.id][2]
                st = 8 if len(pts) > 16 else 1
                f.write(json.dumps({"id": w.id, "name": name, "rel": (relname.get(w.id) or (None, None))[1], "land": iso[:2] if iso != "LI" else "LI",
                                    "region": reg, "iso": iso, "bezirk": bez, "gemeinde": gem, "laenge": round(lang),
                                    "pts": pts[::max(1, len(pts) // st)] + [pts[-1]], "tags": flags}, ensure_ascii=False) + "\n")
                n += 1
        for p in pbfs: R().apply_file(p)
        h = W()
        for p in pbfs: h.apply_file(p, locations=True, idx="flex_mem")
    print(f"{n} benannte Klettersteig-Wege")

def zusammen(inv, aus):
    """Wege gleichen Namens, die höchstens 1,5 km auseinander liegen, sind ein Steig (doppelte aus Randstreifen weg)"""
    ways = {}
    for l in open(inv, encoding="utf-8"):
        if l.strip():
            w = json.loads(l); ways[w["id"]] = w
    nach = defaultdict(list)
    for w in ways.values(): nach[name_key(w["name"])].append(w)
    steige = []
    for k, ws in nach.items():
        offen = list(ws)
        while offen:
            gr = [offen.pop()]; neu = True
            while neu:
                neu = False
                for w in list(offen):
                    if any(dmin(g["pts"], *p) < 1500 for g in gr for p in w["pts"][::2] + [w["pts"][-1]]):
                        gr.append(w); offen.remove(w); neu = True
            gr.sort(key=lambda w: w["id"])
            lang = max(gr, key=lambda w: w["laenge"])
            mp = lang["pts"][len(lang["pts"]) // 2]
            skalen = [w["tags"].get("via_ferrata_scale", "") for w in gr if w["tags"].get("via_ferrata_scale")]
            gr_b = sorted({SKALA.get(s.strip()[:1], "") for s in skalen if SKALA.get(s.strip()[:1])}, key=grad_ord)
            flags = {}
            for w in gr:
                for kk, vv in w["tags"].items():
                    if kk not in ("highway", "via_ferrata_scale"): flags.setdefault(kk, vv)
            steige.append({"key": k, "name": Counter(w["name"] for w in gr).most_common(1)[0][0], "osm_id": gr[0]["id"],
                           "ways": [w["id"] for w in gr], "rel": next((w["rel"] for w in gr if w["rel"]), None),
                           "lon": mp[0], "lat": mp[1], "pts": [p for w in gr for p in w["pts"]][:120],
                           "land": lang["land"], "region": lang["region"], "bezirk": lang["bezirk"], "gemeinde": lang["gemeinde"],
                           "laenge": sum(w["laenge"] for w in gr), "osm_grad": (gr_b[0] + ("–" + gr_b[-1] if len(gr_b) > 1 else "")) if gr_b else None,
                           "osm_grad_max": gr_b[-1] if gr_b else None, "flags": flags})
    steige.sort(key=lambda s: (s["land"], s["region"] or "", s["name"]))
    json.dump(steige, open(aus, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"{len(steige)} Klettersteige aus {len(ways)} Wegen")

# ---------------------------------------------------------------- via-ferrata.de
VF = "https://www.via-ferrata.de"
R_FELD = re.compile(r'<div class="sabai-directory-field[^"]*sabai-field-name-(field-[a-z0-9-]+)[^"]*">(.*?)</div>\s*</div>', re.S)
def vf_seite(raw):
    s = raw.decode("utf-8", "replace")
    if "leider gibt es diese Seite nicht mehr" in s: return {"weg": True}
    d = {}
    m = re.search(r'<meta property="og:title" content="([^"]*)"', s); d["titel"] = html.unescape(m.group(1)).strip() if m else None
    m = re.search(r'sabai-field-name-field-gpsstart.*?destination=(-?\d+\.\d+),(-?\d+\.\d+)', s, re.S)
    if m: d["lat"], d["lon"] = float(m.group(1)), float(m.group(2))
    felder = {}
    for m in R_FELD.finditer(s):
        name, inner = m.group(1), m.group(2)
        if name in felder: break     # ab hier Liste „in der Nähe“
        lab = re.search(r'sabai-field-label">(.*?)</div>', inner, re.S)
        val = re.search(r'sabai-field-value">(.*)', inner, re.S)
        felder[name] = {"label": re.sub(r"<[^>]+>", "", lab.group(1)).strip() if lab else "",
                        "wert": re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", val.group(1)))).strip()[:120] if val else "",
                        "links": [html.unescape(h) for h in re.findall(r'href="([^"]+)"', inner)]}
    d["grad"] = grad_norm((felder.get("field-schwierigkeit") or {}).get("wert"))
    d["grad_roh"] = (felder.get("field-schwierigkeit") or {}).get("wert")
    zahl = lambda f: (lambda m: int(m.group(1).replace(".", "")) if m else None)(re.search(r"([\d.]+)\s*m", (felder.get(f) or {}).get("wert", "")))
    d["hm"] = zahl("field-hoehenunterschied"); d["gipfel_m"] = zahl("field-hoehe")
    m = re.search(r"([\d]+(?:[,.]\d+)?)", (felder.get("field-dauer") or {}).get("wert", "")); d["dauer_h"] = float(m.group(1).replace(",", ".")) if m else None
    topos = []
    for f in ("field-topo", "field-topo1"):
        for h in (felder.get(f) or {}).get("links", []):
            if h.startswith("http") and h not in topos: topos.append(h)
    d["topos"] = topos
    # Hinweise auf Sperre/Sanierung nur aus dem kurzen Beschreibungs-Anfang (wird nie automatisch übernommen)
    m = re.search(r'<div class="sabai-directory-body">(.*?)</div>', s, re.S)
    kurz = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))) if m else ""
    d["hinweis"] = [x.strip() for x in re.findall(r"[^.]{0,70}\b(?:gesperrt|geschlossen|abgebaut|rückgebaut|saniert|Sanierung|nicht begehbar|chiusa|fermée?)\b[^.]{0,70}", kurz, re.I)
                    if not re.search(r"(straße|strasse|zufahrt|anfahrt|parkplatz|winter|seilbahn|bahn |lift)", x, re.I)][:2]
    return d

def vf(ordner):
    """Sitemap holen; nur neue oder seit dem letzten Lauf geänderte Steige lesen. Zwischenstand alle 40 Seiten."""
    pfad = os.path.join(ordner, "vf-stand.json"); st = lade(pfad, {})
    code, ct, _, raw = holen(VF + "/klettersteige/sitemap.xml", maxb=20_000_000)
    if code != 200: print("Sitemap nicht erreichbar:", code, ct); return
    karte = dict(re.findall(r"<loc>https://www\.via-ferrata\.de/klettersteige/topo/([^<]+?)/?</loc>\s*<lastmod>([^<]+)</lastmod>", raw.decode("utf-8", "replace")))
    idx = vfidx()
    for slug in list(st):
        if slug not in karte and not st[slug].get("weg"): st[slug].update({"weg": True, "weg_seit": HEUTE})
    todo = []
    for slug, lm in karte.items():
        e = st.get(slug) or {}
        lat, lon = e.get("lat"), e.get("lon")
        if lat is None and slug in idx: lat, lon = idx[slug]
        if lat is not None and not (GEBIET[0] <= lon <= GEBIET[2] and GEBIET[1] <= lat <= GEBIET[3]):
            st.setdefault(slug, {"lat": lat, "lon": lon, "aussen": True}); continue
        if e.get("lastmod") == lm and e.get("gelesen"): continue
        todo.append((slug, lm))
    mx = int(os.environ.get("MAX_VF", "4000")); print(f"via-ferrata.de: {len(karte)} Steige in der Sitemap, {len(todo)} zu lesen (höchstens {mx})")
    for i, (slug, lm) in enumerate(todo[:mx]):
        code, ct, end, raw = holen(f"{VF}/klettersteige/topo/{slug}")
        if code != 200: st.setdefault(slug, {})["fehler"] = code; continue
        d = vf_seite(raw)
        if d.get("weg"): st[slug] = {**st.get(slug, {}), "weg": True, "weg_seit": st.get(slug, {}).get("weg_seit", HEUTE)}; continue
        alt = st.get(slug) or {}
        d.update({"lastmod": lm, "gelesen": HEUTE})
        if alt.get("gelesen"):
            d["vorher"] = {k: alt.get(k) for k in ("grad", "topos", "titel") if alt.get(k) != d.get(k)} or None
        if d.get("lat") is not None and not (GEBIET[0] <= d["lon"] <= GEBIET[2] and GEBIET[1] <= d["lat"] <= GEBIET[3]): d = {"lat": d["lat"], "lon": d["lon"], "aussen": True, "lastmod": lm, "gelesen": HEUTE}
        st[slug] = d
        if i % 40 == 39: speichern(pfad, st); print(f"  {i + 1} gelesen")
    speichern(pfad, st); print("via-ferrata.de fertig")

def vfidx():
    """Bild-Verzeichnis aus index.html (Kurzname|Breite|Länge|Bild) als Startwert für Koordinaten"""
    s = open(os.path.join(REPO, "index.html"), encoding="utf-8").read()
    m = re.search(r'const VFIDX="(.*?)";', s)
    out = {}
    for r in (m.group(1).split(";") if m else []):
        p = r.split("|")
        if len(p) >= 3:
            try: out[p[0]] = (float(p[1]), float(p[2]))
            except ValueError: pass
    return out

# ---------------------------------------------------------------- Zusatzquellen (Tourismus, Alpenverein, Gemeinden ...)
def ks_quellen():
    q, off = [], []
    for l in open(os.path.join(HIER, "ks_quellen.txt"), encoding="utf-8"):
        l = l.strip()
        if not l or l.startswith("#"): continue
        if l.lower().startswith("offiziell;"): off.append(l.split(";", 1)[1].strip()); continue
        p = [x.strip() for x in l.split(";")]
        if len(p) >= 2: q.append({"region": p[0], "url": p[1], "max": int(p[2]) if len(p) > 2 and p[2].isdigit() else 150})
    return q, off
R_KS_URL = re.compile(r"(klettersteig|via-ferrata|viaferrata|via_ferrata|ferrata|klettersteige|kletterst)", re.I)
R_TOPO = re.compile(r"(topo|skizze|routenverlauf|ks[-_]?plan|klettersteigplan|routenplan)", re.I)
def sitemaps(start):
    """alle Sitemap-Adressen einer Seite (robots.txt + /sitemap.xml), Unter-Sitemaps eine Ebene tief"""
    u = urllib.parse.urlparse(start); host = u.scheme + "://" + u.netloc
    code, _, _, raw = holen(host + "/robots.txt", maxb=300_000)
    sm = re.findall(r"(?im)^sitemap:\s*(\S+)", raw.decode("utf-8", "replace")) if code == 200 else []
    sm = sm or [host + "/sitemap.xml", host + "/sitemap_index.xml"]
    locs, gesehen = [], set()
    def lies(x, tiefe):
        if x in gesehen or len(gesehen) > 60: return
        gesehen.add(x)
        code, ct, _, raw = holen(x, maxb=30_000_000)
        if code != 200: return
        t = raw
        if x.endswith(".gz") or raw[:2] == b"\x1f\x8b":
            import gzip
            try: t = gzip.decompress(raw)
            except Exception: return
        t = t.decode("utf-8", "replace")
        for l in re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*(\S+?)\s*(?:\]\]>)?\s*</loc>", t):
            l = html.unescape(l)
            if "<sitemapindex" in t[:2000] or re.search(r"sitemap[^/]*\.xml", l):
                if tiefe < 2: lies(l, tiefe + 1)
            else: locs.append(l)
    for x in sm: lies(x, 0)
    return locs
def json_ld_geo(s):
    for m in re.finditer(r'"latitude"\s*:\s*"?(-?\d+\.\d+)"?\s*,\s*"longitude"\s*:\s*"?(-?\d+\.\d+)', s): return float(m.group(1)), float(m.group(2))
    for m in re.finditer(r'data-lat(?:itude)?="(-?\d+\.\d+)"[^>]*data-(?:lng|lon|longitude)="(-?\d+\.\d+)"', s): return float(m.group(1)), float(m.group(2))
    return None
def seite_topos(url, raw):
    """Topo-Bilder/-PDFs einer Seite: Adresse, Linktext oder Alt-Text enthält „Topo“ o. ä."""
    s = raw.decode("utf-8", "replace")
    tit = re.search(r"<title>(.*?)</title>", s, re.S); h1 = re.search(r"<h1[^>]*>(.*?)</h1>", s, re.S)
    titel = " ".join(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x.group(1)))).strip() for x in (tit, h1) if x)
    out = []
    for m in re.finditer(r'<(a|img|source)\b([^>]*)>(.*?)(?=<(?:/a|a|img|source)\b)', s, re.S | re.I):
        tag, attr, nach = m.group(1).lower(), m.group(2), m.group(3)[:200]
        ziel = re.search(r'\b(?:href|src|data-src|srcset|data-srcset)="([^"\s]+)', attr)
        if not ziel: continue
        z = urllib.parse.urljoin(url, html.unescape(ziel.group(1)))
        if not re.search(r"\.(jpe?g|png|webp|gif|pdf)(\?|$)", z, re.I): continue
        alt = " ".join(re.findall(r'(?:alt|title|aria-label)="([^"]*)"', attr))
        txt = re.sub(r"<[^>]+>", " ", nach) if tag == "a" else ""
        if R_TOPO.search(urllib.parse.unquote(z)) or R_TOPO.search(alt) or R_TOPO.search(txt):
            if not VERBOTEN.search(z) and z not in [o["url"] for o in out]:
                out.append({"url": z, "typ": "p" if re.search(r"\.pdf(\?|$)", z, re.I) else "i", "text": re.sub(r"\s+", " ", (alt + " " + txt)).strip()[:120]})
    return titel[:200], json_ld_geo(s), out[:8]
def quellen(ordner):
    """Klettersteig-Seiten der Zusatzquellen finden (Sitemap, sonst Startseite) und Topo-Links mit Titel/Lage merken.
    Seiten mit unverändertem Inhalt werden nicht neu ausgewertet."""
    pfad = os.path.join(ordner, "quellen-stand.json"); st = lade(pfad, {"seiten": {}, "quellen": {}})
    q, _ = ks_quellen()
    import concurrent.futures as cf, threading
    lock = threading.Lock()
    def eine(qu):
        ziel = qu["url"]; host = urllib.parse.urlparse(ziel).netloc.replace("www.", "")
        kand = [l for l in sitemaps(ziel) if R_KS_URL.search(urllib.parse.unquote(l)) and urllib.parse.urlparse(l).netloc.endswith(host)]
        art = "Sitemap"
        if not kand:   # keine Sitemap: Startseite und dort verlinkte Klettersteig-Seiten
            art = "Startseite"
            code, ct, end, raw = holen(ziel)
            if code == 200:
                _, links = sammler.text_und_links(raw.decode("utf-8", "replace"))
                kand = [ziel] + [urllib.parse.urljoin(end, h).split("#")[0] for h in links if R_KS_URL.search(urllib.parse.unquote(h))]
        kand = [k for k in dict.fromkeys(kand) if not re.search(r"\.(jpe?g|png|pdf|gif|webp|zip)$", k, re.I)][:qu["max"]]
        seiten, mit = {}, 0
        for u in kand:
            alt = st["seiten"].get(u)
            if alt and alt.get("geprueft") == HEUTE: continue
            code, ct, end, raw = holen(u)
            if code != 200 or "html" not in ct: seiten[u] = {"fehler": code, "geprueft": HEUTE, "region": qu["region"]}; continue
            titel, geo, topos = seite_topos(end, raw)
            seiten[u] = {"titel": titel, "geo": geo, "topos": topos, "region": qu["region"], "geprueft": HEUTE}; mit += bool(topos)
        with lock:
            st["seiten"].update(seiten)
            st["quellen"][ziel] = {"region": qu["region"], "seiten": len(kand), "mit_topo": mit, "art": art, "geprueft": HEUTE}
            speichern(pfad, st)
        print(f"Quelle {qu['region']}: {ziel} – {len(kand)} Seiten ({art}), {mit} mit Topo", flush=True)
    with cf.ThreadPoolExecutor(int(os.environ.get("QUELLEN_PARALLEL", "8"))) as ex:
        for f in [ex.submit(eine, qu) for qu in q]:
            try: f.result()
            except Exception as e: print("Fehler Quelle:", str(e)[:120])
    n = sum(1 for v in st["seiten"].values() if v.get("topos"))
    print(f"Zusatzquellen: {len(st['seiten'])} Seiten, davon {n} mit Topo-Links")

# ---------------------------------------------------------------- Links prüfen
def link_pruefen(url, cache):
    """ok / tot / unklar mit Grund; Ergebnis 30 Tage gemerkt (zwei Versuche bei Fehlern)"""
    c = cache.get(url)
    if c and c.get("datum") and (time.time() - time.mktime(time.strptime(c["datum"], "%Y-%m-%d"))) < 30 * 86400 and c["ergebnis"] != "fehler": return c
    if VERBOTEN.search(url): r = {"ergebnis": "tot", "grund": "bergsteigen.com (kostenpflichtig)"}
    else:
        code, ct, end, raw = holen(url, maxb=4096, rng=True)
        if code in (0, 429, 500, 502, 503, 504): time.sleep(3); code, ct, end, raw = holen(url, maxb=4096, rng=True)
        typ = "pdf" if "pdf" in ct or raw[:4] == b"%PDF" else "bild" if ct.startswith("image/") or raw[:3] in (b"\xff\xd8\xff", b"\x89PN", b"GIF", b"RIF") else "html" if "html" in ct else ct.split(";")[0]
        if code == -1: r = {"ergebnis": "unklar", "grund": "robots.txt verbietet Prüfung"}
        elif code in (200, 206):
            if re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", url, re.I) and typ == "html": r = {"ergebnis": "tot", "grund": "Bild-Adresse liefert eine Webseite"}
            elif re.search(r"\.pdf(\?|$)", url, re.I) and typ == "html": r = {"ergebnis": "tot", "grund": "PDF-Adresse liefert eine Webseite"}
            else: r = {"ergebnis": "ok", "typ": typ, "ende": end if end != url else None}
        elif code in (404, 410): r = {"ergebnis": "tot", "grund": f"HTTP {code}"}
        elif code in (401, 403): r = {"ergebnis": "unklar", "grund": f"HTTP {code} (Seite sperrt Abrufe von Programmen)"}
        else: r = {"ergebnis": "fehler", "grund": f"HTTP {code} {ct}"[:80]}
    if r["ergebnis"] == "ok" and SCHWACH.search(url): r = {**r, "schwach": True}
    r["datum"] = HEUTE; cache[url] = r
    return r

# ---------------------------------------------------------------- Abgleich
def offiziell(url, off):
    h = urllib.parse.urlparse(url).netloc.lower()
    return any(re.search(p, h) for p in off)
def alte_liste():
    """bisherige feste Zuordnung aus index.html (OSMTOPO) + vf-osm.json"""
    s = open(os.path.join(REPO, "index.html"), encoding="utf-8").read()
    m = re.search(r"const OSMTOPO=(\[.*?\]\]);", s)
    ot = json.loads(m.group(1)) if m else []
    vo = lade(os.path.join(REPO, "vf-osm.json"), [])
    vt = (lade(os.path.join(REPO, "vf-topo.json"), {}) or {}).get("t", {})
    alt = []
    for i, r in enumerate(ot):
        v = vo[i] if i < len(vo) and abs(vo[i]["lat"] - r[0]) < 1e-3 else next((x for x in vo if name_key(x["n"]) == r[2] and abs(x["lat"] - r[0]) < 1e-3), None)
        alt.append({"lat": r[0], "lon": r[1], "key": r[2], "typ": r[3], "url": r[4], "name": v["n"] if v else r[2], "vf": (v or {}).get("best", {}) and v["best"]["s"] if v and v.get("best") else None})
    return alt, vt
def vf_match(steig, vfst):
    """wie vfMatch() in der App: Lage (≤ 2,5 km, mit Namen ≤ 4 km), Name entscheidet bei mehreren"""
    tk = norm_tok(steig["name"]); out = []
    for slug, e in vfst.items():
        if e.get("weg") or e.get("aussen") or e.get("lat") is None: continue
        d = dmin(steig["pts"], e["lon"], e["lat"])
        if d > 4000: continue
        ts = set(w for w in slug.split("-") if w not in BS_STOP) | set(norm_tok(e.get("titel") or ""))
        hit = sum(1 for w in tk if w in ts); nm = hit / len(tk) if tk else 0
        if d > 2500 and not hit: continue
        out.append({"s": slug, "d": d, "hit": hit, "nm": nm, "sc": d / 1000 - nm * 2.5})
    out.sort(key=lambda x: x["sc"])
    if not out: return None, []
    b = out[0]; close = [e for e in out[1:] if e["sc"] - b["sc"] < 0.35]
    if b["hit"] and all(e["hit"] < b["hit"] for e in close): return b, out[1:4]
    return None, out[:5]
def quellen_kandidaten(steig, qst):
    """Topo-Funde der Zusatzquellen zu einem Steig: alle Namensteile im Seitentitel; Lage (falls auf der Seite) ≤ 3 km"""
    tk = norm_tok(steig["name"]); out = []
    if not tk: return out
    for u, p in qst.get("seiten", {}).items():
        if not p.get("topos"): continue
        tt = set(norm_tok(p.get("titel")))
        hit = sum(1 for w in tk if w in tt)
        if hit < len(tk): continue
        d = dmin(steig["pts"], p["geo"][1], p["geo"][0]) if p.get("geo") else None
        if d is not None and d > 3000: continue
        for t in p["topos"]:
            if re.search(r"(lazy|placeholder|logo|icon|sprite|blank)\.", t["url"], re.I): continue
            if not (R_TOPO.search(urllib.parse.unquote(t["url"])) or R_TOPO.search(t.get("text", ""))): continue
            datei = set(norm_tok(urllib.parse.unquote(urllib.parse.urlparse(t["url"]).path.rsplit("/", 1)[-1])) + norm_tok(t.get("text", "")))
            out.append({"seite": u, "titel": p["titel"], "d": d, "url": t["url"], "typ": t["typ"], "text": t.get("text", ""), "anz": len(p["topos"]),
                        "region": p.get("region"), "datei_name": any(w in datei for w in tk)})
    return out

def abgleich(steigefile, ordner, grenzfile=None):
    steige = json.load(open(steigefile, encoding="utf-8"))
    vfst = lade(os.path.join(ordner, "vf-stand.json"), {})
    qst = lade(os.path.join(ordner, "quellen-stand.json"), {"seiten": {}})
    cache = lade(os.path.join(ordner, "links.json"), {})
    ent = lade(os.path.join(ordner, "entscheidungen.json"), {})
    vorher = {s["id"]: s for s in lade(os.path.join(ordner, "steige.json"), [])}   # Ergebnis des letzten Laufs
    alt, vt = alte_liste()
    _, off = ks_quellen()
    benutzt = set(); pruef = defaultdict(list); erg = []; stat = Counter()
    def merke(): speichern(os.path.join(ordner, "links.json"), cache)
    for i, s in enumerate(steige):
        sid = f"{s['key']}|{s['lat']:.3f}|{s['lon']:.3f}"
        # 1) frühere Zuordnung (gleicher Name ≤ 6 km, sonst anderer Name ≤ 60 m = umbenannt)
        best = None; passt = []
        stk = set(norm_tok(s["name"]))
        for j, a in enumerate(alt):
            d = dmin(s["pts"], a["lon"], a["lat"])
            if d > 6000: continue
            atk = set(norm_tok(a["name"]))
            same = a["key"] == s["key"] or (stk and atk == stk)          # gleicher Name (auch andere Wortfolge)
            if same: passt.append((j, d, True))
            elif d < 150 or (d < 1500 and stk & atk): passt.append((j, d, False))   # umbenannt / anders geschrieben
        # gleicher Name vor anderem Name, dann mit Topo vor ohne, dann nächster
        passt.sort(key=lambda x: (not x[2], alt[x[0]]["typ"] == "n", x[1]))
        if passt: best = passt[0]
        for x in passt: benutzt.add(x[0])
        a = alt[best[0]] if best else None
        r = {"id": sid, "osm_id": s["osm_id"], "ways": s["ways"], "name": s["name"], "key": s["key"], "lat": s["lat"], "lon": s["lon"],
             "land": s["land"], "region": s["region"], "bezirk": s["bezirk"], "gemeinde": s["gemeinde"], "laenge_m": s["laenge"],
             "osm_grad": s["osm_grad"], "alt": bool(a), "umbenannt": (a["name"] if a and not best[2] else None)}
        if a and not best[2]: pruef["Umbenannt (Kartendaten)"].append(f"{a['name']} → **{s['name']}** ({s['region']}) {osm_l(s)}")
        if not a: stat["neu"] += 1
        # 2) via-ferrata.de
        # früher gewählter via-ferrata.de-Eintrag zählt nur, wenn damals auch ein Topo daraus übernommen wurde
        slug = a["vf"] if a and a.get("vf") and a["typ"] != "n" and not (vfst.get(a["vf"]) or {}).get("weg") and vfst.get(a["vf"], {}).get("lat") is not None else None
        m, altern = vf_match(s, vfst)
        if not slug and m: slug = m["s"]
        if a and a.get("vf") and (vfst.get(a["vf"]) or {}).get("weg"): pruef["Eintrag auf via-ferrata.de nicht mehr vorhanden"].append(f"{s['name']} ({s['region']}): {VF}/klettersteige/topo/{a['vf']}")
        v = vfst.get(slug) or {} if slug else {}
        r["vf"] = slug; r["vf_titel"] = v.get("titel")
        r["grad"] = v.get("grad") or (s["osm_grad_max"] if s["osm_grad_max"] else None)
        r["grad_quelle"] = "via-ferrata.de" if v.get("grad") else ("Kartendaten" if s["osm_grad_max"] else None)
        r["hm"] = v.get("hm"); r["dauer_h"] = v.get("dauer_h"); r["gipfel_m"] = v.get("gipfel_m")
        if v.get("grad") and s["osm_grad_max"]:
            # vf-Schwierigkeit ist die schwerste Stelle; OSM: Höchstwert der Abschnitte – gleich = zwei Quellen
            go, gv = grad_ord(s["osm_grad_max"]), grad_ord(v["grad"].split("/")[-1]); gv0 = grad_ord(v["grad"].split("/")[0])
            r["grad_sicherheit"] = "bestaetigt" if gv0 <= go <= gv or abs(go - gv) == 0 else "unklar" if abs(go - gv) >= 2 else "eine_quelle"
            if abs(go - gv) >= 4: pruef["Schwierigkeit widersprüchlich"].append(f"{s['name']} ({s['region']}): via-ferrata.de {v['grad']}, Kartendaten {s['osm_grad']} {osm_l(s)}")
        else: r["grad_sicherheit"] = "eine_quelle" if r["grad"] else None
        vo = vorher.get(sid)
        if vo and vo.get("grad") and r["grad"] and vo["grad"] != r["grad"]:
            r["grad_vorher"] = vo["grad"]; stat["grad_geaendert"] += 1
        if v.get("vorher") and v["vorher"].get("grad"): r["grad_vorher"] = v["vorher"]["grad"]
        # 3) Zustand: Kartendaten-Flags und Hinweise (nie automatisch „gesperrt“, nur Prüfliste)
        f = s.get("flags") or {}
        zust, zgrund = "offen", None
        if any(k.startswith(("disused", "abandoned", "razed", "demolished")) for k in f) or f.get("lifecycle"): zust, zgrund = "abgebaut", "Kartendaten: " + ", ".join(k for k in f if k.startswith(("disused", "abandoned", "razed", "demolished")))
        elif f.get("access") in ("no", "private") or f.get("foot") == "no": zust, zgrund = "gesperrt", f"Kartendaten: access={f.get('access') or ''} foot={f.get('foot') or ''}".strip()
        elif f.get("construction") or f.get("opening_date", "") > HEUTE: zust, zgrund = "gesperrt", "Kartendaten: im Bau / Eröffnung " + (f.get("opening_date") or "")
        if zust != "offen": pruef["Zustand laut Kartendaten (bitte bestätigen)"].append(f"{s['name']} ({s['region']}): {zgrund} {osm_l(s)}")
        e = ent.get(sid) or ent.get(f"{s['key']}|{round(s['lat'], 2):.2f}|{round(s['lon'], 2):.2f}")
        if v.get("hinweis") and not (e and e.get("zustand")): pruef["Hinweis auf Sperre/Sanierung (via-ferrata.de)"].append(f"{s['name']} ({s['region']}): „…{v['hinweis'][0].strip()[:110]}…“ – {VF}/klettersteige/topo/{slug}")
        r["zustand"] = "unbekannt" if zust != "offen" else "offen"; r["zustand_hinweis"] = zgrund
        # 4) Topo
        topo = None; kand = []
        if a and a["typ"] != "n" and a["url"]:
            lk = link_pruefen(a["url"], cache)
            if lk["ergebnis"] != "tot": topo = {"typ": a["typ"], "url": a["url"], "quelle": "feste Zuordnung (bisher)", "link": lk}
            else: pruef["Toter Topo-Link (feste Zuordnung)"].append(f"{s['name']} ({s['region']}): {a['url']} – {lk.get('grund')}"); stat["tot"] += 1; r["topo_tot"] = a["url"]
        if v.get("topos"):
            for u in v["topos"]:
                if VERBOTEN.search(u): continue
                typ = "p" if re.search(r"\.pdf(\?|$)", u, re.I) else "i" if re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", u, re.I) else "w"
                kand.append({"url": u, "typ": typ, "quelle": f"via-ferrata.de, Feld „Topo zur Tour“ ({slug})", "stark": bool(m and m["s"] == slug and m["d"] < 2500 and m["hit"] >= 1) or bool(a and a.get("vf") == slug and a["typ"] != "n")})
        for c in quellen_kandidaten(s, qst):
            stark = (c["d"] is not None and c["d"] < 1500 and (c["anz"] == 1 or c["datei_name"])) or \
                    (c["datei_name"] and c["region"] == s["region"] and offiziell(c["seite"], off) and (c["d"] is None or c["d"] < 3000))
            if c["url"] not in [k["url"] for k in kand]:
                kand.append({"url": c["url"], "typ": c["typ"], "quelle": c["seite"], "stark": stark, "titel": c["titel"], "d": c["d"]})
        if not topo:
            for k in kand:
                if not k["stark"]: continue
                lk = link_pruefen(k["url"], cache)
                if lk["ergebnis"] == "ok" and not lk.get("schwach"):
                    topo = {"typ": k["typ"] if lk.get("typ") != "html" else "w", "url": k["url"], "quelle": k["quelle"], "link": lk}
                    if r.get("topo_tot"): stat["ersetzt"] += 1; r["topo_ersetzt"] = True
                    elif a and a["typ"] == "n" or not a: stat["topo_neu"] += 1; r["topo_neu"] = True
                    break
            if not topo and not (e and e.get("topo")):
                for k in kand:
                    if VERBOTEN.search(k["url"]): continue
                    lk = link_pruefen(k["url"], cache)
                    if lk["ergebnis"] != "tot":
                        pruef["Topo-Kandidat (bitte prüfen)"].append(f"{s['name']} ({s['region']}, {r['grad'] or '?'}): [{'Bild' if k['typ'] == 'i' else 'PDF' if k['typ'] == 'p' else 'Seite'}]({k['url']}) – Quelle {k['quelle']}"
                                                                    + (f", Seite „{k.get('titel', '')[:70]}“" if k.get("titel") else "") + (f", {k['d'] / 1000:.1f} km" if k.get("d") is not None else "") + f" · id `{sid}`")
        elif v.get("topos") and topo["url"] not in v["topos"] and not any(VERBOTEN.search(x) for x in v["topos"]):
            r["vf_topo_anders"] = v["topos"][0]
        if e:   # Entscheidung von Claude/Marcel hat Vorrang
            if e.get("topo") == "keins": topo = None
            elif e.get("topo"): topo = {"typ": e.get("typ", "i"), "url": e["topo"], "quelle": e.get("quelle", "von Claude geprüft"), "link": link_pruefen(e["topo"], cache), "geprueft": e.get("datum")}
            if e.get("zustand"): r["zustand"] = e["zustand"]; r["zustand_seit"] = e.get("seit"); r["zustand_bis"] = e.get("bis"); r["zustand_hinweis"] = e.get("grund"); r["zustand_quelle"] = e.get("zustand_quelle")
        if topo:
            topo["offiziell"] = offiziell(topo["url"], off)
            topo["sicherheit"] = "unklar" if topo["link"].get("schwach") or topo["link"]["ergebnis"] != "ok" else "bestaetigt" if topo["offiziell"] else "eine_quelle"
            topo.pop("link_alt", None)
            stat["mit_topo"] += 1
        r["topo"] = topo
        if vo and vo.get("topo") and topo and vo["topo"]["url"] != topo["url"]: r["topo_vorher"] = vo["topo"]["url"]; stat["topo_geaendert"] += 1
        erg.append(r)
        if i % 50 == 49: merke(); print(f"  {i + 1}/{len(steige)} abgeglichen")
    merke()
    weg = [a for j, a in enumerate(alt) if j not in benutzt]
    if grenzfile and os.path.exists(grenzfile):
        fnd = Finder(grenzfile); aussen = [a for a in weg if not fnd(a["lon"], a["lat"], "4")[0]]
        weg = [a for a in weg if a not in aussen]
        for a in aussen: pruef["Bisherige Liste, außerhalb des Abgleich-Gebiets (bleibt unverändert in der App)"].append(f"{a['name']} ({a['lat']:.4f}, {a['lon']:.4f})")
    for a in weg: pruef["Nicht mehr in den Kartendaten (bisherige Liste)"].append(f"{a['name']} ({a['lat']:.4f}, {a['lon']:.4f})" + (f" – Topo war {a['url']}" if a["url"] else ""))
    stat["nicht_mehr_gefunden"] = len(weg); stat["gesamt"] = len(erg); stat["alt"] = len(alt); stat["alt_mit_topo"] = sum(1 for a in alt if a["typ"] != "n")
    speichern(os.path.join(ordner, "steige.json"), erg)
    bericht(erg, stat, pruef, weg, ordner)

def osm_l(s): return f"[OSM](https://www.openstreetmap.org/way/{s['osm_id']})"

def bericht(erg, stat, pruef, weg, ordner):
    z = [f"# Klettersteige – Abgleich {HEUTE}", "",
         "Automatisch erzeugt von karte/klettersteige.py (ohne KI). Claude arbeitet nur die Prüfliste ab (pruefliste.md).", "",
         "## Zahlen", "",
         f"- Klettersteige in den Kartendaten (AT, Südtirol, CH, LI): **{stat['gesamt']}** (bisherige Liste: {stat['alt']}, davon {stat['alt_mit_topo']} mit Topo)",
         f"- mit Topo: **{stat['mit_topo']}**, ohne Topo: {stat['gesamt'] - stat['mit_topo']}",
         f"- neu in den Kartendaten: {stat['neu']}, nicht mehr gefunden: {stat['nicht_mehr_gefunden']}",
         f"- neue Topos: {stat['topo_neu']}, ersetzt: {stat['ersetzt']}, Topo geändert gegenüber letztem Lauf: {stat['topo_geaendert']}, tote Links: {stat['tot']}",
         f"- Schwierigkeit geändert: {stat['grad_geaendert']}", ""]
    z += ["## Je Region", "", "| Land | Region | Steige | mit Topo | neu |", "|---|---|---|---|---|"]
    g = defaultdict(lambda: [0, 0, 0])
    for r in erg: x = g[(r["land"], r["region"])]; x[0] += 1; x[1] += bool(r["topo"]); x[2] += not r["alt"]
    for (l, rg), x in sorted(g.items(), key=lambda t: (t[0][0], t[0][1] or "")): z.append(f"| {l} | {rg} | {x[0]} | {x[1]} | {x[2]} |")
    neu = [r for r in erg if not r["alt"]]
    if neu:
        z += ["", "## Neu in den Kartendaten", ""] + [f"- {r['name']} ({r['region']}, {r['gemeinde'] or ''}, {r['grad'] or '?'}) – Topo: {r['topo']['url'] if r['topo'] else 'keins'}" for r in neu]
    gg = [r for r in erg if r.get("grad_vorher")]
    if gg: z += ["", "## Schwierigkeit geändert", ""] + [f"- {r['name']}: {r['grad_vorher']} → {r['grad']}" for r in gg]
    tn = [r for r in erg if r.get("topo_neu") or r.get("topo_ersetzt") or r.get("topo_vorher")]
    if tn: z += ["", "## Topos neu oder ersetzt", ""] + [f"- {r['name']} ({r['region']}): {r['topo']['url']} – {r['topo']['quelle']} ({r['topo']['sicherheit']})" for r in tn]
    open(os.path.join(ordner, "bericht.md"), "w", encoding="utf-8").write("\n".join(z) + "\n")
    p = [f"# Klettersteige – Prüfliste {HEUTE}", "",
         "Für Claude: jeden Punkt kurz prüfen (nur ansehen, nichts kopieren). Ergebnis in daten/klettersteige/entscheidungen.json:",
         '`{"<id>": {"topo": "<url>" | "keins", "typ": "i|p|w", "quelle": "<Seite>", "zustand": "offen|gesperrt|abgebaut|saniert", "seit": "JJJJ-MM-TT", "bis": "JJJJ-MM-TT", "grund": "…", "zustand_quelle": "<url>", "datum": "JJJJ-MM-TT"}}`',
         "Danach den Ablauf „Klettersteige“ mit Modus „eintragen“ starten (liest keine Webseiten neu).", ""]
    for k, v in pruef.items():
        p += [f"## {k} ({len(v)})", ""] + [f"- {x}" for x in v] + [""]
    open(os.path.join(ordner, "pruefliste.md"), "w", encoding="utf-8").write("\n".join(p) + "\n")
    o = [f"# Klettersteige ohne Topo – Stand {HEUTE}", "", f"{sum(1 for r in erg if not r['topo'])} von {len(erg)} benannten Klettersteigen (AT, Südtirol, CH, LI) haben kein freies Topo.", ""]
    for r in sorted((r for r in erg if not r["topo"]), key=lambda r: (r["land"], r["region"] or "", r["lon"])):
        o.append(f"- {r['name']} ({r['region']}{', ' + r['gemeinde'] if r['gemeinde'] else ''}; {r['lat']:.3f}, {r['lon']:.3f})")
    open(os.path.join(ordner, "ohne-topo.md"), "w", encoding="utf-8").write("\n".join(o) + "\n")
    print("\n".join(z[:12]))
    print("Prüfliste:", {k: len(v) for k, v in pruef.items()})

# ---------------------------------------------------------------- Datenbank
SB_URL = os.environ.get("SB_URL", "https://vehrlrjegxlhxibrxqxm.supabase.co")
def sb(method, path, body=None, prefer=None):
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(SB_URL + "/rest/v1/" + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r: return r.read()
def db(ordner):
    if not os.environ.get("SUPABASE_SERVICE_KEY"): print("Kein SUPABASE_SERVICE_KEY – Datenbank bleibt unverändert"); return
    erg = json.load(open(os.path.join(ordner, "steige.json"), encoding="utf-8"))
    rows = []
    for r in erg:
        t = r["topo"] or {}
        sich = t.get("sicherheit") or r.get("grad_sicherheit") or "unklar"
        rows.append({"osm_typ": "way", "osm_id": r["osm_id"], "name": r["name"], "ort": r["gemeinde"] or r["bezirk"], "geom": f"SRID=4326;POINT({r['lon']} {r['lat']})",
                     "lat": r["lat"], "lng": r["lon"],
                     "land": "IT" if r["land"] == "IT" else r["land"], "region": r["region"], "bezirk": r["bezirk"], "gemeinde": r["gemeinde"],
                     "schwierigkeit": r["grad"], "laenge_m": r["laenge_m"], "hoehenmeter": r["hm"], "zustand": r["zustand"],
                     "topo_url": t.get("url"), "topo_frei": None if not t else False,   # nur Link gespeichert, Bild bleibt beim Anbieter
                     "hinweise": r.get("zustand_hinweis"),
                     "quelle": t.get("quelle") or ("via-ferrata.de" if r["vf"] else "OpenStreetMap"),
                     "quelle_url": (t.get("quelle") if str(t.get("quelle", "")).startswith("http") else f"{VF}/klettersteige/topo/{r['vf']}" if r["vf"] else f"https://www.openstreetmap.org/way/{r['osm_id']}"),
                     "pruef_datum": HEUTE, "sicherheit": sich, "status": "aktiv", "geaendert": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "daten": {"lauf": "klettersteige", "key": r["key"], "id": r["id"], "ways": r["ways"], "topo_typ": t.get("typ"), "topo_sicherheit": t.get("sicherheit"),
                               "topo_offiziell": t.get("offiziell"), "topo_geprueft": (t.get("link") or {}).get("datum"), "vf": r["vf"],
                               "grad_quelle": r["grad_quelle"], "grad_sicherheit": r.get("grad_sicherheit"), "osm_grad": r["osm_grad"], "dauer_h": r.get("dauer_h"),
                               "zustand_seit": r.get("zustand_seit"), "zustand_bis": r.get("zustand_bis"), "zustand_quelle": r.get("zustand_quelle"),
                               "hm_quelle": "via-ferrata.de (Höhenunterschied der ganzen Tour)" if r["hm"] else None, "laenge_quelle": "Kartendaten"}})
    for i in range(0, len(rows), 300):
        sb("POST", "gb_klettersteige?on_conflict=osm_typ,osm_id", rows[i:i + 300], "resolution=merge-duplicates,return=minimal")
    ids = {r["osm_id"] for r in erg}
    vorh = json.loads(sb("GET", "gb_klettersteige?select=osm_id&status=eq.aktiv&daten->>lauf=eq.klettersteige"))
    weg = [x["osm_id"] for x in vorh if x["osm_id"] not in ids]
    for i in range(0, len(weg), 100):
        sb("PATCH", f"gb_klettersteige?osm_typ=eq.way&osm_id=in.({','.join(map(str, weg[i:i + 100]))})", {"status": "nicht_mehr_gefunden", "pruef_datum": HEUTE}, "return=minimal")
    st = Counter(bool(r["topo"]) for r in erg)
    sb("POST", "gb_abgleich_laeufe", {"gruppe": "klettersteige", "region": "Österreich, Südtirol, Schweiz, Liechtenstein", "gestartet": HEUTE,
       "beendet": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "fortschritt": 100, "status": "fertig", "neu": sum(1 for r in erg if not r["alt"]),
       "geaendert": sum(1 for r in erg if r.get("topo_neu") or r.get("topo_ersetzt") or r.get("grad_vorher")), "nicht_gefunden": len(weg),
       "bericht": "daten/klettersteige/bericht.md", "daten": {"mit_topo": st[True], "ohne_topo": st[False]}}, "return=minimal")
    print(f"Datenbank: {len(rows)} Klettersteige eingetragen, {len(weg)} als nicht mehr gefunden markiert")

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    c = a[0]
    if c == "grenzen": grenzen(a[1], a[2])
    elif c == "osm": osm(a[1], a[2], a[3:])
    elif c == "zusammen": zusammen(a[1], a[2])
    elif c == "vf": vf(a[1])
    elif c == "quellen": quellen(a[1])
    elif c == "abgleich": abgleich(a[1], a[2], a[3] if len(a) > 3 else None)
    elif c == "db": db(a[1])
    else: print(__doc__); sys.exit(1)
