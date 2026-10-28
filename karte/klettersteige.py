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
def norm_tok(n):
    """Namensteile ohne Füllwörter; „familienklettersteig“ und „familien klettersteig“ ergeben dasselbe"""
    out = []
    for w in re.split(r"[^a-z0-9]+", _ascii(n)):
        if w not in BS_STOP and w.endswith("klettersteig") and len(w) > 12: w = w[:-12]
        if w.endswith("klettersteige") and len(w) > 13: w = w[:-13]
        if w and len(w) >= 3 and w not in BS_STOP: out.append(w)
    return out
def name_grad(n):
    """Schwierigkeit, die in den Kartendaten im Namen steht, z. B. „Spieljoch Klettersteig C“ oder „(D/E)“"""
    m = re.search(r"(?:^|[\s(])([A-F](?:\s*[/–-]\s*[A-F])?)\s*\)?\s*$", n or "") or re.search(r"\(([A-F](?:/[A-F])?)\)", n or "")
    return grad_norm(m.group(1)) if m else None
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
    h = {"User-Agent": sammler.UA, "Accept-Language": "de,en;q=0.5", "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
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
R_KS_URL = re.compile(r"(klettersteig|via-ferrata|viaferrata|via_ferrata|ferrata|klettersteige|kletterst)", re.I)
R_TOPO = re.compile(r"(topo|skizze|routenverlauf|ks[-_]?plan|klettersteigplan|routenplan|routenskizze|(^|[\s_-])ks[\s_-].*75\s?dpi)", re.I)
R_NAV = re.compile(r"(klettersteig|ferrata|klettern|climb|bergsport|alpin|tour|aktiv|activ|sommer|summer|erleben|outdoor|berg)", re.I)
R_UNWICHTIG = re.compile(r"(hotel|unterkunft|unterkuenfte|accommodation|lodging|gastro|event|veranstaltung|news|blog|presse|press|image|video|jobs|shop|angebot|pauschal|webcam|zimmer|ferienwohnung|apartment)", re.I)
R_BILD = re.compile(r"\.(jpe?g|png|webp|gif|pdf)(\?|$)", re.I)
R_GRAD = re.compile(r"(?<![A-Za-z0-9])([A-F])(?:\s*[/–-]\s*([A-F]))?(?![A-Za-z0-9])")
R_SPERRE = re.compile(r"\b(gesperrt|geschlossen|abgebaut|rückgebaut|rueckgebaut|demontiert|aufgelassen|außer betrieb|ausser betrieb|nicht begehbar|nicht mehr begehbar|chius[ao]|ferm[ée]e?|closed|dismantled)\b", re.I)
R_SPERRE_NICHT = re.compile(r"(straße|strasse|zufahrt|anfahrt|parkplatz|winter|seilbahn|bahn\b|lift|hütte|huette|winterraum|montag|dienstag|ruhetag|cookie|newsletter|öffnungszeit|oeffnungszeit|warenkorb|formular)", re.I)

def sitemaps(start, max_dateien=300):
    """alle Seiten-Adressen aus den Sitemaps einer Seite (robots.txt + /sitemap.xml), Unter-Sitemaps bis 2 Ebenen;
    Unter-Sitemaps für Hotels, Veranstaltungen usw. werden übersprungen"""
    u = urllib.parse.urlparse(start); host = u.scheme + "://" + u.netloc
    code, _, _, raw = holen(host + "/robots.txt", maxb=300_000)
    sm = re.findall(r"(?im)^sitemap:\s*(\S+)", raw.decode("utf-8", "replace")) if code == 200 else []
    sm = sm or [host + "/sitemap.xml", host + "/sitemap_index.xml"]
    locs, gesehen = [], set()
    def lies(x, tiefe):
        if x in gesehen or len(gesehen) > max_dateien: return
        gesehen.add(x)
        code, ct, _, raw = holen(x, maxb=60_000_000, timeout=120)
        if code != 200: return
        t = raw
        if x.endswith(".gz") or raw[:2] == b"\x1f\x8b":
            import gzip
            try: t = gzip.decompress(raw)
            except Exception: return
        t = t.decode("utf-8", "replace")
        index = "<sitemapindex" in t[:3000]
        for l in re.findall(r"<loc>\s*(?:<!\[CDATA\[)?\s*(\S+?)\s*(?:\]\]>)?\s*</loc>", t):
            l = html.unescape(l)
            if index or re.search(r"sitemap[^/]*\.xml", l):
                if tiefe < 2 and not (R_UNWICHTIG.search(l) and not R_NAV.search(l)): lies(l, tiefe + 1)
            else: locs.append(l)
    for x in sm: lies(x, 0)
    return locs

def kriechen(start, max_seiten=60):
    """keine Sitemap: von der Startseite aus über passende Links (Klettern, Touren, Sommer …) bis 2 Ebenen nach Klettersteig-Seiten suchen"""
    host = urllib.parse.urlparse(start).netloc
    gefunden, offen, gesehen = [], [(start, 0)], set()
    while offen and len(gesehen) < max_seiten:
        u, t = offen.pop(0)
        if u in gesehen: continue
        gesehen.add(u)
        code, ct, end, raw = holen(u)
        if code != 200 or "html" not in ct: continue
        _, links = sammler.text_und_links(raw.decode("utf-8", "replace"))
        for h in links:
            z = urllib.parse.urljoin(end, h).split("#")[0]
            if urllib.parse.urlparse(z).netloc != host or R_BILD.search(z): continue
            p = urllib.parse.unquote(urllib.parse.urlparse(z).path)
            if R_KS_URL.search(p): gefunden.append(z)
            elif t < 2 and R_NAV.search(p) and not R_UNWICHTIG.search(p): offen.append((z, t + 1))
    return list(dict.fromkeys(gefunden))

def json_ld_geo(s):
    for m in re.finditer(r'"latitude"\s*:\s*"?(-?\d+\.\d+)"?\s*,\s*"longitude"\s*:\s*"?(-?\d+\.\d+)', s): return float(m.group(1)), float(m.group(2))
    for m in re.finditer(r'data-lat(?:itude)?="(-?\d+\.\d+)"[^>]*data-(?:lng|lon|longitude)="(-?\d+\.\d+)"', s): return float(m.group(1)), float(m.group(2))
    return None

def bild_schluessel(u):
    """gleiches Bild in mehreren Größen erkennen"""
    m = re.search(r"image-thumb__(\d+)__", u) or re.search(r"/img2/(\d+)/", u) or re.search(r"/ImageStorage/([^/?]+)", u)
    if m: return m.group(1)
    b = urllib.parse.unquote(urllib.parse.urlparse(u).path.rsplit("/", 1)[-1]).lower()
    b = re.sub(r"(~-~.*|@\dx|[-_]\d{2,4}x\d{2,4}|\.[0-9a-f]{8}|_[0-9a-f]{10})(?=\.)", "", b)
    b = re.sub(r"^csm_", "", re.sub(r"\s+", "_", b))
    return re.sub(r"\.(jpe?g|png|webp|gif)$", "", b)

def bild_original(u):
    """Bild-Vermittler (z. B. climbers-paradise.com/images/<sig>/<opt>/<base64>) → ursprüngliche Bildadresse"""
    m = re.search(r"climbers-paradise\.com/images/[^/]+/[^/]+/([A-Za-z0-9_-]{20,})", u or "")
    if m:
        import base64
        try:
            o = base64.urlsafe_b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4)).decode()
            if o.startswith("http"): return o
        except Exception: pass
    return u

