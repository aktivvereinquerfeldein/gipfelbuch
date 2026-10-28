#!/usr/bin/env python3
"""Gipfelbuch – Sperren und Zutrittsregeln (Prompt 6), ohne KI.

Quellen (nur lesen, nichts anmelden, keine fremden Texte übernehmen – nur Fakten):
  - Digitize the Planet (offene Schnittstelle, Daten CC0, Grenzen mit eigener Lizenz): Schutzgebiete mit Regeln je Aktivität
    (Betreten, Radfahren, Hunde, Klettern; Zeitfenster, Wegegebot, Verordnung) und befristete Sperren/Hinweise
    (Alpenverein, Tourismusverbände, Schweizer Wanderwege, Land Tirol usw.). Das ist dieselbe Quelle, die Outdooractive zeigt.
  - Land Vorarlberg (VoGIS): Wildruhezonen (Abgleich der Zeitfenster) und freigegebene Mountainbike-Strecken.
  - Land Tirol (Radrouten Tirol, CC0): freigegebene Mountainbike-Routen und Singletrails (Mountainbikemodell).
  - Kartendaten (OSM): route=mtb als Hinweis für die Positivliste (nie alleinige Quelle für eine Sperre). Die Sperr-Merkmale der
    Wege selbst (access/foot/bicycle/mtb, :conditional, note) liefern weiterhin die Sperr-Zellen des Kartenservers.
  - karte/sperren_regeln.json (Landesregeln), karte/sperren_quellen.txt (Seiten für den täglichen Abruf → Prüfliste).

Aufrufe:
  sperren.py gebiete  <ordner> [Region,…]   Schutzgebiete mit Regeln (voll, April/Oktober; danach täglich nur geänderte)
  sperren.py sperren  <ordner>              befristete Sperren und Hinweise (täglich)
  sperren.py vogis    <ordner>              Vorarlberg: Wildruhezonen abgleichen, MTB-Strecken
  sperren.py tirol    <ordner>              Tirol: Radrouten (MTB-Routen, Singletrails)
  sperren.py osm-mtb  <ordner> <grenzen.json> <pbf…>   route=mtb aus den Kartendaten (Hinweis, „eine Quelle“)
  sperren.py regeln   <ordner>              Landesregeln aus sperren_regeln.json
  sperren.py seiten   <ordner>              tägliche Quellen-Seiten lesen, Änderungen auf die Prüfliste
  sperren.py bericht  <ordner>              daten/sperren/bericht.md, pruefliste.md, quellen.md
  sperren.py entscheidungen <ordner>        Entscheidungen aus daten/sperren/entscheidungen.json übernehmen
Datenbank: gb_sperren, gb_mtb_routen (Upsert über ext_id, nichts wird gelöscht; Verschwundenes = „nicht_mehr_gefunden“),
Lauf in gb_abgleich_laeufe (gruppe sperren). Braucht SUPABASE_SERVICE_KEY, ohne Schlüssel nur Dateien.
Optional GRENZEN=<grenzen.json> (aus bergbahnen.py grenzen) für Bezirk und Gemeinde.
"""
import datetime, gzip, hashlib, html, json, math, os, re, sys, time
import urllib.parse, urllib.request, urllib.robotparser, urllib.error
from collections import Counter, defaultdict

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
HEUTE = datetime.date.today(); H = HEUTE.isoformat()
UA = "Gipfelbuch-Sperren/1.0 (+https://aktivvereinquerfeldein.github.io/gipfelbuch/; nur lesen)"
SB_URL = os.environ.get("SB_URL", "https://vehrlrjegxlhxibrxqxm.supabase.co")
DTP = "https://platform.digitizetheplanet.org/api/v2"

# Reihenfolge laut Prompt 6: Vorarlberg und Tirol zuerst, dann Südtirol, übriges Österreich, Liechtenstein, Schweiz, Deutschland
REGIONEN = [("Vorarlberg", "AT", {"state_id": 36}), ("Tirol", "AT", {"state_id": 35}), ("Südtirol", "IT", {"bbox": "10.38,46.22,12.48,47.09"}),
            ("Salzburg", "AT", {"state_id": 33}), ("Kärnten", "AT", {"state_id": 30}), ("Steiermark", "AT", {"state_id": 34}),
            ("Oberösterreich", "AT", {"state_id": 32}), ("Niederösterreich", "AT", {"state_id": 31}), ("Wien", "AT", {"state_id": 37}),
            ("Burgenland", "AT", {"state_id": 29}), ("Liechtenstein", "LI", {"country_id": 11}), ("Schweiz", "CH", {"country_id": 4}),
            ("Bayern", "DE", {"state_id": 2}), ("Baden-Württemberg", "DE", {"state_id": 1}), ("Deutschland", "DE", {"country_id": 1})]
SPERR_BBOX = "5.9,45.8,17.2,55.1"   # DACH + Südtirol
# Aktivitäten der Schnittstelle, die für Wege zählen
AKT = {50: "fuss", 5: "rad", 24: "hund", 25: "leine", 1: "klettern"}
ORT = {1: "flaeche", 2: "strassen", 3: "ausgewiesen", 4: "forstwege", 5: "pfade", 6: "wege"}
ORT_TXT = {"flaeche": "im ganzen Gebiet", "strassen": "auf öffentlichen Straßen", "ausgewiesen": "auf ausgewiesenen Wegen", "forstwege": "auf Forststraßen",
           "pfade": "auf Pfaden", "wege": "auf Wegen"}
ERL = {1: "erlaubt", 2: "verboten", 5: "nicht_empfohlen", 6: "empfohlen"}
JZ = {1: "Frühling", 2: "Sommer", 3: "Herbst", 4: "Winter"}
TZ = {1: "tagsüber", 2: "nachts", 3: "in der Dämmerung"}
WETTER = {1: "bei Schnee", 2: "bei Eis", 3: "bei Regen", 4: "bei Nässe", 5: "bei Nebel", 6: "bei Sonne", 7: "bei Trockenheit", 8: "bei Sturm"}
AKT_APP = {"fuss": ["wandern", "bergwandern", "klettersteig"], "rad": ["mtb", "emtb", "rad"], "hund": ["wandern", "bergwandern"],
           "leine": ["wandern", "bergwandern"], "klettern": ["klettersteig"]}
MON = ["Jän.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sep.", "Okt.", "Nov.", "Dez."]
AMTLICH = re.compile(r"(Amt|Land |Landes|Regierung|Kanton|Bundesamt|Landratsamt|Bezirk|Nationalpark|Provin|Gemeinde|Stadt |Naturpark|Landkreis|"
                     r"Regierungspräsidium|Ministerium|Umweltbundesamt|Staat|Bundesforste|Forstverwaltung|Biosphär|Naturschutzbehörde|Kreis|Alpenverein)", re.I)

# ---------------------------------------------------------------- Hilfen
def lade(p, leer):
    try:
        with (gzip.open(p, "rt") if p.endswith(".gz") else open(p)) as f: return json.load(f)
    except Exception: return leer
def speichern(p, d):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p + ".tmp", "w") as f: json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(p + ".tmp", p)

