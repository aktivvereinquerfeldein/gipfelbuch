#!/usr/bin/env python3
"""Gaststätten (Restaurant, Café, Gasthaus/Pub, Biergarten aus den Kartenstücken „rest“): Öffnungszeiten von der
eigenen Webseite lesen – ohne KI, nach festen Regeln, lieber auf die Prüfliste als etwas Falsches.

Anders als bei Hütten geht es um Wochenzeiten (Mo–So mit Uhrzeiten), Ruhetage und Betriebsurlaub.
Quellen je Gaststätte: eigene Webseite aus OpenStreetMap (Startseite + bis zu 3 passende Unterseiten derselben Seite),
darin (1) maschinenlesbare Angaben (schema.org openingHours / openingHoursSpecification) und (2) der Abschnitt
„Öffnungszeiten“ im Text. OSM-Öffnungszeiten zählen nicht als Quelle, nur zum Vergleich.

Regeln (wie bei den Hütten, Prompt 1 / Block A):
- Nur lesen (robots.txt, höchstens 1 Anfrage pro Sekunde und Seite), nur Fakten, keine fremden Texte.
- Prüfliste statt Eintrag bei: verschiedenen Zeiten auf der Seite, Zeiten je nach Saison/Monat, offenen Enden („ab 17 Uhr“),
  nicht lesbaren Tageszeilen, alten Jahreszahlen, „vorübergehend/dauerhaft geschlossen“, Seiten mehrerer Betriebe.
- Küchenzeiten, Frühstück, Mittagstisch, Rezeption, Lieferdienst zählen nicht als Öffnungszeiten.
- Sicherheit: „bestaetigt“, wenn zwei Angaben übereinstimmen (Text + schema.org, oder Webseite + OSM);
  sonst „eine_quelle“ (App: „ohne Gewähr“). Widerspruch Webseite/OSM: Webseite gilt (Betreiber), kommt in den Bericht.
- Nichts überschreiben, was von Hand recherchiert wurde (daten.recherche = 'fertig').

Aufrufe:
  gaststaetten.py inventar <rest-zellen-ordner> <neu.json>       Gaststätten mit Webseite aus den Kartenstücken
  gaststaetten.py sammeln  <neu.json> <auto.json> <bericht-ordner>   Webseiten lesen, Prüflisten schreiben
  gaststaetten.py test     <url> [name]                           eine Webseite lesen und Ergebnis zeigen
  gaststaetten.py db       <neu.json> <auto.json>                 in gb_gaststaetten schreiben (Secret SUPABASE_SERVICE_KEY)
  gaststaetten.py daten    <daten.json>                           Tabelle lesen (öffentlicher Schlüssel)
  gaststaetten.py anreichern <zellen-ordner> <aus-ordner> <daten.json>   Feld „gb“ in die rest-Zellen schreiben
"""
import gzip, json, os, re, sys, time, urllib.parse, urllib.request, concurrent.futures as cf
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sammler

HEUTE = time.strftime("%Y-%m-%d")
JAHR = int(time.strftime("%Y"))
WT = ["mo", "di", "mi", "do", "fr", "sa", "so"]
WT_DE = {"mo": "Mo", "di": "Di", "mi": "Mi", "do": "Do", "fr": "Fr", "sa": "Sa", "so": "So"}
WT_OSM = {"Mo": "mo", "Tu": "di", "We": "mi", "Th": "do", "Fr": "fr", "Sa": "sa", "Su": "so"}

# ---------- Inventar aus den Kartenstücken ----------
FREMD = re.compile(r"(facebook\.|instagram\.|tripadvisor\.|google\.|goo\.gl|yelp\.|thefork\.|lieferando\.|foursquare\.|"
                   r"falstaff\.|gault|wikipedia\.|booking\.com|holidaycheck|linktr\.ee|tiktok\.)", re.I)

class Umriss:
    """Punkt-in-Fläche ohne Zusatzpakete (karte/oesterreich_umriss.json, Grenze aus OSM, auf ca. 30 m vereinfacht)"""
    def __init__(s, path):
        import math
        s.ringe = json.load(open(path)); s.band = defaultdict(list); s.m = math
        for ri, r in enumerate(s.ringe):
            p = r["p"]
            for i in range(len(p) - 1):
                (x1, y1), (x2, y2) = p[i], p[i + 1]
                for b in range(math.floor(min(y1, y2) * 100), math.floor(max(y1, y2) * 100) + 1): s.band[b].append((ri, x1, y1, x2, y2))
    def __contains__(s, lonlat):
        x, y = lonlat; drin = defaultdict(bool)
        for ri, x1, y1, x2, y2 in s.band.get(s.m.floor(y * 100), ()):
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1): drin[ri] = not drin[ri]
        return any(v for ri, v in drin.items() if not s.ringe[ri]["loch"]) and not any(v for ri, v in drin.items() if s.ringe[ri]["loch"])

