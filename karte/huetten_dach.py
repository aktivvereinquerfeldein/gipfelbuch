#!/usr/bin/env python3
"""Hütten und Almen im ganzen DACH-Raum (Österreich, Deutschland, Schweiz, Liechtenstein, Südtirol):
Bestand aus OpenStreetMap, Texte der Hütten-Webseiten holen und vorsichtig auswerten – ohne KI.

Endprodukt wie bei Prompt 1 (Tabelle gb_huetten, gleiche Felder). Regeln:
- Nur lesen (robots.txt, höchstens 1 Anfrage pro Sekunde und Seite), nur Fakten, keine fremden Texte.
- Nichts überschreiben: neue Einträge nur anlegen, maschinell Gelesenes nur bei Einträgen mit recherche='offen' oder 'auto'.
- Maschinell gelesen = sicherheit 'unklar', daten.recherche='auto' (App zeigt „ohne Gewähr“). Nur eindeutige
  Treffer werden eingetragen, alles andere kommt auf die Prüfliste für Claude (daten/dach/pruef-<Land>-<Region>.md).
- OSM-Öffnungszeiten zählen nicht als Quelle.

Aufrufe:
  huetten_dach.py regionen <grenzen.osm.pbf> <regionen.wkb.json>     Bundesländer/Kantone (admin_level 4) als Umrisse
  huetten_dach.py inventar <land> <regionen.wkb.json> <aus.jsonl> <datei.osm.pbf>...
  huetten_dach.py zusammen <inventar.jsonl> <neu.json>                doppelte weg, Zeilen für die Datenbank
  huetten_dach.py sammeln  <neu.json> <auto.json> <pruef-ordner>      Webseiten lesen, eindeutige Treffer nach auto.json
  huetten_dach.py db-neu   <neu.json>      neue Einträge anlegen (vorhandene bleiben)
  huetten_dach.py db-auto  <auto.json>     eindeutige Treffer eintragen (nur recherche offen/auto)
Datenbank schreiben braucht das GitHub-Secret SUPABASE_SERVICE_KEY; ohne Schlüssel bleiben nur die Dateien im Repo.
"""
import json, os, re, sys, time, urllib.request, urllib.parse, concurrent.futures as cf
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sammler   # holen(), text_und_links(), auszug()

HEUTE = time.strftime("%Y-%m-%d")
JAHR = int(time.strftime("%Y"))

# gleiche Auswahl wie die Hütten-Zellen (karte/zellen.py), für Bayern zusätzlich „…alm“
R_HUT_T = re.compile(r"^(alpine_hut|wilderness_hut)$")
R_PLACE = re.compile(r"^(locality|isolated_dwelling|hamlet|farm)$")
R_PNAME = re.compile(r"^(Alpe?|Alp|Alm|Maisäß|Vorsäß)( |$)|(alpe|alm|alp)$", re.I)
R_AM = re.compile(r"^(restaurant|cafe|biergarten|pub)$")
R_ANAME = re.compile(r"(alpe?( |$)|^alp |hütte|huette|maisäß|vorsäß|alm( |$)|alm$|berggasth|alpengasth|bergrestaurant|berghaus)", re.I)

def art(t):
    n = t.get("name", "")
    if re.search(r"vorsäß|maisäß", n, re.I): return "vorsaess"
    if re.search(r"alpengasth|alpgasth|alpwirtschaft", n, re.I): return "alpgasthaus"
    if re.search(r"berggasth|berghaus|bergrest|berghof|gipfelrest|panoramarest", n, re.I): return "berggasthaus"
    if t.get("tourism") == "wilderness_hut": return "biwak"
    if t.get("tourism") == "alpine_hut" or re.search(r"hütte|huette|hüsli", n, re.I): return "huette"
    if re.search(r"alm\b|alm$", n, re.I): return "alm"
    if re.search(r"alp", n, re.I): return "alpe"
    return "sonstiges"

def ist_huette(t):
    if "name" not in t: return False
    if R_HUT_T.match(t.get("tourism", "")): return True
    if R_PLACE.match(t.get("place", "")) and R_PNAME.search(t["name"]): return True
    if R_AM.match(t.get("amenity", "")) and R_ANAME.search(t["name"]): return True
    return False