_letzt = {}
def holen(url, accept="application/json", timeout=120, roh=False):
    """Nur lesen, höchstens 1 Anfrage pro Sekunde und Seite, 4 Versuche."""
    host = urllib.parse.urlparse(url).netloc
    w = 1.0 - (time.time() - _letzt.get(host, 0))
    if w > 0: time.sleep(w)
    for v in range(4):
        _letzt[host] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
                return b if roh else json.loads(b)
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 410): raise
            time.sleep(5 + v * 15)
        except Exception:
            if v == 3: raise
            time.sleep(5 + v * 15)
    raise RuntimeError("nicht erreichbar: " + url)

def dtp_alle(pfad, params):
    u = DTP + pfad + "?" + urllib.parse.urlencode(params); out = []
    while u:
        r = holen(u)
        if isinstance(r, list): return out + r
        out += r.get("results", []); u = r.get("next")
    return out

def sb(method, path, body=None, prefer=None):
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(SB_URL + "/rest/v1/" + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    for v in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r: return r.read()
        except urllib.error.HTTPError as e:
            txt = e.read()[:400]
            if e.code < 500: print("Datenbank-Fehler:", e.code, txt); raise
            print("Datenbank-Fehler (Versuch", v + 1, "):", e.code, txt, file=sys.stderr)
            time.sleep(5 + v * 10)
        except Exception: time.sleep(5 + v * 10)
    raise RuntimeError("Datenbank nicht erreichbar")
def q_in(ids): return urllib.parse.quote(ids, safe=',"')
def db_an(): return bool(os.environ.get("SUPABASE_SERVICE_KEY"))

def upsert(tabelle, rows):
    if os.environ.get("TROCKEN") and rows:   # Testlauf: statt Datenbank in eine Datei schreiben
        with open(os.environ["TROCKEN"] + "-" + tabelle + ".jsonl", "a") as f:
            for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if not db_an() or not rows: return
    jetzt = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows = list({r["ext_id"]: r for r in rows}.values())
    for r in rows:
        r["geaendert"] = jetzt
        if "osm_wege" in r and r["osm_wege"] is None: r["osm_wege"] = []
    for i in range(0, len(rows), 50):
        sb("POST", f"{tabelle}?on_conflict=ext_id", rows[i:i + 50], "resolution=merge-duplicates,return=minimal")

def markiere_weg(tabelle, praefix, behalten, extra=None):
    """Einträge mit diesem ext_id-Präfix, die nicht mehr vorkommen → nicht_mehr_gefunden (nie löschen)."""
    if not db_an(): return []
    vorh = json.loads(sb("GET", f"{tabelle}?select=ext_id,status&ext_id=like.{urllib.parse.quote(praefix)}*&limit=200000"))
    weg = [x["ext_id"] for x in vorh if x["ext_id"] not in behalten and x["status"] not in ("nicht_mehr_gefunden",)]
    for i in range(0, len(weg), 80):
        ids = ",".join('"' + x.replace('"', '') + '"' for x in weg[i:i + 80])
        sb("PATCH", f"{tabelle}?ext_id=in.({q_in(ids)})", {"status": "nicht_mehr_gefunden", "pruef_datum": H, **(extra or {})}, "return=minimal")
    return weg

def lauf(region, neu, geaendert, weg, bericht, daten):
    if not db_an(): return
    sb("POST", "gb_abgleich_laeufe", {"gruppe": "sperren", "region": region, "gestartet": H, "beendet": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "fortschritt": 100, "status": "fertig", "neu": neu, "geaendert": geaendert, "nicht_gefunden": weg, "bericht": bericht, "daten": daten}, "return=minimal")

_finder = None
def ort(lon, lat):
    """Bezirk und Gemeinde aus den OSM-Grenzen (GRENZEN=…), sonst leer."""
    global _finder
    g = os.environ.get("GRENZEN")
    if not g or not os.path.exists(g): return None, None, None
    if _finder is None:
        import bergbahnen
        _finder = bergbahnen.Finder(g)
    try:
        bl = _finder(lon, lat, "4")[0]
        if bl in ("Bozen", "Bolzano - Bozen", "Provinz Bozen", "Autonome Provinz Bozen - Südtirol"): bl = "Südtirol"
        return bl, _finder(lon, lat, "6")[0], _finder(lon, lat, "8")[0]
    except Exception: return None, None, None

def ewkt(geo, tol=0.00002):
    from shapely.geometry import shape
    from shapely import wkt as W
    g = shape(geo)
    if tol and g.geom_type not in ("Point", "MultiPoint"): g = g.simplify(tol, preserve_topology=True)
    if g.is_empty: return None, None
    c = g.representative_point() if "Polygon" in g.geom_type else g.centroid
    return "SRID=4326;" + W.dumps(g, rounding_precision=6), (round(c.x, 6), round(c.y, 6))

def mmtt(d):
    if not d: return None
    return d[5:10]
def datum_txt(d):
    m, t = int(d[0:2]), int(d[3:5]); return f"{t}. {MON[m - 1]}"
def norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"))

# ---------------------------------------------------------------- Schutzgebiete (Digitize the Planet)
def stamm(ordner):
    p = os.path.join(ordner, "stamm.json"); st = lade(p, {})
    if st.get("datum") != H:
        st = {"datum": H}
        for k in ("protected-area-categories", "reasons", "reason-categories", "closures-and-notices-sources", "activities"):
            st[k] = {str(x["id"]): x for x in dtp_alle("/" + k, {})}
        speichern(p, st)
    return st

def regel(x):
    a = AKT.get(x.get("activity_id"))
    if not a: return None
    r = {"a": a, "p": ERL.get(x.get("activitypermission_id"), "unbekannt"), "ort": ORT.get(x.get("activityplace_id"), "flaeche")}
    s0, s1 = x.get("season_start"), x.get("season_end")
    if s0 and s1:
        if s0[:4] == "2000" and s1[:4] == "2000": r["von"], r["bis"] = mmtt(s0), mmtt(s1)     # jedes Jahr
        else: r["ab"], r["bis_datum"] = s0, s1                                                # einmaliger Zeitraum
    if x.get("season_id"): r["jz"] = JZ.get(x["season_id"])
    if x.get("daytime_start") and x.get("daytime_end"): r["zv"], r["zb"] = x["daytime_start"][:5], x["daytime_end"][:5]
    if x.get("daytime_id"): r["tz"] = TZ.get(x["daytime_id"])
    if x.get("weathercondition_id"): r["wetter"] = WETTER.get(x["weathercondition_id"])
    if x.get("minimum_path_width"): r["breite"] = x["minimum_path_width"]
    return r

def regel_txt(r):
    wer = {"fuss": "Betreten", "rad": "Radfahren", "hund": "Hunde", "leine": "Hunde", "klettern": "Klettern"}[r["a"]]
    if r["a"] == "leine": kern = "Hunde an der Leine" + (" " + ORT_TXT[r["ort"]] if r["ort"] != "flaeche" else "")
    elif r["p"] == "verboten": kern = f"{wer} verboten" + (" " + ORT_TXT[r["ort"]] if r["ort"] != "flaeche" else "")
    elif r["p"] == "erlaubt": kern = f"{wer} nur {ORT_TXT[r['ort']]} erlaubt" if r["ort"] != "flaeche" else f"{wer} erlaubt"
    elif r["p"] == "nicht_empfohlen": kern = f"{wer} nicht empfohlen" + (" " + ORT_TXT[r["ort"]] if r["ort"] != "flaeche" else "")
    else: kern = f"{wer} empfohlen " + ORT_TXT[r["ort"]]
    z = []
    if r.get("von"): z.append(f"jedes Jahr {datum_txt(r['von'])} – {datum_txt(r['bis'])}")
    if r.get("ab"): z.append(f"{r['ab'][8:10]}.{r['ab'][5:7]}.{r['ab'][:4]} – {r['bis_datum'][8:10]}.{r['bis_datum'][5:7]}.{r['bis_datum'][:4]}")
    if r.get("jz"): z.append("im " + r["jz"])
    if r.get("zv"): z.append(f"{r['zv']}–{r['zb']} Uhr")
    if r.get("tz"): z.append(r["tz"])
    if r.get("wetter"): z.append(r["wetter"])
    if r.get("breite"): z.append(f"Wege ab {r['breite']} m Breite")
    return kern + (" (" + ", ".join(z) + ")" if z else "")

def verbot_art(rs):
    v = []
    for r in rs:
        if r["a"] == "fuss" and r["p"] == "verboten" and r["ort"] == "flaeche": v.append("Betretungsverbot")
        elif r["a"] == "fuss" and r["p"] == "erlaubt" and r["ort"] != "flaeche": v.append("Wegegebot")
        elif r["a"] == "rad" and r["p"] == "verboten": v.append("Fahrverbot Rad")
        elif r["a"] == "rad" and r["p"] == "erlaubt" and r["ort"] != "flaeche": v.append("Rad nur auf Wegen")
        elif r["a"] == "hund" and r["p"] == "verboten": v.append("Hundeverbot")
        elif r["a"] == "leine": v.append("Leinenpflicht")
        elif r["a"] == "klettern" and r["p"] == "verboten": v.append("Kletterverbot")
    return list(dict.fromkeys(v))

def gebiet_zeile(d, st, region, land):
    rs = [x for x in (regel(r) for r in d.get("rules") or []) if x]
    if not rs: return None
    gd = d.get("geodata") or {}
    if not gd.get("geometry"): return None
    geom, c = ewkt(gd["geometry"])
    if not geom: return None
    kat = (st["protected-area-categories"].get(str(d.get("category_id"))) or {})
    katn = kat.get("name_de") or kat.get("name") or "Schutzgebiet"
    tiere = [w.get("name_de") or w.get("name") for w in (d.get("wildlife") or []) if (w.get("name_de") or w.get("name"))]
    rg = list(dict.fromkeys(x.get("name_de") or x.get("name") for x in (d.get("legal_documents") or []) if (x.get("name_de") or x.get("name"))))
    rg = [x for x in rg if not any(y != x and y.startswith(x) for y in rg)]   # „Verordnung“ weglassen, wenn „Verordnung 20000793“ da ist
    src = gd.get("source") or {}
    amtlich = bool(AMTLICH.search(src.get("name") or "")) or bool(rg)
    saison = any(r.get("von") or r.get("ab") or r.get("jz") for r in rs if r["a"] in ("fuss", "rad"))
    bl, bez, gem = ort(*c)
    url = d.get("url") or d.get("dtp_url")
    return {"ext_id": "dtp-g:" + d["uuid"], "bezeichnung": d.get("name_de") or d.get("name"), "art": "saisonal" if saison else "dauerhaft",
            "grund": katn + (" – Schutz von " + ", ".join(tiere[:6]) if tiere else ""),
            "aktivitaeten": sorted({a for r in rs if r["p"] in ("verboten", "erlaubt") for a in AKT_APP[r["a"]]}),
            "verbot": "; ".join(verbot_art(rs)) or None, "geom": geom, "lat": c[1], "lng": c[0],
            "land": land, "region": bl or region, "bezirk": bez, "gemeinde": gem,
            "zeiten": "; ".join(regel_txt(r) for r in rs if r.get("von") or r.get("ab") or r.get("jz")) or None,
            "rechtsgrundlage": "; ".join(list(dict.fromkeys(rg))[:4]) or None, "hinweise": None,
            "quelle": (src.get("name") or "Digitize the Planet") + " (über Digitize the Planet)", "quelle_url": url,
            "quelle_datum": (d.get("updated_at") or "")[:10] or None, "pruef_datum": H,
            "sicherheit": "bestaetigt" if amtlich else "eine_quelle", "status": "aktiv", "pruefliste": not amtlich,
            "daten": {"lauf": "sperren", "herkunft": "dtp", "uuid": d["uuid"], "kategorie": katn, "regeln": [dict(r, t=regel_txt(r)) for r in rs],
                      "tiere": tiere[:12], "dtp_url": d.get("dtp_url"), "lizenz_grenze": (gd.get("license") or {}).get("name"),
                      "quelle_grenze": src.get("name"), "eltern": d.get("parent_uuid"), "km2": d.get("area_in_km2")}}

FELDER_DETAIL = "name,name_de,category_id,updated_at,url,dtp_url,geodata,rules,legal_documents,wildlife,parent_uuid,area_in_km2,state_ids,country_ids"
def gebiete(ordner, nur=None):
    st = stamm(ordner); stand_p = os.path.join(ordner, "stand.json"); stand = lade(stand_p, {})
    seen_all = set(stand.get("gebiete_uuid") or [])
    gesamt = Counter(); bericht = stand.setdefault("gebiete_bericht", {})
    for region, land, filt in REGIONEN:
        if nur and region not in nur: continue
        t0 = time.time()
        # nur Gebiete mit Regeln für Betreten, Rad, Hunde oder Klettern
        uu = {}
        for a in AKT:
            q = dict(filt, **{"rule.activity_id": a, "fields": "uuid,name,updated_at,category_id"})
            if os.environ.get("SEIT"): q["updated_after"] = os.environ["SEIT"]
            for x in dtp_alle("/protected-areas", q):
                uu[x["uuid"]] = x
        if region == "Deutschland":   # Bayern/BW sind schon erledigt
            uu = {k: v for k, v in uu.items() if k not in (stand.get("region_uuid", {}).get("Bayern", []) + stand.get("region_uuid", {}).get("Baden-Württemberg", []))}
        alt = stand.setdefault("gebiete", {})
        rows, neu, geaendert, fehler = [], 0, 0, 0
        for u, x in uu.items():
            if alt.get(u) == x["updated_at"] and not os.environ.get("ALLE"): continue   # unverändert seit dem letzten Lauf
            try: d = holen(f"{DTP}/protected-areas/{u}?fields={FELDER_DETAIL}"); d["uuid"] = u
            except Exception as e: print("Fehler", u, e); fehler += 1; continue
            z = gebiet_zeile(d, st, region, land)
            if z: rows.append(z)
            if u in alt: geaendert += 1
            else: neu += 1
            alt[u] = x["updated_at"]
            if len(rows) >= 50: upsert("gb_sperren", rows); rows = []
        upsert("gb_sperren", rows)
        if os.environ.get("SEIT"): stand.setdefault("region_uuid", {}).setdefault(region, []); stand["region_uuid"][region] = sorted(set(stand["region_uuid"][region]) | set(uu))
        else: stand.setdefault("region_uuid", {})[region] = sorted(uu)
        seen_all |= set(uu)
        stand["gebiete_uuid"] = sorted(seen_all)
        bericht[region] = {"gebiete": len(uu), "neu": neu, "geaendert": geaendert, "fehler": fehler, "datum": H, "minuten": round((time.time() - t0) / 60, 1)}
        speichern(stand_p, stand)        # Fortschritt je Region: ein abgebrochener Lauf macht hier weiter
        gesamt.update(gebiete=len(uu), neu=neu, geaendert=geaendert)
        print(f"{region}: {len(uu)} Gebiete mit Weg-Regeln, {neu} neu, {geaendert} geändert, {fehler} Fehler")
    # Gebiete, die nicht mehr geliefert werden → nicht mehr gefunden (nur nach einem vollständigen Lauf über alle Regionen)
    weg = []
    if not nur and not os.environ.get("SEIT"):
        weg = markiere_weg("gb_sperren", "dtp-g:", {"dtp-g:" + u for r in stand.get("region_uuid", {}).values() for u in r})
    if not nur:   # von der Quelle gelöschte Gebiete → nicht mehr gefunden
        geloescht = [x["uuid"] for x in dtp_alle("/protected-areas/deleted", {"date": stand.get("gebiete_datum") or "2026-01-01"} if stand.get("gebiete_datum") else {})]
        if db_an() and geloescht:
            for i in range(0, len(geloescht), 80):
                ids = ",".join('"dtp-g:' + u + '"' for u in geloescht[i:i + 80])
                sb("PATCH", f"gb_sperren?ext_id=in.({q_in(ids)})&status=eq.aktiv", {"status": "nicht_mehr_gefunden", "pruef_datum": H}, "return=minimal")
        weg = list(dict.fromkeys(weg + ["dtp-g:" + u for u in geloescht]))
        stand["gebiete_geloescht"] = geloescht[-500:]; stand["gebiete_datum"] = H
        speichern(stand_p, stand)
    lauf("Schutzgebiete " + (",".join(nur) if nur else "alle"), gesamt["neu"], gesamt["geaendert"], len(weg), "daten/sperren/bericht.md", {"gebiete": gesamt["gebiete"]})

# ---------------------------------------------------------------- befristete Sperren (täglich)
FELDER_SP = "uuid,name,name_de,type,reason_id,start_on,end_on,end_on_preliminary,daytime_start,daytime_end,activity_ids,source_id,license,responsible_organization,geometry,one_way,osm_ways,updated_at"
def sperren(ordner):
    st = stamm(ordner); stand_p = os.path.join(ordner, "stand.json"); stand = lade(stand_p, {})
    alle = dtp_alle("/closures-and-notices", {"bbox": SPERR_BBOX, "fields": FELDER_SP})
    rows, pruef, zahl = [], [], Counter()
    for x in alle:
        if not x.get("geometry") or not x.get("end_on"): zahl["ohne_geometrie"] += 1; continue
        akt = [AKT.get(a) for a in x.get("activity_ids") or [] if AKT.get(a)]
        if not akt: zahl["andere_aktivitaet"] += 1; continue
        geom, c = ewkt(x["geometry"], 0.00001)
        if not geom: continue
        rs = st["reasons"].get(str(x.get("reason_id"))) or {}
        kat = st["reason-categories"].get(str(rs.get("category_id"))) or {}
        org = x.get("responsible_organization") or {}; quelle = st["closures-and-notices-sources"].get(str(x.get("source_id"))) or {}
        amtlich = bool(org.get("name"))
        hinweis = x.get("type") != "CLOSURE"
        bl, bez, gem = ort(*c)
        v = []
        for a in akt:
            v.append({"fuss": "Betretungsverbot", "rad": "Fahrverbot Rad", "hund": "Hundeverbot", "leine": "Leinenpflicht", "klettern": "Kletterverbot"}[a])
        grund = (rs.get("name_de") or rs.get("name") or "")
        if grund in ("Sonstiges", "") and kat.get("name_de"): grund = kat.get("name_de")
        z = {"ext_id": "dtp-s:" + x["uuid"], "bezeichnung": x.get("name_de") or x.get("name"), "art": "befristet",
             "grund": grund or None, "aktivitaeten": sorted({a2 for a in akt for a2 in AKT_APP[a]}),
             "verbot": ("Hinweis: " if hinweis else "") + "; ".join(v), "osm_wege": sorted({w["way_id"] for w in x.get("osm_ways") or [] if w.get("way_id")}),
             "geom": geom, "lat": c[1], "lng": c[0], "land": None, "region": bl, "bezirk": bez, "gemeinde": gem,
             "gueltig_von": x["start_on"], "gueltig_bis": x["end_on"],
             "zeiten": f"{x['daytime_start'][:5]}–{x['daytime_end'][:5]} Uhr" if x.get("daytime_start") and x.get("daytime_end") else None,
             "quelle": (org.get("name") or quelle.get("name") or "Digitize the Planet") + " (über Digitize the Planet)",
             "quelle_url": org.get("url") or quelle.get("url") or "https://platform.digitizetheplanet.org/",
             "quelle_datum": (x.get("updated_at") or "")[:10] or None, "pruef_datum": H,
             "sicherheit": "bestaetigt" if amtlich else "eine_quelle", "status": "aktiv", "pruefliste": not amtlich,
             "daten": {"lauf": "sperren", "herkunft": "dtp", "uuid": x["uuid"], "hinweis": hinweis, "bis_vorlaeufig": bool(x.get("end_on_preliminary")),
                       "einbahn": bool(x.get("one_way")), "akt": akt, "grund_kategorie": kat.get("name_de"), "lizenz": (x.get("license") or {}).get("name"),
                       "geschlossen_von": org.get("name"), "quelle_id": x.get("source_id")}}
        rows.append(z); zahl["eingetragen"] += 1
        if not amtlich: pruef.append(z)
    vorher = set(stand.get("sperren_uuid") or [])
    jetzt = {r["daten"]["uuid"] for r in rows}
    upsert("gb_sperren", rows)
    # Gelöschte Sperren der Quelle → nicht mehr gefunden. Abgelaufene erledigt gb_sperren_ablaufen() täglich in der Datenbank.
    try: geloescht = [x["uuid"] for x in dtp_alle("/closures-and-notices/deleted", {"date": (HEUTE - datetime.timedelta(days=8)).isoformat()})]
    except Exception: geloescht = []
    weg = []
    if db_an() and geloescht:
        for i in range(0, len(geloescht), 80):
            ids = ",".join('"dtp-s:' + u + '"' for u in geloescht[i:i + 80])
            sb("PATCH", f"gb_sperren?ext_id=in.({q_in(ids)})&status=eq.aktiv", {"status": "nicht_mehr_gefunden", "pruef_datum": H}, "return=minimal")
        weg = geloescht
    if db_an(): sb("POST", "rpc/gb_sperren_ablaufen", {})
    stand["sperren_uuid"] = sorted(jetzt); stand["sperren_datum"] = H
    stand["sperren_zahl"] = dict(zahl); stand["sperren_neu"] = sorted(jetzt - vorher)
    speichern(stand_p, stand)
    speichern(os.path.join(ordner, "sperren-pruef.json"), [{k: r[k] for k in ("ext_id", "bezeichnung", "grund", "gueltig_bis", "quelle", "lat", "lng")} for r in pruef])
    # kompakte Liste fürs Repo (ohne Geometrie)
    speichern(os.path.join(ordner, "sperren.json"), [{"id": r["ext_id"], "n": r["bezeichnung"], "g": r["grund"], "v": r["verbot"], "bis": r["gueltig_bis"],
               "vorl": r["daten"]["bis_vorlaeufig"], "q": r["daten"]["geschlossen_von"], "r": r["region"], "ll": [r["lat"], r["lng"]]} for r in rows])
    lauf("Befristete Sperren DACH + Südtirol", len(jetzt - vorher), len(jetzt & vorher), len(weg), "daten/sperren/bericht.md", dict(zahl))
    print(f"Befristete Sperren: {len(alle)} gelesen, {zahl['eingetragen']} eingetragen ({len(jetzt - vorher)} neu), {len(pruef)} auf der Prüfliste, {len(weg)} gelöscht gemeldet")

# ---------------------------------------------------------------- Vorarlberg (VoGIS)
VOGIS = "https://vogis.cnv.at/geoserver/ows?service=WFS&version=2.0.0&request=GetFeature&outputFormat=application/json&srsName=EPSG:4326&typeNames=vogis:"
MON_NAME = {"jän": 1, "jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dez": 12}
def tag_monat(s):
    s = (s or "").strip().lower()
    m = re.match(r"(\d{1,2})\.\s*(\d{1,2})\.?$", s)
    if m: return f"{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"(\d{1,2})\.\s*([a-zä]{3})", s)
    if m and m.group(2) in MON_NAME: return f"{MON_NAME[m.group(2)]:02d}-{int(m.group(1)):02d}"
    return None

def vogis(ordner):
    # 1) Wildruhezonen: Zeitfenster mit den Gebieten der Schnittstelle vergleichen (zweite amtliche Quelle)
    wrz = holen(VOGIS + "wildruhezone", timeout=180).get("features", [])
    dtp = []
    if db_an(): dtp = json.loads(sb("GET", "gb_sperren?select=ext_id,bezeichnung,daten,sicherheit&ext_id=like.dtp-g:*&daten->>kategorie=in.(Wildruhezone,Jagdrechtliches%20Betretungsverbot,Ruhezone,Wildschutzgebiet)&limit=5000"))
    elif os.environ.get("TROCKEN") and os.path.exists(os.environ["TROCKEN"] + "-gb_sperren.jsonl"):
        dtp = [r for r in (json.loads(l) for l in open(os.environ["TROCKEN"] + "-gb_sperren.jsonl")) if r["ext_id"].startswith("dtp-g:") and r["daten"].get("kategorie") in ("Wildruhezone", "Jagdrechtliches Betretungsverbot", "Ruhezone", "Wildschutzgebiet")]
    def kern(n):
        n = re.sub(r"(?i)\s*-\s*jagdl.*$|\bsperrgebiet\b|\bbereich\b|\bwildruhezone\b|\bej\b|-gatter", " ", n or "")
        return norm(n)
    nach_name = defaultdict(list)
    for d in dtp: nach_name[kern(d["bezeichnung"])].append(d)
    def finde(n):
        k = kern(n)
        if k in nach_name: return nach_name[k]
        tr = [d for kk, ds in nach_name.items() if len(k) > 4 and len(kk) > 4 and (k in kk or kk in k) for d in ds]
        return tr
    erg = {"gleich": [], "widerspruch": [], "fehlt_bei_dtp": [], "ohne_zeit": []}
    def tage(v, b):   # Zeitraum MM-TT → Menge der Tage im Jahr (über den Jahreswechsel möglich)
        d0 = datetime.date(2001, int(v[:2]), int(v[3:])).timetuple().tm_yday; d1 = datetime.date(2001, int(b[:2]), int(b[3:])).timetuple().tm_yday
        return set(range(d0, d1 + 1)) if d0 <= d1 else set(range(d0, 366)) | set(range(1, d1 + 1))
    gruppen = defaultdict(list)
    for f in wrz: gruppen[kern(f["properties"].get("name"))].append(f["properties"])
    for k, ps in gruppen.items():
        n = min((p.get("name") or "" for p in ps), key=len)
        zr = [(tag_monat(p.get("befristet_")), tag_monat(p.get("befristet1"))) for p in ps]
        for p in ps:   # jagdliche Sperrzeit steht beim Land oft nur im Namen („Jagdl. Sperrg. 01.11. bis 15.12.“)
            m = re.search(r"(\d{1,2}\.\s?\d{1,2}\.)\s*bis\s*(\d{1,2}\.\s?\d{1,2}\.)", p.get("name") or "")
            if m: zr.append((tag_monat(m.group(1)), tag_monat(m.group(2))))
        e = {"name": n, "von": ", ".join(dict.fromkeys(a for a, _ in zr if a)), "bis": ", ".join(dict.fromkeys(b for _, b in zr if b)),
             "bescheid": "; ".join(sorted({p.get("zahl") or "" for p in ps})), "bescheid_datum": "; ".join(sorted({p.get("bescheid_d") or "" for p in ps}))}
        zr = [(a, b) for a, b in zr if a and b]
        if not zr: erg["ohne_zeit"].append(e); continue
        tr = finde(n)
        if not tr: erg["fehlt_bei_dtp"].append(e); continue
        land = set().union(*(tage(a, b) for a, b in zr))
        dt = [(r.get("von"), r.get("bis")) for d in tr for r in (d["daten"] or {}).get("regeln", []) if r["a"] == "fuss"]
        dtp_t = set().union(*((tage(a, b) if a else set(range(1, 366))) for a, b in dt)) if dt else set()
        p = ps[0]; von, bis = zr[0]
        if dtp_t and land == dtp_t:
            erg["gleich"].append(e)
            for d in tr:
                if db_an():
                    dd = dict(d["daten"] or {}); dd["zweite_quelle"] = {"name": "Land Vorarlberg – VoGIS Wildruhezonen", "bescheid": e["bescheid"], "datum": H}
                    sb("PATCH", f"gb_sperren?ext_id=eq.{urllib.parse.quote(d['ext_id'])}", {"sicherheit": "bestaetigt", "daten": dd, "pruef_datum": H}, "return=minimal")
        else:
            erg["widerspruch"].append(dict(e, dtp=sorted(f"{a}–{b}" if a else "ganzjährig" for a, b in dt) or ["keine Betreten-Regel"]))
            for d in tr:   # zwei amtliche Quellen widersprechen sich → unklar, bis der Abgleich es klärt (Regel 3/4)
                if db_an():
                    dd = dict(d["daten"] or {}); dd["widerspruch"] = {"quelle": "Land Vorarlberg – VoGIS Wildruhezonen", "zeitraum": [list(x) for x in zr], "bescheid": e["bescheid"], "bescheid_datum": e["bescheid_datum"], "datum": H}
                    sb("PATCH", f"gb_sperren?ext_id=eq.{urllib.parse.quote(d['ext_id'])}", {"sicherheit": "unklar", "pruefliste": True, "daten": dd, "pruef_datum": H}, "return=minimal")
    speichern(os.path.join(ordner, "vogis-wildruhezonen.json"), erg)
    print(f"VoGIS Wildruhezonen: {len(wrz)} Flächen in {len(gruppen)} Zonen – gleich {len(erg['gleich'])}, Widerspruch {len(erg['widerspruch'])}, fehlt bei Digitize {len(erg['fehlt_bei_dtp'])}, ohne Zeit {len(erg['ohne_zeit'])}")
    # 2) freigegebene Mountainbike-Strecken des Landes
    mtb = holen(VOGIS + "mountainbike_strecken", timeout=300).get("features", [])
    rows, gesehen = [], set()
    for f in mtb:
        p = f["properties"]
        if not f.get("geometry"): continue
        geom, c = ewkt(f["geometry"], 0.00001)
        if not geom: continue
        eid = "vbg:" + str(p.get("gipoid") or p.get("objectid"))
        if eid in gesehen: eid += ":" + str(p.get("objectid"))   # gleiche Kennung für mehrere Teilstücke
        gesehen.add(eid)
        rows.append({"ext_id": eid, "name": f"MTB-Strecke {p.get('mtb_id')}" if p.get("mtb_id") else "MTB-Strecke",
                     "art": "route", "modell": "Mountainbike-Netz Vorarlberg", "geom": geom,
                     "land": "AT", "region": "Vorarlberg", "gemeinde": p.get("pgem_name"), "quelle": "Land Vorarlberg – VoGIS Mountainbike-Strecken",
                     "quelle_url": "https://vogis.cnv.at/geonetwork/srv/api/records/9d65291e-4504-4273-b285-4947aa276c7d",
                     "quelle_datum": (p.get("modify_tim") or "")[:10] or None, "pruef_datum": H, "sicherheit": "bestaetigt", "status": "aktiv",
                     "daten": {"lauf": "sperren", "typ": p.get("weg_katego"), "mtb_id": p.get("mtb_id"), "laenge_m": p.get("weg_laenge"), "steigung_max": p.get("steig_max")}})
    upsert("gb_mtb_routen", rows)
    weg = markiere_weg("gb_mtb_routen", "vbg:", {r["ext_id"] for r in rows})
    lauf("MTB-Strecken Vorarlberg", 0, len(rows), len(weg), "daten/sperren/bericht.md", {"strecken": len(rows), "wildruhezonen": {k: len(v) for k, v in erg.items()}})
    stand_p = os.path.join(ordner, "stand.json"); stand = lade(stand_p, {}); stand["mtb_vorarlberg"] = {"anzahl": len(rows), "datum": H, "weg": len(weg)}; speichern(stand_p, stand)
    print(f"VoGIS MTB-Strecken: {len(rows)} eingetragen, {len(weg)} nicht mehr gefunden")

# ---------------------------------------------------------------- Tirol (Radrouten, CC0)
TIROL = "https://services3.arcgis.com/hG7UfxX49PQ8XkXh/arcgis/rest/services/Radrouten_Tirol/FeatureServer/0/query"
def tirol(ordner):
    feats, off = [], 0
    while True:
        q = {"where": "1=1", "outFields": "*", "outSR": "4326", "f": "geojson", "resultOffset": off, "resultRecordCount": 1000}
        r = holen(TIROL + "?" + urllib.parse.urlencode(q), timeout=180)
        fs = r.get("features", []); feats += fs
        if len(fs) < 1000: break
        off += 1000
    rows, status = [], Counter()
    for f in feats:
        p = f["properties"]
        if not f.get("geometry"): continue
        geom, c = ewkt(f["geometry"], 0.00001)
        if not geom: continue
        stt = (p.get("STATUS") or "").lower(); status[stt] += 1
        ts = p.get("UPDATETIMESTAMP")
        rows.append({"ext_id": f"tirol:{p.get('OBJECTID')}", "name": p.get("ROUTENNAME") or f"Route {p.get('ROUTENNUMMER')}",
                     "art": "singletrail" if "single" in (p.get("ROUTEN_TYP") or "").lower() else "route", "modell": "Mountainbikemodell Tirol", "schwierigkeit": p.get("ROUTEN_SCHWIERIGKEIT"), "geom": geom,
                     "land": "AT", "region": "Tirol", "quelle": "Land Tirol – Radrouten Tirol (CC0)",
                     "quelle_url": "https://data-tiris.opendata.arcgis.com/datasets/radrouten-tirol",
                     "quelle_datum": datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).date().isoformat() if ts else None, "pruef_datum": H,
                     "sicherheit": "bestaetigt", "status": "aktiv" if stt in ("offen", "") else "gesperrt",
                     "daten": {"lauf": "sperren", "typ": p.get("ROUTEN_TYP"), "nummer": p.get("ROUTENNUMMER"), "abschnitt": p.get("ROUTENSEKTION_TYP"), "status_land": p.get("STATUS"), "km": p.get("LAENGE_KM")}})
    upsert("gb_mtb_routen", rows)
    weg = markiere_weg("gb_mtb_routen", "tirol:", {r["ext_id"] for r in rows})
    lauf("Radrouten Tirol", 0, len(rows), len(weg), "daten/sperren/bericht.md", {"abschnitte": len(rows), "status": dict(status)})
    stand_p = os.path.join(ordner, "stand.json"); stand = lade(stand_p, {}); stand["mtb_tirol"] = {"anzahl": len(rows), "datum": H, "status": dict(status), "weg": len(weg)}; speichern(stand_p, stand)
    print(f"Radrouten Tirol: {len(rows)} Abschnitte ({dict(status)}), {len(weg)} nicht mehr gefunden")

