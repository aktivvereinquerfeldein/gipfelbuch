#!/usr/bin/env python3
"""Parkplatz-Gebühren (Prompt 2) sparsam wie bei den Hütten: Skript holt Bestand und Quelltexte, Claude liest nur Auszüge.

Ablauf (GitHub → Actions → „Parkplätze“):
  1. Bestand: alle Parkplätze, die die App zeigt (öffentlich/benannt/bezahlt/groß, Parkhäuser, Kundenparkplätze),
     aus OpenStreetMap für Österreich und Südtirol, mit Bundesland, Bezirk und Gemeinde.
  2. Datenbank (gb_parkplaetze): neue Einträge anlegen (gebuehr 'unbekannt', recherche 'offen'); vorhandene bleiben.
     Was in einer abgearbeiteten Region nicht mehr in den Kartendaten ist: status 'nicht_mehr_gefunden' (nie löschen).
  3. Quellen lesen (nur lesen, robots.txt, 1 Anfrage/Sekunde je Seite): Gemeinde-Webseite (aus OSM bzw. Wikidata),
     dort Seiten und PDFs zu Parken/Kurzparkzone/Parkgebühren/Verordnungen; eigene Webseite des Parkplatzes;
     Zusatzquellen aus karte/parkquellen.txt (Seilbahnen, Mautstraßen, Naturparks, Handyparken-Zonen).
     Auszüge nur mit Zeilen zu Gebühren/Zeiten → daten/park/<Region>.md (je Bezirk und Gemeinde, mit Parkplatzliste).
  4. Claude liest nur diese Auszüge und trägt Fakten ein (Regeln aus Prompt 2). Keine Werte werden vom Skript geraten.
  5. „Zusatzdaten einspielen“ packt die Gebühren als Feld „gb“ in die Parkplatz-Zellen (anreichern).

Aufrufe:
  parkplaetze.py grenzen  <grenzen.osm.pbf> <gem.json>          Bundesländer, Bezirke, Gemeinden (Umrisse, Webseite, Wikidata)
  parkplaetze.py inventar <gem.json> <aus.jsonl> <land> <datei.osm.pbf>
  parkplaetze.py zusammen <inv.jsonl> <neu.json>
  parkplaetze.py db-neu   <neu.json> <regionen>              (Komma-Liste, z. B. Vorarlberg,Tirol)
  parkplaetze.py sammeln  <neu.json> <gem.json> <aus-ordner> <regionen>
  parkplaetze.py daten    <daten.json>                       Tabelle lesen (öffentlicher Schlüssel)
  parkplaetze.py anreichern <zellen-ordner> <aus-ordner> <daten.json>
  parkplaetze.py zellen   <daten.json>                       Liste der Parkplatz-Zellen (für rclone --files-from)
"""
import gzip, hashlib, html, json, math, os, re, subprocess, sys, tempfile, time, urllib.parse, urllib.request
import concurrent.futures as cf
from collections import defaultdict
from html.parser import HTMLParser
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sammler   # holen() mit robots.txt und 1 Anfrage/Sekunde je Seite

HEUTE = time.strftime("%Y-%m-%d")
HIER = os.path.dirname(os.path.abspath(__file__))
REG = {"AT-1": "Burgenland", "AT-2": "Kärnten", "AT-3": "Niederösterreich", "AT-4": "Oberösterreich", "AT-5": "Salzburg",
       "AT-6": "Steiermark", "AT-7": "Tirol", "AT-8": "Vorarlberg", "AT-9": "Wien", "IT-BZ": "Südtirol"}
CELL_PKA = 0.05   # muss zu CELL.pka in index.html passen

# ---------- Grenzen ----------
def grenzen(pbf, aus):
    import osmium
    from osmium.geom import WKBFactory
    wkb = WKBFactory(); out = []
    class H(osmium.SimpleHandler):
        def area(s, a):
            t = {x.k: x.v for x in a.tags}
            if t.get("boundary") != "administrative": return
            lv, iso = t.get("admin_level"), t.get("ISO3166-2") or ""
            if lv == "4" and iso in REG: e = "land"
            elif iso == "IT-BZ": e = "land"
            elif lv == "6": e = "bezirk"
            elif lv == "8": e = "gemeinde"
            else: return
            try: g = wkb.create_multipolygon(a)
            except Exception: return
            out.append({"e": e, "name": t.get("name:de") or t.get("name"), "iso": iso, "web": t.get("website") or t.get("contact:website") or "",
                        "wd": t.get("wikidata") or "", "wkb": g})
    H().apply_file(pbf, locations=True, idx="flex_mem")
    alt = json.load(open(aus)) if os.path.exists(aus) else []
    json.dump(alt + out, open(aus, "w"))
    print(f"{sum(1 for x in out if x['e']=='land')} Länder, {sum(1 for x in out if x['e']=='bezirk')} Bezirke, {sum(1 for x in out if x['e']=='gemeinde')} Gemeinden")