def seite_lesen(url, raw):
    """Fakten einer Klettersteig-Seite: Titel, Lage, Topo-Bilder/-PDFs, Schwierigkeit, Länge, Höhenmeter, Sperr-Hinweise"""
    s = raw.decode("utf-8", "replace")
    def text(x): return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x))).strip() if x else ""
    tit = re.search(r"<title[^>]*>(.*?)</title>", s, re.S); h1 = re.search(r"<h1[^>]*>(.*?)</h1>", s, re.S)
    ld = re.search(r'"@type"\s*:\s*"(?:TouristAttraction|Place|SportsActivityLocation|Trip|Route|TouristTrip|LandmarksOrHistoricalBuildings)"[^}]*?"name"\s*:\s*"([^"]+)"', s)
    d = {"titel": text(tit.group(1))[:200] if tit else "", "h1": text(h1.group(1))[:150] if h1 else "", "ldname": html.unescape(ld.group(1))[:150] if ld else "",
         "geo": json_ld_geo(s)}
    # Topo-Bilder: Bildadresse aus src/srcset/data-*, Topo-Hinweis im Dateinamen oder in Alt-/Titel-/Bildunterschrift
    kand = {}; alle = {}
    def nimm(z, txt):
        z = bild_original(urllib.parse.urljoin(url, html.unescape(z.strip())))
        z = re.sub(r"(oastatic\.com/img2/\d+)/[^/]+/variant\.\w+(\?.*)?$", r"\1/max/variant.jpg", z)   # größte Fassung
        if not R_BILD.search(z) or VERBOTEN.search(z) or re.search(r"(lazy|placeholder|logo|icon|sprite|blank)\.", z, re.I): return
        if len(alle) < 80: alle.setdefault(bild_schluessel(z), z)   # für umgezogene Bilder (gleicher Dateiname, neue Adresse)
        datei = urllib.parse.unquote(urllib.parse.urlparse(z).path.rsplit("/", 1)[-1])
        if not (R_TOPO.search(datei) or R_TOPO.search(txt or "")): return
        k = bild_schluessel(z); alt = kand.get(k)
        rang = (0 if z.lower().split("?")[0].endswith(".webp") else 1) + (1 if re.search(r"(lightbox|max|original|full|gross|large)", z, re.I) else 0)
        if not alt or rang > alt["rang"]:
            kand[k] = {"url": z, "typ": "p" if re.search(r"\.pdf(\?|$)", z, re.I) else "i", "text": re.sub(r"\s+", " ", txt or "").strip()[:120], "rang": rang}
    for m in re.finditer(r"<(img|a|source|div|figure|li|span)\b([^>]*)>", s, re.I):
        attrs = dict((k.lower(), v) for k, v in re.findall(r'([\w:-]+)\s*=\s*"([^"]*)"', m.group(2)))
        txt = " ".join(attrs.get(k, "") for k in ("alt", "title", "aria-label", "data-sub-html", "data-caption", "data-title"))
        if m.group(1).lower() == "a":
            nach = s[m.end():m.end() + 300]; txt += " " + text(nach.split("</a>", 1)[0])
        urls = []
        for k in ("data-srcset", "srcset", "data-src", "data-lazy-src", "data-original", "data-full", "data-image", "data-href", "src", "href"):
            if attrs.get(k):
                urls.append(attrs[k].split(",")[0].split()[0] if "srcset" in k else attrs[k])
        for z in urls:
            if R_BILD.search(bild_original(z)): nimm(z, txt); break
    for m in re.finditer(r"\{[^{}]{0,700}\}", s):   # eingebettete Bilddaten (JSON)
        o = m.group(0)
        if R_TOPO.search(o):
            for z in re.findall(r'"(?:url|contentUrl|src|image|href|original|large)"\s*:\s*"([^"]+?\.(?:jpe?g|png|webp|pdf)[^"]*)"', o.replace("\\/", "/")):
                t = " ".join(re.findall(r'"(?:title|name|alt|caption)"\s*:\s*"([^"]*)"', o)); nimm(z, t)
    for m in re.finditer(r'\{\s*"@type"\s*:\s*"ImageObject"[^{}]*\}', s):   # Bilder im JSON-LD über einen Bild-Vermittler (ohne Dateiendung)
        o = m.group(0).replace("\\/", "/"); u = (re.search(r'"(?:url|contentUrl)"\s*:\s*"([^"]+)"', o) or [None, ""])[1]
        if u and bild_original(u) != u: nimm(u, " ".join(re.findall(r'"(?:name|caption|description)"\s*:\s*"([^"]*)"', o)))
    d["topos"] = [{k: v for k, v in x.items() if k != "rang"} for x in sorted(kand.values(), key=lambda x: -x["rang"])][:6]
    d["bilder"] = alle
    zeilen, _ = sammler.text_und_links(s)
    for i, z in enumerate(zeilen):   # Empfehlungen anderer Touren am Seitenende nicht mitlesen
        if i > 15 and re.search(r"(?i)^(das könnte dich auch|das könnte sie auch|ähnliche touren|weitere touren|touren in der nähe|kartenempfehlungen|die schönsten|die \d+ schönsten|auch interessant|you might also|weitere klettersteige|andere klettersteige|empfohlene touren)", z):
            zeilen = zeilen[:i]; break
    # Schwierigkeit: Zeile mit „Schwierigkeit“, Wert in derselben oder den nächsten 2 kurzen Zeilen; Winter-Angaben zählen nicht
    grade = []
    for i, z in enumerate(zeilen):
        if re.search(r"(?i)(schwierigkeit|difficolt|difficult)", z) and not re.search(r"(?i)winter", z):
            teil = z.split(re.search(r"(?i)(schwierigkeit\w*|difficolt\w*|difficult\w*)", z).group(0), 1)[1][:40]
            # Wert direkt nach dem Wort (z. B. „Schwierigkeit: C“, „Schwierigkeitsgrad C/D“) oder allein in der nächsten Zeile
            g = re.match(r"\s*[:\-–]?\s*(?:\(?\s*)?([A-F])(?:\s*[/–-]\s*([A-F]))?(?![A-Za-z0-9])", teil)
            if not g:
                for x in zeilen[i + 1:i + 3]:
                    g = re.fullmatch(r"\s*(?:KS\s*)?([A-F])(?:\s*[/–-]\s*([A-F]))?\s*", x)
                    if g: break
            if g and re.match(r"\s*[:\-–]?\s*\(?\s*[A-F]\s*(bis|und|oder|,)\s*[A-F]", teil, re.I): g = None   # Bereich/Varianten: nicht eindeutig
            if g: grade.append(grad_norm(g.group(1) + ("/" + g.group(2) if g.group(2) else "")))
    d["grade"] = sorted(set(g for g in grade if g), key=grad_ord)
    tz = " | ".join(zeilen)
    m = re.search(r"(?i)(streckenlänge|klettersteiglänge|länge(?: des klettersteigs)?)[^\d|]{0,20}([\d.,]+)\s*(km|m)\b", tz)
    if m:
        try: d["laenge_m"] = round(float(m.group(2).replace(".", "").replace(",", ".")) * (1000 if m.group(3).lower() == "km" else 1))
        except ValueError: pass
    m = re.search(r"(?i)(höhenmeter|höhendifferenz|höhenunterschied|aufstieg)[^\d|]{0,20}([\d.]+)\s*(hm|m)\b", tz)
    if m:
        try: d["hm"] = int(m.group(2).replace(".", ""))
        except ValueError: pass
    d["hinweise"] = [z[:160] for z in zeilen if len(z) < 250 and R_SPERRE.search(z) and not R_SPERRE_NICHT.search(z)
                     and re.search(r"(?i)(klettersteig|steig\b|ferrata|route)", z)][:3]
    return d

def ks_quellen():
    q, off = [], []
    for l in open(os.path.join(HIER, "ks_quellen.txt"), encoding="utf-8"):
        l = l.strip()
        if not l or l.startswith("#"): continue
        if l.lower().startswith("offiziell;"): off.append(l.split(";", 1)[1].strip()); continue
        p = [x.strip() for x in l.split(";")]
        if len(p) >= 2: q.append({"region": p[0], "url": p[1], "max": int(p[2]) if len(p) > 2 and p[2].isdigit() else 200})
    return q, off