# ---------------------------------------------------------------- MTB-Routen aus den Kartendaten (Hinweis)
def osm_mtb(ordner, grenzfile, pbfs):
    import osmium
    from shapely.geometry import LineString, MultiLineString
    from shapely import wkt as W
    os.environ["GRENZEN"] = grenzfile
    rels = {}
    class R(osmium.SimpleHandler):
        def relation(s, r):
            t = {x.k: x.v for x in r.tags}
            if t.get("type") == "route" and t.get("route") == "mtb" and t.get("state") not in ("proposed", "disused"):
                rels[r.id] = (t, [m.ref for m in r.members if m.type == "w"])
    ways = {}
    for p in pbfs: R().apply_file(p)
    need = {w for _, ws in rels.values() for w in ws}
    class Wy(osmium.SimpleHandler):
        def way(s, w):
            if w.id in need:
                try: ways[w.id] = [(n.lon, n.lat) for n in w.nodes]
                except Exception: pass
    for p in pbfs: Wy().apply_file(p, locations=True, idx="flex_mem")
    rows = []
    for rid, (t, ws) in rels.items():
        ls = [LineString(ways[w]) for w in ws if w in ways and len(ways[w]) > 1]
        if not ls: continue
        g = MultiLineString(ls).simplify(0.00001)
        c = g.representative_point(); bl, bez, gem = ort(c.x, c.y)
        if bl in ("Vorarlberg", "Tirol"): continue   # dort gibt es die amtlichen Landesdaten
        if not bl: continue
        land = {"Südtirol": "IT", "Liechtenstein": "LI"}.get(bl, "AT")
        rows.append({"ext_id": f"osm:r{rid}", "osm_typ": "relation", "osm_id": rid, "name": t.get("name") or t.get("ref") or f"MTB-Route {rid}",
                     "art": "route", "osm_wege": ws[:2000], "geom": "SRID=4326;" + W.dumps(g, rounding_precision=6),
                     "land": land, "region": bl, "bezirk": bez, "gemeinde": gem, "betreiber": t.get("operator"), "webseite": t.get("website") or t.get("url"),
                     "schwierigkeit": t.get("mtb:scale") or t.get("mtb:scale:imba"), "quelle": "OpenStreetMap (Kartendaten)",
                     "quelle_url": f"https://www.openstreetmap.org/relation/{rid}", "pruef_datum": H, "sicherheit": "eine_quelle", "status": "aktiv",
                     "daten": {"lauf": "sperren", "typ": "OSM-Route (Kartendaten)", "hinweis": "nur Kartendaten – Freigabe vor Ort beachten", "operator": t.get("operator")}})
    upsert("gb_mtb_routen", rows)
    weg = markiere_weg("gb_mtb_routen", "osm:", {r["ext_id"] for r in rows})
    c = Counter(r["region"] for r in rows)
    stand_p = os.path.join(ordner, "stand.json"); stand = lade(stand_p, {}); stand["mtb_osm"] = {"anzahl": len(rows), "je_region": dict(c), "datum": H, "weg": len(weg)}; speichern(stand_p, stand)
    lauf("MTB-Routen Kartendaten", 0, len(rows), len(weg), "daten/sperren/bericht.md", {"routen": len(rows), "je_region": dict(c)})
    print(f"MTB-Routen aus den Kartendaten: {len(rows)} ({dict(c)}), {len(weg)} nicht mehr gefunden")