class Orte:
    def __init__(s, path):
        from shapely import wkb as W
        from shapely.strtree import STRtree
        s.e = {}
        for x in json.load(open(path)):
            s.e.setdefault(x["e"], []).append((x, W.loads(x["wkb"], hex=True)))
        s.t = {k: STRtree([g for _, g in v]) for k, v in s.e.items()}
    def finde(s, e, lon, lat):
        from shapely.geometry import Point
        p = Point(lon, lat)
        if e not in s.t: return None
        for i in s.t[e].query(p):
            x, g = s.e[e][int(i)]
            if g.contains(p): return x
        return None

# ---------- Bestand: gleiche Auswahl wie die App (index.html, k==="pka") ----------
R_ACC_NO = re.compile(r"^(private|no|residents|destination|permit|delivery|employees|agricultural|forestry)$")
def flaeche(c):
    if len(c) < 4: return 0
    kx, ky = 111320 * math.cos(c[0][1] * math.pi / 180), 110540
    a = sum((c[i-1][0]*kx)*(c[i][1]*ky) - (c[i][0]*kx)*(c[i-1][1]*ky) for i in range(1, len(c)))
    return abs(a) / 2

def art(t):
    n = (t.get("name") or "").lower()
    if t.get("access") == "customers": return "kunden"
    if t.get("parking") in ("multi-storey", "underground"): return "parkhaus"
    if re.search(r"wander|tourenstart|ausgangspunkt|naturpark|alpe|alm|hütte", n): return "wanderparkplatz"
    if re.search(r"bahn|lift|gondel|seilbahn|talstation|bergbahn|skigebiet|piste", n): return "seilbahn"
    if t.get("park_ride") == "yes" or re.search(r"p\s?\+\s?r|park\s?&\s?ride|bahnhof", n): return "park_ride"
    if re.search(r"bad|schwimm|freibad|see", n): return "bad"
    return "parkplatz"

def inventar(gemfile, aus, land, pbf):
    import osmium
    orte = Orte(gemfile); n = defaultdict(int)
    with open(aus, "a", encoding="utf-8") as f:
        class H(osmium.SimpleHandler):
            def emit(s, typ, oid, lon, lat, t, area):
                if t.get("amenity") != "parking" or R_ACC_NO.match(t.get("access", "")): return
                cust = t.get("access") == "customers"
                pub = bool(re.match(r"^(yes|public|permissive)$", t.get("access", ""))) or any(t.get(k) for k in ("fee", "parking:fee", "charge", "name", "operator")) \
                      or t.get("parking") in ("multi-storey", "underground") or t.get("park_ride") == "yes"
                try: cap = int(t.get("capacity", "0"))
                except ValueError: cap = 0
                big = area >= 450 or cap >= 12
                if not (cust or pub or big): n["klein"] += 1; return   # kleine ohne Angaben: keine Quelle zu erwarten
                l = orte.finde("land", lon, lat)
                if not l: return
                r = REG.get(l["iso"]) or ("Südtirol" if l["iso"] == "IT-BZ" else None)
                if not r or (land == "AT") != l["iso"].startswith("AT"): return
                g = orte.finde("gemeinde", lon, lat); b = orte.finde("bezirk", lon, lat) if l["iso"].startswith("AT") else None
                if not g and b: g = b   # Statutarstadt (z. B. Innsbruck): Bezirk = Gemeinde
                keep = {k: v for k, v in t.items() if k in ("name", "fee", "parking:fee", "charge", "parking:charge", "fee:conditional", "capacity",
                        "access", "parking", "maxstay", "opening_hours", "operator", "website", "contact:website", "park_ride", "capacity:charging",
                        "motorhome", "caravan", "payment:coins", "payment:cards", "payment:app", "payment:debit_cards", "payment:credit_cards")}
                f.write(json.dumps({"t": typ, "id": oid, "lon": round(lon, 6), "lat": round(lat, 6), "l": land, "r": r,
                                    "b": (b or {}).get("name") or "", "g": (g or {}).get("name") or "", "gw": (g or {}).get("web") or "", "gwd": (g or {}).get("wd") or "", "cap": cap or None,
                                    "a": art(t), "z": "kunden" if cust else "oeffentlich", "tags": keep}, ensure_ascii=False) + "\n")
                n[r] += 1
            def node(s, o):
                if o.tags.get("amenity") == "parking":
                    s.emit("node", o.id, o.location.lon, o.location.lat, {x.k: x.v for x in o.tags}, 0)
            def way(s, o):
                if o.tags.get("amenity") != "parking": return
                try: c = [(nd.lon, nd.lat) for nd in o.nodes if nd.location.valid()]
                except Exception: return
                if len(c) < 3: return
                if c[0] != c[-1]: c.append(c[0])
                cc = c[:-1]
                s.emit("way", o.id, sum(p[0] for p in cc) / len(cc), sum(p[1] for p in cc) / len(cc), {x.k: x.v for x in o.tags}, flaeche(c))
        H().apply_file(pbf, locations=True, idx="flex_mem")
    print(land, dict(n))

