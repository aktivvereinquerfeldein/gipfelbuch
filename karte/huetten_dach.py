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
  huetten_dach.py sammeln  <neu.json> <auto.json> <pruef-ordner> [quellen.json]   alle Quellen je Hütte lesen (siehe quellen.py)
  huetten_dach.py db-neu   <neu.json>      neue Einträge anlegen (vorhandene bleiben)
  huetten_dach.py db-auto  <auto.json>     eindeutige Treffer eintragen (nur recherche offen/auto)
Datenbank schreiben braucht das GitHub-Secret SUPABASE_SERVICE_KEY; ohne Schlüssel bleiben nur die Dateien im Repo.
"""
import json, os, re, sys, time, unicodedata, urllib.request, urllib.parse, concurrent.futures as cf
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
                keep = {k: v for k, v in t.items() if k in ("name", "tourism", "amenity", "place", "ele", "website", "contact:website", "url", "phone", "contact:phone", "operator", "wikidata")}
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
                     "w": t.get("website") or t.get("contact:website") or t.get("url"), "tag": t.get("tourism") or t.get("amenity") or t.get("place"), "wd": t.get("wikidata")})
    json.dump(rows, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    n = defaultdict(int)
    for r in rows: n[r["l"] + " " + r["r"]] += 1
    print(f"{len(rows)} Hütten/Almen gesamt"); [print(f"  {k}: {v}") for k, v in sorted(n.items())]

# ---------- Webseiten lesen und vorsichtig auswerten ----------
MON = {"jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12}
WT = {"montag": "mo", "dienstag": "di", "mittwoch": "mi", "donnerstag": "do", "freitag": "fr", "samstag": "sa", "sonntag": "so"}
# Jahr am Ende ist freiwillig: Bereiche ohne Jahr zählen nicht als Saison, werden aber verglichen (Seite nennt
# z. B. „17. Mai bis 18. Oktober“ und an anderer Stelle „17. Mai bis 28. Oktober 2026“ -> Prüfliste)
R_RANGE = re.compile(r"(\d{1,2})\.\s?(\d{1,2})\.(\d{2,4})?\s*(?:–|-|bis)\s*(?:voraussichtlich\s+|vorauss\.\s*|ca\.\s*|etwa\s+)?(\d{1,2})\.\s?(\d{1,2})\.(\d{2,4})?(?!\d)")
MONATE = {"jänner": 1, "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7, "august": 8,
          "september": 9, "oktober": 10, "november": 11, "dezember": 12,
          "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9,
          "ottobre": 10, "novembre": 11, "dicembre": 12,
          "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
          "septembre": 9, "octobre": 10, "décembre": 12, "decembre": 12}
_M = "|".join(MONATE)
R_RANGE_M = re.compile(rf"(\d{{1,2}})\.\s*({_M})\s*(\d{{4}})?\s*(?:–|-|bis)\s*(?:voraussichtlich\s+|vorauss\.\s*|ca\.\s*|etwa\s+)?(\d{{1,2}})\.\s*({_M})\s*(\d{{4}})?", re.I)

R_RANGE_IF = re.compile(rf"\b(?:dal|dall'|du|le)?\s*(\d{{1,2}})(?:er)?\s+({_M})\s*(\d{{4}})?\s*(?:–|-|al|all'|au|à|jusqu'au)\s*(\d{{1,2}})\s+({_M})\s*(\d{{4}})?", re.I)

def zeitraeume(text):
    """alle Datumsbereiche: (start, ende, d1, m1, d2, m2, y2) – Ziffern- und Monatsnamen-Schreibweise, y2 = None ohne Jahr"""
    out = []
    for m in R_RANGE.finditer(text):
        d1, m1, _, d2, m2, y2 = m.groups()
        out.append((m.start(), m.end(), d1, m1, d2, m2, y2))
    for m in R_RANGE_M.finditer(text):
        d1, m1, _, d2, m2, y2 = m.groups()
        out.append((m.start(), m.end(), d1, MONATE[m1.lower()], d2, MONATE[m2.lower()], y2))
    for m in R_RANGE_IF.finditer(text):
        d1, m1, _, d2, m2, y2 = m.groups()
        out.append((m.start(), m.end(), d1, MONATE[m1.lower()], d2, MONATE[m2.lower()], y2))
    for m in R_RANGE_U.finditer(text):   # „Anfang Juni bis Mitte Oktober“ -> ungefähr (Tag 1 / 15 / Monatsende), nie mit Jahr
        a1, m1, a2, m2 = m.groups(); m1, m2 = MONATE[m1.lower()], MONATE[m2.lower()]
        tag = lambda a, mo: {"anfang": 1, "mitte": 15}.get(a.lower(), [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1])
        out.append((m.start(), m.end(), tag(a1, m1), m1, tag(a2, m2), m2, "ca"))
    return sorted(out)
R_RANGE_U = re.compile(rf"\b(anfang|mitte|ende)\s+({_M})\s*(?:–|-|bis)\s*(anfang|mitte|ende)\s+({_M})\b", re.I)
# ---------- Hüttenname auf der Seite? ----------
GENERISCH = set("""huette hutte hütte alm alpe alp haus berghaus berggasthof berggasthaus gasthof gasthaus alpengasthof alpengasthaus
schutzhaus schutzhuette schutzhütte naturfreundehaus naturfreunde rifugio malga capanna cabane chamanna bergrestaurant restaurant
sac cas dav oeav öav clubhaus skihuette skihütte jagdhuette jagdhütte selbstversorgerhuette berg neue alte obere untere hintere
vordere mittlere grosse große kleine die der das am an im auf zum zur bei und mountain lodge cafe café stueberl stüberl stube
hof wirtshaus jausenstation alpengasthaus almhuette almhütte vereinsheim waldheim heim wanderheim skiheim bergheim""".split())

def _n(s):
    s = (s or "").lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()

def kern(name):
    """kennzeichnende Namensteile (ohne Hütte/Alm/Gasthof …), z. B. „Gaudeamushütte“ -> ['gaudeamus']"""
    out = []
    for w in re.split(r"[^a-zäöüß]+", (name or "").lower()):
        if not w or w in GENERISCH: continue
        w2 = re.sub(r"(huette|hütte|hutte|alm|alpe|haus|hof|stube|stüberl)$", "", w)
        if len(w2) >= 4 and w2 not in GENERISCH: out.append(_n(w2))
    return out

def nennt_huette(name, zeilen, url=""):
    """Hüttenname auf der Seite oder in der eigenen Adresse? Leicht abweichende Schreibweisen zählen
    („Gasselhöh-Hütte“ für „Gasselhöhe Hütte“, „Fränkis“ für „Fränkies“): Namensteil ohne die letzten 1–2 Buchstaben."""
    k = kern(name)
    if not k: return True   # Name nur aus Allgemeinwörtern -> nicht prüfbar
    t = re.sub(r"[^a-z]", "", _n(" ".join(zeilen)))
    dom = re.sub(r"[^a-z]", "", _n(urllib.parse.urlparse(url).netloc)) if url else ""
    for w in k:
        varianten = {w} | ({w[:max(6, len(w) - 2)], w[:-1]} if len(w) >= 7 else set())
        if any(v in t or (dom and v in dom) for v in varianten): return True
    return False

# ---------- Auswerten ----------
R_SAISONWORT = re.compile(r"(geöffnet|geoeffnet|öffnungszeit|oeffnungszeit|saison|bewirtschaftet|sommerbetrieb|winterbetrieb|sommer|winter|offen\b|"
                          r"aperto|apertura|stagione|estate|inverno|ouvert|ouverture|saison|été|hiver|gardienn)", re.I)
R_ZU = re.compile(r"(geschlossen|betriebsurlaub|urlaub|ruhezeit|pause|closed|revision|ferien|nicht geöffnet|außer|ausser|ausgenommen|umbau|renovierung|"
                  r"chiuso|chiusura|ferie|fermé|ferme\b|fermeture|vacances|tranne|sauf)", re.I)
TAGE = "montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag"
TAG_S = r"(?:" + TAGE + r")s?"   # auch „montags“, „dienstags“
R_RUHE = re.compile(r"ruhetage?\s*:?\s*(" + TAG_S + r"(?:\s*(?:,|und|&|/)\s*" + TAG_S + r")*)\s*$")
# Bedingung am Ruhetag (gilt nur zeitweise) -> nicht als fester Ruhetag, sondern als Text in „zeiten“
R_RUHE_BED = re.compile(r"(außer|ausser|ausgenommen|juli|august|hochsaison|nebensaison|vorsaison|nachsaison|zwischensaison|"
                        r"winter|sommer|saison|wetter|möglich|je nach|bei |schlecht|feiertag|ferien|nur |ab \d|bis \d|uhr|nachmittag|abend)")
R_RUHE_VOR = re.compile(r"(winter|sommer|nebensaison|hochsaison|vorsaison|nachsaison|zwischensaison)")
# Betrieb zu oder ohne Wirt: dann keine Einkehr eintragen, sondern Prüfliste
R_BETRIEB_ZU = re.compile(r"(vorübergehend geschlossen|vorruebergehend geschlossen|dauerhaft geschlossen|bis auf weiteres geschlossen|"
                          r"betrieb eingestellt|für immer geschlossen|endgültig geschlossen|pächter gesucht|pächterin gesucht|"
                          r"pächter/in gesucht|neue pächter gesucht|hüttenwirt gesucht|hüttenwirte gesucht|kein restaurant|"
                          r"keine bewirtung|keine bewirtschaftung|derzeit nicht bewirtschaftet|closed permanently|temporarily closed|"
                          r"aufgrund bauarbeiten|wegen bauarbeiten|aufgrund von bauarbeiten|wegen umbau|umbaubedingt geschlossen)")
# Essen/Trinken: jedes dieser Wörter zeigt Bewirtung (Selbstkocherküche zählt nicht: \b vor „küche“)
R_ESSEN = re.compile(r"((?<!un)(?<!nicht )(?<!teil)bewirtschaftet|bewirtet|hüttenwirt|speisekarte|\bküche\b|warme speisen|bewirtung|"
                     r"halbpension|kaiserschmarr|jause|brotzeit|mittagstisch|tageskarte|schmankerl|kulinari|gaststube|einkehr|"
                     r"getränkekarte|menükarte|vollbewirtschaftet|hausgemacht|kuchen|essen und trinken|speisen und getränke|"
                     r"frühstück|abendessen|mittagessen|bergsteigeressen|gastronomie|selbstgemacht|knödel|strudel|gulasch|schnitzel|"
                     r"brettljause|kasspatzen|kässpätzle|käsespätzle|germknödel|speckbrot|torte|suppe\b|suppen|"
                     r"ristorante|buona cucina|pranzo|cena\b|mezza pensione|piatti|polenta|canederli|restauration|repas\b|"
                     r"demi-pension|petit-déjeuner|plats\b|menu du jour|fondue|raclette|croûte)")
R_SELBST = re.compile(r"(selbstversorgerhütte|selbstversorgerhaus|selbstversorger-hütte|selbstversorger-haus|unbewirtschaftet|"
                      r"nicht bewirtschaftet|nur für selbstversorger|reine selbstversorger|als selbstversorger|selbstversorger\b|"
                      r"non custodit|non gestit|incustodit|autogestit|non gardienn|sans gardien|non gardée)")
R_WINTERRAUM = re.compile(r"(winterraum|winterlager|im winter|außerhalb der|ausserhalb der|nebensaison|notlager|selbstversorgerraum|"
                          r"locale invernale|bivacco invernale|local d'hiver|salle d'hiver|hors saison|fuori stagione)")
R_ANDERE_HUETTE = re.compile(r"\b[a-zäöüß-]{3,}(hütte|huette|haus|alm|alpe)\b")
R_NEBEN = re.compile(r"(geschäftsstelle|büro|sekretariat|kletterhalle|boulder|seilbahn|bergbahn|\bbahn\b|lift|kassa|kasse|telefon|"
                     r"erreichbar|rezeption|check-in|anreise|abreise|bus\b|zug\b|fahrplan|fahrt|gottesdienst|messe\b|kurs|museum|shop|"
                     r"sprechstunde|bürozeit|hotline|parkplatz|schwimmbad|freibad|sauna|wellness|frühstück|brunch|weihnachtsmarkt|markt\b|abholung|"
                     r"vorbestell|bestellung|eintritt|snowtubing|tubing|apéro|apero|happy hour|verleih|skischule|rodelbahn|flutlicht|loipe|"
                     r"kinderprogramm|führung|treffpunkt|abfahrt|start\b)", re.I)

def plausibel(a, m1, m2):
    if a == "sommer": return 4 <= m1 <= 7 and 8 <= m2 <= 11
    return m1 in (11, 12, 1) and 3 <= m2 <= 5

def _verpflegung(name, zeilen, links, gasthaus):
    """('ja'|'nein'|'nur_getraenke'|'unbekannt', grund)"""
    k = kern(name)
    vlow = "\n".join(zeilen).lower()
    if re.search(r"selbstversorger", (name or "").lower()) and not re.search(r"(?<!un)(?<!nicht )(?<!teil)bewirtschaftet|hüttenwirt|speisekarte", vlow):
        return "nein", None   # „Hasenaualm – Selbstversorgerhütte“
    teil = re.search(r"teilbewirtschaftet|teilweise bewirtschaftet|nur getränke|nur getraenke|getränkeverkauf", vlow)
    pos = R_ESSEN.search(vlow) or next((t for _, t, _m in links if re.search(r"^(speisekarte|speisen|kulinarik|menü|menu|unsere küche|essen & trinken|essen und trinken|gastronomie)$", t.lower())), None)
    neg = None
    for z in zeilen:
        zl = z.lower()
        for m in R_SELBST.finditer(zl):
            umfeld = zl[max(0, m.start() - 80):m.end() + 80]
            if R_WINTERRAUM.search(umfeld): continue   # bewirtschaftete Hütte mit Winterraum für Selbstversorger
            fremd = [h.group(0) for h in R_ANDERE_HUETTE.finditer(zl) if k and not any(w in _n(h.group(0)) for w in k)]
            if fremd: continue   # Zeile handelt von einer anderen Hütte (Teaser, Liste)
            neg = m; break
        if neg: break
    if teil:
        if re.search(r"(teilbewirtschaftet|teilweise bewirtschaftet)\s*\(?\s*(nur )?mit getränken|nur getränke|nur getraenke", vlow) \
           and not re.search(r"warme küche|warme speisen|speisekarte|vollbewirtschaftet", vlow):   # auch bei OSM-Tag restaurant
            return "nur_getraenke", None
        return "unbekannt", f"Verpflegung unklar („{teil.group(0)}“)"
    if neg and not pos:
        return ("unbekannt", "Gasthaus, aber Seite nennt Selbstversorger") if gasthaus else ("nein", None)
    if pos and not neg: return "ja", None
    if pos and neg: return "unbekannt", "Verpflegung widersprüchlich (Selbstversorger und Bewirtung erwähnt)"
    return "unbekannt", None

# Tageszeiten: „täglich 9–17 Uhr“, „Mi–So 11:00 – 20:00 Uhr“, „Sa, So & Feiertag 8:30-16:30“
_D = r"(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|mo|di|mi|do|fr|sa|so)s?\.?"
R_TAGSPEC = re.compile(rf"(täglich|tgl\.|durchgehend|jeden tag|sonn-\s*(?:und|&|u\.)\s*feiertags?e?n?|{_D}(?:\s*(?:–|-|bis|,|&|und|/)\s*{_D})*(?:\s*(?:,|&|und|/|\+)\s*feiertags?e?n?)?(?:\s*(?:,|&|und)\s*brückentage?n?)?)\s*[:–-]?\s*(?:von\s*|ab\s*)?$", re.I)
R_UHRZEIT = re.compile(r"(?<![\d.,])(\d{1,2})(?:[:.](\d{2}))?\s*(?:uhr|h)?\s*(?:–|-|bis)\s*(\d{1,2})(?:[:.](\d{2}))?\s*(uhr|h\b)?", re.I)
KURZ = {"montag": "Mo", "dienstag": "Di", "mittwoch": "Mi", "donnerstag": "Do", "freitag": "Fr", "samstag": "Sa", "sonntag": "So",
        "mo": "Mo", "di": "Di", "mi": "Mi", "do": "Do", "fr": "Fr", "sa": "Sa", "so": "So"}

def _tagtext(s):
    s = s.lower().strip()
    if s.startswith(("täglich", "tgl", "durchgehend", "jeden tag")): return "täglich"
    if s.startswith("sonn"): return "So, Feiertag"
    teile = re.findall(rf"{_D}|–|-|bis|feiertag", s)
    out = ""
    for t in teile:
        t = t.rstrip(".")
        if t in ("–", "-", "bis"): out += "–"
        elif t.startswith("feiertag"): out += (", " if out and not out.endswith("–") else "") + "Feiertag"
        else:
            t = re.sub(r"s$", "", t) if t not in ("mo", "di", "mi", "do", "fr", "sa", "so") else t
            kz = KURZ.get(t)
            if kz: out += (", " if out and not out.endswith("–") else "") + kz
    return out.strip(", –")

def _uhr(h, m): return f"{int(h)}" + (f":{m}" if m and m != "00" else "")

R_AB = re.compile(r"\bab\s+(\d{1,2})(?:[:.](\d{2}))?\s*uhr\b(?!\s*(?:–|-|bis))", re.I)
R_ANDERER_BETRIEB = re.compile(r"(stadl|\bbar\b|lounge|après|apres|schirmbar|skibar|hotel|pizzeria|disco|club\b)", re.I)

def tageszeiten(zeilen, name=""):
    """Liste kurzer Texte wie ['täglich 9–17 Uhr', 'Küche 11:30–20 Uhr', 'Mi, Fr ab 10 Uhr']"""
    out = []
    k = kern(name)
    for i, z in enumerate(zeilen):
        if len(z) > 220 or R_NEBEN.search(z): continue
        if any(len(zeilen[j]) < 200 and R_NEBEN.search(zeilen[j]) and not R_UHRZEIT.search(zeilen[j]) for j in range(max(0, i - 2), i)): continue   # Überschrift „Snowtubing-Bahn“
        if R_ANDERER_BETRIEB.search(z) and not (k and any(w in _n(z).replace(" ", "") for w in k)): continue   # z. B. „Fernau Stadl täglich 11–18 Uhr“
        davor = " ".join(zeilen[max(0, i - 4):i])
        zeile_out = []
        for m in R_AB.finditer(z):
            t = R_TAGSPEC.search(z[max(0, m.start() - 60):m.start()])
            if t and 5 <= int(m.group(1)) <= 14:
                txt = f"{_tagtext(t.group(1))} ab {_uhr(m.group(1), m.group(2))} Uhr"
                if txt not in out: out.append(txt)
        letzte = 0
        for m in R_UHRZEIT.finditer(z):
            h1, m1, h2, m2, uhr = m.groups()
            von_ = letzte; letzte = m.end()
            nur_zeiten = re.fullmatch(r"[\d\s:./–\-&,+]*(?:und[\d\s:./–\-&,+]*)*uhr\.?", z.strip().lower())   # „10 - 15 / 18 - 23 Uhr“
            if not (uhr or (m1 and m2) or nur_zeiten): continue
            spaet = int(h2) <= 3 and uhr
            if not (5 <= int(h1) <= 18 and ((11 <= int(h2) <= 24 and int(h2) >= int(h1) + 2) or spaet)): continue
            vor = z[max(von_, m.start() - 60):m.start()]   # nur Text seit der letzten Uhrzeit derselben Zeile
            t = R_TAGSPEC.search(vor)
            kueche = re.search(r"küche|warme speisen|\bessen\b", vor, re.I)
            if not t and not kueche and i > 0 and len(zeilen[i - 1]) <= 45:
                t = R_TAGSPEC.search(zeilen[i - 1])
            kueche = kueche or re.match(r"\s*\(?\s*(?:warme\s+)?küche\b(?!\s*:?\s*(?:von\s*|ab\s*)?\d)", z[m.end():], re.I)   # „11:30 - 16:00 Uhr (Küche)“
            if not t and not kueche:
                if not (re.search(r"(öffnungszeit|geöffnet|geoeffnet|offen)", vor, re.I)
                        or (m.start() < 3 and re.search(r"(öffnungszeit|geöffnet|geoeffnet)", davor, re.I))): continue
            tag = _tagtext(t.group(1)) if t else ""
            jz = {w.lower() for w in re.findall(r"\b(sommer|winter)\b", (" ".join(l_ for l_ in zeilen[max(0, i - 2):i] if not re.search(r"\d{1,2}\.\s*(\d{1,2}\.|[a-zä])", l_, re.I))[-120:] + " " + z[:m.start()]), re.I)}
            pre = (jz.pop().title() + ": ") if len(jz) == 1 else ""
            zeile_out.append((pre, bool(kueche), tag, f"{_uhr(h1, m1)}–{_uhr(h2, m2)} Uhr"))
        haupt = [x for x in zeile_out if not x[1]]; kue = [x for x in zeile_out if x[1]]
        if len(haupt) == 2 and haupt[0][:3] == haupt[1][:3]:   # „12:00-14:30/17:00-21:00“ -> „12–14:30 und 17–21 Uhr“
            j = (haupt[0][0], False, haupt[0][2], haupt[0][3].removesuffix(" Uhr") + " und " + haupt[1][3])
            zeile_out = [j] + kue; haupt = [j]
        if len(haupt) == 1 and len(kue) == 1 and not kue[0][2]:   # „Di: 10 - 17 Uhr (Küche 11 - 16 Uhr)“ zusammen lassen
            pre, _, tag, zeit = haupt[0]; zeile_out = [(pre, False, tag, f"{zeit} (Küche {kue[0][3]})")]
        for pre, ku, tag, zeit in zeile_out:
            txt = pre + ("Küche " if ku else "") + (tag + " " if tag else "") + zeit
            if txt not in out: out.append(txt)
    return out

WOCHE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
R_SEG = re.compile(r"^((?:Sommer|Winter): )?(Küche )?(.*?)\s*((?:ab )?\d[\d:]*(?:–\d[\d:]*)?(?: und \d[\d:]*–\d[\d:]*)? Uhr(?: \(Küche [^)]*\))?)$")

def zusammenfassen(zt):
    """gleiche Uhrzeiten zusammenlegen: ['Mo 11–18 Uhr', 'Di 11–18 Uhr'] -> ['Mo, Di 11–18 Uhr']; Mo–Fr als Bereich"""
    gruppen, rest, reihenfolge = {}, [], []
    for seg in zt:
        m = R_SEG.match(seg)
        if not m: rest.append(seg); continue
        pre, ku, tag, zeit = m.group(1) or "", m.group(2) or "", m.group(3).strip(), m.group(4)
        key = (pre, ku, zeit)
        tage = set()
        for teil in [t.strip() for t in tag.split(",") if t.strip()]:
            if "–" in teil:
                a, b = (x.strip() for x in teil.split("–", 1))
                if a in WOCHE and b in WOCHE:
                    i, j = WOCHE.index(a), WOCHE.index(b)
                    tage |= set(WOCHE[i:j + 1] if i <= j else WOCHE[i:] + WOCHE[:j + 1]); continue
            tage.add(teil)
        if key not in gruppen: gruppen[key] = set(); reihenfolge.append(key)
        gruppen[key] |= tage
    out = []
    for key in reihenfolge:
        pre, ku, zeit = key; tage = gruppen[key]
        if "täglich" in tage or set(WOCHE) <= tage: tt = "täglich"
        else:
            idx = sorted(WOCHE.index(t) for t in tage if t in WOCHE)
            laeufe, lauf = [], []
            for i in idx:
                if lauf and i == lauf[-1] + 1: lauf.append(i)
                else:
                    if lauf: laeufe.append(lauf)
                    lauf = [i]
            if lauf: laeufe.append(lauf)
            if len(laeufe) == 2 and laeufe[0][0] == 0 and laeufe[1][-1] == 6:   # über das Wochenende: Fr–Di
                laeufe = [laeufe[1] + laeufe[0]]
            teile = [f"{WOCHE[l[0]]}–{WOCHE[l[-1]]}" if len(l) >= 3 else ", ".join(WOCHE[i] for i in l) for l in laeufe]
            teile += sorted(t for t in tage if t not in WOCHE and t)
            tt = ", ".join(teile)
        out.append(f"{pre}{ku}{tt + ' ' if tt else ''}{zeit}")
    out = [x for x in out if not any(y != x and y.endswith(": " + x) for y in out)]   # „Sa, So 12–18 Uhr“ neben „Sommer: Sa, So 12–18 Uhr“
    return out + rest

def auswerten(text, voll=None, gasthaus=False, name="", links=None):
    """gibt (felder, grund). felder auch dann, wenn nur ein Teil eindeutig ist (grund nennt den unklaren Teil für die
    Prüfliste). text = Auszug, voll = ganzer Haupttext der Seite(n). Streng: lieber weglassen als etwas Falsches eintragen."""
    voll = voll or text
    zeilen = [z for z in voll.split("\n") if z.strip()]
    vlow = voll.lower()
    # Seite mit mehreren Betrieben („Öffnungszeiten Restaurant“ … „Öffnungszeiten Weinbergshütte:“): nur den Block dieser Hütte
    kk0 = kern(name); alle_zeilen = zeilen
    oz = [i for i, z in enumerate(zeilen) if re.search(r"öffnungszeit", z, re.I) and len(z) < 60]
    eigen = [i for i in oz if kk0 and any(w in _n(zeilen[i]).replace(" ", "") for w in kk0)]
    andere = [i for i in oz if i not in eigen and re.search(r"(restaurant|hotel|biergarten|stadl|\bbar\b|café|cafe|hütte|huette|alm\b|alpe|gasthof|gasthaus|lift|bahn|shop|laden)", zeilen[i], re.I)]
    if eigen and andere:
        block = []
        for i in eigen:
            j = i + 1
            while j < len(zeilen) and j - i <= 14 and j not in oz: j += 1
            block += zeilen[i:j]
        zeilen = block; voll = "\n".join(block)
    low = voll.lower()
    gruende = []
    for zu in R_BETRIEB_ZU.finditer(vlow):
        danach = vlow[zu.end():zu.end() + 160]
        if zu.group(0).endswith(("geschlossen", "closed")) and re.search(r"(wieder|ab dem|ab \d|saisonstart|saisonbeginn|öffnen am|eröffnen|riapr|réouverture|reopen)", danach):
            continue   # Saisonpause („vorübergehend geschlossen, ab 23. Mai wieder für euch da“)
        return None, f"Betrieb evtl. geschlossen/ohne Wirt („{zu.group(0)}“)"
    v, g = _verpflegung(name, alle_zeilen, links or [], gasthaus)
    if g: gruende.append(g)
    # Saison: Zeiträume mit Saisonwort davor; Schließzeiten und ungewöhnliche Zeiträume werden übersprungen
    saisons, ohne_jahr, ca_set = set(), set(), set()
    for start, ende, d1, m1, d2, m2, y2 in zeitraeume(voll):
        vor = voll[max(0, start - 60):start]; nach = voll[ende:ende + 40]
        if R_ZU.search(vor) or R_ZU.search(nach) or R_NEBEN.search(vor.rsplit("\n", 1)[-1]): continue
        ca = y2 == "ca"
        if ca: y2 = None
        if not (R_SAISONWORT.search(vor) or re.match(r"\s*(geöffnet|geoeffnet|bewirtschaftet|offen)\b", nach, re.I)): continue
        try: d1, m1, d2, m2 = int(d1), int(m1), int(d2), int(m2)
        except ValueError: continue
        if not (1 <= m1 <= 12 and 1 <= m2 <= 12 and 1 <= d1 <= 31 and 1 <= d2 <= 31): continue
        a = "sommer" if 4 <= m1 <= 9 else "winter"
        tage = ((m2 - m1) * 30.4 + (d2 - d1)) % 365
        if tage < 45 or not plausibel(a, m1, m2): continue
        if y2 is None:
            ohne_jahr.add((a, f"{m1:02d}-{d1:02d}", f"{m2:02d}-{d2:02d}"))
            if ca: ca_set.add((a, f"{m1:02d}-{d1:02d}", f"{m2:02d}-{d2:02d}"))
            continue
        y2 = int(y2) + (2000 if len(y2) == 2 else 0)
        if y2 not in (JAHR, JAHR + 1): continue
        saisons.add((a, f"{m1:02d}-{d1:02d}", f"{m2:02d}-{d2:02d}", y2))
    arten = defaultdict(list)
    for s in saisons: arten[s[0]].append(s)
    for a, lst in list(arten.items()):
        if len(lst) > 1:
            neu_ = [s for s in lst if s[3] == max(x[3] for x in lst)]   # neuestes Jahr gilt
            if len({(s[1], s[2]) for s in neu_}) > 1: gruende.append(f"mehrere verschiedene {a.title()}saison-Daten"); del arten[a]
            else: arten[a] = neu_[:1]
    for a, von, bis in ohne_jahr - ca_set:
        if a in arten and any((s[1], s[2]) != (von, bis) for s in arten[a]):
            gruende.append(f"Seite nennt verschiedene {a.title()}saison-Daten (eine ohne Jahr: {von[3:]}.{von[:2]}.–{bis[3:]}.{bis[:2]}.)"); del arten[a]
    ungefaehr = {}
    for a, von, bis in ohne_jahr:   # Saison ohne Jahr nur, wenn die Seite für diese Art nichts anderes nennt (genaue Daten vor „ca.“)
        if a in arten: continue
        genau = {(x[1], x[2]) for x in ohne_jahr - ca_set if x[0] == a}
        kand_ = genau or {(x[1], x[2]) for x in ohne_jahr if x[0] == a}
        if len(kand_) == 1 and (von, bis) in kand_: ungefaehr[a] = (a, von, bis, None)
    saisons = [lst[0] for lst in arten.values()] + list(ungefaehr.values())
    ganzj = re.search(r"(ganzjährig (geöffnet|bewirtschaftet|offen)|das ganze jahr (über |hindurch )?(geöffnet|bewirtschaftet)|ganzes jahr geöffnet|365 tage)", low)
    if ganzj: saisons = []   # z. B. „Winter (1.11.–30.4.): Mo & Di Ruhetag“ ist dann keine Wintersaison
    # Ruhetage: ohne Bedingung -> Feld ruhetage, mit Bedingung -> als Text in zeiten
    ruhe, ergebnisse, ruhe_text = set(), set(), []
    kk = kern(name)
    def _fremd(pos):
        a_ = max(low.rfind("|", 0, pos), low.rfind("\n", 0, pos)) + 1
        e_ = min([x for x in (low.find("|", pos), low.find("\n", pos)) if x >= 0] or [len(low)])
        zl = low[max(a_, pos - 120):e_]
        return bool(re.search(r"(hotel|restaurant|stadl|\bbar\b|pizzeria|café|cafe)", zl)) and not (kk and any(w in _n(zl).replace(" ", "") for w in kk))
    def _bed(umfeld, vor80, vor150=""):
        if "sommer" in vor150 and "winter" in vor150: vor80 = ""   # Liste „Sommer: … Winter: …“ davor -> gilt für beide
        m_ = R_RUHE_VOR.search(vor80) or R_RUHE_BED.search(umfeld)
        return m_.group(0).strip() if m_ else None
    for m in re.finditer(r"ruhetag", low):
        roh = low[m.start():m.start() + 90]
        stueck = re.split(r"[.|;\n(]", roh)[0]
        r = R_RUHE.search(stueck)
        if not r or _fremd(m.start()): continue
        tage_ = frozenset(WT[w] for w in re.findall(TAGE, r.group(1)))
        b = _bed(roh[len(stueck):len(stueck) + 50] + " " + stueck, low[max(0, m.start() - 80):m.start()], low[max(0, m.start() - 150):m.start()])
        if b: ruhe_text.append((tage_, b)); continue
        ergebnisse.add(tage_); ruhe |= tage_
    for m in re.finditer(r"(" + TAG_S + r"(?:\s*(?:,|und|&|/)\s*" + TAG_S + r")*)[ \t]*[:–-]?[ \t]*(?:ist\s+|sind\s+|haben wir\s+|immer\s+)?ruhetage?\b", low):   # gleiche Zeile; Tabellen siehe unten
        a_ = max(low.rfind("|", 0, m.start()), low.rfind("\n", 0, m.start())) + 1
        e_ = min([x for x in (low.find("|", m.end()), low.find("\n", m.end())) if x >= 0] or [len(low)])
        if _fremd(m.start()): continue
        tage_ = frozenset(WT[w] for w in re.findall(TAGE, m.group(1)))
        b = _bed(low[a_:e_], low[max(0, m.start() - 80):m.start()], low[max(0, m.start() - 150):m.start()])
        if b: ruhe_text.append((tage_, b)); continue
        ergebnisse.add(tage_); ruhe |= tage_
    # Tabelle: „Mittwoch“ in einer Zeile, „RUHETAG“ in der nächsten
    for i in range(1, len(zeilen)):
        if re.fullmatch(r"\s*(ruhetag|geschlossen|closed|chiuso|fermé)\s*!?\s*", zeilen[i], re.I):
            t = R_TAGSPEC.fullmatch(zeilen[i - 1].strip()) if len(zeilen[i - 1]) <= 40 else None
            if not t or t.group(1).lower().startswith(("täglich", "tgl", "durchgehend", "jeden")): continue
            kurz = _tagtext(t.group(1)); tage_ = set()
            for teil in [x.strip() for x in kurz.split(",")]:
                if "–" in teil:
                    a, b = (x.strip() for x in teil.split("–", 1))
                    if a in WOCHE and b in WOCHE: tage_ |= set(WOCHE[WOCHE.index(a):WOCHE.index(b) + 1])
                elif teil in WOCHE: tage_.add(teil)
            if tage_: ruhe |= {x.lower() for x in tage_}; tabelle = True
    if len(ergebnisse) > 1: gruende.append("verschiedene Ruhetage genannt"); ruhe = set()
    ruhe = sorted(ruhe, key=list(WT.values()).index)
    # Text für zeiten: Tageszeiten + bedingte Ruhetage
    zt = zusammenfassen(tageszeiten(zeilen, name))
    if len(zt) > 6: gruende.append(f"viele verschiedene Uhrzeiten ({len(zt)})"); zt = []
    # nach Jahreszeit gruppieren: „ganzjährig …, Mo–Fr 10–17 Uhr; Sommer: täglich 9–18 Uhr; Winter: 10–16 Uhr, Dienstag Ruhetag“
    # (die App zeigt davon nur den allgemeinen Teil und die gerade laufende Jahreszeit)
    gr = {"": [], "Sommer": [], "Winter": []}
    for seg in zt:
        m_ = re.match(r"^(Sommer|Winter): (.*)$", seg)
        if m_: gr[m_.group(1)].append(m_.group(2))
        else: gr[""].append(seg)
    for tage_, b in [x for x in ruhe_text if not x[0] <= set(ruhe)][:2]:
        namen = ", ".join(k.title() for k, x in WT.items() if x in tage_)
        if not namen: continue
        jz_ = {"winter": "Winter", "sommer": "Sommer"}.get(b)
        if jz_: gr[jz_].append(f"{namen} Ruhetag")
        else: gr[""].append(f"{namen} Ruhetag (nicht immer)")
    if ganzj: gr[""].insert(0, "ganzjährig geöffnet")
    for k_ in gr:
        gr[k_] = list(dict.fromkeys(gr[k_]))
        if len(", ".join(gr[k_])) > 140: gr[k_] = gr[k_][:3]
    zeiten = "; ".join(x for x in [", ".join(gr[""]), ("Sommer: " + ", ".join(gr["Sommer"])) if gr["Sommer"] else "",
                                   ("Winter: " + ", ".join(gr["Winter"])) if gr["Winter"] else ""] if x)
    if v == "unbekannt" and not saisons and not ruhe and not zeiten:
        return None, "; ".join(gruende) or "nichts Eindeutiges gefunden"
    f = {"verpflegung": v}
    o = {}
    if saisons:
        mit_jahr = [s for s in saisons if s[3]]
        if mit_jahr: o["jahr"] = max(s[3] for s in mit_jahr)
        o["saisons"] = [{"art": a, "von": von, "bis": bis, **({} if y else {"ohne_jahr": True}), **({"ungefaehr": True} if (a, von, bis) in ca_set else {})}
                        for a, von, bis, y in sorted(saisons, key=lambda s: s[0])]
        for a, von, bis, y in saisons:
            f["saison_" + a] = f"{'ca. ' if (a, von, bis) in ca_set else ''}{von[3:]}.{von[:2]}.–{bis[3:]}.{bis[:2]}.{y or ''}"
    if ruhe:
        o["ruhetage"] = ruhe; f["ruhetage"] = ", ".join(k.title() for k, x in WT.items() if x in ruhe)
    if zeiten: o["zeiten"] = zeiten; f["zeiten"] = zeiten
    if o: f["oeffnung"] = o
    return f, ("; ".join(gruende) or None)

FERTIG = [x.strip() for x in os.environ.get("FERTIG_REGIONEN", "Vorarlberg").split(",") if x.strip()]   # schon von Claude recherchiert

R_REL = re.compile(r"(geöffnet|geoeffnet|öffnungszeit|oeffnungszeit|saison|ruhetag|bewirt|selbstversorger|geschlossen|offen|opening|"
                   r"jänner|januar|februar|märz|april|mai|juni|juli|august|september|oktober|november|dezember|\d{1,2}\.\s?\d{1,2}\.)", re.I)
R_UNTER_GUT = re.compile(r"(öffnungszeit|oeffnungszeit|offnungszeit|opening|geöffnet|saison|bewirtung|bewirtschaftung|speisekarte|"
                         r"kulinarik|küche|kueche|essen|gastronomie|einkehr|die hütte|die alm|unsere hütte|über uns|ueber-uns|info)", re.I)
R_UNTER_NEIN = re.compile(r"(kletterhalle|impressum|datenschutz|agb|login|shop|jobs|presse|newsletter|galerie|bilder|video|webcam|"
                          r"anfahrt|gutschein|kontaktformular|wp-content|\.(jpe?g|png|gif|zip|docx?)$)", re.I)

R_PDF_GUT = re.compile(r"(öffnungszeit|oeffnungszeit|speisekarte|speisen|getränkekarte|karte|menü|menu|flyer|prospekt|info|saison|preise|hütte|huette|alm)", re.I)
HOLER = None   # im Browser-Durchgang: Funktion url -> (html, fehler) statt sammler.holen

def _holen(u):
    return (HOLER or sammler.holen)(u)

def _pdf_zeilen(u):
    t, err = sammler.holen_pdf(u)
    if err or not t: return []
    return [re.sub(r"\s+", " ", z).strip() for z in t.split("\n") if z.strip()]

def seite_lesen(w, name=""):
    """Startseite plus bis zu 3 passende Unterseiten derselben Seite (Öffnungszeiten, Bewirtung …, bei Seiten mit mehreren
    Hütten bevorzugt die Unterseiten dieser Hütte) und bis zu 2 PDFs (Öffnungszeiten, Speisekarte, Flyer); nur Haupttext
    ohne Menü/Fußzeile. Gibt (zeilen, links, fehler)."""
    raw, err = _holen(w)
    if err and "PDF" in err:   # Quelle ist selbst eine PDF
        z = _pdf_zeilen(w)
        return (z, [], None) if z else (None, [], "PDF ohne Text")
    if err: return None, [], err
    zeilen, links = sammler.text_haupt(raw)
    k = kern(name)
    host = urllib.parse.urlparse(w).netloc.removeprefix("www.")
    kand, pdfs = {}, {}
    for h, txt, _menue in links:
        u = urllib.parse.urljoin(w, h).split("#")[0]
        if u == w: continue
        pfad = urllib.parse.unquote(urllib.parse.urlparse(u).path).lower()
        such = pfad + " " + txt.lower()
        if pfad.endswith(".pdf"):   # PDFs auch von anderen Adressen (z. B. Speisekarte beim Tourismusverband), aber nur passende
            if R_PDF_GUT.search(such) and not re.search(r"(agb|datenschutz|impressum|anmeldung|formular|stellen|job)", such):
                pdfs[u] = (3 if re.search(r"öffnungszeit|oeffnungszeit|saison", such) else 1) + (2 if k and any(x in _n(such).replace("-", "") for x in k) else 0)
            continue
        if urllib.parse.urlparse(u).netloc.removeprefix("www.") != host: continue
        if R_UNTER_NEIN.search(such): continue
        p = 4 if R_UNTER_GUT.search(such) else 0
        if k and any(x in _n(such).replace("-", "") for x in k): p += 3
        if p: kand[u] = max(kand.get(u, 0), p)
    if k and any(p >= 3 and any(x in _n(u).replace("-", "") for x in k) for u, p in kand.items()):
        kand = {u: p for u, p in kand.items() if any(x in _n(u).replace("-", "") for x in k)}   # Seite mit mehreren Hütten
    for u, _ in sorted(kand.items(), key=lambda x: -x[1])[:3]:
        r2, _ = _holen(u)
        if r2:
            z2, l2 = sammler.text_haupt(r2); zeilen += z2; links += l2
    speise = [u for u in pdfs if re.search(r"(speise|getränke|karte|menü|menu|kulinarik)", urllib.parse.unquote(u).lower())
              and not re.search(r"öffnungszeit|oeffnungszeit|saison", urllib.parse.unquote(u).lower())]
    if speise: zeilen.append("Speisekarte (PDF)")   # Speisekarte belegt Essen; Preise darin würden als Uhrzeiten stören
    for u, _ in sorted(((u, p) for u, p in pdfs.items() if u not in speise), key=lambda x: -x[1])[:2]:
        zeilen += _pdf_zeilen(u)
    return zeilen, links, None

def kombinieren(ergebnisse):
    """mehrere Quellen zusammenführen: Widerspruch -> Prüfliste, Übereinstimmung von 2+ Quellen -> bestätigt"""
    v = {e["verpflegung"] for _, e in ergebnisse if e.get("verpflegung") not in (None, "unbekannt")}
    if len(v) > 1: return None, "Quellen widersprechen sich bei der Verpflegung", 0
    saisons, ruhe, zeiten, belege, quellen = {}, None, None, {}, []
    bestaetigt = 0; bestaetigt_felder = set()
    for q, e in ergebnisse:
        quellen.append(q)
        o = e.get("oeffnung") or {}
        for s_ in o.get("saisons") or []:
            k = (s_["von"], s_["bis"])
            if s_["art"] in saisons and saisons[s_["art"]][0] != k: return None, f"Quellen widersprechen sich bei der {s_['art'].title()}saison", 0
            if s_["art"] in saisons: bestaetigt_felder.add(s_["art"])
            saisons[s_["art"]] = (k, s_, o.get("jahr"))
        if o.get("ruhetage"):
            if ruhe is not None and ruhe != o["ruhetage"]: return None, "Quellen widersprechen sich bei den Ruhetagen", 0
            if ruhe is not None: bestaetigt_felder.add("ruhe")
            ruhe = o["ruhetage"]
        zeiten = zeiten or o.get("zeiten")
        if e.get("belege"): belege[q["url"]] = e["belege"]
    vv = [e["verpflegung"] for _, e in ergebnisse if e.get("verpflegung") not in (None, "unbekannt")]
    if len(vv) >= 2: bestaetigt_felder.add("verpflegung")
    f = {"verpflegung": v.pop() if v else "unbekannt"}
    o = {}
    if saisons:
        o["saisons"] = [x[1] for x in saisons.values()]
        jahre = {x[2] for x in saisons.values() if x[2]}
        if len(jahre) == 1: o["jahr"] = jahre.pop()
        namen = {"sommer": "saison_sommer", "winter": "saison_winter"}
        for art_, (k, s_, j) in saisons.items():
            jj = "" if s_.get("ohne_jahr") else str(o.get("jahr") or "")
            f[namen[art_]] = f"{'ca. ' if s_.get('ungefaehr') else ''}{k[0][3:]}.{k[0][:2]}.–{k[1][3:]}.{k[1][:2]}.{jj}"
    if ruhe:
        o["ruhetage"] = ruhe
        f["ruhetage"] = ", ".join(n.title() for n, x in WT.items() if x in ruhe)
    if zeiten: o["zeiten"] = zeiten; f["zeiten"] = zeiten
    if o: f["oeffnung"] = o
    f["belege"] = belege or None
    f["quellen"] = quellen
    # „bestätigt“ nur, wenn JEDE eingetragene Angabe von mindestens zwei Quellen gleich genannt wird
    noetig = set(saisons) | ({"ruhe"} if ruhe else set()) | ({"verpflegung"} if f["verpflegung"] != "unbekannt" else set())
    bestaetigt = 1 if noetig and noetig <= bestaetigt_felder else 0
    return f, None, bestaetigt

def sammeln(neu, aus, pruefdir, quellen_datei=None):
    import hashlib, ki_leser
    os.makedirs(pruefdir, exist_ok=True)
    ki = bool(os.environ.get("ANTHROPIC_API_KEY")); max_ki = int(os.environ.get("MAX_KI", "5000"))
    if ki:
        try: ki_leser.anfrage("Geöffnet 1. Juni bis 30. September", "Test")
        except Exception as e:
            print(f"KI-Leser nicht nutzbar ({str(e)[:120]}) – Regel-Lesen wird verwendet"); ki = False
    cache_p = os.path.join(pruefdir, "ki-cache.json")
    cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}
    qu = json.load(open(quellen_datei, encoding="utf-8")) if quellen_datei and os.path.exists(quellen_datei) else {}
    jobs, ohne_quelle = [], []
    for x in json.load(open(neu, encoding="utf-8")):
        if x["r"] in FERTIG: continue
        qs = qu.get(f"{x['t']}/{x['id']}") or ([{"url": x["w"], "art": "webseite", "name": "Eigene Webseite"}] if (x.get("w") or "").startswith("http") else [])
        if qs: jobs.append((x, qs))
        else: ohne_quelle.append(x)   # früher zugeordnete Quelle gilt nicht mehr (z. B. falsche Namenszuordnung)
    print(f"{len(jobs)} Hütten mit mindestens einer Quelle (ohne {', '.join(FERTIG)}), "
          f"{sum(1 for _, q in jobs if len(q) > 1)} mit mehreren, KI-Leser: {'an' if ki else 'aus'}")
    stat = defaultdict(int); tok = {"in": 0, "out": 0}; neu_cache = {}
    def eine_quelle(x, q, gast):
        if q["art"] == "webseite": zeilen, links, err = seite_lesen(q["url"], x["n"])
        else:
            raw, err = sammler.holen(q["url"]); zeilen, links = sammler.text_haupt(raw) if raw else (None, [])
        if err or not zeilen: return None, "nicht lesbar", ""
        if not nennt_huette(x["n"], zeilen, q["url"]):   # Seite handelt nicht von dieser Hütte (z. B. Inhalt per Skript nachgeladen, Domain verkauft)
            stat["name_fehlt"] += 1; return None, "Seite nennt die Hütte nicht", ""
        voll = "\n".join(zeilen); txt = sammler.auszug(zeilen, 2500)
        if not ki:
            f, grund = auswerten(sammler.auszug(zeilen, 1500), voll[:200000], gast, x["n"], links)
            stat["regel"] += 1
            return f, grund, txt
        if not R_REL.search(txt): return None, "keine Angaben", txt
        h = hashlib.sha1((txt + ki_leser.MODELL).encode()).hexdigest()[:16]
        alt = cache.get(q["url"])
        if alt and alt.get("h") == h: ant = alt.get("a"); stat["cache"] += 1
        else:
            if stat["_anfragen"] >= max_ki: return None, "KI-Obergrenze erreicht", txt
            stat["_anfragen"] += 1
            ant, usage = ki_leser.anfrage(txt, x["n"])
            tok["in"] += usage.get("input_tokens", 0); tok["out"] += usage.get("output_tokens", 0)
            if ant is not None: neu_cache[q["url"]] = {"h": h, "a": ant}
        f, grund = ki_leser.pruefen(ant, txt, gast)
        return f, grund, txt
    ende = time.time() + 60 * float(os.environ.get("SAMMEL_MINUTEN", "200"))   # danach Rest überspringen, damit der Lauf nicht abbricht
    fertig_n = [0]
    def huette(job):
        x, qs = job
        fertig_n[0] += 1
        if fertig_n[0] % 250 == 0: print(f"  … {fertig_n[0]}/{len(jobs)} Hütten, KI-Anfragen {stat['_anfragen']}", flush=True)
        if time.time() > ende: stat["zeit_ueberschritten"] += 1; return x, None, None, "", 0
        gast = x["a"] in ("berggasthaus", "alpgasthaus") or x["tag"] in ("restaurant", "cafe", "biergarten", "pub")
        ergebnisse, gruende, texte = [], [], []
        for q in qs[:4]:
            f, grund, txt = eine_quelle(x, q, gast)
            if f: ergebnisse.append((q, f))
            if grund and grund not in ("nicht lesbar", "keine Angaben"): gruende.append(f"{q['name']}: {grund}"); texte.append(f"[{q['url']}] {txt[:1200]}")
        if not ergebnisse: return x, None, "; ".join(gruende), "\n".join(texte), 0
        f, grund, best = kombinieren(ergebnisse)
        if grund: gruende.append(grund)
        return x, f, "; ".join(gruende), "\n".join(texte), best
    pruef, ok = defaultdict(list), []
    qs_von = {(x["t"], x["id"]): qs for x, qs in jobs}
    with cf.ThreadPoolExecutor(6 if ki else 16) as ex:
        for x, f, grund, txt, best in ex.map(huette, jobs):
            if f:
                # Marcel: eine seriöse Quelle (eigene Hüttenseite, Alpenverein-Verzeichnis) genügt; Widersprüche gingen schon auf die Prüfliste
                hart = bool([s_ for s_ in (f.get("oeffnung") or {}).get("saisons") or [] if not s_.get("ohne_jahr")] or (f.get("oeffnung") or {}).get("ruhetage"))
                sicher = "bestaetigt" if (best >= 1 or hart) else ("eine_quelle" if ki else "unklar")
                ok.append({"t": x["t"], "id": x["id"], "url": f["quellen"][0]["url"], "art_lesen": "ki" if ki else "regel", "sicherheit": sicher, **f})
                stat["bestaetigt" if sicher == "bestaetigt" else "eine_quelle"] += 1
                if grund:   # Teil unklar: eingetragen wird nur der eindeutige Teil, der Rest kommt auf die Prüfliste
                    pruef[x["l"] + "-" + x["r"]].append(f"## {x['n']} ({x['t']}/{x['id']}) – teilweise eingetragen\n{grund}\n{txt}\n"); stat["teilweise"] += 1
            elif grund:
                pruef[x["l"] + "-" + x["r"]].append(f"## {x['n']} ({x['t']}/{x['id']})\n{grund}\n{txt}\n")
                # früher maschinell gelesene Werte gelten nicht mehr, wenn die Seite jetzt unklar ist (db-auto setzt sie zurück)
                ok.append({"t": x["t"], "id": x["id"], "zurueck": grund[:200]}); stat["zurueck"] += 1
            elif grund == "":
                # nichts gelesen (Seite nicht erreichbar/ohne Angaben): alte Werte bleiben nur, wenn sie von einer heutigen Quelle stammen
                ok.append({"t": x["t"], "id": x["id"], "quellen_jetzt": [q["url"] for q in qs_von[(x["t"], x["id"])]]})
    for x in ohne_quelle: ok.append({"t": x["t"], "id": x["id"], "zurueck": "keine Quelle mehr zugeordnet"})
    if ki: json.dump({**cache, **neu_cache}, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(ok, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    for f_ in os.listdir(pruefdir):
        if f_.startswith("pruef-"): os.remove(os.path.join(pruefdir, f_))
    for k, v in pruef.items():
        with open(os.path.join(pruefdir, f"pruef-{re.sub(r'[^A-Za-z0-9ÄÖÜäöüß-]+', '_', k)}.md"), "w", encoding="utf-8") as fo:
            fo.write(f"# Prüfliste {k} ({HEUTE})\n\nNicht eindeutig lesbar. Nur Fakten übernehmen, Regeln aus Prompt 1.\n\n" + "\n".join(v))
    print(f"gelesen: {sum(1 for e in ok if not e.get('zurueck') and 'quellen_jetzt' not in e)}, Prüfliste: {sum(len(v) for v in pruef.values())}, Abläufe: {dict(stat)}, KI-Token ein/aus: {tok['in']}/{tok['out']}")

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
    """gelesene Werte eintragen – nur bei Einträgen, die noch niemand recherchiert hat (offen oder schon auto)"""
    n = nz = 0
    auto_ids, off = set(), 0   # alle maschinell gelesenen Einträge einmal holen (spart eine Anfrage je Hütte beim Zurücksetzen)
    while True:
        part = json.loads(sb("GET", f"gb_huetten?select=osm_typ,osm_id&daten->>recherche=eq.auto&order=id&limit=1000&offset={off}"))
        auto_ids |= {(p["osm_typ"], p["osm_id"]) for p in part}
        if len(part) < 1000: break
        off += 1000
    for e in json.load(open(auto, encoding="utf-8")):
        if (e.get("zurueck") or "quellen_jetzt" in e) and (e["t"], e["id"]) not in auto_ids: continue
        if "quellen_jetzt" in e:
            cur = json.loads(sb("GET", f"gb_huetten?select=quelle_url&osm_typ=eq.{e['t']}&osm_id=eq.{e['id']}&daten->>recherche=eq.auto"))
            if not cur or cur[0].get("quelle_url") in e["quellen_jetzt"]: continue
            e = {**e, "zurueck": "frühere Quelle gilt nicht mehr (z. B. falsche Namenszuordnung)"}
        flt = f"osm_typ=eq.{e['t']}&osm_id=eq.{e['id']}&daten->>recherche=in.(offen,auto)"
        try:
            if e.get("zurueck"):
                # nur bisher maschinell gelesene Einträge (auto), nie von Claude recherchierte
                f2 = f"osm_typ=eq.{e['t']}&osm_id=eq.{e['id']}&daten->>recherche=eq.auto"
                cur = json.loads(sb("GET", f"gb_huetten?select=daten&{f2}"))
                if not cur: continue
                body = {k: None for k in ("oeffnung", "saison_sommer", "saison_winter", "ruhetage", "zeiten")}
                body.update({"verpflegung": "unbekannt", "sicherheit": "unklar", "pruef_datum": HEUTE,
                             "daten": {**cur[0]["daten"], "recherche": "offen", "zurueckgesetzt": HEUTE, "zurueck_grund": e["zurueck"]}})
                sb("PATCH", f"gb_huetten?{f2}", body, "return=minimal"); nz += 1
                continue
            cur = json.loads(sb("GET", f"gb_huetten?select=daten&{flt}"))
            if not cur: continue
            ki = e.get("art_lesen") == "ki"
            body = {k: e.get(k) for k in ("oeffnung", "saison_sommer", "saison_winter", "ruhetage", "zeiten")}
            qs = e.get("quellen") or [{"url": e["url"], "art": "webseite", "name": "Eigene Webseite"}]
            namen = ", ".join(dict.fromkeys(q["name"] for q in qs))
            web = next((q["url"] for q in qs if q["art"] == "webseite"), None)
            body.update({"verpflegung": e.get("verpflegung", "unbekannt"), "webseite": web,
                         "quelle": f"{namen} ({'automatisch gelesen, Zitat geprüft' if ki else 'maschinell gelesen'})",
                         "quelle_url": e["url"], "quelle_datum": HEUTE, "pruef_datum": HEUTE,
                         "sicherheit": e.get("sicherheit") or ("eine_quelle" if ki else "unklar"),
                         "daten": {**cur[0]["daten"], "recherche": "auto", "auto_datum": HEUTE, "auto_art": e.get("art_lesen", "regel"),
                                   "belege": e.get("belege"), "quellen_weitere": [q["url"] for q in qs[1:]] or None}})
            sb("PATCH", f"gb_huetten?{flt}", body, "return=minimal"); n += 1
        except Exception as ex:
            print("Fehler", e["id"], str(ex)[:80])
    print(f"{n} Einträge befüllt, {nz} frühere maschinelle Werte zurückgesetzt (Seite jetzt unklar)")

if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c == "regionen": regionen(sys.argv[2], sys.argv[3])
    elif c == "inventar": inventar(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:])
    elif c == "zusammen": zusammen(sys.argv[2], sys.argv[3])
    elif c == "sammeln": sammeln(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else None)
    elif c == "db-neu": db_neu(sys.argv[2])
    elif c == "db-auto": db_auto(sys.argv[2])
    else: print(__doc__); sys.exit(2)