def quellen(ordner):
    """Klettersteig-Seiten der Zusatzquellen finden (Sitemap, sonst über Links) und Fakten je Seite merken.
    Schon heute gelesene Seiten werden nicht nochmal geholt (Fortsetzen nach Abbruch)."""
    pfad = os.path.join(ordner, "quellen-stand.json"); st = lade(pfad, {"seiten": {}, "quellen": {}})
    q, off = ks_quellen()
    nur = [x.strip() for x in os.environ.get("NUR_REGIONEN", "").split(",") if x.strip()]
    if nur: q = [x for x in q if x["region"] in nur]
    nh = [x.strip() for x in os.environ.get("NUR_HOSTS", "").split(",") if x.strip()]
    if nh: q = [x for x in q if any(h in x["url"] for h in nh)]
    import concurrent.futures as cf, threading
    lock = threading.Lock()
    def eine(qu):
        ziel = qu["url"]; host = urllib.parse.urlparse(ziel).netloc.replace("www.", "")
        pfad0 = urllib.parse.urlparse(ziel).path.rstrip("/")
        kand = [l for l in sitemaps(ziel) if R_KS_URL.search(urllib.parse.unquote(l)) and urllib.parse.urlparse(l).netloc.endswith(host)
                and (len(pfad0) < 4 or urllib.parse.urlparse(l).path.startswith(pfad0))]
        art = "Sitemap"
        if not kand:
            art = "Links"; kand = kriechen(ziel)
        kand = [k for k in dict.fromkeys(kand) if not R_BILD.search(k)][:qu["max"]]
        gesehen = set(kand)
        def dazu(links):   # Sitemaps sind oft lückenhaft: Klettersteig-Links auf gelesenen Seiten (z. B. Übersichten je Region) mitnehmen
            for l in links or []:
                l = l.split("#")[0]
                if l in gesehen or len(kand) >= qu["max"] or R_BILD.search(l): continue
                if urllib.parse.urlparse(l).netloc.endswith(host) and R_KS_URL.search(urllib.parse.unquote(l)) and not R_UNWICHTIG.search(l):
                    gesehen.add(l); kand.append(l)
        seiten, mit = {}, 0
        i = 0
        while i < len(kand):
            u = kand[i]; i += 1
            alt = st["seiten"].get(u)
            if alt and alt.get("geprueft") == HEUTE and "grade" in alt and "links" in alt: dazu(alt["links"]); continue
            code, ct, end, raw = holen(u)
            if code != 200 or "html" not in ct: seiten[u] = {"fehler": code, "geprueft": HEUTE, "region": qu["region"]}; continue
            dd = seite_lesen(end, raw); dd.update({"region": qu["region"], "geprueft": HEUTE, "offiziell": offiziell(end, off)})
            _, ls = sammler.text_und_links(raw.decode("utf-8", "replace"))
            ls = [urllib.parse.urljoin(end, l) for l in ls]
            dd["links"] = [l for l in dict.fromkeys(ls) if urllib.parse.urlparse(l).netloc.endswith(host) and R_KS_URL.search(urllib.parse.unquote(l))][:150]
            dazu(dd["links"])
            seiten[u] = dd; mit += bool(dd["topos"])
        with lock:
            st["seiten"].update(seiten)
            st["quellen"][ziel] = {"region": qu["region"], "seiten": len(kand), "mit_topo": mit, "art": art, "geprueft": HEUTE}
            speichern(pfad, st)
        print(f"Quelle {qu['region']}: {ziel} – {len(kand)} Seiten ({art}), {mit} mit Topo", flush=True)
    with cf.ThreadPoolExecutor(int(os.environ.get("QUELLEN_PARALLEL", "10"))) as ex:
        for f in [ex.submit(eine, qu) for qu in q]:
            try: f.result()
            except Exception as e: print("Fehler Quelle:", str(e)[:120])
    n = sum(1 for v in st["seiten"].values() if v.get("topos"))
    print(f"Zusatzquellen: {len(st['seiten'])} Seiten, davon {n} mit Topo-Links")


# ---------------------------------------------------------------- Outdooractive / alpenvereinaktiv (Plattform der Tourismusverbände und Alpenvereine)
OA = "https://www.outdooractive.com"
R_OA_AUSSEN = re.compile(r"/route/klettersteig/(italy|italia|italien|trentino|veneto|lombard|friuli|aosta|piemont|dolomiti-d-ampezzo|brenta|gardasee|lago-di|deutschland|germany|bayern|allgaeu|zugspitzregion|chiemsee|berchtesgaden|sauerland|eifel|mosel|pfalz|harz|frankreich|france|spain|spanien|slowen|sloven|kroat|croat|tschech|czech|polen|poland|norw|schwed|uk-|england|wales|schott)", re.I)
R_OA_OFFIZIELL = re.compile(r"(touris|tvb|ferienregion|region|gemeinde|stadt |alpenverein|alpenvereinaktiv|naturfreunde|bergbahn|seilbahn|nationalpark|naturpark|verkehrsverein|turismo|consorzio|ente|office|bergführer|bergfuehrer|outdoor-?center|gmbh)", re.I)
def oa_seite(url, raw):
    d = seite_lesen(url, raw)
    s = raw.decode("utf-8", "replace")
    zeilen, _ = sammler.text_und_links(s)
    org = None
    for i, z in enumerate(zeilen[:400]):
        if re.match(r"^Autor\b", z) and i + 1 < len(zeilen):
            org = zeilen[i + 1][:120]; break
    d["autor"] = org
    d["offiziell"] = bool(org and R_OA_OFFIZIELL.search(org))
    # Bildunterschriften im JSON-LD (Topo-Bilder), größere Fassung verwenden
    top = []
    for m in re.finditer(r'\{\s*"@type"\s*:\s*"ImageObject"[^{}]*\}', s):
        o = m.group(0); cap = (re.search(r'"caption"\s*:\s*"([^"]*)"', o) or [None, ""])[1]
        u = (re.search(r'"url"\s*:\s*"([^"]+)"', o) or [None, ""])[1]
        if u and R_TOPO.search(cap or ""):
            u = re.sub(r"/img2/(\d+)/[^/]+/variant\.\w+", r"/img2/\1/max/variant.jpg", u)
            if u not in [t["url"] for t in top]: top.append({"url": u, "typ": "i", "text": cap[:120]})
    if top: d["topos"] = top[:6]
    m = re.search(r'"fromLocation"\s*:\s*\{[^{}]*"geo"\s*:\s*\{[^{}]*"latitude"\s*:\s*"?(-?\d+\.\d+)"?\s*,\s*"longitude"\s*:\s*"?(-?\d+\.\d+)', s)
    if m: d["geo"] = (float(m.group(1)), float(m.group(2)))
    return d