# ---------------------------------------------------------------- Landesregeln
def regeln(ordner):
    rg = json.load(open(os.path.join(HIER, "sperren_regeln.json")))["regeln"]
    rows = []
    for r in rg:
        w, s, e, n = r["bbox"]
        rows.append({"ext_id": "regel:" + r["code"], "bezeichnung": "Landesregel " + r["region"], "art": "dauerhaft", "grund": "Landesrecht",
                     "aktivitaeten": ["mtb", "emtb", "rad"], "verbot": {"nur_freigegeben": "Rad nur auf freigegebenen Wegen", "geeignete_wege": "Rad nur auf geeigneten Wegen",
                     "unter_2m_verboten": "Rad auf Wegen unter 2 m verboten", "kantonal": "Rad: kantonale Regeln", "gemeinde_verbote": "Rad: Sperren durch Gemeinden"}[r["rad_wald"]],
                     "geom": f"SRID=4326;POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))", "lat": (s + n) / 2, "lng": (w + e) / 2,
                     "land": r["land"], "region": r["region"], "rechtsgrundlage": r.get("rechtsgrundlage"), "hinweise": r["text"],
                     "quelle": r["quelle"], "quelle_url": r["quelle_url"], "quelle_datum": r.get("quelle_datum"), "pruef_datum": H,
                     "sicherheit": r["sicherheit"], "status": "aktiv", "pruefliste": bool(r.get("pruefen")),
                     "daten": {"lauf": "sperren", "herkunft": "landesregel", "landesregel": {"code": r["code"], "rad_wald": r["rad_wald"]}, "pruefen": r.get("pruefen")}})
    upsert("gb_sperren", rows)
    print(f"Landesregeln: {len(rows)}")