# ---------- Regionen (Bundesland / Kanton) ----------
def regionen(pbf, aus):
    import osmium
    from osmium.geom import WKBFactory
    wkb = WKBFactory(); out = []
    class H(osmium.SimpleHandler):
        def area(s, a):
            t = {x.k: x.v for x in a.tags}
            iso = t.get("ISO3166-2") or ""
            ok = (t.get("boundary") == "administrative" and t.get("admin_level") == "4" and iso[:3] in ("AT-", "DE-", "CH-")) \
                 or iso == "IT-BZ" or (t.get("admin_level") == "2" and t.get("ISO3166-1") == "LI")
            if ok:
                try: out.append({"name": t.get("name:de") or t.get("name"), "iso": iso or "LI", "wkb": wkb.create_multipolygon(a)})
                except Exception: pass
    H().apply_file(pbf, locations=True, idx="flex_mem")
    alt = json.load(open(aus)) if os.path.exists(aus) else []
    json.dump(alt + out, open(aus, "w")); print(f"{len(out)} Regionen")

class Finder:
    def __init__(s, path):
        from shapely import wkb as W
        from shapely.strtree import STRtree
        s.r = [(x["name"], x["iso"], W.loads(x["wkb"], hex=True)) for x in json.load(open(path))]
        s.tree = STRtree([g for _, _, g in s.r])
    def __call__(s, lon, lat):
        from shapely.geometry import Point
        p = Point(lon, lat)
        for i in s.tree.query(p):
            n, iso, g = s.r[int(i)]
            if g.contains(p): return n, iso
        return None, None

# ---------- Bestand ----------
def inventar(land, regfile, aus, pbfs):
    import osmium
    fnd = Finder(regfile); n = 0
    LAND = land
    with open(aus, "a", encoding="utf-8") as f:
        class H(osmium.SimpleHandler):
            def emit(s, typ, oid, lon, lat, t):
                nonlocal n
                reg, iso = fnd(lon, lat)
                if not reg: return   # außerhalb des Gebiets (Randstreifen)
                if iso[:2] != LAND and not (LAND == "LI" and iso == "LI"): return   # Randstreifen gehört zum Nachbarland
                keep = {k: v for k, v in t.items() if k in ("name", "tourism", "amenity", "place", "ele", "website", "contact:website", "url", "phone", "contact:phone", "operator")}
                f.write(json.dumps({"t": typ, "id": oid, "lon": round(lon, 6), "lat": round(lat, 6), "land": LAND, "region": reg, "tags": keep}, ensure_ascii=False) + "\n"); n += 1
            def node(s, o):
                t = {x.k: x.v for x in o.tags}
                if ist_huette(t): s.emit("node", o.id, o.location.lon, o.location.lat, t)
            def way(s, o):
                t = {x.k: x.v for x in o.tags}
                if not ist_huette(t): return
                try: pts = [(nd.lon, nd.lat) for nd in o.nodes if nd.location.valid()]
                except Exception: return
                if pts: s.emit("way", o.id, sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), t)
        h = H()
        for p in pbfs: h.apply_file(p, locations=True, idx="flex_mem")
    print(f"{land}: {n} Hütten/Almen")

# ---------- Zusammenführen (doppelte aus Randstreifen der Länder-Dateien weg) ----------
def lese(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]