def oa(ordner):
    """alle Klettersteig-Touren der Plattform über die Sitemap; nur neue oder seit 300 Tagen nicht gelesene Seiten holen"""
    pfad = os.path.join(ordner, "oa-stand.json"); st = lade(pfad, {})
    code, ct, _, raw = holen(OA + "/de/contents-index.xml", maxb=2_000_000, timeout=120)
    if code != 200: print("Outdooractive-Sitemap nicht erreichbar:", code); return
    teile = [html.unescape(x) for x in re.findall(r"<loc>\s*([^<]+?tour-sitemap[^<]+?)\s*</loc>", raw.decode("utf-8", "replace"))]
    urls = []
    for t in teile:
        c, _, _, r = holen(t, maxb=80_000_000, timeout=300)
        if c == 200: urls += [u for u in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", r.decode("utf-8", "replace")) if "/route/klettersteig/" in u and not R_OA_AUSSEN.search(u)]
    urls = list(dict.fromkeys(urls))
    jetzt = time.time(); todo = []
    for u in urls:
        e = st.get(u)
        if e and e.get("geprueft") and jetzt - time.mktime(time.strptime(e["geprueft"], "%Y-%m-%d")) < 300 * 86400: continue
        todo.append(u)
    mx = int(os.environ.get("MAX_OA", "9000")); print(f"Outdooractive: {len(urls)} Klettersteig-Touren (ohne fremde Länder), {len(todo)} zu lesen (höchstens {mx})", flush=True)
    for i, u in enumerate(todo[:mx]):
        c, ct, end, r = holen(u)
        if c != 200 or "html" not in ct: st[u] = {"fehler": c, "geprueft": HEUTE}; continue
        d = oa_seite(end, r); d.update({"region": "alle", "geprueft": HEUTE, "plattform": "outdooractive"})
        g = d.get("geo")
        if g and not (GEBIET[0] <= g[1] <= GEBIET[2] and GEBIET[1] <= g[0] <= GEBIET[3]): d = {"aussen": True, "geprueft": HEUTE, "geo": g}
        elif not g: d = {"ohne_lage": True, "geprueft": HEUTE, "titel": d.get("titel")}
        st[u] = d
        if i % 100 == 99: speichern(pfad, st); print(f"  {i + 1} gelesen", flush=True)
    for u in list(st):
        if u not in urls and not st[u].get("weg"): st[u]["weg"] = HEUTE
    speichern(pfad, st)
    n = sum(1 for v in st.values() if v.get("topos") and not v.get("aussen")); o = sum(1 for v in st.values() if v.get("offiziell") and not v.get("aussen"))
    print(f"Outdooractive fertig: {sum(1 for v in st.values() if v.get('titel') and not v.get('aussen'))} im Gebiet, {o} von offiziellen Anbietern, {n} mit Topo")

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
GENERISCH = {"seilbruecke", "uebung", "uebungssteig", "kamin", "ausstieg", "abstieg", "einstieg", "zustieg", "nordwand", "suedwand", "westgrat",
             "ostgrat", "nordgrat", "suedgrat", "normalweg", "kinder", "kinderklettersteig", "familienklettersteig", "familien", "notausstieg",
             "teil", "sektion", "leiter", "leitern", "bruecke", "haengebruecke", "panorama", "gipfel", "steige", "training", "basic", "nord", "sued", "ost", "west"}
ZUST_ART = [("abgebaut", re.compile(r"(abgebaut|rückgebaut|rueckgebaut|demontiert|aufgelassen|dismantled)", re.I)),
            ("gesperrt", re.compile(r"(gesperrt|geschlossen|außer betrieb|ausser betrieb|nicht begehbar|chius|ferm|closed)", re.I))]
MON_W = {"jaenner": 1, "jänner": 1, "januar": 1, "january": 1, "februar": 2, "february": 2, "maerz": 3, "märz": 3, "march": 3, "april": 4,
         "mai": 5, "may": 5, "juni": 6, "june": 6, "juli": 7, "july": 7, "august": 8, "september": 9, "oktober": 10, "october": 10,
         "november": 11, "dezember": 12, "december": 12,
         "jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "okt": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12}
R_HINW_IGNOR = re.compile(r"(do not|don't|should be|avoid|observe|keep to|wann werden|when are|nicht geschlossen|not closed|können|bei bedarf|en 958|blockades|users weighing|damage to the|beschädig|erkundigen sie sich vor|before you set off|folgende klettersteige|\bev\.|eventuell|evtl|möglicherweise|unter umständen|nach starkregen|bei nässe|bei gewitter|if necessary|may be closed|let you know|informieren sie sich|will inform|reasons for this)", re.I)
def _tag(t, m, ende=False):
    import calendar
    m = MON_W.get((m or "").lower().rstrip("."))
    if not m: return None
    if t is None: t = calendar.monthrange(2026, m)[1] if ende else 1
    return f"{m:02d}-{int(t):02d}"
def hinweis_art(h):
    """Sperr-Hinweis einordnen: None (allgemeiner Hinweis), ("saison", {...}), ("befristet", {"bis": JJJJ-MM-TT}), ("gesperrt"|"abgebaut", {})"""
    if not h or R_HINW_IGNOR.search(h): return None
    t = h.lower()
    if re.search(r"(abgebaut|rückgebaut|rueckgebaut|demontiert|dismantled|seile (wurden )?entfernt)", t): return ("abgebaut", {"text": h[:120]})
    if re.search(r"(nicht mehr begehbar|permanently closed|dauerhaft (geschlossen|gesperrt)|endgültig (geschlossen|gesperrt))", t): return ("gesperrt", {"text": h[:120], "dauerhaft": True})
    jaehrlich = re.search(r"(jeden jahres|jährlich|jaehrlich|jedes jahr|every year|annually|d\.\s?j\.)", t)
    M = r"(jänner|jaenner|januar|january|februar|february|märz|maerz|march|april|mai|may|juni|june|juli|july|august|september|oktober|october|november|dezember|december|jan|feb|mär|mar|apr|jun|jul|aug|sept|sep|okt|oct|nov|dez|dec)\.?"
    m = re.search(r"(?:vom|von|ab|from|zwischen)\s+(?:(\d{1,2})\.?\s*|anfang\s+|beginning of\s+)?" + M + r"\s+(?:bis|to|und|until)\s+(?:(\d{1,2})\.?\s*|ende\s+|end of\s+)?" + M, t)
    if not m:
        m2 = re.search(r"(?:from)\s+" + M + r"\s+(\d{1,2})\s+(?:to|until)\s+" + M + r"\s+(\d{1,2})", t)
        if m2: m = None; von, bis = _tag(m2.group(2), m2.group(1)), _tag(m2.group(4), m2.group(3))
        else: von = bis = None
    else:
        von, bis = _tag(m.group(1), m.group(2)), _tag(m.group(3), m.group(4), ende=True)
    if von and bis and not re.search(r"20\d\d", t): return ("saison", {"von": von, "bis": bis, "text": h[:120]})
    if jaehrlich or re.search(r"(karfreitag|ostern|pfingsten|saisonal|seasonal|wintersperre|winterpause|außerhalb der saison|ausserhalb der saison)", t): return ("saison", {"text": h[:120]})
    # befristet mit Datum (z. B. „bis 16.06.2025“, „from 27.10 through 30.10.2026“, „am 10. Oktober 2026“)
    ds = re.findall(r"(\d{1,2})\.(\d{1,2})\.(20\d\d)", t) + [(d, str(MON_W[mm]), y) for d, mm, y in re.findall(r"(\d{1,2})\.\s*" + M + r"\s+(20\d\d)", t)]
    if ds:
        bis = max(f"{y}-{int(mo):02d}-{int(d):02d}" for d, mo, y in ds)
        return ("befristet", {"bis": bis, "text": h[:120]})
    if re.search(r"(gesperrt|geschlossen|closed|chius|ferm|nicht begehbar|außer betrieb|ausser betrieb)", t): return ("gesperrt", {"text": h[:120]})
    return None

def zust_art(t):
    for a, r in ZUST_ART:
        if r.search(t or ""): return a
    return None

NACHBARN = {"Vorarlberg": ("Schweiz", "Liechtenstein", "Tirol"), "Tirol": ("Vorarlberg", "Salzburg", "Südtirol", "Schweiz", "Kärnten"),
            "Salzburg": ("Tirol", "Kärnten", "Steiermark", "Oberösterreich"), "Kärnten": ("Salzburg", "Steiermark", "Tirol"),
            "Steiermark": ("Oberösterreich", "Niederösterreich", "Kärnten", "Salzburg"), "Oberösterreich": ("Salzburg", "Steiermark", "Niederösterreich"),
            "Niederösterreich": ("Oberösterreich", "Steiermark"), "Südtirol": ("Tirol", "Schweiz"), "Schweiz": ("Vorarlberg", "Tirol", "Südtirol", "Liechtenstein"),
            "Liechtenstein": ("Vorarlberg", "Schweiz")}
def gleich(a, b): return a == b or (min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a)))
def teil(A, B): return all(any(gleich(a, b) for b in B) for a in A)   # A ⊆ B mit Wortanfängen („gauablick“ ~ „gauablickhoehle“)
class Seiten:
    """Index der gelesenen Quellseiten für die Zuordnung zu den Steigen"""
    def __init__(s, qst):
        s.l = []
        for u, p in qst.get("seiten", {}).items():
            if (p.get("fehler") is not None and "titel" not in p) or p.get("aussen") or p.get("weg") or p.get("ohne_lage"): continue
            slug = urllib.parse.unquote(urllib.parse.urlparse(u).path.rstrip("/").rsplit("/", 1)[-1])
            slug = re.sub(r"(_vc\d+|_tour_\d+|-\d{3,}$|\.html?$)", "", slug)
            pt = set(norm_tok(" ".join([p.get("titel", ""), p.get("h1", ""), p.get("ldname", ""), slug])))
            core = set(norm_tok(p.get("h1") or slug or p.get("ldname") or ""))   # JSON-LD-Name ist manchmal die Hütte
            if pt: s.l.append((u, p, pt, core))
    def fuer(s, steig):
        tk = set(norm_tok(steig["name"]))
        if not tk: return []
        generisch = tk <= GENERISCH
        out = []
        for u, p, pt, core in s.l:
            voll = teil(tk, pt)
            rueck = bool(core) and teil(core, tk) and (len(core) >= 2 or len(tk) <= 2) and not core <= GENERISCH
            if not (voll or rueck): continue
            d = dmin(steig["pts"], p["geo"][1], p["geo"][0]) if p.get("geo") else None
            if d is not None:
                if d > 3000: continue
            else:
                if generisch: continue
                reg = p.get("region")
                sreg = "Schweiz" if steig["land"] == "CH" else "Südtirol" if steig["land"] == "IT" else "Liechtenstein" if steig["land"] == "LI" else steig["region"]
                if not (reg == sreg or reg == "alle" or sreg in NACHBARN.get(reg, ())): continue
            reg = p.get("region")
            sicher = (d is not None) or (voll and len(tk - GENERISCH) >= 1 and reg not in ("alle", None))
            out.append({"url": u, "p": p, "d": d, "voll": voll, "sicher": sicher, "abweichung": len((core or pt) ^ tk) if core else len(tk - pt)})
        return out
    def nahe(s, steig, max_m=400):
        """Seiten mit Lage ganz in der Nähe, egal welcher Name (Kandidaten für Steige ohne Topo)"""
        return [{"url": u, "p": p, "d": dmin(steig["pts"], p["geo"][1], p["geo"][0])} for u, p, pt, core in s.l
                if p.get("geo") and p.get("topos") and dmin(steig["pts"], p["geo"][1], p["geo"][0]) <= max_m]