# ---------------------------------------------------------------- tägliche Quellen-Seiten
SPERR_ZEILE = re.compile(r"(gesperrt|sperre|wegsperr|nicht begehbar|nicht befahrbar|geschlossen|unpassierbar|forstarbeit|holzschl|steinschlag|mure|felssturz|"
                         r"hangrutsch|brücke|sprengung|schießübung|übungsgelände)", re.I)
DATUM = re.compile(r"\b\d{1,2}\.\s?\d{1,2}\.(\s?\d{2,4})?|\b\d{1,2}\.\s?(jän|jan|feb|mär|apr|mai|jun|jul|aug|sep|okt|nov|dez)[a-zä]*", re.I)
_robots = {}
def darf(url):
    u = urllib.parse.urlparse(url); h = u.scheme + "://" + u.netloc
    if h not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(holen(h + "/robots.txt", "text/plain", 20, roh=True).decode("utf-8", "replace").splitlines())
        except Exception: rp = None
        _robots[h] = rp
    return _robots[h] is None or _robots[h].can_fetch(UA, url)

def text_aus(b):
    t = b.decode("utf-8", "replace")
    t = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>|</(p|li|tr|h\d|div)>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return [re.sub(r"\s+", " ", z).strip() for z in t.split("\n") if z.strip()]