def lese(p): return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]

def zusammen(inv, aus):
    rows, seen = [], set()
    for x in lese(inv):
        if (x["t"], x["id"]) in seen: continue
        seen.add((x["t"], x["id"])); rows.append(x)
    json.dump(rows, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    n = defaultdict(int)
    for r in rows: n[r["r"]] += 1
    print(f"{len(rows)} Parkplätze gesamt"); [print(f"  {k}: {v}") for k, v in sorted(n.items())]

# ---------- Datenbank (Secret SUPABASE_SERVICE_KEY) ----------
SB_URL = os.environ.get("SB_URL", "https://vehrlrjegxlhxibrxqxm.supabase.co")
SB_PUB = os.environ.get("SB_KEY", "sb_publishable_5sO5VJ5W232hdV_gAbzplA_A4UgzW6M")   # öffentlicher Leseschlüssel
def sb(method, path, body=None, prefer=None, key=None):
    key = key or os.environ.get("SUPABASE_SERVICE_KEY", "")
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(SB_URL + "/rest/v1/" + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r: return r.read()

def alle(sel, flt="", key=None):
    rows, off = [], 0
    while True:
        part = json.loads(sb("GET", f"gb_parkplaetze?select={sel}{flt}&order=id&limit=1000&offset={off}", key=key))
        rows += part
        if len(part) < 1000: return rows
        off += 1000

def db_neu(neu, regionen):
    regs = [r.strip() for r in regionen.split(",") if r.strip()]
    xs = [x for x in json.load(open(neu, encoding="utf-8")) if x["r"] in regs]
    rows = [{"osm_typ": x["t"], "osm_id": x["id"], "name": x["tags"].get("name"), "geom": f"SRID=4326;POINT({x['lon']} {x['lat']})",
             "land": x["l"], "region": x["r"], "bezirk": x["b"] or None, "gemeinde": x["g"] or None, "betreiber": x["tags"].get("operator"),
             "plaetze": x["cap"], "zugang": x["z"], "gebuehr": "unbekannt", "quelle": "OpenStreetMap",
             "quelle_url": f"https://www.openstreetmap.org/{x['t']}/{x['id']}", "pruef_datum": HEUTE, "sicherheit": "unklar", "status": "aktiv",
             "daten": {"lauf": "park", "recherche": "offen", "art": x["a"], "osm": x["tags"]}} for x in xs]
    for i in range(0, len(rows), 500):
        sb("POST", "gb_parkplaetze?on_conflict=osm_typ,osm_id", rows[i:i + 500], "resolution=ignore-duplicates,return=minimal")
    print(f"{len(rows)} Parkplätze gemeldet (vorhandene bleiben unverändert)")
    # nicht mehr in den Kartendaten: nur markieren, nie löschen
    da = {(x["t"], x["id"]) for x in xs}; weg = []
    for r in regs:
        for y in alle("id,osm_typ,osm_id,name,gemeinde,status", f"&region=eq.{urllib.parse.quote(r)}&status=eq.aktiv"):
            if (y["osm_typ"], y["osm_id"]) not in da and y["osm_id"]: weg.append(y)
    for y in weg:
        sb("PATCH", f"gb_parkplaetze?id=eq.{y['id']}", {"status": "nicht_mehr_gefunden", "pruef_datum": HEUTE}, "return=minimal")
    print(f"nicht mehr gefunden: {len(weg)}")
    json.dump(weg, open("nicht_gefunden.json", "w"), ensure_ascii=False)

# ---------- Quellen lesen ----------
PARK = re.compile(r"(park(en|platz|plätze|gebühr|schein|raum|zone|haus|deck|garage|ordnung|tarif|ticket|uhr|automat|abgabe)|kurzpark|"
                  r"tiefgarage|parkier|parcheggi|sosta|handyparken|easypark|parkster|paybyphone|blaue zone|grüne zone)", re.I)
HUB = re.compile(r"(verordnung|kundmachung|amtstafel|amtsblatt|gebühren|abgaben|tarife|bürgerservice|buergerservice|verkehr|mobilität|"
                 r"mobilitaet|rechtsvorschrift|satzung|regolament|tariff)", re.I)
GELD = re.compile(r"(€|eur\b|euro|\d+,\d{2}\b|gebühr|kostenlos|gratis|gebührenfrei|frei\b|tarif|stunde|std\.|tag(es)?(ticket|karte|pauschale)?\b|"
                  r"pauschal|höchst|max\.|minuten|min\.|\b\d{1,2}[:.]\d{2}\b|uhr\b|mo\s?[-–]|montag|werktag|samstag|sonn|feiertag|"
                  r"münz|karte|bankomat|app\b|handy|wohnmobil|camper|ladestation|e-tankstelle|gästekarte|skipass|liftticket)", re.I)
UNWICHTIG = re.compile(r"(cookie|datenschutz|newsletter|impressum|javascript|instagram|facebook)", re.I)

class Seite(HTMLParser):
    def __init__(s):
        super().__init__(); s.out = []; s.links = []; s.skip = 0; s.a = None
    def handle_starttag(s, t, a):
        if t in ("script", "style", "noscript", "svg", "nav", "footer"): s.skip += 1
        if t in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "td", "th", "dt", "dd", "section", "article"): s.out.append("\n")
        if t in ("td", "th"): s.out.append(" | ")
        if t == "a": s.a = [dict(a).get("href") or "", ""]
    def handle_endtag(s, t):
        if t in ("script", "style", "noscript", "svg", "nav", "footer") and s.skip: s.skip -= 1
        if t == "a" and s.a:
            s.links.append((s.a[0], s.a[1].strip())); s.a = None
    def handle_data(s, d):
        if s.a is not None: s.a[1] += d
        if not s.skip: s.out.append(d)

def zerlegen(raw):
    p = Seite()
    try: p.feed(raw)
    except Exception: pass
    z = [re.sub(r"\s+", " ", html.unescape(x)).strip(" |") for x in "".join(p.out).split("\n")]
    return [x for x in z if x], p.links

def pdf_text(url):
    u = urllib.parse.urlparse(url); host = u.scheme + "://" + u.netloc
    rp = sammler._robots.get(host)
    if rp is not None and not rp.can_fetch(sammler.UA, url): return None
    w = 1.0 - (time.time() - sammler._zuletzt.get(host, 0))
    if w > 0: time.sleep(w)
    sammler._zuletzt[host] = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": sammler.UA})
        with urllib.request.urlopen(req, timeout=40) as r: data = r.read(15_000_000)
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tf:
            tf.write(data); tf.flush()
            t = subprocess.run(["pdftotext", "-layout", "-l", "12", tf.name, "-"], capture_output=True, timeout=60).stdout.decode("utf-8", "replace")
        return [re.sub(r"\s+", " ", x).strip() for x in t.split("\n") if x.strip()]
    except Exception:
        return None

def auszug(zeilen, maxlen=1800):
    """Zeilen mit Park-Wort und die Nachbarzeilen mit Geld/Zeiten; nur wenn irgendwo Geld/Gebühr vorkommt"""
    nimm = set()
    for i, z in enumerate(zeilen):
        if len(z) > 400 or UNWICHTIG.search(z): continue
        if PARK.search(z):
            for j in range(i - 1, i + 6):
                if 0 <= j < len(zeilen) and len(zeilen[j]) <= 400 and (j <= i or GELD.search(zeilen[j])): nimm.add(j)
    out, seen = [], set()
    for i in sorted(nimm):
        z = zeilen[i][:240]
        if z not in seen: seen.add(z); out.append(z)
    s = " | ".join(out)
    if not re.search(r"(€|eur|euro|\d+,\d{2}|kostenlos|gratis|gebührenfrei|gebührenpflichtig|kurzparkzone)", s, re.I): return ""
    return s[:maxlen]

def gleiche_seite(a, b):
    na, nb = urllib.parse.urlparse(a).netloc.lower(), urllib.parse.urlparse(b).netloc.lower()
    return na.removeprefix("www.") == nb.removeprefix("www.")

PROTO = []
def sitemap_urls(start, maxn=25):
    """Park-Seiten aus der sitemap.xml der Seite (ohne Suche oder Formulare)"""
    u = urllib.parse.urlparse(start); host = u.scheme + "://" + u.netloc
    maps, gefunden = [host + "/sitemap.xml", host + "/sitemap_index.xml"], []
    rp = sammler._robots.get(host)
    try:
        if rp is not None and rp.site_maps(): maps = list(rp.site_maps()) + maps
    except Exception: pass
    gesehen = set(); i = 0
    while maps and i < 8 and len(gefunden) < maxn:
        m = maps.pop(0)
        if m in gesehen: continue
        gesehen.add(m); i += 1
        raw, err = sammler.holen(m)
        if err or not raw: continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", raw)
        for l in locs:
            l = html.unescape(l)
            if l.endswith(".xml") or ".xml?" in l:
                if PARK.search(l) or re.search(r"(page|seite|post|de)", l, re.I): maps.insert(0, l)
            elif PARK.search(urllib.parse.unquote(l)) and l not in gefunden:
                gefunden.append(l)
                if len(gefunden) >= maxn: break
    return gefunden

def kriechen(start, max_seiten=22, max_pdf=8):
    """Gemeinde- oder Betreiberseite: Startseite, Park-Seiten aus der sitemap, Park-Links (Seiten und PDFs),
    Sammelseiten wie Verordnungen/Amtstafel (nur nach Linktext)"""
    raw0, err0 = sammler.holen(start)
    if err0 and "robots" not in err0:   # z. B. Zertifikat nur ohne/mit www gültig
        p = urllib.parse.urlparse(start); alt = p.netloc[4:] if p.netloc.startswith("www.") else "www." + p.netloc
        r2, e2 = sammler.holen(p._replace(netloc=alt).geturl())
        if not e2: start, raw0, err0 = p._replace(netloc=alt).geturl(), r2, None
    PROTO.append(f"{start} -> {err0 or len(raw0 or '')}")
    if err0: return [], 1
    besucht, funde, pdfs = {start}, [], 0
    q = [(u, 1, 0) for u in sitemap_urls(start)]
    PROTO.append(f"sitemap: {len(q)} Park-Seiten")
    def links_von(url, raw, tiefe):
        z, links = zerlegen(raw)
        for h, txt in links:
            u = urllib.parse.urljoin(url, h).split("#")[0]
            if re.search(r"[?&](contrast|cHash|print|tx_|L=|type=)", u): u = u.split("?")[0]
            istpdf = bool(re.search(r"\.pdf($|\?)", u, re.I))
            if not u.startswith("http") or u in besucht or (not gleiche_seite(u, start) and not istpdf): continue
            if re.search(r"\.(jpe?g|png|gif|zip|docx?|xlsx?|mp4|ics)($|\?)", u, re.I): continue
            if PARK.search(urllib.parse.unquote(u) + " " + txt): q.append((u, tiefe + 1, 0))
            elif tiefe == 0 and txt and len(txt) < 40 and HUB.search(txt) and sum(1 for x in q if x[2] == 1) < 5: q.append((u, tiefe + 1, 1))
        return z
    z0 = links_von(start, raw0, 0)
    a = auszug(z0)
    if a: funde.append((start, a))
    while q and len(besucht) < max_seiten:
        q.sort(key=lambda x: (x[2], x[1])); url, tiefe, _ = q.pop(0)
        if url in besucht: continue
        besucht.add(url)
        if re.search(r"\.pdf($|\?)", url, re.I):
            if pdfs >= max_pdf: continue
            pdfs += 1; z = pdf_text(url); PROTO.append(f"{url} -> PDF {len(z or [])}")
            if z:
                a = auszug(z, 2200)
                if a: funde.append((url, a))
            continue
        raw, err = sammler.holen(url)
        PROTO.append(f"{url} -> {err or len(raw or '')}")
        if err and err.startswith("PDF") and pdfs < max_pdf:   # z. B. gem2go-Verordnungen ohne .pdf in der Adresse
            pdfs += 1; z = pdf_text(url)
            if z:
                a = auszug(z, 2200)
                if a: funde.append((url, a))
            continue
        if err or not raw: continue
        z = links_von(url, raw, tiefe) if tiefe < 2 else zerlegen(raw)[0]
        a = auszug(z)
        if a: funde.append((url, a))
    return funde, len(besucht)

def wikidata_web(ids):
    """offizielle Webseite (P856) für Gemeinden ohne website-Tag in OSM"""
    out = {}
    ids = [i for i in ids if re.match(r"^Q\d+$", i)]
    for k in range(0, len(ids), 200):
        qry = "SELECT ?i ?w WHERE { VALUES ?i { " + " ".join("wd:" + i for i in ids[k:k + 200]) + " } ?i wdt:P856 ?w }"
        try:
            req = urllib.request.Request("https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(qry), headers={"User-Agent": sammler.UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                for b in json.load(r)["results"]["bindings"]:
                    out.setdefault(b["i"]["value"].rsplit("/", 1)[1], b["w"]["value"])
        except Exception as e:
            print("Wikidata:", str(e)[:80])
    return out

def kurzliste(x):
    t = x["tags"]; h = []
    for k in ("fee", "parking:fee", "charge", "parking:charge", "fee:conditional", "maxstay", "opening_hours", "operator"):
        if t.get(k): h.append(f"{k}={t[k]}")
    return f"- {x['t'][0]}{x['id']} {t.get('name') or '(ohne Namen)'} [{x['a']}{', ' + str(x['cap']) + ' Pl.' if x['cap'] else ''}] {x['lat']},{x['lon']}" + (f" OSM: {'; '.join(h)}" if h else "")

def sammeln(neu, gemfile, ausdir, regionen):
    regs = [r.strip() for r in regionen.split(",") if r.strip()]
    os.makedirs(ausdir, exist_ok=True)
    xs = [x for x in json.load(open(neu, encoding="utf-8")) if x["r"] in regs]
    gweb = {}
    for x in xs: gweb.setdefault((x["r"], x["g"]), (x.get("gw") or "", x.get("gwd") or ""))
    wd = wikidata_web([w for v, w in gweb.values() if not v and w])
    extra = defaultdict(list)
    pq = os.path.join(HIER, "parkquellen.txt")
    if os.path.exists(pq):
        for l in open(pq, encoding="utf-8"):
            p = [s.strip() for s in l.split(";")]
            if len(p) == 3 and not l.startswith("#") and p[0] in regs: extra[(p[0], p[1])].append(p[2])
    nach = defaultdict(list)
    for x in xs: nach[(x["r"], x["b"], x["g"])].append(x)
    regionweit = defaultdict(list)
    def eine_rw(ru):
        r, u = ru
        try: f, n = kriechen(u, 30, 6)
        except Exception: f, n = [], 0
        return r, u, f, n
    sp = os.path.join(ausdir, "stand.json")
    stand = json.load(open(sp)) if os.path.exists(sp) else {}

    def eine(key):
        r, b, g = key; ps = nach[key]
        v, w = gweb.get((r, g), ("", ""))
        web = v or wd.get(w, "")
        teile, seiten = [], 0
        quellen = ([("Gemeinde", web)] if web else []) + [("Zusatzquelle", u) for u in extra.get((r, g), [])]
        eigene = []
        for x in ps:
            w = x["tags"].get("website") or x["tags"].get("contact:website")
            if w and w.startswith("http") and w not in eigene and w != web: eigene.append(w)
        quellen += [("Parkplatz-Webseite", u) for u in eigene[:6]]
        for art_, u in quellen:
            try: funde, n = kriechen(u, 22 if art_ == "Gemeinde" else 8, 8 if art_ == "Gemeinde" else 3)
            except Exception as e: funde, n = [], 0
            seiten += n
            for fu, a in funde:
                if any(a[:300] in t for t in teile): continue   # gleicher Text schon da (Sprach- oder Kontrastvariante)
                teile.append(f"[{art_}] {fu}\n{a}")
        return key, web, teile, seiten

    bloecke = defaultdict(list); ohne_web = []; neu_stand = dict(stand); n_seiten = 0; n_geaendert = 0
    with cf.ThreadPoolExecutor(16) as ex:
        for (r, b, g), web, teile, seiten in ex.map(eine, sorted(nach)):
            n_seiten += seiten
            ps = nach[(r, b, g)]
            text = "\n".join(teile)
            h = hashlib.sha1(text.encode()).hexdigest()[:12]; k = f"{r}/{g}"
            geaendert = stand.get(k) != h; neu_stand[k] = h; n_geaendert += geaendert
            if not web: ohne_web.append(f"{r} / {b} / {g}")
            kopf = f"### {g or '(keine Gemeinde)'} – {len(ps)} Parkplätze" + ("" if geaendert else " – Quellen unverändert seit letztem Lauf")
            blk = [kopf, f"Gemeinde-Webseite: {web or 'nicht gefunden'}", *[kurzliste(x) for x in sorted(ps, key=lambda x: (x['tags'].get('name') is None, x['tags'].get('name') or ''))[:60]]]
            if len(ps) > 60: blk.append(f"- … und {len(ps) - 60} weitere ohne Namen/Angaben")
            blk.append("Auszüge:" if teile else "Auszüge: keine Gebühren-Angaben gefunden")
            blk += teile
            bloecke[(r, b)].append("\n".join(blk) + "\n")
    rw = [(r, u) for (r, g), us in extra.items() if g == "*" for u in us]
    with cf.ThreadPoolExecutor(16) as ex:
        for r, u, f_, n in ex.map(eine_rw, rw):
            n_seiten += n
            regionweit[r].append(f"### {u} ({n} Seiten gelesen)\n" + ("\n".join(f"[Regionsquelle] {fu}\n{a}" for fu, a in f_) or "keine Gebühren-Angaben gefunden") + "\n")
    for r in regs:
        with open(os.path.join(ausdir, f"{r}.md"), "w", encoding="utf-8") as f:
            f.write(f"# Parkplätze {r} – Auszüge ({HEUTE})\n\nNur lesen, nur Fakten übernehmen (Prompt 2). Gebühr „bestätigt“ nur aus Gemeinde/Betreiber "
                    "oder zwei unabhängigen Quellen; OSM-Angaben sind nur Hinweis. Format Parkplatz: <n|w><OSM-ID> Name [Art] lat,lon OSM-Hinweise.\n\n")
            if regionweit.get(r): f.write("## Regionsweite Quellen (Tourismus, Bergbahnen, Mautstraßen)\n\n" + "\n".join(regionweit[r]) + "\n")
            for (rr, b) in sorted(k for k in bloecke if k[0] == r):
                f.write(f"## Bezirk {b or '(ohne Bezirk)'}\n\n" + "\n".join(bloecke[(rr, b)]) + "\n")
    json.dump(neu_stand, open(sp, "w"), ensure_ascii=False, indent=0, sort_keys=True)
    with open(os.path.join(ausdir, "ohne-webseite.md"), "w", encoding="utf-8") as f:
        f.write(f"# Gemeinden ohne gefundene Webseite ({HEUTE})\n\nIn karte/parkquellen.txt nachtragen (Region;Gemeinde;Adresse).\n\n" + "\n".join("- " + x for x in sorted(ohne_web)) + "\n")
    print(f"{len(nach)} Gemeinden, {n_seiten} Seiten gelesen, {n_geaendert} mit geänderten Quellen, {len(ohne_web)} ohne Webseite")

# ---------- in die Parkplatz-Zellen packen (wie Hütten) ----------
def daten(out):
    rows = alle("osm_typ,osm_id,name,geom,gebuehr,tarife,gebuehr_zeiten,zahlungsarten,ermaessigungen,wohnmobile,ladestation,hinweise,"
                "quelle,quelle_url,quelle_datum,pruef_datum,sicherheit,status,daten", "&daten->>recherche=in.(fertig,auto)", key=SB_PUB)
    json.dump(rows, open(out, "w"), ensure_ascii=False)
    print(f"{len(rows)} recherchierte Parkplätze geholt")

def kurz(r):
    g = {"g": r.get("gebuehr") or "unbekannt"}
    t = r.get("tarife") or {}
    if t.get("posten"): g["t"] = [[p.get("dauer"), p.get("eur")] for p in t["posten"] if p.get("dauer")]
    if t.get("hoechst") is not None: g["m"] = t["hoechst"]
    for k, f in (("z", "gebuehr_zeiten"), ("e", "ermaessigungen"), ("wm", "wohnmobile"), ("h", "hinweise"), ("q", "quelle"), ("u", "quelle_url"),
                 ("qd", "quelle_datum"), ("d", "pruef_datum"), ("k", "sicherheit")):
        v = r.get(f)
        if v not in (None, "", "unbekannt"): g[k] = v
    if r.get("zahlungsarten"): g["za"] = r["zahlungsarten"]
    if r.get("ladestation") is not None: g["el"] = 1 if r["ladestation"] else 0
    if r.get("status") and r["status"] != "aktiv": g["st"] = r["status"]
    return json.dumps(g, ensure_ascii=False, separators=(",", ":"))

def lonlat(r):
    g = r.get("geom")
    if isinstance(g, str):
        try: g = json.loads(g)
        except Exception: return None
    return tuple(g["coordinates"][:2]) if isinstance(g, dict) and g.get("type") == "Point" else None

def zellen(dfile):
    s = set()
    for r in json.load(open(dfile)):
        ll = lonlat(r)
        if not ll: continue
        ix, iy = math.floor(ll[0] / CELL_PKA), math.floor(ll[1] / CELL_PKA)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1): s.add(f"pka/{ix + dx}_{iy + dy}.json.gz")
    print("\n".join(sorted(s)))

def anreichern(zin, zout, dfile):
    T = {"node": "n", "way": "w", "relation": "r"}
    by = {T[r["osm_typ"]] + str(r["osm_id"]): kurz(r) for r in json.load(open(dfile)) if r.get("osm_typ") and r.get("osm_id")}
    src, dst = os.path.join(zin, "pka"), os.path.join(zout, "pka"); os.makedirs(dst, exist_ok=True)
    n_tr, n_z = 0, 0
    for fn in sorted(os.listdir(src)) if os.path.isdir(src) else []:
        if not fn.endswith(".json.gz"): continue
        with gzip.open(os.path.join(src, fn), "rt", encoding="utf-8") as f: j = json.load(f)
        hit = 0
        for el in j.get("elements", []):
            g = by.get(el["type"][0] + str(el["id"]))
            if g: el.setdefault("tags", {})["gb"] = g; hit += 1
        if hit:
            with gzip.open(os.path.join(dst, fn), "wt", encoding="utf-8", compresslevel=9) as f:
                f.write(json.dumps(j, ensure_ascii=False, separators=(",", ":")))
            n_z += 1; n_tr += hit
    print(f"Parkplatz-Zellen: {n_z} geschrieben, {n_tr} Parkplatz-Einträge mit Gebührendaten")
    return n_tr

if __name__ == "__main__":
    a = sys.argv[1:]; c = a[0] if a else ""
    if c == "grenzen": grenzen(a[1], a[2])
    elif c == "inventar": inventar(a[1], a[2], a[3], a[4])
    elif c == "zusammen": zusammen(a[1], a[2])
    elif c == "db-neu": db_neu(a[1], a[2])
    elif c == "sammeln": sammeln(a[1], a[2], a[3], a[4])
    elif c == "probe":
        for u in a[1:]:
            PROTO.clear(); f, n = kriechen(u)
            print("=====", u, n, "Seiten,", len(f), "Funde"); print("\n".join(PROTO))
            for fu, x in f: print("FUND", fu, x[:300])
    elif c == "daten": daten(a[1])
    elif c == "zellen": zellen(a[1])
    elif c == "anreichern": anreichern(a[1], a[2], a[3])
    else: print(__doc__); sys.exit(2)