def abgleich(steigefile, ordner, grenzfile=None):
    steige = json.load(open(steigefile, encoding="utf-8"))
    vfst = lade(os.path.join(ordner, "vf-stand.json"), {})
    qst = lade(os.path.join(ordner, "quellen-stand.json"), {"seiten": {}})
    qst["seiten"].update(lade(os.path.join(ordner, "oa-stand.json"), {}))
    cache = lade(os.path.join(ordner, "links.json"), {})
    # alle Bilder der gelesenen Seiten: Schlüssel (Dateiname) → aktuelle Adresse, für umgezogene Topos (Seite umgebaut, gleiches Bild)
    BILDER = {}
    for u0, x0 in qst["seiten"].items():
        for k0, b0 in (x0.get("bilder") or {}).items(): BILDER.setdefault(k0, b0)
    def umgezogen(alt_url):
        b = BILDER.get(bild_schluessel(alt_url))
        if b and b != alt_url and link_pruefen(b, cache)["ergebnis"] == "ok":
            stat["umgezogen"] += 1; return b
        return None
    ent = lade(os.path.join(ordner, "entscheidungen.json"), {})
    vorher = {s["id"]: s for s in lade(os.path.join(ordner, "steige.json"), [])}   # Ergebnis des letzten Laufs
    alt, vt = alte_liste()
    _, off = ks_quellen()
    sx = Seiten(qst)
    benutzt = set(); pruef = defaultdict(list); erg = []; stat = Counter(); seite_benutzt = set()
    def merke(): speichern(os.path.join(ordner, "links.json"), cache)
    treffer = [sx.fuer(s) for s in steige]
    seite_steige = defaultdict(list)
    for s, t in zip(steige, treffer):
        for x in t: seite_steige[x["url"]].append(s)
    def exklusiv_grad(u):   # Schwierigkeit nur von Seiten, die genau einen Steig (Name) beschreiben
        return len({x["key"] for x in seite_steige[u]}) == 1
    def exklusiv_ort(u):    # Topo: alle passenden Steige liegen beieinander (Varianten am selben Ort)
        l = seite_steige[u]
        return all(hav(a["lon"], a["lat"], b["lon"], b["lat"]) < 3000 for a in l for b in l)
    HEUTE = time.strftime("%Y-%m-%d")
    def saison(h):   # jährliche oder befristete Sperre → nie „gesperrt“
        k = hinweis_art(h)
        if not k: return {"leer": True}
        if k[0] == "saison": return k[1]
        if k[0] == "befristet": return {"befristet": True, **k[1]}
        return None
    def art(h):
        k = hinweis_art(h)
        return k[0] if k and k[0] in ("gesperrt", "abgebaut") else None
    for i, s in enumerate(steige):
        sid = f"{s['key']}|{s['lat']:.3f}|{s['lon']:.3f}"
        # 1) frühere Zuordnung (gleicher Name ≤ 6 km, anderer Name ≤ 150 m oder mit gemeinsamem Namensteil ≤ 1,5 km = umbenannt)
        best = None; passt = []
        stk = set(norm_tok(s["name"]))
        for j, a in enumerate(alt):
            d = dmin(s["pts"], a["lon"], a["lat"])
            if d > 6000: continue
            atk = set(norm_tok(a["name"]))
            same = a["key"] == s["key"] or (stk and atk == stk)
            if same: passt.append((j, d, True))
            elif d < 150 or (d < 1500 and stk & atk): passt.append((j, d, False))
        passt.sort(key=lambda x: (not x[2], alt[x[0]]["typ"] == "n", x[1]))
        if passt: best = passt[0]
        for x in passt: benutzt.add(x[0])
        a = alt[best[0]] if best else None
        r = {"id": sid, "osm_id": s["osm_id"], "ways": s["ways"], "name": s["name"], "key": s["key"], "lat": s["lat"], "lon": s["lon"],
             "land": s["land"], "region": s["region"], "bezirk": s["bezirk"], "gemeinde": s["gemeinde"], "laenge_m": s["laenge"],
             "osm_grad": s["osm_grad"], "alt": bool(a), "umbenannt": (a["name"] if a and not best[2] else None)}
        if a and not best[2]: pruef["Umbenannt (Kartendaten)"].append(f"{a['name']} → **{s['name']}** ({s['region']}) {osm_l(s)}")
        if not a: stat["neu"] += 1
        e = ent.get(sid) or ent.get(f"{s['key']}|{round(s['lat'], 2):.2f}|{round(s['lon'], 2):.2f}")
        # 2) Quellseiten (Tourismus, Alpenverein …) mit passendem Namen und passender Lage/Region
        seiten = treffer[i]
        offi = [x for x in seiten if x["p"].get("offiziell") and x["sicher"]]
        if offi:   # nur die am besten passenden Seiten (z. B. „Familienklettersteig Partnunblick“ ≠ „Klettersteig Partnunblick“)
            beste = min(x["abweichung"] for x in offi); offi = [x for x in offi if x["abweichung"] == beste]
        for x in seiten: seite_benutzt.add(x["url"])
        r["quellen_seiten"] = [x["url"] for x in offi][:5]
        # 3) via-ferrata.de (früher gewählter Eintrag zählt nur, wenn damals auch ein Topo daraus übernommen wurde)
        slug = a["vf"] if a and a.get("vf") and a["typ"] != "n" and not (vfst.get(a["vf"]) or {}).get("weg") and vfst.get(a["vf"], {}).get("lat") is not None else None
        m, altern = vf_match(s, vfst)
        if m and "winter" in m["s"] and "winter" not in s["key"]: m = None   # z. B. Winterklettersteig Burg ≠ Klettersteig Burg
        if slug and "winter" in slug and "winter" not in s["key"]: slug = None
        if not slug and m: slug = m["s"]
        if a and a.get("vf") and (vfst.get(a["vf"]) or {}).get("weg"): pruef["Eintrag auf via-ferrata.de nicht mehr vorhanden"].append(f"{s['name']} ({s['region']}): {VF}/klettersteige/topo/{a['vf']}")
        v = (vfst.get(slug) or {}) if slug else {}
        r["vf"] = slug; r["vf_titel"] = v.get("titel")
        # 4) Schwierigkeit: offizielle Seite vor via-ferrata.de vor Kartendaten
        offi_g = [x for x in offi if len(x["p"].get("grade", [])) == 1 and exklusiv_grad(x["url"])]
        og = sorted({x["p"]["grade"][0] for x in offi_g}, key=grad_ord)   # Seiten mit mehreren Werten oder für mehrere Steige zählen nicht
        ng = name_grad(s["name"])
        if len(og) == 1 or (len(og) == 2 and "/" not in og[0] + og[1] and abs(grad_ord(og[0]) - grad_ord(og[1])) <= 2):
            r["grad"] = og[0] if len(og) == 1 else og[0] + "/" + og[1]
            gq = offi_g[0]
            r["grad_quelle"] = (gq["p"].get("autor") or urllib.parse.urlparse(gq["url"]).netloc.replace("www.", ""))[:80]
            r["grad_sicherheit"] = "bestaetigt"
            if v.get("grad") and v["grad"] != r["grad"] and abs(grad_ord(v["grad"].split("/")[-1]) - grad_ord(r["grad"].split("/")[-1])) >= 2:
                pruef["Schwierigkeit: offizielle Seite und via-ferrata.de verschieden (offizielle gilt)"].append(f"{s['name']} ({s['region']}): offiziell {r['grad']} ({r['grad_quelle']}), via-ferrata.de {v['grad']}")
        else:
            if len(og) > 1: pruef["Schwierigkeit auf offiziellen Seiten verschieden"].append(f"{s['name']} ({s['region']}): {', '.join(og)} – " + ", ".join(x["url"] for x in offi if x["p"].get("grade"))[:300])
            r["grad"] = ng or v.get("grad") or (s["osm_grad_max"] if s["osm_grad_max"] else None)
            r["grad_quelle"] = "Name in den Kartendaten" if ng else "via-ferrata.de" if v.get("grad") else ("Kartendaten" if s["osm_grad_max"] else None)
            if ng: r["grad_sicherheit"] = "bestaetigt" if v.get("grad") == ng else "eine_quelle"
            elif v.get("grad") and s["osm_grad_max"]:
                go, gv = grad_ord(s["osm_grad_max"]), grad_ord(v["grad"].split("/")[-1]); gv0 = grad_ord(v["grad"].split("/")[0])
                r["grad_sicherheit"] = "bestaetigt" if gv0 <= go <= gv else "unklar" if abs(go - gv) >= 2 else "eine_quelle"
                if abs(go - gv) >= 4: pruef["Schwierigkeit widersprüchlich (keine offizielle Angabe)"].append(f"{s['name']} ({s['region']}): via-ferrata.de {v['grad']}, Kartendaten {s['osm_grad']} {osm_l(s)}")
            else: r["grad_sicherheit"] = "eine_quelle" if r["grad"] else None
        r["hm"] = next((x["p"]["hm"] for x in offi if x["p"].get("hm")), None) or v.get("hm"); r["dauer_h"] = v.get("dauer_h"); r["gipfel_m"] = v.get("gipfel_m")
        r["laenge_offiziell_m"] = next((x["p"]["laenge_m"] for x in offi if x["p"].get("laenge_m")), None)
        vo = vorher.get(sid)
        if vo and vo.get("grad") and r["grad"] and vo["grad"] != r["grad"]:
            r["grad_vorher"] = vo["grad"]; stat["grad_geaendert"] += 1
        # 5) Zustand: gesperrt/abgebaut nur mit zwei seriösen Quellen (offizielle Seiten, via-ferrata.de) und ohne Gegenbeleg
        belege = defaultdict(set); offi_bel = defaultdict(dict); r["sperre_saison"] = None
        for x in seiten:
            if re.search(r"/(blog|news|magazin|aktuelles|presse)(/|$)", x["url"], re.I): continue   # Artikel über mehrere Steige zählen nicht für den Zustand
            for h in x["p"].get("hinweise", []):
                sz = saison(h)
                if sz:   # jährliche Sperre (z. B. 1.12.–15.3.) – offizielle Seite genügt, Steig bleibt sonst offen
                    if sz.get("befristet"):
                        zl = f"{s['name']} ({s['region']}): bis {sz['bis']} – {sz['text']} – {x['url']}"
                        if sz["bis"] >= HEUTE and x["sicher"] and zl not in pruef["Befristete Sperre (bleibt offen, Datum beachten)"]: pruef["Befristete Sperre (bleibt offen, Datum beachten)"].append(zl)
                    elif not sz.get("leer") and x["p"].get("offiziell") and x["sicher"] and not r["sperre_saison"]: r["sperre_saison"] = {**sz, "quelle": x["url"]}
                    continue
                k = art(h)
                if k:
                    belege[k].add(x["url"])
                    if x["p"].get("offiziell") and x["sicher"]: offi_bel[k].setdefault(x["url"], h)
        if v.get("hinweis"):
            k = next((art(h) for h in v["hinweis"] if art(h)), None)
            if k: belege[k].add(f"{VF}/klettersteige/topo/{slug}")
        f = s.get("flags") or {}
        osm_z = None
        if any(k.startswith(("disused", "abandoned", "razed", "demolished")) for k in f) or f.get("lifecycle"): osm_z = "abgebaut"
        elif f.get("access") in ("no", "private") or f.get("foot") == "no" or f.get("construction"): osm_z = "gesperrt"
        r["zustand"] = "offen"; r["zustand_hinweis"] = None
        ohne_hinweis = [x["url"] for x in offi if not any(art(h) for h in x["p"].get("hinweise", []))]
        for k in ("abgebaut", "gesperrt"):
            hosts = {urllib.parse.urlparse(u).netloc.replace("www.", "") for u in belege[k]}
            if not hosts and osm_z != k: continue
            # Marcel (28.10.2026): „wenn die offiziellen seiten stimmen dann stimmt es auch“ – Hinweis auf einer offiziellen Seite
            # (Tourismusverband, Gemeinde, Alpenverein, Betreiber) genügt; sonst zwei Seiten und keine offizielle ohne Hinweis
            if offi_bel[k] or (len(hosts) >= 2 and not ohne_hinweis):
                r["zustand"] = k; r["zustand_quellen"] = sorted(offi_bel[k]) or sorted(belege[k])
                txt = next(iter(offi_bel[k].values()), None)
                r["zustand_hinweis"] = (txt or f"{k} laut {', '.join(sorted(hosts))}")[:160]
                stat["zustand_" + k] += 1
                pruef[f"Zustand „{k}“ gesetzt (offizielle Seite oder zwei Quellen)"].append(f"{s['name']} ({s['region']}): {r['zustand_hinweis']} – {', '.join(r['zustand_quellen'][:3])} · id `{sid}`")
            else:
                quellen_t = ", ".join(sorted(belege[k])) or "nur Kartendaten"
                gegen = f"; aktuell beschrieben auf {', '.join(ohne_hinweis[:2])}" if ohne_hinweis else ""
                pruef[f"Hinweis „{k}“ – nur eine Quelle oder Gegenbeleg (bleibt offen)"].append(f"{s['name']} ({s['region']}): {quellen_t}{gegen} {osm_l(s)} · id `{sid}`")
            break
        # 6) Topo
        topo = None; kand = []
        if a and a["typ"] != "n" and a["url"]:
            lk = link_pruefen(a["url"], cache)
            neu_u = umgezogen(a["url"]) if lk["ergebnis"] == "tot" else None
            if neu_u: topo = {"typ": a["typ"], "url": neu_u, "quelle": "feste Zuordnung (bisher), gleiches Bild unter neuer Adresse", "link": link_pruefen(neu_u, cache), "umgezogen_von": a["url"]}
            elif lk["ergebnis"] != "tot": topo = {"typ": a["typ"], "url": a["url"], "quelle": "feste Zuordnung (bisher)", "link": lk}
            else: pruef["Toter Topo-Link (feste Zuordnung)"].append(f"{s['name']} ({s['region']}): {a['url']} – {lk.get('grund')}"); stat["tot"] += 1; r["topo_tot"] = a["url"]
        for x in seiten:   # offizielle Seiten zuerst
            p = x["p"]; tops = p.get("topos") or []
            for t in tops:
                datei = set(norm_tok(urllib.parse.unquote(urllib.parse.urlparse(t["url"]).path.rsplit("/", 1)[-1]) + " " + t.get("text", "")))
                namensbild = bool(datei & (stk - GENERISCH))
                stark = x in offi and p.get("offiziell") and x["sicher"] and x["voll"] and exklusiv_ort(x["url"]) and (len(tops) == 1 or namensbild) and (x["d"] is None or x["d"] < 3000)
                kand.append({"url": t["url"], "typ": t["typ"], "quelle": x["url"], "stark": bool(stark), "offi": bool(p.get("offiziell")), "titel": p.get("ldname") or p.get("h1") or p.get("titel"), "d": x["d"]})
        for x in sx.nahe(s):
            for t in x["p"]["topos"][:1]:
                kand.append({"url": t["url"], "typ": t["typ"], "quelle": x["url"], "stark": False, "offi": bool(x["p"].get("offiziell")),
                             "titel": (x["p"].get("ldname") or x["p"].get("h1") or x["p"].get("titel")), "d": x["d"]})
        if v.get("topos"):
            for u in v["topos"]:
                if VERBOTEN.search(u): continue
                typ = "p" if re.search(r"\.pdf(\?|$)", u, re.I) else "i" if re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", u, re.I) else "w"
                kand.append({"url": u, "typ": typ, "quelle": f"via-ferrata.de, Feld „Topo zur Tour“ ({slug})", "offi": False,
                             "stark": bool(m and m["s"] == slug and m["d"] < 2500 and m["hit"] >= 1) or bool(a and a.get("vf") == slug and a["typ"] != "n")})
        kand = list({k["url"]: k for k in reversed(kand)}.values())[::-1]
        kand.sort(key=lambda k: (not k["stark"], not k["offi"]))
        # bisheriges Topo nicht offiziell oder schwach → offizielles starkes Topo bevorzugen
        exakt = [x for x in offi if x["abweichung"] == 0 and x["p"].get("topos")]
        exakt_bilder = {bild_schluessel(t["url"]) for x in exakt for t in x["p"]["topos"]}
        if topo and (not offiziell(topo["url"], off) or (exakt_bilder and bild_schluessel(topo["url"]) not in exakt_bilder)):
            for k in kand:
                if k["stark"] and k["offi"] and k["url"] != topo["url"] and (not exakt_bilder or bild_schluessel(k["url"]) in exakt_bilder):
                    lk = link_pruefen(k["url"], cache)
                    if lk["ergebnis"] == "ok" and not lk.get("schwach"):
                        r["topo_vorher"] = topo["url"]; topo = {"typ": k["typ"] if lk.get("typ") != "html" else "w", "url": k["url"], "quelle": k["quelle"], "link": lk, "offi": k.get("offi")}
                        stat["offiziell_ersetzt"] += 1; r["topo_offiziell_ersetzt"] = True
                    break
        if e and e.get("nicht_topo"): kand = [] if e["nicht_topo"] == "alle" else [k for k in kand if k["url"] not in e["nicht_topo"]]   # von Claude/Marcel abgelehnt
        if not topo:
            for k in kand:
                if not k["stark"]: continue
                lk = link_pruefen(k["url"], cache)
                if lk["ergebnis"] == "ok" and not lk.get("schwach"):
                    topo = {"typ": k["typ"] if lk.get("typ") != "html" else "w", "url": k["url"], "quelle": k["quelle"], "link": lk, "offi": k.get("offi")}
                    if r.get("topo_tot"): stat["ersetzt"] += 1; r["topo_ersetzt"] = True
                    else: stat["topo_neu"] += 1; r["topo_neu"] = True
                    break
            if not topo and not (e and e.get("topo")):
                for k in kand[:4]:
                    lk = link_pruefen(k["url"], cache)
                    if lk["ergebnis"] != "tot":
                        pruef["Topo-Kandidat (bitte prüfen)"].append(f"{s['name']} ({s['region']}, {r['grad'] or '?'}): [{'Bild' if k['typ'] == 'i' else 'PDF' if k['typ'] == 'p' else 'Seite'}]({k['url']}) – Quelle {k['quelle']}"
                                                                    + (f", Seite „{(k.get('titel') or '')[:70]}“" if k.get("titel") else "") + (f", {k['d'] / 1000:.1f} km" if k.get("d") is not None else "") + f" · id `{sid}`")
        if e:   # Entscheidung von Claude/Marcel hat Vorrang
            if e.get("topo") == "keins": topo = None
            elif e.get("topo"):
                eu = e["topo"]; lk = link_pruefen(eu, cache)
                nu = umgezogen(eu) if lk["ergebnis"] == "tot" else None
                if nu: eu = nu; lk = link_pruefen(eu, cache)
                elif lk["ergebnis"] == "tot": pruef["Toter Topo-Link (Entscheidung)"].append(f"{s['name']} ({s['region']}): {eu} – {lk.get('grund')} · id `{sid}`")
                topo = {"typ": e.get("typ", "i"), "url": eu, "quelle": e.get("quelle", "von Claude geprüft"), "link": lk, "geprueft": e.get("datum")}
            if e.get("zustand") in ("gesperrt", "abgebaut") and len(e.get("zustand_quellen") or []) < 2:
                pruef["Entscheidung „gesperrt/abgebaut“ ohne zwei Quellen – nicht übernommen"].append(f"{s['name']}: id `{sid}`")
            elif e.get("zustand"):
                r["zustand"] = e["zustand"]; r["zustand_seit"] = e.get("seit"); r["zustand_bis"] = e.get("bis"); r["zustand_hinweis"] = e.get("grund")
                r["zustand_quellen"] = e.get("zustand_quellen") or ([e["zustand_quelle"]] if e.get("zustand_quelle") else None)
            if e.get("grad"): r["grad"] = e["grad"]; r["grad_quelle"] = e.get("grad_quelle", "von Claude geprüft"); r["grad_sicherheit"] = "bestaetigt"
        if topo:
            topo["offiziell"] = bool(topo.pop("offi", None)) or offiziell(topo["url"], off) or offiziell(topo.get("quelle", ""), off)
            topo["sicherheit"] = "unklar" if topo["link"].get("schwach") or topo["link"]["ergebnis"] != "ok" else "bestaetigt" if topo["offiziell"] else "eine_quelle"
            stat["mit_topo"] += 1
        r["topo"] = topo
        if vo and vo.get("topo") and topo and vo["topo"]["url"] != topo["url"] and not r.get("topo_vorher"): r["topo_vorher"] = vo["topo"]["url"]; stat["topo_geaendert"] += 1
        erg.append(r)
        if i % 50 == 49: merke(); print(f"  {i + 1}/{len(steige)} abgeglichen")
    merke()
    weg = [a for j, a in enumerate(alt) if j not in benutzt]
    if grenzfile and os.path.exists(grenzfile):
        fnd = Finder(grenzfile); aussen = [a for a in weg if not fnd(a["lon"], a["lat"], "4")[0]]
        weg = [a for a in weg if a not in aussen]
        for a in aussen: pruef["Bisherige Liste, außerhalb des Abgleich-Gebiets (bleibt unverändert in der App)"].append(f"{a['name']} ({a['lat']:.4f}, {a['lon']:.4f})")
    # Verschwundene: Topo jetzt bei Abschnitten in der Nähe (aufgeteilt) oder offizielle Seite kennt den Steig noch
    echt = []
    for a in weg:
        teile = [r["name"] for r in erg if r["topo"] and a["url"] and r["topo"]["url"] == a["url"] and hav(r["lon"], r["lat"], a["lon"], a["lat"]) < 6000]
        if teile:
            pruef["In den Kartendaten in Abschnitte aufgeteilt (gleiches Topo, nicht verschwunden)"].append(f"{a['name']} → {', '.join(sorted(set(teile)))}"); continue
        atk = set(norm_tok(a["name"]))
        offi = [u for u, p, pt, core in sx.l if atk and atk <= pt and p.get("offiziell") and (not p.get("geo") or hav(p["geo"][1], p["geo"][0], a["lon"], a["lat"]) < 5000)]
        if offi:
            pruef["Laut offizieller Seite vorhanden, in den Kartendaten anders benannt"].append(f"{a['name']} ({a['lat']:.4f}, {a['lon']:.4f}) – {offi[0]}"); continue
        echt.append(a)
    for a in echt: pruef["Nicht mehr in den Kartendaten (bisherige Liste)"].append(f"{a['name']} ({a['lat']:.4f}, {a['lon']:.4f})" + (f" – Topo war {a['url']}" if a["url"] else ""))
    # offizielle Klettersteig-Seiten mit Topo, die zu keinem Steig der Kartendaten passen (fehlt in OSM oder anders benannt)
    rest = [(u, p) for u, p, pt, core in sx.l if p.get("offiziell") and p.get("topos") and u not in seite_benutzt
            and not re.search(r"/(en|nl|it|fr|cs|pl|da|es|sv|hu)(/|-[a-z]{2}/)", u) and not re.search(r"/(klettersteige|via-ferrata|klettern)/?$", u)]
    for u, p in rest[:150]:
        pruef["Offizielle Seite mit Topo ohne passenden Steig in den Kartendaten"].append(f"„{(p.get('ldname') or p.get('h1') or p.get('titel'))[:70]}“ ({p.get('region')}) – {u}")
    stat["nicht_mehr_gefunden"] = len(echt); stat["gesamt"] = len(erg); stat["alt"] = len(alt); stat["alt_mit_topo"] = sum(1 for a in alt if a["typ"] != "n")
    stat["offizielle_seiten"] = sum(1 for u, p, pt, core in sx.l if p.get("offiziell"))
    speichern(os.path.join(ordner, "steige.json"), erg)
    bericht(erg, stat, pruef, echt, ordner)