def seiten(ordner):
    stand_p = os.path.join(ordner, "seiten.json"); alt = lade(stand_p, {})
    neu, aend = {}, []
    for z in open(os.path.join(HIER, "sperren_quellen.txt"), encoding="utf-8"):
        z = z.strip()
        if not z or z.startswith("#"): continue
        region, art, url = (z.split(";", 2) + ["", ""])[:3]
        if art != "seite": continue
        e = {"region": region, "datum": H}
        try:
            if not darf(url): e["fehler"] = "robots.txt verbietet"; neu[url] = e; continue
            b = holen(url, "text/html,application/pdf", 60, roh=True)
            zeilen = [x for x in text_aus(b) if SPERR_ZEILE.search(x) and 15 < len(x) < 400]
            # nur Fakten-Zeilen mit Datum oder Ortsangabe merken; im Repo nur die Prüfsumme + Anzahl, die Zeilen selbst nur in der Prüfliste
            mit_datum = [x for x in zeilen if DATUM.search(x)]
            e["hash"] = hashlib.sha1("\n".join(zeilen).encode()).hexdigest()[:16]; e["zeilen"] = len(zeilen); e["mit_datum"] = len(mit_datum)
            if url in alt and (alt.get(url) or {}).get("hash") != e["hash"]:
                vorher = set((alt.get(url) or {}).get("auszug") or [])
                e["auszug"] = [x[:220] for x in mit_datum[:40]]
                aend.append({"url": url, "region": region, "neu_zeilen": [x for x in e["auszug"] if x not in vorher][:25]})
            else: e["auszug"] = (alt.get(url) or {}).get("auszug") or [x[:220] for x in mit_datum[:40]]
        except Exception as ex: e["fehler"] = str(ex)[:120]
        neu[url] = e
    speichern(stand_p, neu)
    speichern(os.path.join(ordner, "seiten-aenderungen.json"), {"datum": H, "aenderungen": aend})
    print(f"Quellen-Seiten: {len(neu)} gelesen, {len(aend)} geändert")