def inventar(zdir, aus, land=""):
    """land=AT: nur Gaststätten innerhalb Österreichs (Kartenstücke enthalten auch Südtirol und den Randstreifen)"""
    um = Umriss(os.path.join(os.path.dirname(os.path.abspath(__file__)), "oesterreich_umriss.json")) if land == "AT" else None
    rows, seen = [], set()
    for fn in sorted(os.listdir(zdir)):
        if not fn.endswith(".json.gz") and not fn.endswith(".json"): continue
        op = gzip.open if fn.endswith(".gz") else open
        with op(os.path.join(zdir, fn), "rt", encoding="utf-8") as f: j = json.load(f)
        for el in j.get("elements", []):
            t = el.get("tags") or {}
            key = (el["type"], el["id"])
            if key in seen or not t.get("name"): continue
            seen.add(key)
            w = (t.get("website") or t.get("contact:website") or t.get("url") or "").strip().split(";")[0].strip()
            if w and not w.startswith("http"): w = "https://" + w
            c = el.get("center") or el
            if c.get("lat") is None: continue
            if um is not None and (c["lon"], c["lat"]) not in um: continue
            rows.append({"t": el["type"], "id": el["id"], "n": t["name"], "a": t.get("amenity") or "restaurant",
                         "lon": round(c["lon"], 6), "lat": round(c["lat"], 6), "w": w or None,
                         "oh": t.get("opening_hours"), "tel": t.get("phone") or t.get("contact:phone"),
                         "l": land or (t.get("addr:country") or "").upper() or None, "plz": t.get("addr:postcode"),
                         "str": t.get("addr:street"), "ort": t.get("addr:city") or t.get("addr:place"),
                         "mail": (t.get("email") or t.get("contact:email") or "").split(";")[0].strip() or None,
                         "wd": t.get("wikidata"), "marke": t.get("brand")})
    n_w = Counter(r["w"] for r in rows if r["w"])
    for r in rows:
        if r["w"] and FREMD.search(r["w"]): r["skip"] = "keine eigene Webseite (Plattform)"
        elif r["w"] and n_w[r["w"]] > 3: r["skip"] = f"Webseite für {n_w[r['w']]} Lokale (Kette/Sammelseite)"
    json.dump(rows, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    mw = [r for r in rows if r["w"] and not r.get("skip")]
    print(f"{len(rows)} Gaststätten in den Kartenstücken{' (nur Österreich)' if land == 'AT' else ''}, {sum(1 for r in rows if r['w'])} mit Webseite, "
          f"{len(mw)} davon lesbar (ohne Plattformen/Ketten), {sum(1 for r in rows if r['oh'])} mit OSM-Zeiten, "
          f"{sum(1 for r in rows if r['mail'])} mit E-Mail, {sum(1 for r in rows if r['plz'] or r['str'] or r['tel'])} mit Adresse oder Telefon")

# ---------- Wochenzeiten aus Text lesen ----------
TAGNAMEN = {
    "mo": r"montags?|monday|mon|lunedì|lunedi|mo", "di": r"dienstags?|tuesday|tue|tues|martedì|martedi|di",
    "mi": r"mittwochs?|wednesday|wed|mercoledì|mercoledi|mi", "do": r"donnerstags?|thursday|thu|thur|thurs|giovedì|giovedi|do",
    "fr": r"freitags?|friday|fri|venerdì|venerdi|fr", "sa": r"samstags?|sonnabends?|saturday|sat|sabato|sa",
    "so": r"sonntags?|sunday|sun|domenica|so"}
TAG1 = {}
for k, v in TAGNAMEN.items():
    for n in v.split("|"):
        if n.endswith("s?"): TAG1[n[:-2]] = k; TAG1[n[:-1]] = k
        else: TAG1[n] = k
DAY_RE = None
DAY = r"(?:" + "|".join(sorted(TAG1, key=len, reverse=True)) + r")\.?(?![a-zäöüß])"
TAEGLICH = r"(?:täglich|taeglich|jeden tag|daily|tutti i giorni|7 tage die woche|montag bis sonntag)"
DAYSPEC = rf"(?:{TAEGLICH}|{DAY}(?:\s*\|?\s*(?:-|,|/|\+|&|und|and|e)\s*\|?\s*{DAY})*)"
T2 = r"\d{1,2}[:.]\d{2}"
T1 = r"\d{1,2}(?:[:.]\d{2})?"
RANGE = rf"(?:{T2}\s*-\s*{T2}(?:\s*(?:uhr|h)\b)?|{T1}\s*-\s*{T1}\s*(?:uhr|h)\b|{T2}\s*-\s*24(?:\s*uhr)?)"
TIMES = rf"{RANGE}(?:\s*(?:,|/|\+|&|und|and|e|sowie|\|)\s*\|?\s*(?:von\s*)?{RANGE})*"
ZU = r"(?:ruhetage?|geschlossen|closed|chiuso|giorno di riposo|zu)(?![a-zäöüß])"
R_REGEL = re.compile(rf"(?<![a-zäöüß])({DAYSPEC})\s*(?:[:|,]\s*)*(?:von\s*|ab\s*(?=\d{{1,2}}[:.]\d{{2}}\s*-))?(?:({TIMES})|({ZU}))", re.I)
R_ZU_VOR = re.compile(rf"(?<![a-zäöüß])(?:ruhetage?|geschlossen|closed)\s*:?\s*\|?\s*({DAYSPEC})(?![\s|:]*\d)", re.I)
R_TAG_STRENG = re.compile(r"(?<![a-zäöüß])(montags?|dienstags?|mittwochs?|donnerstags?|freitags?|samstags?|sonntags?|mo|di|mi|do|fr|sa|so)[.:]?\s*[-–,/|:]?\s*\d", re.I)
R_TAG_LANG = re.compile(r"(?<![a-zäöüß])(montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?(?![a-zäöüß])", re.I)
R_UEBERSCHRIFT = re.compile(r"(öffnungszeit|oeffnungszeit|offnungszeit|opening hours|opening times|orari|unsere zeiten|wann wir)", re.I)
R_SONDER = re.compile(r"(weihnacht|silvester|neujahr|feiertag|ostern|pfingst|besondere|sonderöffnung|sonder-|abweichende|festtage|xmas|christmas|advent)", re.I)
R_UEBERSCHRIFT_KURZ = re.compile(r"^\W*(geöffnet|wir haben geöffnet|wir sind für (euch|sie) da|zeiten)\W*$", re.I)
R_KUECHE = re.compile(r"(küche|kueche|kitchen|cucina|frühstück|fruehstueck|breakfast|colazione|mittagstisch|mittagsmenü|mittagsmenue|menü|brunch|rezeption|"
                      r"reception|check-in|lieferservice|lieferzeit|liefer|abholung|take[ -]?away|büro|buero|shop|"
                      r"laden|metzgerei|bäckerei|hofladen|happy hour|sauna|wellness|hotel|zimmer|bürozeit|sprechzeit|seilbahn|bahn|lift|museum|markt)", re.I)
R_LOKAL = re.compile(r"(öffnungszeit|oeffnungszeit|restaurant|gaststätte|gasthaus|gasthof|lokal|café|cafe|bar\b|wirtshaus|geöffnet|opening)", re.I)
R_SAISON = re.compile(r"(sommer|winter|saison|nebensaison|hauptsaison|vorsaison|ferien|schulferien|monat|januar|jänner|februar|märz|april|"
                      r"mai\b|juni|juli|august|september|oktober|november|dezember|\bab \d{1,2}\.\s?\d{1,2}\.|\bvom \d{1,2}\.|\bbis \d{1,2}\.\s?\d{1,2}\.|"
                      r"summer|season|estate|inverno|stagione|wetter|witterung|bei schönwetter|schönwetter)", re.I)
R_OFFEN_ENDE = re.compile(rf"(?<![a-zäöüß])({DAY}|{TAEGLICH})[^|]{{0,12}}(?:ab|from|dalle)\s*{T1}\s*(?:uhr|h)?(?!\s*-\s*\d)", re.I)
R_URLAUB = re.compile(r"(betriebsurlaub|betriebsferien|urlaub|winterpause|sommerpause|ruhepause|geschlossen vom|geschlossen von|ferie|chiuso dal)", re.I)
R_DATUM_BEREICH = re.compile(r"(\d{1,2})\.\s?(\d{1,2})\.(\d{2,4})?\s*(?:-|bis|–)\s*(\d{1,2})\.\s?(\d{1,2})\.(\d{2,4})")
R_FEIERTAG = re.compile(r"(feiertag|feiertage|festtag|holiday|festivi|heiligabend|silvester|weihnacht|ostern|pfingst)", re.I)
R_BETRIEB_ZU = re.compile(r"(vorübergehend geschlossen|vorruebergehend geschlossen|dauerhaft geschlossen|bis auf weiteres geschlossen|"
                          r"betrieb eingestellt|für immer geschlossen|endgültig geschlossen|haben geschlossen|wir schließen|wir schliessen|"
                          r"pächter gesucht|pächterin gesucht|nachfolger gesucht|nachpächter|closed permanently|permanently closed|temporarily closed|"
                          r"chiuso definitivamente|neueröffnung|neueroeffnung|eröffnung im|demnächst|in kürze|coming soon)", re.I)
R_JAHR = re.compile(r"(?<![©\d])(20[12]\d)(?!\d)")

def _norm(s):
    s = (s or "").replace(" ", " ").replace("–", "-").replace("—", "-").replace("−", "-").replace("‐", "-")
    s = re.sub(r"(?<=\d)\s*(?:bis|to|alle)\s*(?=\d)", " - ", s, flags=re.I)
    s = re.sub(r"(?<=[a-zäöüß.])\s+bis\s+(?=[a-zäöüß])", " - ", s, flags=re.I)   # „Montag bis Freitag“
    s = re.sub(r"(?<=\d)\s*uhr\b", " uhr", s, flags=re.I)
    s = re.sub(r"\bsonn-\s*(?:und|&|\+|,|/)\s*", "sonntag & ", s, flags=re.I)
    return s

def _tage(spec):
    s = spec.lower().strip()
    if re.fullmatch(TAEGLICH, s): return list(WT)
    teile = re.findall(rf"({DAY})|(-)", s)
    out, vor, bereich = [], None, False
    for d, strich in teile:
        if strich: bereich = True; continue
        k = TAG1[d.rstrip(".").lower()]
        if bereich and vor is not None:
            i = WT.index(vor)
            while WT[i] != k:
                i = (i + 1) % 7
                if WT[i] not in out: out.append(WT[i])
        elif k not in out: out.append(k)
        vor, bereich = k, False
    return out

def _zeit(x, ende=False):
    t = re.split(r"[:.]", x)
    h, m = int(t[0]), int(t[1]) if len(t) > 1 and t[1] else 0
    if h > 24 or m > 59 or (h == 24 and m): return None
    if h == 24 and not ende: return None
    return f"{h:02d}:{m:02d}"

def _bereiche(times):
    out = []
    for a, b in re.findall(rf"({T1})\s*-\s*({T1})", times):
        va, vb = _zeit(a), _zeit(b, True)
        if not va or not vb or va == vb: return None
        out.append([va, vb])
    return out or None

def abschnitte(zeilen):
    """Abschnitte „Öffnungszeiten“: Überschriftzeile plus folgende Zeilen mit Tagen/Uhrzeiten (Tabellenzellen getrennt durch |)"""
    out = []
    i = 0
    while i < len(zeilen):
        z = zeilen[i]
        if (len(z) < 220 and R_UEBERSCHRIFT.search(z)) or R_UEBERSCHRIFT_KURZ.search(z):
            teil, leer, j = [z], 0, i + 1
            while j < len(zeilen) and j < i + 30 and leer < 3:
                y = zeilen[j]
                if len(y) > 200: break
                if R_UEBERSCHRIFT.search(y) and len(teil) > 3: break
                hat = bool(re.search(DAY, y, re.I) or re.search(r"\d{1,2}[:.]\d{2}|\buhr\b|ruhetag|geschlossen|closed", y, re.I))
                leer = 0 if hat else leer + 1
                teil.append(y); j += 1
            while teil and not re.search(r"\d|ruhetag|geschlossen|closed", teil[-1], re.I): teil.pop()
            sonder = R_SONDER.search(" ".join(teil[:2])) and re.search(r"\d{1,2}\.\s?(\d{1,2}\.|[a-zä]{3})", " ".join(teil))
            if not teil or sonder or R_KUECHE.search(teil[0]): pass   # auch reine Küchen-/Liefer-Abschnitte   # Sonderöffnungszeiten (Weihnachten …) zählen nicht als Wochenzeiten
            elif len(teil) > 1 or re.search(r"\d", z): out.append(_norm(" | ".join(teil)))
            i = j
        else: i += 1
    return out

def wochenzeiten(abschnitt):
    """liest einen Abschnitt; gibt ({tag: [[von,bis],..]}, ruhetage, None) oder (None, None, grund)"""
    low = abschnitt.lower()
    if R_BETRIEB_ZU.search(low): return None, None, f"Betrieb evtl. geschlossen („{R_BETRIEB_ZU.search(low).group(0)}“)"
    jahre = [int(y) for y in R_JAHR.findall(low)]
    if jahre and max(jahre) < JAHR: return None, None, f"Angabe evtl. veraltet (Jahr {max(jahre)})"
    if R_OFFEN_ENDE.search(abschnitt): return None, None, f"offenes Ende („{R_OFFEN_ENDE.search(abschnitt).group(0)[:50]}“)"
    # „So & Feiertag 10–14“: Feiertags-Zusatz weg, die Regel gilt für den Tag (Hinweis auf Feiertage kommt extra)
    abschnitt = re.sub(r"\s*(?:,|&|und|\+|/)\s*(?:an\s+)?(?:feiertage?n?|feiertags|ph)(?![a-zäöüß])", " ", abschnitt, flags=re.I)
    # Klammerzusätze wie „(warme Küche bis 21.30)“ weg; „(Montag Ruhetag)“ bleibt
    abschnitt = re.sub(r"\([^()]{0,120}\)", lambda k: " " if (R_KUECHE.search(k.group(0)) or not re.search(DAY + "|" + ZU, k.group(0), re.I)) else k.group(0)[1:-1], abschnitt)
    oe = re.search(r"(?<![-\d:.])\s(?:ab|from)\s+\d{1,2}(?:[:.]\d{2})?\s*uhr(?!\s*-)", abschnitt, re.I)
    if oe: return None, None, f"offenes Ende („{abschnitt[max(0, oe.start() - 25):oe.end()].strip()}“)"
    # Küchen-/Liefer-/Frühstücksblöcke: beginnen beim Stichwort, gelten für folgende Regeln, solange diese nur schon
    # genannte Tage wiederholen (typisch „Warme Küche: Mo–Sa …“); eine Regel mit neuen Tagen, ein Ruhetag oder ein
    # Lokal-Stichwort beendet den Block (z. B. „Küche: 17–21 | Freitag + Samstag 10–22“)
    spannen, regeln, letzte, kueche, k_start, bekannt = [], [], 0, False, 0, set()
    for m in R_REGEL.finditer(abschnitt):
        luecke = abschnitt[letzte:m.start()]
        k_ = None
        for k_ in R_KUECHE.finditer(luecke): pass
        l_ = None
        for l_ in R_LOKAL.finditer(luecke): pass
        frisch = False
        if k_ and not (l_ and l_.start() > k_.end() + 3):
            if not kueche: k_start = letzte + k_.start()
            kueche = True; frisch = not re.search(r"\d", luecke[k_.end():])
        elif l_ and kueche: spannen.append((k_start, letzte + l_.start())); kueche = False
        letzte = m.end()
        tage = _tage(m.group(1))
        if not tage: continue
        if kueche and (m.group(3) or (not frisch and not set(tage) <= bekannt)):
            spannen.append((k_start, m.start())); kueche = False
        if not kueche and R_KUECHE.search(abschnitt[m.end():m.end() + 22].split("|")[0]):
            return None, None, f"Zeit gilt evtl. nur für Küche/Frühstück o. Ä. („{abschnitt[m.start():m.end() + 22][:70]}“)"
        if kueche: continue
        bekannt |= set(tage)
        if m.group(2):
            b = _bereiche(m.group(2))
            if not b: return None, None, f"Uhrzeit nicht lesbar („{m.group(0)[:60]}“)"
            regeln.append((tage, b, m.start(), m.end()))
        else: regeln.append((tage, "zu", m.start(), m.end()))
    if kueche: spannen.append((k_start, len(abschnitt)))
    else:
        k_ = None
        for k_ in R_KUECHE.finditer(abschnitt, letzte): pass
        if k_: spannen.append((k_.start(), len(abschnitt)))   # „Küche: 17–21“ nach der letzten Regel
    in_kueche = lambda i: any(a <= i < e for a, e in spannen)
    for m in R_ZU_VOR.finditer(abschnitt):   # „Ruhetag: Montag“
        if R_KUECHE.search(abschnitt[max(0, m.start() - 40):m.start()]): continue
        if not any(a <= m.start() < e for _, _, a, e in regeln): regeln.append((_tage(m.group(1)), "zu", m.start(), m.end()))
    if not regeln: return None, None, None
    rest = abschnitt
    for _, _, a, e in sorted(regeln, key=lambda x: -x[2]): rest = rest[:a] + " " * (e - a) + rest[e:]
    if R_SAISON.search(re.sub(r"\(.*?\)", "", low)) and not R_URLAUB.search(low):
        return None, None, f"Zeiten je nach Saison/Datum („{R_SAISON.search(low).group(0)}“)"
    if R_SAISON.search(low) and R_URLAUB.search(low):
        # Saisonwort nur in der Urlaubszeile erlaubt
        ohne = " ".join(x for x in low.split("|") if not R_URLAUB.search(x))
        if R_SAISON.search(ohne): return None, None, f"Zeiten je nach Saison/Datum („{R_SAISON.search(ohne).group(0)}“)"
    # übrig gebliebene Tage oder Uhrzeiten (z. B. Tabelle mit Tagen in einer Zeile, Zeiten in der nächsten) -> nicht eindeutig
    for m in list(R_TAG_LANG.finditer(rest)) + list(re.finditer(RANGE, rest, re.I)):
        if in_kueche(m.start()): continue
        nur_feiertag = R_FEIERTAG.search(rest[max(0, m.start() - 25):m.end() + 25]) and not re.search(DAY, rest[max(0, m.start() - 45):m.start()], re.I)
        if not R_KUECHE.search(rest[max(0, m.start() - 16):m.start()]) and not nur_feiertag:
            return None, None, f"Tag oder Uhrzeit ohne Zuordnung („{rest[max(0, m.start() - 15):m.end() + 15].strip()}“)"
    for m in R_TAG_STRENG.finditer(rest):
        if in_kueche(m.start()): continue
        if not R_KUECHE.search(rest[max(0, m.start() - 50):m.start()]) and not R_FEIERTAG.search(rest[max(0, m.start() - 15):m.end() + 15]):
            return None, None, f"Tageszeile nicht eindeutig („{rest[m.start():m.start() + 40].strip()}“)"
    woche, zu = {}, set()
    breit = {}
    for tage, b, _, _ in regeln:
        for t in tage:
            if b == "zu":
                if t in woche and not breit.get(t): return None, None, f"{WT_DE[t]} offen und geschlossen genannt"
                woche.pop(t, None); zu.add(t)
            else:
                if t in zu:
                    if len(tage) >= 5: continue   # „Di Ruhetag … täglich 11–22“: Ruhetag hat Vorrang vor der Sammelangabe
                    return None, None, f"{WT_DE[t]} offen und geschlossen genannt"
                if t in woche and woche[t] != b:
                    if breit.get(t) and len(tage) < 5: pass   # Einzeltag ersetzt „täglich“
                    else: return None, None, f"verschiedene Zeiten für {WT_DE[t]}"
                woche[t] = b; breit[t] = len(tage) >= 5
    if not woche: return None, None, "nur Ruhetage, keine Zeiten" if zu else None
    for t, b in list(woche.items()):
        b = sorted({tuple(x) for x in b})
        for (a1, e1), (a2, e2) in zip(b, b[1:]):
            if a2 < e1 and e1 > a1: return None, None, f"überlappende Zeiten am {WT_DE[t]} (Tabelle mit mehreren Spalten?)"
        woche[t] = [list(x) for x in b]
    if len(woche) < 2 and len(woche) + len(zu) < 3: return None, None, "zu wenige Tage genannt"
    return woche, sorted(zu, key=WT.index), None

def urlaub(text):
    """Betriebsurlaub mit vollständigem Datum (Jahr), nur künftige/laufende"""
    out = []
    for m in R_URLAUB.finditer(text):
        s = text[m.start():m.start() + 120]
        d = R_DATUM_BEREICH.search(s)
        if not d: continue
        d1, m1, y1, d2, m2, y2 = d.groups()
        y2 = int(y2) + (2000 if len(y2) == 2 else 0); y1 = (int(y1) + (2000 if len(y1) == 2 else 0)) if y1 else (y2 if int(m1) <= int(m2) else y2 - 1)
        try:
            von, bis = f"{y1:04d}-{int(m1):02d}-{int(d1):02d}", f"{y2:04d}-{int(m2):02d}-{int(d2):02d}"
            time.strptime(von, "%Y-%m-%d"); time.strptime(bis, "%Y-%m-%d")
        except ValueError: continue
        if bis >= HEUTE and von <= bis and {"von": von, "bis": bis} not in out: out.append({"von": von, "bis": bis})
    return out

# ---------- schema.org ----------
SCHEMA_TAG = {"monday": "mo", "tuesday": "di", "wednesday": "mi", "thursday": "do", "friday": "fr", "saturday": "sa", "sunday": "so"}
R_LD = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)