def zusammen(inv, aus):
    """schreibt die neuen Zeilen für gb_huetten als JSON """
    rows, seen = [], set()
    for x in lese(inv):
        if (x["t"], x["id"]) in seen: continue
        seen.add((x["t"], x["id"])); t = x["tags"]
        ele = re.match(r"^\d{3,4}", t.get("ele", "") or "")
        rows.append({"t": x["t"], "id": x["id"], "n": t["name"], "a": art(t), "lon": x["lon"], "lat": x["lat"],
                     "h": int(ele.group(0)) if ele else None, "l": x["land"], "r": x["region"],
                     "w": t.get("website") or t.get("contact:website") or t.get("url"), "tag": t.get("tourism") or t.get("amenity") or t.get("place")})
    json.dump(rows, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    n = defaultdict(int)
    for r in rows: n[r["l"] + " " + r["r"]] += 1
    print(f"{len(rows)} Hütten/Almen gesamt"); [print(f"  {k}: {v}") for k, v in sorted(n.items())]

# ---------- Webseiten lesen und vorsichtig auswerten ----------
MON = {"jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12}
WT = {"montag": "mo", "dienstag": "di", "mittwoch": "mi", "donnerstag": "do", "freitag": "fr", "samstag": "sa", "sonntag": "so"}
R_RANGE = re.compile(r"(\d{1,2})\.\s?(\d{1,2})\.(\d{2,4})?\s*(?:–|-|bis)\s*(\d{1,2})\.\s?(\d{1,2})\.(\d{2,4})")
R_SAISONWORT = re.compile(r"(geöffnet|geoeffnet|öffnungszeit|saison|bewirtschaft|offen|open)", re.I)

def auswerten(text, voll=None):
    """gibt (felder, grund) – felder nur, wenn eindeutig. text = Auszug, voll = ganzer Seitentext (für Selbstversorger-Hinweise)"""
    low = text.lower(); vlow = (voll or text).lower()
    v = "unbekannt"
    selbst = re.search(r"selbstversorger|unbewirtschaftet|nicht bewirtschaftet|keine bewirtung|keine bewirtschaftung", vlow)
    bew = re.search(r"(?<!un)(?<!nicht )bewirtschaftet|hüttenwirt|speisekarte|warme küche|warme speisen|bewirtung", vlow)
    if selbst and not bew: v = "nein"
    elif bew and not selbst: v = "ja"
    elif bew and selbst: return None, "Verpflegung widersprüchlich (Selbstversorger und Bewirtung erwähnt)"
    saisons = set()
    for m in R_RANGE.finditer(text):
        ctx = text[max(0, m.start() - 120):m.start()]
        if not R_SAISONWORT.search(ctx): continue
        d1, m1, y1, d2, m2, y2 = m.groups()
        y2 = int(y2) + (2000 if len(y2) == 2 else 0)
        if y2 not in (JAHR, JAHR + 1): continue
        try: d1, m1, d2, m2 = int(d1), int(m1), int(d2), int(m2)
        except ValueError: continue
        if not (1 <= m1 <= 12 and 1 <= m2 <= 12 and 1 <= d1 <= 31 and 1 <= d2 <= 31): continue
        if m1 == m2 and d1 == d2: continue
        a = "sommer" if 4 <= m1 <= 9 else "winter"
        saisons.add((a, f"{m1:02d}-{d1:02d}", f"{m2:02d}-{d2:02d}", y2))
    arten = defaultdict(list)
    for s in saisons: arten[s[0]].append(s)
    if any(len(x) > 1 for x in arten.values()): return None, "mehrere verschiedene Saisondaten"
    ruhe = set()
    for m in re.finditer(r"ruhetag", low):
        stueck = re.split(r"[.|;\n]", low[m.end():m.end() + 45])[0]
        ruhe |= {WT[w] for w in re.findall(r"montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag", stueck)}
    if ruhe and re.search(r"ruhetag[^|]{0,60}(außer|ausser|juli|august|hochsaison|schlecht)", low): return None, "Ruhetag mit Bedingung"
    ruhe = sorted(ruhe, key=list(WT.values()).index)
    if v == "unbekannt" and not saisons: return None, "nichts Eindeutiges gefunden"
    f = {"verpflegung": v}
    if saisons:
        f["oeffnung"] = {"jahr": max(s[3] for s in saisons), "saisons": [{"art": a, "von": von, "bis": bis} for a, von, bis, _ in sorted(saisons)]}
        for a, von, bis, y in saisons:
            f["saison_" + a] = f"{von[3:]}.{von[:2]}.–{bis[3:]}.{bis[:2]}.{y}"
    if ruhe:
        f.setdefault("oeffnung", {})["ruhetage"] = ruhe; f["ruhetage"] = ", ".join(k.title() for k, x in WT.items() if x in ruhe)
    return f, None

FERTIG = [x.strip() for x in os.environ.get("FERTIG_REGIONEN", "Vorarlberg").split(",") if x.strip()]   # schon von Claude recherchiert

def sammeln(neu, aus, pruefdir):
    os.makedirs(pruefdir, exist_ok=True)
    jobs = [x for x in json.load(open(neu, encoding="utf-8")) if (x.get("w") or "").startswith("http") and x["r"] not in FERTIG]
    print(f"{len(jobs)} Hütten mit Webseite (ohne {', '.join(FERTIG)})")
    def eins(x):
        w = x["w"]
        raw, err = sammler.holen(w)
        if err: return x, None, f"nicht lesbar ({err})", ""
        zeilen, links = sammler.text_und_links(raw)
        for h in links:   # Unterseite „Öffnungszeiten“ dazunehmen, wenn auf derselben Seite verlinkt
            if re.search(r"(oeffnungszeit|öffnungszeit|offnungszeit|opening|saison)", urllib.parse.unquote(h), re.I):
                u = urllib.parse.urljoin(w, h)
                if urllib.parse.urlparse(u).netloc == urllib.parse.urlparse(w).netloc and u != w:
                    r2, _ = sammler.holen(u)
                    if r2: zeilen += sammler.text_und_links(r2)[0]
                    break
        txt = sammler.auszug(zeilen, 1500)
        f, grund = auswerten(txt, "\n".join(zeilen)[:200000])
        return x, f, grund, txt
    pruef, ok = defaultdict(list), []
    with cf.ThreadPoolExecutor(16) as ex:
        for x, f, grund, txt in ex.map(eins, jobs):
            if f: ok.append({"t": x["t"], "id": x["id"], "url": x["w"], **f})
            elif not grund.startswith("nicht lesbar"):
                pruef[x["l"] + "-" + x["r"]].append(f"## {x['n']} ({x['t']}/{x['id']})\n{x['w']} – {grund}\n{txt}\n")
    json.dump(ok, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    for k, v in pruef.items():
        with open(os.path.join(pruefdir, f"pruef-{re.sub(r'[^A-Za-z0-9ÄÖÜäöüß-]+', '_', k)}.md"), "w", encoding="utf-8") as fo:
            fo.write(f"# Prüfliste {k} ({HEUTE})\n\nVom Skript nicht eindeutig lesbar. Nur Fakten übernehmen, Regeln aus Prompt 1.\n\n" + "\n".join(v))
    print(f"eindeutig gelesen: {len(ok)}, Prüfliste: {sum(len(v) for v in pruef.values())}")

# ---------- Datenbank (braucht das GitHub-Secret SUPABASE_SERVICE_KEY) ----------
SB_URL = os.environ.get("SB_URL", "https://vehrlrjegxlhxibrxqxm.supabase.co")
def sb(method, path, body=None, prefer=None):
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(SB_URL + "/rest/v1/" + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r: return r.read()

def db_neu(neu):
    """neue Einträge anlegen, vorhandene (z. B. Vorarlberg) bleiben unverändert"""
    rows = [{"osm_typ": x["t"], "osm_id": x["id"], "name": x["n"], "art": x["a"], "geom": f"SRID=4326;POINT({x['lon']} {x['lat']})",
             "hoehe": x["h"], "land": x["l"], "region": x["r"], "verpflegung": "unbekannt", "quelle": "OpenStreetMap",
             "quelle_url": f"https://www.openstreetmap.org/{x['t']}/{x['id']}", "pruef_datum": HEUTE, "sicherheit": "unklar", "status": "aktiv",
             "daten": {"lauf": "dach", "recherche": "offen", "osm_tag": x["tag"], "osm_webseite": x["w"]}} for x in json.load(open(neu, encoding="utf-8"))]
    for i in range(0, len(rows), 500):
        sb("POST", "gb_huetten?on_conflict=osm_typ,osm_id", rows[i:i + 500], "resolution=ignore-duplicates,return=minimal")
    print(f"{len(rows)} Einträge gemeldet (vorhandene bleiben unverändert)")

def db_auto(auto):
    """maschinell gelesene Werte eintragen – nur bei Einträgen, die noch niemand recherchiert hat (offen oder schon auto)"""
    n = 0
    for e in json.load(open(auto, encoding="utf-8")):
        flt = f"osm_typ=eq.{e['t']}&osm_id=eq.{e['id']}&daten->>recherche=in.(offen,auto)"
        try:
            cur = json.loads(sb("GET", f"gb_huetten?select=daten&{flt}"))
            if not cur: continue
            body = {k: e[k] for k in ("verpflegung", "oeffnung", "saison_sommer", "saison_winter", "ruhetage") if k in e}
            body.update({"webseite": e["url"], "quelle": "Eigene Webseite (maschinell gelesen)", "quelle_url": e["url"], "quelle_datum": HEUTE,
                         "pruef_datum": HEUTE, "sicherheit": "unklar", "daten": {**cur[0]["daten"], "recherche": "auto", "auto_datum": HEUTE}})
            sb("PATCH", f"gb_huetten?{flt}", body, "return=minimal"); n += 1
        except Exception as ex:
            print("Fehler", e["id"], str(ex)[:80])
    print(f"{n} Einträge maschinell befüllt")

if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c == "regionen": regionen(sys.argv[2], sys.argv[3])
    elif c == "inventar": inventar(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:])
    elif c == "zusammen": zusammen(sys.argv[2], sys.argv[3])
    elif c == "sammeln": sammeln(sys.argv[2], sys.argv[3], sys.argv[4])
    elif c == "db-neu": db_neu(sys.argv[2])
    elif c == "db-auto": db_auto(sys.argv[2])
    else: print(__doc__); sys.exit(2)