# ---------------------------------------------------------------- Entscheidungen aus dem Abgleich mit Claude
FELDER_OK = {"bezeichnung", "art", "grund", "aktivitaeten", "verbot", "gueltig_von", "gueltig_bis", "zeiten", "ausnahmen", "rechtsgrundlage", "hinweise",
             "quelle", "quelle_url", "quelle_datum", "sicherheit", "status", "pruefliste", "gemeinde", "bezirk", "region", "land"}
def entscheidungen(ordner):
    """daten/sperren/entscheidungen.json: {"<ext_id>": {Feld: Wert, "daten": {...}, "grund_entscheidung": "...", "datum": "JJJJ-MM-TT"}}
    Neue Einträge (z. B. Gemeinde-Sperre von einer Quellen-Seite): ext_id „manuell:<kurz>“ mit allen Feldern und "geojson" (Linie/Fläche).
    Nichts wird gelöscht; status „abgelaufen“/„nicht_mehr_gefunden“ ist erlaubt."""
    e = lade(os.path.join(ordner, "entscheidungen.json"), {})
    n = 0
    for eid, v in e.items():
        if eid.startswith("_") or not isinstance(v, dict): continue
        body = {k: v[k] for k in v if k in FELDER_OK}
        body["pruef_datum"] = v.get("datum") or H
        dd = {"entscheidung": {"grund": v.get("grund_entscheidung"), "datum": v.get("datum") or H}}
        if v.get("geojson"):
            geom, c = ewkt(v["geojson"], 0.00001); body.update({"geom": geom, "lat": c[1], "lng": c[0]})
        if eid.startswith("manuell:"):
            body.update({"ext_id": eid, "daten": dict(v.get("daten") or {}, lauf="sperren", herkunft="manuell", **dd)}); body.setdefault("status", "aktiv")
            upsert("gb_sperren", [body]); n += 1; continue
        if not db_an(): continue
        tab = "gb_mtb_routen" if eid.split(":")[0] in ("vbg", "tirol", "osm") else "gb_sperren"
        alt = json.loads(sb("GET", f"{tab}?select=daten&ext_id=eq.{urllib.parse.quote(eid)}"))
        if not alt: print("Entscheidung ohne Eintrag:", eid); continue
        body["daten"] = dict(alt[0]["daten"] or {}, **(v.get("daten") or {}), **dd)
        sb("PATCH", f"{tab}?ext_id=eq.{urllib.parse.quote(eid)}", body, "return=minimal"); n += 1
    print(f"Entscheidungen übernommen: {n}")