def osm_l(s): return f"[OSM](https://www.openstreetmap.org/way/{s['osm_id']})"

def bericht(erg, stat, pruef, weg, ordner):
    z = [f"# Klettersteige – Abgleich {HEUTE}", "",
         "Automatisch erzeugt von karte/klettersteige.py (ohne KI). Claude arbeitet nur die Prüfliste ab (pruefliste.md).", "",
         "## Zahlen", "",
         f"- Klettersteige in den Kartendaten (AT, Südtirol, CH, LI): **{stat['gesamt']}** (bisherige Liste: {stat['alt']}, davon {stat['alt_mit_topo']} mit Topo)",
         f"- mit Topo: **{stat['mit_topo']}**, ohne Topo: {stat['gesamt'] - stat['mit_topo']}",
         f"- neu in den Kartendaten: {stat['neu']}, nicht mehr gefunden: {stat['nicht_mehr_gefunden']}",
         f"- neue Topos: {stat['topo_neu']}, ersetzt: {stat['ersetzt']}, Topo geändert gegenüber letztem Lauf: {stat['topo_geaendert']}, tote Links: {stat['tot']}, umgezogene Bilder (gleiches Bild, neue Adresse): {stat['umgezogen']}",
         f"- durch offizielles Topo ersetzt (bisher nicht offiziell): {stat['offiziell_ersetzt']}",
         f"- Schwierigkeit geändert: {stat['grad_geaendert']}; Schwierigkeit von offizieller Seite: {sum(1 for r in erg if r.get('grad_sicherheit') == 'bestaetigt' and r.get('grad_quelle') not in ('via-ferrata.de', 'Kartendaten'))}",
         f"- gesperrt (2 Quellen): {stat['zustand_gesperrt']}, abgebaut (2 Quellen): {stat['zustand_abgebaut']}",
         f"- gelesene offizielle Seiten: {stat['offizielle_seiten']}, Steige mit offizieller Seite: {sum(1 for r in erg if r.get('quellen_seiten'))}", ""]
    z += ["## Je Region", "", "| Land | Region | Steige | mit Topo | neu |", "|---|---|---|---|---|"]
    g = defaultdict(lambda: [0, 0, 0])
    for r in erg: x = g[(r["land"], r["region"])]; x[0] += 1; x[1] += bool(r["topo"]); x[2] += not r["alt"]
    for (l, rg), x in sorted(g.items(), key=lambda t: (t[0][0], t[0][1] or "")): z.append(f"| {l} | {rg} | {x[0]} | {x[1]} | {x[2]} |")
    neu = [r for r in erg if not r["alt"]]
    if neu:
        z += ["", "## Neu in den Kartendaten", ""] + [f"- {r['name']} ({r['region']}, {r['gemeinde'] or ''}, {r['grad'] or '?'}) – Topo: {r['topo']['url'] if r['topo'] else 'keins'}" for r in neu]
    gg = [r for r in erg if r.get("grad_vorher")]
    if gg: z += ["", "## Schwierigkeit geändert", ""] + [f"- {r['name']}: {r['grad_vorher']} → {r['grad']}" for r in gg]
    tn = [r for r in erg if r["topo"] and (r.get("topo_neu") or r.get("topo_ersetzt") or r.get("topo_vorher") or r.get("topo_offiziell_ersetzt"))]
    if tn: z += ["", "## Topos neu oder ersetzt", ""] + [f"- {r['name']} ({r['region']}): {r['topo']['url']} – {r['topo']['quelle']} ({r['topo']['sicherheit']})" for r in tn]
    open(os.path.join(ordner, "bericht.md"), "w", encoding="utf-8").write("\n".join(z) + "\n")
    p = [f"# Klettersteige – Prüfliste {HEUTE}", "",
         "Für Claude: jeden Punkt kurz prüfen (nur ansehen, nichts kopieren). Ergebnis in daten/klettersteige/entscheidungen.json:",
         '`{"<id>": {"topo": "<url>" | "keins", "typ": "i|p|w", "quelle": "<Seite>", "grad": "A–F", "grad_quelle": "<Seite>", "zustand": "offen|gesperrt|abgebaut|saniert", "seit": "JJJJ-MM-TT", "bis": "JJJJ-MM-TT", "grund": "…", "zustand_quellen": ["<url1>", "<url2>"], "datum": "JJJJ-MM-TT"}}`',
         "Regel: gesperrt/abgebaut nur mit zwei seriösen Quellen (offizielle Seiten, Alpenverein, via-ferrata.de) – sonst bleibt der Steig offen.",
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
                               "zustand_seit": r.get("zustand_seit"), "zustand_bis": r.get("zustand_bis"), "zustand_quelle": (r.get("zustand_quellen") or [None])[0],
                               "zustand_quellen": r.get("zustand_quellen"), "quellen_seiten": r.get("quellen_seiten"), "laenge_offiziell_m": r.get("laenge_offiziell_m"), "sperre_saison": r.get("sperre_saison"),
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
    elif c == "oa": oa(a[1])
    elif c == "abgleich": abgleich(a[1], a[2], a[3] if len(a) > 3 else None)
    elif c == "db": db(a[1])
    else: print(__doc__); sys.exit(1)