def _objekte(x):
    if isinstance(x, list):
        for y in x: yield from _objekte(y)
    elif isinstance(x, dict):
        yield x
        for k in ("@graph", "mainEntity", "location", "department", "containsPlace"):
            if k in x: yield from _objekte(x[k])

def osm_lesen(s):
    """einfache OSM-/schema.org-Zeiten „Mo-Fr 11:00-22:00; Sa 10:00-23:00; Tu off“ -> woche, zu; sonst None"""
    if not s: return None
    woche, zu = {}, set()
    for teil in re.split(r"\s*;\s*", s.strip()):
        if not teil: continue
        if re.search(r"PH|SH|week|[A-Z][a-z]{2}\b(?<!Mo|Tu|We|Th|Fr|Sa|Su)|\d{4}|\"|\|\|", teil):
            if re.match(r"^PH\b", teil): continue
            return None
        m = re.fullmatch(r"((?:Mo|Tu|We|Th|Fr|Sa|Su)(?:\s*[-,]\s*(?:Mo|Tu|We|Th|Fr|Sa|Su))*)\s+(off|closed|\d{1,2}:\d{2}-\d{1,2}:\d{2}(?:,\s*\d{1,2}:\d{2}-\d{1,2}:\d{2})*)", teil.strip())
        if not m: return None
        tage = []
        for a, b in re.findall(r"(Mo|Tu|We|Th|Fr|Sa|Su)(?:\s*-\s*(Mo|Tu|We|Th|Fr|Sa|Su))?", m.group(1)):
            i, j = WT.index(WT_OSM[a]), WT.index(WT_OSM[b or a])
            while True:
                tage.append(WT[i])
                if i == j: break
                i = (i + 1) % 7
        for t in tage:
            if m.group(2) in ("off", "closed"): woche.pop(t, None); zu.add(t)
            else:
                woche[t] = [[_zeit(a), _zeit(b, True)] for a, b in re.findall(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", m.group(2))]
                zu.discard(t)
    if any(None in x for v in woche.values() for x in v): return None
    return (woche, sorted(zu, key=WT.index)) if woche else None

def schema_zeiten(raw):
    """gibt (woche, zu, grund); grund bei mehreren Betrieben oder saisonalen Angaben"""
    funde = []
    for blk in R_LD.findall(raw or ""):
        try: j = json.loads(blk.strip())
        except Exception: continue
        for o in _objekte(j):
            spec, txt = o.get("openingHoursSpecification"), o.get("openingHours")
            if not spec and not txt: continue
            woche, zu = {}, set()
            if spec:
                for s_ in spec if isinstance(spec, list) else [spec]:
                    if not isinstance(s_, dict): continue
                    if s_.get("validFrom") or s_.get("validThrough"): return None, None, "schema.org: Zeiten mit Gültigkeitszeitraum"
                    tage = s_.get("dayOfWeek") or []
                    tage = tage if isinstance(tage, list) else [tage]
                    a, b = str(s_.get("opens") or "")[:5], str(s_.get("closes") or "")[:5]
                    for d in tage:
                        k = SCHEMA_TAG.get(str(d).rstrip("/").split("/")[-1].lower())
                        if not k: continue
                        if (a in ("", "00:00") and b in ("", "00:00")): zu.add(k); continue
                        va, vb = _zeit(a), _zeit("24:00" if b == "23:59" else b, True)
                        if not va or not vb: return None, None, "schema.org: Uhrzeit nicht lesbar"
                        woche.setdefault(k, [])
                        if [va, vb] not in woche[k]: woche[k].append([va, vb])
            elif txt:
                r = osm_lesen("; ".join(txt) if isinstance(txt, list) else str(txt))
                if not r: return None, None, f"schema.org: openingHours nicht lesbar („{str(txt)[:60]}“)"
                woche, zu = r[0], set(r[1])
            if woche: funde.append((json.dumps(woche, sort_keys=True), woche, sorted(zu, key=WT.index)))
    if not funde: return None, None, None
    if len({f[0] for f in funde}) > 1: return None, None, "schema.org: mehrere verschiedene Zeiten (mehrere Betriebe?)"
    w = funde[0][1]
    for v in w.values(): v.sort()
    return w, funde[0][2], None

# ---------- Webseite lesen ----------
R_UNTER_1 = re.compile(r"(oeffnungszeit|öffnungszeit|offnungszeit|opening|hours|orari|zeiten)", re.I)
R_UNTER_2 = re.compile(r"(kontakt|contact|contatti|anfahrt|lage|info|ueber-uns|über-uns|about|restaurant|gasthaus|cafe|home)", re.I)

def seite_lesen(w):
    """Startseite + bis zu 3 Unterseiten (zuerst „Öffnungszeiten“, dann Kontakt/Info); gibt [(url, raw, zeilen)], fehler"""
    raw, err = sammler.holen(w)
    if err: return None, err
    zeilen, links = sammler.text_und_links(raw)
    seiten = [(w, raw, zeilen)]
    netloc = urllib.parse.urlparse(w).netloc.removeprefix("www.")
    kand = []
    for h in links:
        u = urllib.parse.urljoin(w, h).split("#")[0]
        p = urllib.parse.urlparse(u)
        if p.scheme not in ("http", "https") or p.netloc.removeprefix("www.") != netloc: continue
        pfad = urllib.parse.unquote(p.path + "?" + p.query)
        if re.search(r"\.(jpe?g|png|gif|webp|zip|docx?|pdf|mp4)(\?|$)", pfad, re.I) or u.rstrip("/") == w.rstrip("/"): continue
        prio = 0 if R_UNTER_1.search(pfad) else 1 if R_UNTER_2.search(pfad) else None
        if prio is not None and u not in [k[1] for k in kand]: kand.append((prio, u))
    for _, u in sorted(kand, key=lambda x: x[0])[:3]:
        r2, _ = sammler.holen(u)
        if r2: seiten.append((u, r2, sammler.text_und_links(r2)[0]))
    return seiten, None

def gleich(a, b):
    """zwei Wochenpläne gleich? (nur Tage, die beide nennen; mindestens 3 gemeinsame Tage)"""
    if not a or not b: return None
    gem = set(a[0]) & set(b[0])
    if len(gem) < 3: return None
    return all(sorted(a[0][t]) == sorted(b[0][t]) for t in gem) and not (set(a[1]) & set(b[0])) and not (set(b[1]) & set(a[0]))

def text_kurz(woche, zu):
    """„Mo–Fr 11:00–22:00 · Sa 10:00–23:00 · Di Ruhetag“"""
    teile, i = [], 0
    wert = lambda t: "zu" if t in zu else (", ".join(f"{a}–{b}" for a, b in woche[t]) if t in woche else None)
    while i < 7:
        v = wert(WT[i])
        if v is None: i += 1; continue
        j = i
        while j + 1 < 7 and wert(WT[j + 1]) == v: j += 1
        tage = WT_DE[WT[i]] + ("–" + WT_DE[WT[j]] if j > i + 1 else ("," + WT_DE[WT[j]] if j == i + 1 else ""))
        teile.append(f"{tage} {'Ruhetag' if v == 'zu' else v}")
        i = j + 1
    return " · ".join(teile)

def osm_text(woche, zu):
    inv = {v: k for k, v in WT_OSM.items()}
    return "; ".join([f"{inv[t]} " + ",".join(f"{a}-{b}" for a, b in woche[t]) for t in WT if t in woche] + [f"{inv[t]} off" for t in zu])

def lesen(x, seiten=None):
    """eine Gaststätte: gibt (ergebnis | None, grund | None, auszug)"""
    if seiten is None:
        seiten, err = seite_lesen(x["w"])
        if err or not seiten: return None, "nicht lesbar", ""
    voll = "\n".join("\n".join(z) for _, _, z in seiten)
    vlow = voll.lower()
    textfunde, sch, gruende, auszug = [], None, [], []
    for url, raw, zeilen in seiten:
        s_w, s_z, s_g = schema_zeiten(raw)
        if s_g: gruende.append(s_g)
        if s_w:
            if sch and json.dumps(sch[0], sort_keys=True) != json.dumps(s_w, sort_keys=True): gruende.append("schema.org: verschiedene Zeiten auf den Unterseiten")
            sch = (s_w, s_z, url)
        for ab in abschnitte(zeilen):
            w, z, g = wochenzeiten(ab)
            if g: gruende.append(g); auszug.append(f"[{url}] {ab[:500]}")
            elif w: textfunde.append((w, z, url, ab))
    # gleiche Zeiten auf mehreren Seiten (Fußzeile) zusammenfassen
    eindeutig = {}
    for w, z, url, ab in textfunde: eindeutig.setdefault(json.dumps([w, z], sort_keys=True), (w, z, url, ab))
    tf = list(eindeutig.values())
    if len(tf) > 1:
        return None, "verschiedene Zeiten auf der Webseite", "\n".join(f"[{u}] {a[:400]}" for _, _, u, a in tf[:3])
    if gruende:   # irgendein Abschnitt nicht eindeutig (auch wenn ein anderer lesbar wäre) -> lieber Prüfliste
        return None, "; ".join(dict.fromkeys(gruende)), "\n".join(auszug[:3] + ([f"[{tf[0][2]}] {tf[0][3][:400]}"] if tf else []))
    if not tf and not sch: return None, None, ""
    zu_g = R_BETRIEB_ZU.search(vlow)
    if zu_g: return None, f"Betrieb evtl. geschlossen („{zu_g.group(0)}“)", ""
    belege, quelle_url, best = [], None, 0
    if tf and sch:
        g_ = gleich((tf[0][0], tf[0][1]), (sch[0], sch[1]))
        if g_ is False:
            return None, "Text und schema.org-Angaben widersprechen sich", f"[{tf[0][2]}] {tf[0][3][:400]}\nschema.org: {text_kurz(sch[0], sch[1])}"
        woche, zu, quelle_url = tf[0][0], tf[0][1], tf[0][2]; best = 1 if g_ else 0
        belege = [tf[0][3][:300], "schema.org: " + text_kurz(sch[0], sch[1])]
    elif tf: woche, zu, quelle_url = tf[0][0], tf[0][1], tf[0][2]; belege = [tf[0][3][:300]]
    else: woche, zu, quelle_url = sch[0], sch[1], sch[2]; belege = ["schema.org: " + text_kurz(sch[0], sch[1])]
    osm = osm_lesen(x.get("oh"))
    vgl = gleich((woche, zu), osm) if osm else None
    f = {"woche": woche, "ruhetage": zu, "url": quelle_url, "belege": belege, "art": "text+schema" if tf and sch else ("text" if tf else "schema"),
         "osm_vergleich": None if vgl is None else ("gleich" if vgl else "anders"),
         "sicherheit": "bestaetigt" if (best or vgl) else "eine_quelle"}
    u = urlaub(voll)
    if u: f["urlaub"] = u
    if R_FEIERTAG.search(" ".join(belege)): f["hinweis"] = "An Feiertagen abweichende Zeiten möglich (siehe Webseite)"
    return f, None, ""


# ---------- KI-Leser (freies Modell Qwen über Ollama, nur wenn die festen Regeln nicht reichen) ----------
KI_ANWEISUNG = """Du liest Ausschnitte der Webseite einer Gaststätte (Restaurant, Gasthaus, Café, Bar) in Österreich.
Gib NUR ein JSON-Objekt zurück. Gesucht sind die regulären Öffnungszeiten DIESES Lokals pro Wochentag.
Regeln:
- Nur was wörtlich im Text steht. Nichts schätzen, nichts ergänzen. Im Zweifel weglassen.
- KEINE Küchenzeiten, Frühstückszeiten, Mittagsmenü-Zeiten, Liefer-/Abholzeiten, Hotel-Rezeption, Shop, Seilbahn, Veranstaltungen.
- Gelten verschiedene Zeiten je nach Saison/Monat/Ferien: "saisonabhaengig": true und "zeiten": {} – außer der Text sagt klar,
  welche Zeiten HEUTE gelten (Datum unten), dann nur diese.
- Offenes Ende wie "ab 17 Uhr" ohne Endzeit: den Tag weglassen.
- Tage, die nicht genannt sind, weglassen. "täglich" = alle 7 Tage. Ruhetag/geschlossen = "ruhetag".
- Zeiten im Format HH:MM, mehrere Zeitfenster als Liste. Bis Mitternacht = "24:00".
- "beleg": EIN zusammenhängendes, wörtlich kopiertes Zitat aus dem Text (max. 400 Zeichen), in dem ALLE Tage und Zeiten stehen.
- "dauerhaft_geschlossen": true nur, wenn der Text sagt, dass das Lokal geschlossen/aufgelassen ist oder einen Pächter sucht.
- Betriebsurlaub nur mit vollständigem Datum (Tag, Monat, Jahr) als "urlaub".
Format:
{"zeiten":{"mo":["11:30-14:00","17:30-22:00"],"di":"ruhetag"},"beleg":"...","saisonabhaengig":false,"dauerhaft_geschlossen":false,
 "urlaub":[{"von":"2026-12-23","bis":"2027-01-07","beleg":"..."}]}"""

KI_OLLAMA = os.environ.get("KI_OLLAMA", "")
KI_MODELL = os.environ.get("KI_MODELL", "qwen3.5:9b")
R_ZEIT_IM_TEXT = re.compile(r"(?<![\d.:])(\d{1,2})(?:[:.](\d{2}))?(?=\s*(?:uhr|h\b|-|–|bis|$|\s|,|/|\||\)))", re.I)

def ki_anfrage(text, name, versuche=3):
    if not KI_OLLAMA: return None, {}
    body = {"model": KI_MODELL, "stream": False, "format": "json", "think": False, "options": {"temperature": 0, "num_ctx": 4096},
            "messages": [{"role": "system", "content": KI_ANWEISUNG},
                         {"role": "user", "content": f"Lokal: {name}\nHeute: {HEUTE}\n\nText:\n{text}"}]}
    for i in range(versuche):
        try:
            req = urllib.request.Request(KI_OLLAMA.rstrip("/") + "/api/chat", data=json.dumps(body).encode(), headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=900) as r: d = json.load(r)
            t = d.get("message", {}).get("content", "")
            m = re.search(r"\{.*\}", t, re.S)
            return (json.loads(m.group(0)) if m else None), {"ein": d.get("prompt_eval_count", 0), "aus": d.get("eval_count", 0)}
        except Exception:
            time.sleep(5 * (i + 1))
    return None, {}

R_ZEITZEILE = re.compile(rf"(\d{{1,2}}[:.]\d{{2}}|\d{{1,2}}\s*uhr|ruhetag|geschlossen|geöffnet|täglich|{DAY})", re.I)

def ki_auszug(seiten, maxlen=2400):
    """nur Zeilen rund um Öffnungszeiten (Überschrift, Tage mit Uhrzeiten, Ruhetag, Betriebsurlaub), je Seite mit Adresse"""
    teile, gesehen = [], set()
    for url, _, z in seiten:
        nimm = set()
        for i, l in enumerate(z):
            if len(l) > 300: continue
            if R_UEBERSCHRIFT.search(l) or R_URLAUB.search(l) or (R_ZEITZEILE.search(l) and re.search(r"\d", l) and re.search(DAY + "|täglich|ruhetag", l, re.I)):
                nimm.update(range(max(0, i - 2), min(len(z), i + 6)))
        zeilen = [z[i][:200] for i in sorted(nimm) if z[i] not in gesehen]
        gesehen.update(zeilen)
        if zeilen: teile.append(f"[Seite {url}]\n" + "\n".join(zeilen))
    t = "\n\n".join(teile)
    return t[:maxlen]

def _beleg_zeiten(beleg):
    """alle Uhrzeiten im Zitat als HH:MM"""
    b = _norm(beleg)
    out = set()
    for h, m in re.findall(r"(?<![\d.:])(\d{1,2})(?:[:.](\d{2}))?(?![\d])", b):
        hh, mm = int(h), int(m or 0)
        if hh <= 24 and mm < 60: out.add(f"{hh:02d}:{mm:02d}")
    if re.search(r"mitternacht|midnight|(?<![\d.:])0?0[:.]00", b, re.I): out.add("24:00")
    if "00:00" in out: out.add("24:00")
    return out

def _beleg_tage(beleg):
    b = _norm(beleg)
    if re.search(TAEGLICH, b, re.I): return set(WT)
    tage = set()
    for m in re.finditer(DAYSPEC, b, re.I): tage |= set(_tage(m.group(0)))
    return tage

def _beleg_struktur(beleg):
    """Zitat in Tagesabschnitte teilen: welche Tage haben dort Uhrzeiten (offen), welche Ruhetag (zu)"""
    b = _norm(beleg)
    ms = list(re.finditer(rf"(?<![a-zäöüß]){DAYSPEC}", b, re.I))
    offen, zu, kueche = {}, set(), set()
    naechste = None
    for i in range(len(ms) - 1, -1, -1):
        m = ms[i]
        seg = b[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(b)]
        tage = _tage(m.group(0))
        if re.fullmatch(r"[\s&,+.]*(?:und|and|sowie)?[\s&,+.]*", seg, re.I) and naechste:
            art = naechste
        else:
            hatzeit = r"\d{1,2}[:.]\d{2}|\d\s*-\s*\d|\d\s*uhr"
            k = R_KUECHE.search(seg)
            vorne = seg[:k.start()] if k else seg          # Zeiten nach „Küche“ gelten nicht als Öffnungszeit
            zt = _beleg_zeiten(vorne) if re.search(hatzeit, vorne, re.I) else set()
            vor = b[max(ms[i - 1].end() if i else 0, m.start() - 30): m.start()]
            if R_KUECHE.search(vor) and not R_LOKAL.search(vor): zt, k = set(), True   # „Warme Küche: Di–Sa …“
            art = {"zeiten": zt, "zu": bool(re.search(ZU, seg, re.I)), "kueche": bool(k and not zt and re.search(hatzeit, seg, re.I))}
        for t in tage:
            if art["zeiten"]:
                if art["kueche"]: kueche.add(t)
                else: offen.setdefault(t, set()).update(art["zeiten"])
            if art["zu"]: zu.add(t)
        naechste = art
    for m in R_ZU_VOR.finditer(b): zu.update(_tage(m.group(1)))
    return offen, zu, kueche

def ki_pruefen(ant, text):
    """KI-Antwort gegen den Text prüfen; gibt (woche, zu, urlaub, beleg, None) oder (None, …, grund)"""
    if not isinstance(ant, dict): return None, None, None, None, "KI: keine lesbare Antwort"
    if ant.get("dauerhaft_geschlossen"): return None, None, None, None, "KI: Lokal evtl. geschlossen"
    if ant.get("saisonabhaengig"): return None, None, None, None, "KI: Zeiten je nach Saison"
    beleg = str(ant.get("beleg") or "")
    import ki_leser
    if not ki_leser.steht_drin(beleg, text): return None, None, None, None, "KI: Zitat steht nicht im Text"
    zt, bt = _beleg_zeiten(beleg), _beleg_tage(beleg)
    woche, zu = {}, []
    for t, v in (ant.get("zeiten") or {}).items():
        t = str(t).lower()[:2]
        if t not in WT: return None, None, None, None, f"KI: unbekannter Tag {t}"
        if t not in bt: return None, None, None, None, f"KI: {WT_DE[t]} steht nicht im Zitat"
        if isinstance(v, str) and v.lower().startswith("ruhe") or v in ("geschlossen", "zu", []): zu.append(t); continue
        if not isinstance(v, list): return None, None, None, None, "KI: Zeitangabe nicht lesbar"
        b = []
        for f in v:
            m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*", str(f))
            if not m: return None, None, None, None, f"KI: Zeit „{f}“ nicht lesbar"
            a_, e_ = _zeit(f"{m.group(1)}:{m.group(2)}"), _zeit(f"{m.group(3)}:{m.group(4)}", True)
            if not a_ or not e_ or a_ == e_: return None, None, None, None, f"KI: Zeit „{f}“ ungültig"
            if a_ not in zt or e_ not in zt: return None, None, None, None, f"KI: Zeit {a_}–{e_} steht nicht im Zitat"
            b.append([a_, e_])
        if b: woche[t] = sorted(b)
    if not woche: return None, None, None, None, "KI: keine Wochenzeiten"
    s_offen, s_zu, s_kueche = _beleg_struktur(beleg)
    for t, b in woche.items():
        if t in s_kueche and t not in s_offen: return None, None, None, None, f"KI: Zitat nennt am {WT_DE[t]} nur Küchenzeiten"
        if t not in s_offen: return None, None, None, None, f"KI: {WT_DE[t]} im Zitat ohne Uhrzeit"
        if any(a not in s_offen[t] or e not in s_offen[t] for a, e in b): return None, None, None, None, f"KI: Zeit am {WT_DE[t]} steht im Zitat bei anderem Tag"
    for t in zu:
        if t not in s_zu: return None, None, None, None, f"KI: Ruhetag {WT_DE[t]} steht nicht im Zitat"
        if t in s_offen: return None, None, None, None, f"KI: Ruhetag {WT_DE[t]}, im Zitat stehen aber Uhrzeiten"
    for t in s_offen:
        if t not in woche and t not in zu: return None, None, None, None, f"KI: {WT_DE[t]} steht mit Uhrzeit im Zitat, fehlt aber"
    for t, b in woche.items():
        for (a1, e1), (a2, e2) in zip(b, b[1:]):
            if a2 < e1: return None, None, None, None, f"KI: überlappende Zeiten am {WT_DE[t]}"
    if R_KUECHE.search(beleg) and not R_LOKAL.search(beleg): return None, None, None, None, "KI: Zitat nennt nur Küchen-/Frühstückszeiten"
    if R_SAISON.search(" ".join(x for x in _norm(beleg).lower().split("|") if not R_URLAUB.search(x))):
        return None, None, None, None, "KI: Zitat nennt Saison/Monate"
    if len(woche) < 2 and len(woche) + len(zu) < 3: return None, None, None, None, "KI: zu wenige Tage"
    ur = []
    for u in ant.get("urlaub") or []:
        try:
            if u.get("bis", "") >= HEUTE and ki_leser.steht_drin(str(u.get("beleg") or ""), text) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", u["von"]) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", u["bis"]):
                ur.append({"von": u["von"], "bis": u["bis"]})
        except Exception: pass
    return woche, sorted(set(zu), key=WT.index), ur, beleg, None

KI_FRAGEN = re.compile(r"(Tag oder Uhrzeit ohne Zuordnung|Tageszeile nicht eindeutig|verschiedene Zeiten|Uhrzeit nicht lesbar|überlappende|"
                       r"zu wenige Tage|nur Ruhetage|schema\.org|Text und schema|Küche|offenes Ende)")

# ---------- Lauf ----------
def gruppe(x):
    l = x.get("l") or "unbekannt"
    p = re.sub(r"\D", "", x.get("plz") or "")
    return f"{l}-PLZ{p[0]}" if p else f"{l}-ohne-PLZ"

def sammeln(neu, aus, odir):
    os.makedirs(odir, exist_ok=True)
    alle = json.load(open(neu, encoding="utf-8"))
    jobs = [x for x in alle if x.get("w") and not x.get("skip")]
    nmax = int(os.environ.get("MAX_LOKALE", "0") or 0)
    if nmax: jobs = jobs[:nmax]
    ende = time.time() + 60 * float(os.environ.get("SAMMEL_MINUTEN", "280"))
    stat, n = Counter(), [0]
    print(f"{len(jobs)} Gaststätten mit eigener Webseite werden gelesen", flush=True)
    def eins(x):
        n[0] += 1
        if n[0] % 500 == 0: print(f"  … {n[0]}/{len(jobs)}  {dict(stat)}", flush=True)
        if time.time() > ende: return x, None, "zeit", ""
        try: return (x,) + lesen(x)
        except Exception as e: return x, None, f"Fehler beim Lesen ({str(e)[:60]})", ""
    ok, pruef, anders = [], defaultdict(list), []
    with cf.ThreadPoolExecutor(int(os.environ.get("THREADS", "24"))) as ex:
        for x, f, grund, txt in ex.map(eins, jobs):
            if f:
                ok.append({"t": x["t"], "id": x["id"], **f}); stat[f["sicherheit"]] += 1
                if f["osm_vergleich"] == "anders":
                    anders.append(f"- {x['n']} ({x['t']}/{x['id']}): Webseite {text_kurz(f['woche'], f['ruhetage'])} | OSM {x['oh']} | {f['url']}")
            elif grund == "zeit": stat["zeit_ueberschritten"] += 1
            elif grund == "nicht lesbar": stat["nicht_lesbar"] += 1
            elif grund:
                stat["pruefliste"] += 1
                pruef[gruppe(x)].append(f"## {x['n']} ({x['t']}/{x['id']})\n{x['w']}\n{grund}\n{txt[:1200]}\n")
                ok.append({"t": x["t"], "id": x["id"], "zurueck": grund[:200]})
            else: stat["keine_zeiten"] += 1; ok.append({"t": x["t"], "id": x["id"], "zurueck": "keine Öffnungszeiten auf der Webseite gefunden"})
    json.dump(ok, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    for f_ in os.listdir(odir):
        if f_.startswith("pruef-"): os.remove(os.path.join(odir, f_))
    for k, v in sorted(pruef.items()):
        with open(os.path.join(odir, f"pruef-{re.sub(r'[^A-Za-z0-9-]+', '_', k)}.md"), "w", encoding="utf-8") as fo:
            fo.write(f"# Prüfliste Gaststätten {k} ({HEUTE})\n\nNicht eindeutig lesbar. Nur Fakten übernehmen, Regeln aus Prompt 1.\n\n" + "\n".join(v))
    with open(os.path.join(odir, "osm-abweichungen.md"), "w", encoding="utf-8") as fo:
        fo.write(f"# Gaststätten: Webseite und OpenStreetMap verschieden ({HEUTE})\n\nEingetragen ist die Webseite (Betreiber). {len(anders)} Einträge.\n\n" + "\n".join(sorted(anders)) + "\n")
    print(f"gelesen: {sum(1 for e in ok if not e.get('zurueck'))}, Abläufe: {dict(stat)}, Abweichung zu OSM: {len(anders)}")


def eins_mit_ki(x, url, cache, stat, messen=False):
    """Regeln zuerst; Qwen nur, wenn die Regeln nicht eindeutig waren (oder zur Messung)"""
    seiten, err = seite_lesen(url)
    if err or not seiten: return None, "nicht lesbar", ""
    x2 = {**x, "w": url}
    f, grund, txt = lesen(x2, seiten)
    frage = KI_OLLAMA and ((grund and KI_FRAGEN.search(grund)) or (not f and not grund) or (f and messen))
    if not frage: return f, grund, txt
    aus = ki_auszug(seiten)
    if not aus or not re.search(r"\d{1,2}[:.]\d{2}|\d\s*uhr", aus, re.I):
        return f, grund, txt
    import hashlib
    h = hashlib.sha1((aus + KI_MODELL + "g1").encode()).hexdigest()[:16]
    alt = cache.get(url)
    if alt and alt.get("h") == h: ant = alt.get("a"); stat["ki_cache"] += 1
    else:
        if stat["ki_anfragen"] >= int(os.environ.get("MAX_KI", "100000")): return f, grund or "KI-Obergrenze erreicht", txt
        stat["ki_anfragen"] += 1
        ant, _ = ki_anfrage(aus, x["n"])
        if ant is not None: cache[url] = {"h": h, "a": ant}
    w, z, ur, beleg, kg = ki_pruefen(ant, aus)
    if messen and f:   # Messung: Regeln und KI auf derselben Seite
        if w is None: stat["mess_ki_nichts"] += 1
        else: stat["mess_gleich" if gleich((w, z), (f["woche"], f["ruhetage"])) else "mess_anders"] += 1
        return f, grund, txt
    if w is None:
        stat["ki_" + re.sub(r"[^a-z]+", "_", kg.lower())[:30]] += 1
        return None, (grund + "; " if grund else "") + kg, (txt + "\n" if txt else "") + aus[:1200]
    regel = wochenzeiten(_norm(beleg))
    osm = osm_lesen(x.get("oh"))
    sch = None
    for _, raw, _ in seiten:
        s_w, s_z, _ = schema_zeiten(raw)
        if s_w: sch = (s_w, s_z)
    bestaetigt = bool((regel[0] and gleich((w, z), (regel[0], regel[1]))) or (osm and gleich((w, z), osm)) or (sch and gleich((w, z), sch)))
    vgl = gleich((w, z), osm) if osm else None
    f = {"woche": w, "ruhetage": z, "url": url, "belege": [beleg[:400]], "art": "ki", "osm_vergleich": None if vgl is None else ("gleich" if vgl else "anders"),
         "sicherheit": "bestaetigt" if bestaetigt else "eine_quelle"}
    ur = ur or urlaub("\n".join("\n".join(zz) for _, _, zz in seiten))
    if ur: f["urlaub"] = ur
    if R_FEIERTAG.search(beleg): f["hinweis"] = "An Feiertagen abweichende Zeiten möglich (siehe Webseite)"
    stat["ki_ok"] += 1
    return f, None, ""

def sammeln_at(neu, quellen_datei, aus, odir):
    """Österreich: nur Lokale mit geprüfter Webseite (gast_suche.py), Teil TEIL=i/n, Regeln + Qwen"""
    os.makedirs(odir, exist_ok=True)
    q = json.load(open(quellen_datei, encoding="utf-8")); falsch = q.pop("_falsch", {})
    alle = json.load(open(neu, encoding="utf-8"))
    nr, anz = map(int, os.environ.get("TEIL", "0/1").split("/"))
    jobs = [x for i, x in enumerate(sorted(alle, key=lambda x: (x["t"], x["id"]))) if i % anz == nr]
    nmax = int(os.environ.get("MAX_LOKALE", "0") or 0)
    mit = [(x, q[f"{x['t']}/{x['id']}"]) for x in jobs if f"{x['t']}/{x['id']}" in q]
    if nmax: mit = mit[:nmax]
    messen_n = int(os.environ.get("KI_MESSEN", "0") or 0)
    cache_p = os.path.join(odir, "ki-cache.json")
    cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}
    ende = time.time() + 60 * float(os.environ.get("SAMMEL_MINUTEN", "280"))
    stat, n = Counter(), [0]
    print(f"Teil {nr}/{anz}: {len(jobs)} Lokale, {len(mit)} mit geprüfter Webseite, KI: {KI_MODELL if KI_OLLAMA else 'aus'}", flush=True)
    def eins(job):
        x, qq = job
        n[0] += 1
        if n[0] % 200 == 0: print(f"  … {n[0]}/{len(mit)}  {dict(stat)}", flush=True)
        if time.time() > ende: return x, qq, None, "zeit", ""
        try: return (x, qq) + eins_mit_ki(x, qq["url"], cache, stat, messen=(n[0] <= messen_n))
        except Exception as e: return x, qq, None, f"Fehler beim Lesen ({str(e)[:60]})", ""
    ok, pruef = [], defaultdict(list)
    with cf.ThreadPoolExecutor(int(os.environ.get("THREADS", "6"))) as ex:
        for x, qq, f, grund, txt in ex.map(eins, mit):
            base = {"t": x["t"], "id": x["id"], "webseite": qq["url"], "webseite_art": qq["art"], "webseite_beleg": qq.get("beleg")}
            if f: ok.append({**base, **f}); stat[f["sicherheit"]] += 1
            elif grund == "zeit": stat["zeit_ueberschritten"] += 1
            elif grund == "nicht lesbar": stat["nicht_lesbar"] += 1; ok.append({**base, "zurueck": "Webseite nicht lesbar"})
            elif grund:
                stat["pruefliste"] += 1
                pruef[gruppe(x)].append(f"## {x['n']} ({x['t']}/{x['id']})\n{qq['url']} ({qq['art']})\n{grund}\n{txt[:1500]}\n")
                ok.append({**base, "zurueck": grund[:200]})
            else: stat["keine_zeiten"] += 1; ok.append({**base, "zurueck": "keine Öffnungszeiten auf der Webseite gefunden"})
    for k, g in falsch.items():
        t, i = k.split("/"); 
        if any(f"{x['t']}/{x['id']}" == k for x in jobs): ok.append({"t": t, "id": int(i), "osm_webseite_falsch": g})
    json.dump(cache, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(ok, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    for k, v in sorted(pruef.items()):
        with open(os.path.join(odir, f"pruef-{re.sub(r'[^A-Za-z0-9-]+', '_', k)}.md"), "w", encoding="utf-8") as fo:
            fo.write(f"# Prüfliste Gaststätten {k} ({HEUTE})\n\nNicht eindeutig lesbar.\n\n" + "\n".join(v))
    print(f"gelesen: {sum(1 for e in ok if e.get('woche'))}, Abläufe: {dict(stat)}", flush=True)

def test(url, name="Test"):
    f, grund, txt = lesen({"w": url, "n": name, "oh": None})
    print(json.dumps(f, ensure_ascii=False, indent=1) if f else f"kein Eintrag: {grund}\n{txt}")
    if f: print(text_kurz(f["woche"], f["ruhetage"]))

# ---------- Datenbank ----------
SB_URL = os.environ.get("SB_URL", "https://vehrlrjegxlhxibrxqxm.supabase.co")
SB_KEY = os.environ.get("SB_KEY", "sb_publishable_5sO5VJ5W232hdV_gAbzplA_A4UgzW6M")

def sb(method, path, body=None, prefer=None, key=None):
    key = key or os.environ.get("SUPABASE_SERVICE_KEY", "")
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(SB_URL + "/rest/v1/" + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r: return r.read()

def _alle(sel, flt="", key=None):
    rows, off = [], 0
    while True:
        part = json.loads(sb("GET", f"gb_gaststaetten?select={sel}{flt}&order=id&limit=1000&offset={off}", key=key))
        rows += part
        if len(part) < 1000: return rows
        off += 1000

def db(neu, auto):
    inv = {(x["t"], x["id"]): x for x in json.load(open(neu, encoding="utf-8")) if x.get("w")}
    fertig = {(r["osm_typ"], r["osm_id"]) for r in _alle("osm_typ,osm_id", "&daten->>recherche=eq.fertig")}
    erg = {(e["t"], e["id"]): e for e in json.load(open(auto, encoding="utf-8"))}
    rows = []
    for k, x in inv.items():
        if k in fertig: continue
        e = erg.get(k)
        r = {"osm_typ": x["t"], "osm_id": x["id"], "name": x["n"], "art": x["a"], "geom": f"SRID=4326;POINT({x['lon']} {x['lat']})",
             "land": x.get("l"), "webseite": x["w"], "telefon": x.get("tel"), "oeffnungszeiten_osm": x.get("oh"), "pruef_datum": HEUTE,
             "status": "aktiv"}
        if e and not e.get("zurueck"):
            o = {"woche": e["woche"]}
            if e["ruhetage"]: o["ruhetage"] = e["ruhetage"]
            if e.get("urlaub"): o["urlaub"] = e["urlaub"]
            r.update({"oeffnung": o, "zeiten": text_kurz(e["woche"], e["ruhetage"]),
                      "ruhetage": ", ".join(WT_DE[t] for t in e["ruhetage"]) or None, "hinweise": e.get("hinweis"),
                      "quelle": "Eigene Webseite (maschinell gelesen)", "quelle_url": e["url"], "quelle_datum": HEUTE,
                      "sicherheit": e["sicherheit"],
                      "daten": {"recherche": "auto", "auto_datum": HEUTE, "auto_art": e["art"], "belege": e["belege"],
                                "osm_vergleich": e["osm_vergleich"], "osm_text": osm_text(e["woche"], e["ruhetage"])}})
        else:
            grund = (e or {}).get("zurueck") or ("übersprungen: " + x["skip"] if x.get("skip") else "nicht gelesen")
            r.update({"oeffnung": None, "zeiten": None, "ruhetage": None, "hinweise": None, "quelle": None, "quelle_url": None,
                      "quelle_datum": None, "sicherheit": "unklar", "daten": {"recherche": "offen", "grund": grund[:200]}})
        rows.append(r)
    for i in range(0, len(rows), 500):
        sb("POST", "gb_gaststaetten?on_conflict=osm_typ,osm_id", rows[i:i + 500], "resolution=merge-duplicates,return=minimal")
    print(f"{len(rows)} Gaststätten geschrieben ({sum(1 for r in rows if r['oeffnung'])} mit Öffnungszeiten), {len(fertig)} von Hand recherchierte unverändert")

def db_at(neu, auto):
    """Österreich-Lauf eintragen: nur Lokale mit geprüfter Webseite oder Ergebnis; nie recherche=fertig überschreiben"""
    inv = {(x["t"], x["id"]): x for x in json.load(open(neu, encoding="utf-8"))}
    fertig = {(r["osm_typ"], r["osm_id"]) for r in _alle("osm_typ,osm_id", "&daten->>recherche=eq.fertig")}
    erg = defaultdict(dict)
    for e in json.load(open(auto, encoding="utf-8")): erg[(e["t"], e["id"])].update(e)
    rows, stat = [], Counter()
    for k, e in erg.items():
        x = inv.get(k)
        if not x or k in fertig: continue
        if e.get("zurueck") == "Webseite nicht lesbar" and not e.get("woche"): continue   # vorübergehend nicht erreichbar: alten Stand behalten
        web = e.get("webseite") or (x.get("w") if not e.get("osm_webseite_falsch") else None)
        r = {"osm_typ": x["t"], "osm_id": x["id"], "name": x["n"], "art": x["a"], "geom": f"SRID=4326;POINT({x['lon']} {x['lat']})",
             "land": "AT", "webseite": web, "telefon": x.get("tel"), "oeffnungszeiten_osm": x.get("oh"), "pruef_datum": HEUTE, "status": "aktiv"}
        d = {"lauf": "oesterreich", "webseite_art": e.get("webseite_art"), "webseite_beleg": e.get("webseite_beleg"),
             "osm_webseite": x.get("w"), "osm_webseite_falsch": e.get("osm_webseite_falsch")}
        if e.get("woche"):
            o = {"woche": e["woche"]}
            if e["ruhetage"]: o["ruhetage"] = e["ruhetage"]
            if e.get("urlaub"): o["urlaub"] = e["urlaub"]
            ki = e.get("art") == "ki"
            r.update({"oeffnung": o, "zeiten": text_kurz(e["woche"], e["ruhetage"]), "ruhetage": ", ".join(WT_DE[t] for t in e["ruhetage"]) or None,
                      "hinweise": e.get("hinweis"), "quelle": "Eigene Webseite (" + ("KI gelesen, Zitat geprüft" if ki else "maschinell gelesen") + ")",
                      "quelle_url": e["url"], "quelle_datum": HEUTE, "sicherheit": e["sicherheit"],
                      "daten": {**d, "recherche": "auto", "auto_datum": HEUTE, "auto_art": e["art"], "belege": e.get("belege"),
                                "osm_vergleich": e.get("osm_vergleich"), "osm_text": osm_text(e["woche"], e["ruhetage"])}})
            stat["mit_zeiten_" + ("ki" if ki else "regel")] += 1
        else:
            r.update({"oeffnung": None, "zeiten": None, "ruhetage": None, "hinweise": None, "quelle": None, "quelle_url": None, "quelle_datum": None,
                      "sicherheit": "unklar", "daten": {**d, "recherche": "offen", "grund": (e.get("zurueck") or e.get("osm_webseite_falsch") or "")[:200]}})
            stat["ohne_zeiten"] += 1
        rows.append(r)
    for i in range(0, len(rows), 500):
        sb("POST", "gb_gaststaetten?on_conflict=osm_typ,osm_id", rows[i:i + 500], "resolution=merge-duplicates,return=minimal")
    print(f"{len(rows)} Gaststätten in Österreich geschrieben: {dict(stat)}; {len(fertig)} von Hand recherchierte unverändert")

def daten(out):
    rows = _alle("osm_typ,osm_id,name,oeffnung,zeiten,ruhetage,hinweise,quelle,quelle_url,pruef_datum,sicherheit,status,webseite,telefon,daten",
                 "&oeffnung=not.is.null", key=SB_KEY)
    json.dump(rows, open(out, "w"), ensure_ascii=False)
    print(f"{len(rows)} Gaststätten mit Öffnungszeiten aus gb_gaststaetten geholt")

def kurz(r):
    d = r.get("daten") or {}
    if d.get("recherche") not in ("fertig", "auto") or not r.get("oeffnung"): return None
    g = {"v": "ja", "o": r["oeffnung"], "gast": 1}
    for k, f in (("z", "zeiten"), ("r", "ruhetage"), ("h", "hinweise"), ("q", "quelle"), ("u", "quelle_url"), ("d", "pruef_datum"),
                 ("k", "sicherheit"), ("tel", "telefon"), ("web", "webseite")):
        if r.get(f) not in (None, ""): g[k] = r[f]
    if r.get("status") and r["status"] != "aktiv": g["st"] = r["status"]
    return json.dumps(g, ensure_ascii=False, separators=(",", ":"))

def anreichern(zin, zout, dfile):
    T = {"node": "n", "way": "w", "relation": "r"}
    by = {}
    for r in json.load(open(dfile)):
        g = kurz(r)
        if g: by[T[r["osm_typ"]] + str(r["osm_id"])] = g
    src = os.path.join(zin, "rest"); dst = os.path.join(zout, "rest"); os.makedirs(dst, exist_ok=True)
    n_tr = n_z = 0
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".json.gz"): continue
        with gzip.open(os.path.join(src, fn), "rt", encoding="utf-8") as f: j = json.load(f)
        treffer = 0
        for el in j.get("elements", []):
            g = by.get(el["type"][0] + str(el["id"]))
            if g: el.setdefault("tags", {})["gb"] = g; treffer += 1
        if not treffer: continue   # Worker nimmt dann die normale Zelle
        with gzip.open(os.path.join(dst, fn), "wt", encoding="utf-8", compresslevel=9) as f:
            f.write(json.dumps(j, ensure_ascii=False, separators=(",", ":")))
        n_tr += treffer; n_z += 1
    print(f"Gaststätten-Zellen: {n_z} geschrieben, {n_tr} Gaststätten mit Öffnungszeiten")
    return n_tr

if __name__ == "__main__":
    c, a = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if c == "inventar": inventar(*a)
    elif c == "sammeln": sammeln(*a)
    elif c == "test": test(*a)
    elif c == "sammeln-at": sammeln_at(*a)
    elif c == "db-at": db_at(*a)
    elif c == "ki-test":
        st = Counter(); cache = {}
        print(eins_mit_ki({"n": a[1] if len(a) > 1 else "Lokal", "oh": None}, a[0], cache, st), dict(st))
    elif c == "db": db(*a)
    elif c == "daten": daten(*a)
    elif c == "anreichern": anreichern(*a)
    else: print(__doc__); sys.exit(2)