# ---------------------------------------------------------------- Bericht
def bericht(ordner):
    stand = lade(os.path.join(ordner, "stand.json"), {}); seiten_ = lade(os.path.join(ordner, "seiten.json"), {})
    aend = lade(os.path.join(ordner, "seiten-aenderungen.json"), {}); vg = lade(os.path.join(ordner, "vogis-wildruhezonen.json"), {})
    sp = lade(os.path.join(ordner, "sperren.json"), []); pr = lade(os.path.join(ordner, "sperren-pruef.json"), [])
    z = [f"# Sperren-Abgleich – Stand {H}", "", "Automatisch erzeugt von karte/sperren.py (ohne KI). Quelle der Schutzgebiete und befristeten Sperren: Digitize the Planet (offene Schnittstelle, wie bei Outdooractive), dazu Landesdaten Vorarlberg und Tirol.", ""]
    z += ["## Schutzgebiete mit Regeln für Wege (Betreten, Rad, Hunde, Klettern)", "", "| Region | Gebiete | neu | geändert | Fehler | Stand |", "|---|---|---|---|---|---|"]
    for r, _, _ in REGIONEN:
        b = (stand.get("gebiete_bericht") or {}).get(r)
        if b: z.append(f"| {r} | {b['gebiete']} | {b['neu']} | {b['geaendert']} | {b['fehler']} | {b['datum']} |")
    z += ["", "## Befristete Sperren (täglich)", "", f"Stand {stand.get('sperren_datum', '–')}: {len(sp)} aktive oder angekündigte Sperren/Hinweise eingetragen, {len(stand.get('sperren_neu') or [])} neu seit dem letzten Lauf. Zählung: {stand.get('sperren_zahl')}", ""]
    nr = Counter(x.get("q") or "?" for x in sp)
    z += ["Herausgeber (Top 15): " + ", ".join(f"{k} {v}" for k, v in nr.most_common(15)), ""]
    z += ["## Freigegebene Mountainbike-Strecken (Positivliste)", "",
          f"- Vorarlberg (Land, VoGIS): {(stand.get('mtb_vorarlberg') or {}).get('anzahl', '–')} Abschnitte",
          f"- Tirol (Land, Radrouten Tirol): {(stand.get('mtb_tirol') or {}).get('anzahl', '–')} Abschnitte, Status {((stand.get('mtb_tirol') or {}).get('status'))}",
          f"- übrige Regionen aus den Kartendaten (Hinweis): {(stand.get('mtb_osm') or {}).get('anzahl', '–')} Routen {((stand.get('mtb_osm') or {}).get('je_region'))}", ""]
    if vg:
        z += ["## Wildruhezonen Vorarlberg: Abgleich Land (VoGIS) ↔ Digitize the Planet", "",
              f"Gleich: {len(vg.get('gleich', []))} · Widerspruch: {len(vg.get('widerspruch', []))} · nur beim Land: {len(vg.get('fehlt_bei_dtp', []))} · ohne Zeitfenster: {len(vg.get('ohne_zeit', []))}", ""]
    open(os.path.join(ordner, "bericht.md"), "w").write("\n".join(z) + "\n")
    p = [f"# Sperren – Prüfliste ({H})", "", "Für den Abgleich mit Claude (April/Oktober) oder zwischendurch. Entscheidungen in daten/sperren/entscheidungen.json.", ""]
    p += ["## A. Befristete Sperren ohne amtlichen Herausgeber", ""] + [f"- {x['bezeichnung']} – {x.get('grund') or ''} – bis {x.get('gueltig_bis')} – {x['quelle']} ([Karte](https://www.openstreetmap.org/?mlat={x['lat']}&mlon={x['lng']}#map=15/{x['lat']}/{x['lng']}))" for x in pr] + ([] if pr else ["(keine)"])
    p += ["", "## B. Wildruhezonen Vorarlberg mit abweichendem Zeitfenster (Land ↔ Digitize)", ""] + [f"- {x['name']}: Land {x['von']}–{x['bis']} (Bescheid {x.get('bescheid')}, {x.get('bescheid_datum')}), Digitize {', '.join(x.get('dtp') or []) or '–'}" for x in vg.get("widerspruch", [])] + ([] if vg.get("widerspruch") else ["(keine)"])
    p += ["", "## C. Wildruhezonen nur beim Land (nicht bei Digitize)", ""] + [f"- {x['name']}: {x['von']}–{x['bis']} (Bescheid {x.get('bescheid')})" for x in vg.get("fehlt_bei_dtp", [])] + ([] if vg.get("fehlt_bei_dtp") else ["(keine)"])
    p += ["", "## D. Geänderte Quellen-Seiten (neue Sperr-Zeilen, nur Auszug – bitte Original lesen)", ""]
    for a in aend.get("aenderungen", []):
        p.append(f"### {a['region']}: {a['url']}"); p += [f"- {x}" for x in a["neu_zeilen"]] or ["- (Zeilen entfernt oder geändert)"]; p.append("")
    if not aend.get("aenderungen"): p.append("(keine)")
    rg = json.load(open(os.path.join(HIER, "sperren_regeln.json")))["regeln"]
    p += ["", "## E. Landesregeln mit Prüfauftrag", ""] + [f"- {r['region']}: {r['pruefen']}" for r in rg if r.get("pruefen")]
    open(os.path.join(ordner, "pruefliste.md"), "w").write("\n".join(p) + "\n")
    q = [f"# Quellen für Sperren (Stand {H})", "", "Täglich automatisch: Digitize the Planet (befristete Sperren, Schutzgebiets-Änderungen). Täglich gelesen, Änderungen auf die Prüfliste:", "",
         "| Region | Adresse | Sperr-Zeilen | mit Datum | Fehler |", "|---|---|---|---|---|"]
    for u, e in seiten_.items(): q.append(f"| {e.get('region')} | {u} | {e.get('zeilen', '–')} | {e.get('mit_datum', '–')} | {e.get('fehler', '')} |")
    open(os.path.join(ordner, "quellen.md"), "w").write("\n".join(q) + "\n")
    print("Bericht geschrieben")

if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2: print(__doc__); sys.exit(1)
    c, o = a[0], a[1]; os.makedirs(o, exist_ok=True)
    if c == "gebiete": gebiete(o, set(a[2].split(",")) if len(a) > 2 and a[2] else None)
    elif c == "sperren": sperren(o)
    elif c == "vogis": vogis(o)
    elif c == "tirol": tirol(o)
    elif c == "osm-mtb": osm_mtb(o, a[2], a[3:])
    elif c == "regeln": regeln(o)
    elif c == "seiten": seiten(o)
    elif c == "bericht": bericht(o)
    elif c == "entscheidungen": entscheidungen(o)
    else: print(__doc__); sys.exit(1)
