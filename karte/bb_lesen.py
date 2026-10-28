#!/usr/bin/env python3
"""Betriebszeiten von Bergbahn-Webseiten lesen – ohne KI, nur eindeutige Fakten.

Wird von karte/bergbahnen.py benutzt. Liest den Text einer Seite Zeile für Zeile und merkt sich dabei,
in welchem Abschnitt man gerade ist:
  - Jahreszeit („Sommer 2027“, „Winter 2026/27“; Reiter-Leisten „Winter Sommer“ werden übersprungen)
  - Anlage („Golmerbahn“, „Alpine-Coaster“, „Restaurant …“) – nur Angaben im Abschnitt einer Bahn zählen für diese Bahn
Gefunden werden Zeiträume (Betrieb, Wochenende, Revision), tägliche Zeiten (erste/letzte Berg- und Talfahrt),
Ruhetage, „ganzjährig“, Schönwetter, Fahrrad, Hunde, Preis Berg- und Talfahrt Erwachsene.
Nichts wird geschätzt: Datumsangaben ohne erkennbares Jahr werden nicht verwendet.
"""
import re, datetime

HEUTE = datetime.date.today()

MON = {}
for i, ws in enumerate([("jan", "jän", "jaen", "januar", "jänner", "jaenner", "gennaio", "january", "janvier"),
                        ("feb", "februar", "feber", "febbraio", "february", "février", "fevrier"),
                        ("mär", "maer", "mar", "märz", "maerz", "marzo", "march", "mars"),
                        ("apr", "april", "aprile", "avril"),
                        ("mai", "maggio", "may"),
                        ("jun", "juni", "giugno", "june", "juin"),
                        ("jul", "juli", "luglio", "july", "juillet"),
                        ("aug", "august", "agosto", "août", "aout"),
                        ("sep", "sept", "september", "settembre", "septembre"),
                        ("okt", "oct", "oktober", "ottobre", "october", "octobre"),
                        ("nov", "november", "novembre"),
                        ("dez", "dic", "dec", "dezember", "dicembre", "december", "décembre", "decembre")], 1):
    for w in ws: MON[w] = i
MONRE = "|".join(sorted(MON, key=len, reverse=True))
WT = {"mo": "mo", "montag": "mo", "montags": "mo", "lunedì": "mo", "lunedi": "mo", "di": "di", "dienstag": "di", "dienstags": "di", "martedì": "di", "martedi": "di",
      "mi": "mi", "mittwoch": "mi", "mittwochs": "mi", "mercoledì": "mi", "mercoledi": "mi", "do": "do", "donnerstag": "do", "donnerstags": "do", "giovedì": "do", "giovedi": "do",
      "fr": "fr", "freitag": "fr", "freitags": "fr", "venerdì": "fr", "venerdi": "fr", "sa": "sa", "samstag": "sa", "samstags": "sa", "sabato": "sa",
      "so": "so", "sonntag": "so", "sonntags": "so", "domenica": "so"}
WTO = ["mo", "di", "mi", "do", "fr", "sa", "so"]
WTRE = "|".join(sorted(WT, key=len, reverse=True))

# Datum: 15.05.2027 · 15.05. · 15. Mai 2027 · 15 maggio · Sonntag, 15. November 2026
D_NUM = r"(?<![\d.])(\d{1,2})\.\s?(\d{1,2})\.(?:\s?((?:20)?\d{2})(?!\d))?"
D_TXT = r"(?<!\d)(\d{1,2})\.?\s+(" + MONRE + r")\.?(?:\s+(20\d{2}))?(?![a-zäöü])"
DATUM = re.compile(D_NUM + "|" + D_TXT, re.I)
BIS = re.compile(r"^\s*(?:\(?[a-zäöü]{2,10}\.?\)?,?\s*)?(?:[-–—]|bis(?:\s+(?:einschließlich|einschliesslich|inklusive|inkl\.?|und mit))?|to|until|al|fino al|au|jusqu'au)\s*(?:[a-zäöüì]{2,10}\.?,?\s*)?$", re.I)
ZEIT = r"(?<![\d.:])([01]?\d|2[0-3])[:.]([0-5]\d)(?![\d.])"
ZEITSPANNE = re.compile(ZEIT + r"\s*(?:uhr|h)?\s*(?:[-–—]|bis|to|alle|à|a)\s*" + ZEIT + r"\s*(?:uhr|h)?", re.I)
EINZELZEIT = re.compile(ZEIT, re.I)
JAHR = re.compile(r"(?<!\d)(20[2-3]\d)(?:\s*/\s*(?:20)?([2-3]\d))?(?!\d)")

SOMMER = re.compile(r"\b(sommer\w*|summer|estate|estiv\w*|été|ete)\b", re.I)
WINTER = re.compile(r"\b(winter\w*|ski(saison|betrieb|gebiet|season)\w*|inverno|invernal\w*|hiver)\b", re.I)
REVISION = re.compile(r"(revision|betriebsurlaub|wartung|betriebspause|betriebsruhe|betriebsunterbrech|außer betrieb|ausser betrieb|stillstand|geschlossen|kein betrieb|manutenzione|revisione|chiuso|fermo impianti|fermeture|closed)", re.I)
WOCHENENDE = re.compile(r"(wochenend|weekend|fine settimana|nur (an )?(samstag|sonntag|sa\b))", re.I)
GANZJ = re.compile(r"(ganzjährig|ganzjaehrig|das ganze jahr|365 tage|tutto l'anno|toute l'année|ganzes jahr)", re.I)
SCHOEN = re.compile(r"(nur bei (schönem|schoenem|gutem|guter) (wetter|witterung)|schönwetter|schoenwetter|wetterabhängig|wetterabhaengig|bei schlechtwetter (kein|geschlossen)|condizioni meteo favorevoli|bel tempo)", re.I)
RAD = re.compile(r"((fahr)?rad|bike|mtb|e-bike|velo|bici)\w*[\s-]*(transport|mitnahme|beförderung|befoerderung|trasporto|ticket|karte)|(transport|mitnahme|beförderung)\s+(von\s+)?(fahr)?r(ä|ae)der|bikes? (dürfen|können|werden) .{0,30}(mitgenommen|transportiert)", re.I)
HUND = re.compile(r"\bhund(e|en)?\b|\bcani\b|\bdogs?\b|\bchiens?\b", re.I)
NEIN = re.compile(r"(kein|keine|nicht|verboten|untersagt|ausgeschlossen|leider|non (è|e) (consentit|ammess)|vietat|not allowed|no dogs)", re.I)
PREIS = re.compile(r"berg-?\s*(?:und|&|\+|/)\s*tal(?:fahrt)?|retour|andata e ritorno|hin und zurück|hin- und rückfahrt", re.I)
BETRAG = re.compile(r"(?:€|eur|chf|fr\.)\s*(\d{1,3}(?:[.,]\d{2})|\d{1,3}(?:[.,]-))|(\d{1,3}[.,](?:\d{2}|-))\s*(?:€|eur|chf|fr\.)", re.I)
ERW = re.compile(r"erwachsen|adult|adulti", re.I)
# Anlagen, die keine Bahn sind (eigener Abschnitt – Angaben gehören nicht zur Bahn)
ANDERE = re.compile(r"(coaster|rodel|sommerrodel|schlitten|seilpark|kletterpark|hochseil|flying|fox|zipline|flyline|restaurant|gasthaus|gasthof|bergrestaurant|hütte|huette|alpe\b|\balm\b|bistro|\bbar\b|café|cafe|kiosk|shop|kassa|kasse|verkaufsstelle|vorverkauf|skischule|skiverleih|verleih|rent|spielplatz|minigolf|\bbad\b|freibad|therme|museum|sauna|bikepark|trail ?park|pumptrack|e-?bike-?verleih|parkplatz|parkhaus|büro|buero|rezeption|tourismus|information|infobüro|nachtrodeln|nachtski|flutlicht|piste\b|langlauf|loipe|eislauf|eisplatz|golf|tennis|wellness)", re.I)
STARK = re.compile(r"(coaster|rodel|schlitten|seilpark|kletterpark|hochseil|flying|zipline|flyline|bikepark|trail ?park|spielplatz|minigolf|freibad|therme|museum|skischule|verleih|kassa|vorverkauf|verkaufsstelle|langlauf|loipe|eislauf|wellness|sauna)", re.I)
GEBIET = re.compile(r"(wintersportanlagen|liftanlagen|alle (bahnen|anlagen|lifte)|skigebiet|skibetrieb|pistenbetrieb|lifte\b|ski ?area|comprensorio|impianti)", re.I)
LABEL = re.compile(r"^(fahrplan\w*|datum|zeiten|zeitraum|uhrzeit|tage?|art|periode|täglich\w*|taeglich\w*|wochenend\w*|betriebszeit\w*|öffnungszeit\w*|oeffnungszeit\w*|betrieb|bergfahrt|talfahrt|hinweis|info|achtung|start|ende|beginn|saison\w*|zeitraum|fahrzeit\w*|änderungen vorbehalten|erste|letzte|revision\w*)\b", re.I)

def norm(s):
    s = (s or "").lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    import unicodedata
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")).strip()
ALLG = {"bahn", "bahnen", "lift", "lifte", "sessel", "sesselbahn", "sessellift", "sesselift", "gondel", "gondelbahn", "seilbahn", "kabinenbahn", "kabinen",
        "express", "schlepplift", "schlepp", "bergbahn", "bergbahnen", "talstation", "bergstation", "station", "luftseilbahn", "pendelbahn", "standseilbahn",
        "zahnradbahn", "funivia", "seggiovia", "cabinovia", "telecabina", "skilift", "sciovia", "gondola", "chairlift", "der", "die", "das", "und", "mit",
        "ii", "iii", "neu", "alt", "fis", "kombibahn", "jet", "shuttle", "fun", "bergfahrt", "talfahrt", "telesiege", "telecabine", "teleferique", "funiculaire",
        "sesselbahnen", "gondelbahnen", "seilbahnen", "anlage", "anlagen", "the", "von", "zum", "zur", "ski", "skilifte", "kids", "kinder", "baby", "kinderlift", "uebungslift"}
def tokens(name):
    return [w for w in norm(name).split() if len(w) >= 4 and w not in ALLG and not re.fullmatch(r"\d+(er)?", w)]
def nennt(zeile_n, toks, ganz):
    """Zeile (normalisiert) nennt diese Bahn? voller Name oder alle Namensteile (mind. einer)"""
    if ganz and len(ganz) >= 6 and (" " + ganz + " ") in (" " + zeile_n + " "): return True
    if not toks: return False
    w = set(zeile_n.split())
    return all(t in w or any(x.startswith(t) and len(t) >= 6 for x in w) for t in toks)

def _jahr(y):
    if not y: return None
    y = int(y); return y + 2000 if y < 100 else y

def daten_in(z):
    """alle Datumsangaben einer Zeile: [(start, ende, tag, monat, jahr|None)]"""
    out = []
    for m in DATUM.finditer(z):
        if m.group(1):
            d, mo, y = int(m.group(1)), int(m.group(2)), _jahr(m.group(3))
            # 1.2.3 ist keine Uhrzeit/Version: Monat 1-12
        else:
            d, mo, y = int(m.group(4)), MON.get(m.group(5).lower().rstrip(".")), _jahr(m.group(6))
        if not mo or not (1 <= mo <= 12) or not (1 <= d <= 31): continue
        if y is not None and not (2020 <= y <= 2035): continue
        out.append((m.start(), m.end(), d, mo, y))
    return out

def jahre_fuellen(ds, kontext_jahre):
    """fehlende Jahre: aus späteren Angaben derselben Zeile (rückwärts), sonst aus der Überschrift"""
    ds = [list(x) for x in ds]
    nxt = None
    for x in reversed(ds):
        if x[4] is not None: nxt = (x[3], x[4])
        elif nxt: x[4] = nxt[1] - 1 if x[3] > nxt[0] else nxt[1]; nxt = (x[3], x[4])
    for x in ds:
        if x[4] is None and kontext_jahre:
            y1, y2 = kontext_jahre
            if y2 and y2 != y1: x[4] = y1 if x[3] >= 7 else y2
            elif y1: x[4] = y1
    return ds

def bis_zwischen(t):
    """Text zwischen zwei Datumsangaben bedeutet „bis“? (Uhrzeiten, Wochentage, „inklusive“ werden übergangen)"""
    t = re.sub(r"(ab|um|von)?\s*\d{1,2}([:.]\d{2})?\s*uhr|\d{1,2}[:.]\d{2}", " ", t, flags=re.I)
    t = re.sub(r"\b(" + WTRE + r")\b\.?", " ", t, flags=re.I)
    t = re.sub(r"\b(inklusive|inkl\.?|einschließlich|einschliesslich|jeweils|incluso|compreso)\b", " ", t, flags=re.I)
    t = re.sub(r"[,()]", " ", t)
    return len(t) <= 40 and bool(re.fullmatch(r"\s*(?:[-–—]|bis|to|until|al|fino al|au|jusqu'au)\s*", t, re.I))

def zeitraeume(z, kontext_jahre):
    """Zeiträume einer Zeile: [(von, bis)] als date; einzelne Tage nur wenn ausdrücklich (z. B. „Do, 22.10.2026“) – die
    werden als ein-Tages-Zeitraum geliefert"""
    ds = jahre_fuellen(daten_in(z), kontext_jahre)
    out, i = [], 0
    while i < len(ds):
        a = ds[i]
        if i + 1 < len(ds) and bis_zwischen(z[a[1]:ds[i + 1][0]]):
            b = ds[i + 1]; i += 2
        else:
            b = a; i += 1
        if a[4] is None or b[4] is None: continue
        try: von, bis = datetime.date(a[4], a[3], a[2]), datetime.date(b[4], b[3], b[2])
        except ValueError: continue
        if bis < von and a[4] == b[4] and not any(x[4] is not None for x in ds[i - 2:i]):
            try: bis = datetime.date(b[4] + 1, b[3], b[2])
            except ValueError: continue
        if bis < von or (bis - von).days > 300: continue
        out.append((von, bis))
    return out

def tage_aus(z):
    """„(Donnerstag bis Sonntag)“, „jeweils von Freitag bis Sonntag“, „Sa und So“ -> ['do','fr','sa','so']"""
    m = re.search(r"\b(" + WTRE + r")\.?\s*(?:[-–]|bis)\s*(" + WTRE + r")\b", z, re.I)
    if m:
        a, b = WTO.index(WT[m.group(1).lower()]), WTO.index(WT[m.group(2).lower()])
        return WTO[a:b + 1] if a <= b else WTO[a:] + WTO[:b + 1]
    m = re.findall(r"\b(" + WTRE + r")\b\.?(?=\s*(?:,|und|&|\+|sowie|$|\)))", z, re.I)
    if len(m) >= 2 and re.search(r"(nur|jeweils|betrieb|geöffnet|fährt|faehrt)", z, re.I): return sorted({WT[x.lower()] for x in m}, key=WTO.index)
    return None

def ruhetage_aus(z):
    out = set()
    for m in re.finditer(r"(?:ruhetag[e]?|geschlossen|kein betrieb|chiuso|giorno di chiusura)\s*[:(]?\s*((?:(?:" + WTRE + r")\b\.?\s*(?:,|und|&|/)?\s*)+)", z, re.I):
        for w in re.findall(WTRE, m.group(1), re.I): out.add(WT[w.lower()])
    for m in re.finditer(r"\b(montags|dienstags|mittwochs|donnerstags|freitags|samstags|sonntags)\s+(geschlossen|ruhetag|kein betrieb)", z, re.I): out.add(WT[m.group(1).lower()])
    return out

def zeiten_aus(z):
    """tägliche Zeiten einer Zeile -> dict(erste, letzte_berg, letzte_tal) oder None. Zeilen mit Datum = Sondertag, zählen nicht."""
    zl = z.lower()
    r = {}
    m = re.search(r"(erste|1\.)\s*(berg)?fahrt\w*[^0-9]{0,30}" + ZEIT, zl)
    if m: r["erste"] = f"{int(m.group(3)):02d}:{m.group(4)}"
    m = re.search(r"letzte\w*\s*berg-?\s*(?:und|&|/|\+)\s*talfahrt\w*[^0-9]{0,30}" + ZEIT, zl)
    if m: r["letzte_berg"] = r["letzte_tal"] = f"{int(m.group(1)):02d}:{m.group(2)}"
    else:
        m = re.search(r"letzte\w*\s*bergfahrt\w*[^0-9]{0,30}" + ZEIT, zl)
        if m: r["letzte_berg"] = f"{int(m.group(1)):02d}:{m.group(2)}"
        m = re.search(r"letzte\w*\s*talfahrt\w*[^0-9]{0,30}" + ZEIT, zl)
        if m: r["letzte_tal"] = f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.search(r"(ultima corsa|last (ascent|ride)|dernière montée)[^0-9]{0,30}" + ZEIT, zl)
    if m: r.setdefault("letzte_berg", f"{int(m.group(3)):02d}:{m.group(4)}")
    m = re.search(r"(ultima discesa|last descent|dernière descente)[^0-9]{0,30}" + ZEIT, zl)
    if m: r.setdefault("letzte_tal", f"{int(m.group(3)):02d}:{m.group(4)}")
    s = ZEITSPANNE.search(zl)
    if not s:
        h = re.search(r"(?<![\d.:])([01]?\d|2[0-3])\s*(?:[-–—]|bis)\s*([01]?\d|2[0-3])\s*uhr", zl)
        if h: s = type("M", (), {"group": lambda self, k, h=h: {1: h.group(1), 2: "00", 3: h.group(2), 4: "00"}[k], "start": lambda self, h=h: h.start(), "end": lambda self, h=h: h.end()})()
    if s:
        a, b = f"{int(s.group(1)):02d}:{s.group(2)}", f"{int(s.group(3)):02d}:{s.group(4)}"
        if not (a < b and "05:00" <= a <= "12:00" and "11:00" <= b <= "23:59"): return r or None
        vor = zl[:s.start()]
        if re.search(r"(talfahrt|discesa|descent|descente)\s*:?\s*$", vor): r.setdefault("letzte_tal", b)
        elif re.search(r"(bergfahrt|salita|ascent|montée)\s*:?\s*$", vor): r.setdefault("erste", a); r.setdefault("letzte_berg", b)
        elif re.search(r"letzte\w*\s*berg-?\s*(und|&|/|\+)\s*talfahrt", zl): r.setdefault("erste", a); r.setdefault("letzte_berg", b); r.setdefault("letzte_tal", b)
        elif re.search(r"letzte\w*\s*talfahrt", zl[s.end():s.end() + 40]): r.setdefault("erste", a); r.setdefault("letzte_tal", b)
        else: r.setdefault("erste", a); r.setdefault("betrieb_bis", b)
    return r or None

def urllib_path(u):
    import urllib.parse
    try: return urllib.parse.urlparse(u).path
    except Exception: return ""

def ueberschrift(z):
    return len(z) <= 70 and not re.search(r"\d{1,2}[:.]\d{2}", z) and not z.startswith(("-", "•", "*", "–")) and len(daten_in(z)) == 0

class Bahn:
    def __init__(s, key, name):
        s.key, s.name, s.toks, s.ganz = key, name, tokens(name), norm(name)

# Zeilen mit Datum/Zeit, die nichts mit dem Fahrbetrieb zu tun haben
NICHT_BETRIEB = re.compile(r"(park(en|garage|haus|platz|gebühr)|garage|vorverkauf|verkauf|online|www\.|\bcenter\b|ticketschalter|ticketbüro|abholung|abo\b|saisonkarte|jahreskarte|saisonpass|kassa|kasse\b|kassen|erhältlich|erhaeltlich|"
                           r"gültig|gueltig|buchbar|anmeld|versammlung|volksfreund|zeitung|artikel|presse|rabatt|gutschein|küche|kueche|frühstück|fruehstueck|"
                           r"mittagstisch|abendessen|menü|restaurant|gasthof|gasthaus|alpengasthof|hütte|huette|stube|rezeption|büro|buero|telefon|erreichbar|"
                           r"newsletter|veranstaltung|konzert|\bfest\b|festival|messe|markt|kurs\b|führung|fuehrung|frist|bewerb|copyright|©|bestell|lieferung|"
                           r"geburtstag|jubiläum|jubilaeum|ausstellung|vortrag|gottesdienst|messe|schule|ferien(programm)?\b|kinderbetreuung|skikurs|happy hour|"
                           r"coaster|rodel|seilpark|kletterpark|minigolf|spielplatz|schwimm|bad\b|sauna|shop|verleih|e-?bike-?(verleih|vermietung))", re.I)
BETRIEB = re.compile(r"(betrieb|fahrplan|fahrzeit|öffnungszeit|oeffnungszeit|saison|bergfahrt|talfahrt|geöffnet|geoeffnet|in betrieb|fährt|faehrt|"
                     r"revision|täglich|taeglich|wochenend|stagione|orari|apertura|aperto|in funzione|horaire|ouvert|opening|operat|sommer|winter|"
                     r"bahn|lift|seilbahn|gondel|sessel|funivia|cabinovia|seggiovia)", re.I)
FAHRT = re.compile(r"^(berg|tal)fahrt(en)?\s*:?$", re.I)

def lesen(zeilen, bahnen, url="", titel=""):
    """zeilen: Text der Seite; bahnen: [Bahn] dieser Betreiber-Seite.
    Liefert {bahnkey|'*'|'?': [fakt]}; '*' = Abschnitt „ganzes Gebiet/alle Anlagen“, '?' = ohne Anlage auf der Seite.
    fakt: art = periode | revision | ganzjaehrig | ruhetage | schoenwetter | rad | hunde | preis
      periode: von (oder None = „bis …“), bis, typ (taeglich|wochenende|einzeltag|''), tage, saison, zeiten {erste, letzte_berg, letzte_tal, betrieb_bis}"""
    Z = []
    for z in zeilen:
        z = z.strip()
        if not z: continue
        if Z and Z[-1].endswith(":") and len(Z[-1]) <= 40 and not daten_in(Z[-1]) and not FAHRT.match(Z[-1]): Z[-1] = Z[-1] + " " + z
        else: Z.append(z)
    out = {}
    def add(k, f): out.setdefault(k, []).append(f)
    seite_bahn = None
    tn = norm(url + " " + titel)
    treffer = [b for b in bahnen if nennt(tn, b.toks, b.ganz)]
    if len(treffer) == 1: seite_bahn = treffer[0].key
    datz = [bool(daten_in(z) or re.search(r"\d{1,2}[:.]\d{2}|\d\s*uhr", z, re.I)) for z in Z]
    def hat_daten(i): return any(datz[i + 1:i + 7])
    up = urllib_path(url)
    saison = "sommer" if re.search(r"/(sommer|summer|estate|ete)(/|-|$)", up, re.I) else "winter" if re.search(r"/(winter|inverno|hiver)(/|-|$)", up, re.I) else None
    jahre, anlage = None, seite_bahn or "?"
    tarif = bool(re.search(r"(preis|tarif|ticket|price|prezz|abo|karten)", up, re.I)) and not re.search(r"(betriebszeit|oeffnungszeit|offnungszeit|öffnungszeit|fahrplan|fahrzeit|revision|saison)", up, re.I)
    neben = bool(re.search(r"(angebot|aktivit|erlebnis|event|veranstalt|wander|biken|/bike|kinder|familie|spielpark|show|minigolf|schatzsuche|forscher|eisturm|restaurant|gastro|huette|hütte|alpe|hotel|blog|news|magazin)", up, re.I)) \
            and not re.search(r"(betriebszeit|oeffnungszeit|offnungszeit|öffnungszeit|fahrplan|fahrzeit|revision)", up, re.I)
    fahrt = None             # Abschnitt „Bergfahrten“ / „Talfahrten“
    gruppen = []             # Zeiträume dieses Abschnitts: [[fakt, …], …] (eine Gruppe je Datumszeile)
    zeit_gesehen = set()     # Gruppen, die schon Zeiten bekommen haben
    ok = bool(BETRIEB.search(tn))
    def neuer_abschnitt():
        nonlocal gruppen, fahrt, zeit_gesehen, ok
        gruppen, fahrt, zeit_gesehen, ok = [], None, set(), bool(BETRIEB.search(tn))
    for i, z in enumerate(Z):
        if not z: continue
        zn = norm(z)
        # Jahreszeit-Überschrift (Reiter „Winter / Sommer“ direkt hintereinander nicht zählen)
        if ueberschrift(z) and len(z) <= 40 and (SOMMER.search(z) or WINTER.search(z)) and not ANDERE.search(z) and hat_daten(i):
            nxt = Z[i + 1] if i + 1 < len(Z) else ""
            if not (ueberschrift(nxt) and len(nxt) <= 40 and (SOMMER.search(nxt) or WINTER.search(nxt)) and not JAHR.search(z)):
                saison = "sommer" if SOMMER.search(z) and not WINTER.search(z) else "winter" if WINTER.search(z) and not SOMMER.search(z) else saison
                j = JAHR.search(z)
                jahre = (int(j.group(1)), int("20" + j.group(2)) if j.group(2) else None) if j else None
                anlage = seite_bahn or "?"; neuer_abschnitt(); ok = True
                if not GEBIET.search(z) and not any(nennt(zn, b.toks, b.ganz) for b in bahnen): continue
        if FAHRT.match(z):
            fahrt = z[:3].lower(); gruppen, zeit_gesehen = [], set(); continue
        # Anlagen-Überschrift (nur wenn darunter Daten kommen – Menüs zählen nicht)
        if ueberschrift(z) and not LABEL.match(z) and hat_daten(i):
            genannt = [b for b in bahnen if nennt(zn, b.toks, b.ganz)]
            alt = anlage
            if len(genannt) == 1: anlage = genannt[0].key
            elif len(genannt) > 1: anlage = "mehrere"
            elif GEBIET.search(z): anlage = "*"
            elif ANDERE.search(z) and len(z) <= 50 and (STARK.search(z) or not re.search(r"(bahn|lift|seilbahn|gondel|sessel|funivia|cabinovia|seggiovia)", z, re.I)): anlage = "andere"
            if anlage != alt: neuer_abschnitt()
            if genannt or GEBIET.search(z): ok = True
            j = JAHR.search(z)
            if j and len(z) <= 50: jahre = (int(j.group(1)), int("20" + j.group(2)) if j.group(2) else None)
        if BETRIEB.search(z) and not NICHT_BETRIEB.search(z): ok = True
        if anlage in ("andere", "mehrere"): continue
        ziel = anlage
        if ziel in ("?", "*"):
            g = [b for b in bahnen if nennt(zn, b.toks, b.ganz)]
            if len(g) == 1 and not ANDERE.search(z): ziel = g[0].key
        nb = NICHT_BETRIEB.search(z)
        zr = [] if nb else zeitraeume(z, jahre)
        # „ab 16.04.2027“ = Beginn ohne Ende: nicht verwenden; „bis 06.11.2026“ = Betrieb jetzt bis …
        if len(zr) == 1 and zr[0][0] == zr[0][1]:
            ds_ = daten_in(z)
            vor_ = z[:ds_[0][0]] if ds_ else ""
            vor_ = re.sub(r"(" + WTRE + r")\.?,?\s*$", "", vor_.rstrip(), flags=re.I).rstrip()
            if re.search(r"\b(ab|from|dal|dès|des|seit)\s*$", vor_, re.I): zr = []
            elif re.search(r"\b(bis|until|fino al|jusqu'au)\s*$", vor_, re.I) and BETRIEB.search(z): zr = [(None, zr[0][1])]
        if not zr and not nb:
            m = re.search(r"\b(bis|until|fino al|jusqu'au)\s+(?:(?:" + WTRE + r")\.?,?\s*)?$", z[:daten_in(z)[0][0]] if daten_in(z) else "", re.I)
            ds = daten_in(z)
            if m and len(ds) == 1 and ds[0][4] and BETRIEB.search(z):
                try: zr = [(None, datetime.date(ds[0][4], ds[0][3], ds[0][2]))]
                except ValueError: pass
        # „Start Revision: …“ + „Ende Revision: …“
        if len(zr) == 1 and zr[0][0] and zr[0][0] == zr[0][1] and re.search(r"(start|beginn)\w*\s*(der\s*)?revision|revisionsbeginn|revision\w*\s*(start|beginn)", z, re.I):
            for k in range(i + 1, min(i + 4, len(Z))):
                if re.search(r"ende\w*\s*(der\s*)?revision|revisionsende|revision\w*\s*ende", Z[k], re.I):
                    z2 = zeitraeume(Z[k], jahre)
                    if len(z2) == 1 and z2[0][0] >= zr[0][0] and (z2[0][0] - zr[0][0]).days <= 120:
                        zr = [(zr[0][0], z2[0][0])]; Z[k] = ""
                    break
        if zr:
            nxt = Z[i + 1] if i + 1 < len(Z) else ""
            vorz = Z[i - 1] if i else ""
            rev = REVISION.search(z) or (nxt and not daten_in(nxt) and len(nxt) <= 80 and REVISION.search(nxt)) or \
                  (vorz and ueberschrift(vorz) and REVISION.search(vorz) and not (i > 1 and datz[i - 2]))
            if not ok and not rev: continue
            # Preis-/Ticketseiten: nur Zeilen, die selbst von Saison oder Betrieb sprechen
            if tarif and not rev and not re.search(r"(saison|betrieb|geöffnet|geoeffnet|fahrplan|in funzione|stagione)", z, re.I): continue
            if neben and ziel in ("?", "*"): continue
            vor = Z[i - 1] if i and not daten_in(Z[i - 1]) and len(Z[i - 1]) <= 60 else ""
            typ = ""
            for t_ in (z, vor):
                if WOCHENENDE.search(t_): typ = "wochenende"; break
                if re.search(r"täglich|taeglich|daily|tutti i giorni|tous les jours", t_, re.I): typ = "taeglich"; break
            tg = tage_aus(z) or (tage_aus(nxt) if nxt and not daten_in(nxt) else None)
            if tg and len(tg) == 7: tg = None
            grp = []
            for von, bis in zr:
                if rev:
                    if von and von != bis: add(ziel, {"art": "revision", "von": von.isoformat(), "bis": bis.isoformat(), "zeile": z[:160]})
                    continue
                if von == bis and not re.search(r"(betrieb|geöffnet|geoeffnet|fährt|faehrt|offen|in funzione|aperto|fahrten)", z + " " + vor, re.I): continue
                ref = von or bis
                m1, m2 = (von or bis).month, bis.month
                mon = "sommer" if 4 <= m1 <= 10 and 5 <= m2 <= 11 and (von is None or von.year == bis.year) else "winter" if (m1 >= 10 or m1 <= 1) and (m2 >= 12 or m2 <= 5) else None
                sz = saison if saison and (mon is None or mon == saison) else mon
                ziel_f = ziel
                # „Wintersaison 2026/27: …“ ohne Anlage = ganzes Skigebiet
                if ziel == "?" and sz == "winter" and re.search(r"(wintersaison|skisaison|skibetrieb|wintersport|saison\s*20\d\d\s*/)", z + " " + vor, re.I): ziel_f = "*"
                f = {"art": "periode", "von": von.isoformat() if von else None, "bis": bis.isoformat(), "typ": "einzeltag" if von == bis else typ,
                     "saison": sz, "zeiten": {}, "zeile": z[:160]}
                if tg: f["tage"] = tg
                add(ziel_f, f); grp.append(f)
            if grp: gruppen.append(grp)
            continue
        zt = zeiten_aus(z) if not nb else None
        if zt and ((tarif and not re.search(r"(berg|tal)fahrt|betrieb|fahrzeit|geöffnet|geoeffnet", z, re.I)) or (neben and ziel in ("?", "*"))): zt = None
        if zt and not daten_in(z):
            tg = tage_aus(z) or (tage_aus(Z[i - 1]) if i and not re.search(r"\d{1,2}[:.]\d{2}|\d\s*uhr", Z[i - 1], re.I) else None)
            if fahrt == "ber" and "betrieb_bis" in zt: zt["letzte_berg"] = zt.pop("betrieb_bis")
            if fahrt == "tal":
                zt = {"letzte_tal": zt.get("betrieb_bis") or zt.get("letzte_tal")} if (zt.get("betrieb_bis") or zt.get("letzte_tal")) else {}
            if not zt: continue
            if tg and len(tg) < 7: zt["tage"] = tg
            # an die letzte Datumszeile hängen; frühere Gruppen ohne Zeiten bekommen sie auch (Zeiten stehen oft einmal am Ende)
            if gruppen:
                ziele = [gruppen[-1]] + [g for k_, g in enumerate(gruppen[:-1]) if k_ not in zeit_gesehen]
                for g in ziele:
                    for f in g:
                        for k2, v2 in zt.items():
                            if k2 != "tage": f["zeiten"].setdefault(k2, v2)
                        if tg and "tage" not in f["zeiten"]: f["zeiten"]["tage"] = tg
                zeit_gesehen |= set(range(len(gruppen)))
            elif ok:
                add(ziel, {"art": "zeiten", "saison": saison, "zeile": z[:160]} | zt)
            continue
        if nb: continue
        if GANZJ.search(z) and not ANDERE.search(z) and BETRIEB.search(z): add(ziel, {"art": "ganzjaehrig", "zeile": z[:160]})
        rt = ruhetage_aus(z)
        if rt: add(ziel, {"art": "ruhetage", "tage": sorted(rt, key=WTO.index), "saison": saison, "zeile": z[:160]})
        if SCHOEN.search(z): add(ziel, {"art": "schoenwetter", "zeile": z[:160]})
        if RAD.search(z) and len(z) < 400: add(ziel, {"art": "rad", "wert": not NEIN.search(z), "zeile": z[:160]})
        if HUND.search(z) and len(z) < 400 and re.search(r"(erlaubt|willkommen|kostenlos|gratis|frei|mitnehmen|mitgenommen|dürfen|duerfen|transport|fahren|ammess|consentit|allowed|welcome|nicht|verboten|kein)", z, re.I):
            add(ziel, {"art": "hunde", "wert": not NEIN.search(z), "zeile": z[:160]})
    # Preis Berg- und Talfahrt Erwachsene: Zeile mit Betrag oder Tabelle (Spalte „Berg- und Talfahrt“, Zeile „Erwachsene“)
    for i, z in enumerate(Z):
        if not z or not PREIS.search(z): continue
        b = BETRAG.search(z)
        if b and ERW.search(" ".join(Z[max(0, i - 2):i + 2])):
            add(seite_bahn or "?", {"art": "preis", "wert": (b.group(1) or b.group(2)).replace(",-", ",00").replace(".-", ".00"), "waehrung": "CHF" if re.search(r"chf|fr\.", z, re.I) else "EUR", "zeile": z[:160]})
            break
        # Tabelle: Kopf „Einfache Fahrt | Berg- und Talfahrt“, dann „Erwachsene“ und zwei Beträge
        if len(z) <= 30 and i + 4 < len(Z):
            kopf = [x for x in Z[max(0, i - 2):i + 1] if len(x) <= 30]
            for k in range(i + 1, min(i + 4, len(Z))):
                if ERW.search(Z[k]):
                    werte = [x for x in Z[k + 1:k + 4] if re.fullmatch(r"(?:€|eur|chf|fr\.)?\s*\d{1,3}[.,](?:\d{2}|-)\s*(?:€|eur|chf)?", x, re.I)]
                    sp = len(kopf) - 1
                    if len(werte) >= len(kopf) and sp >= 0:
                        w = re.search(r"\d{1,3}[.,](?:\d{2}|-)", werte[sp]).group(0)
                        add(seite_bahn or "?", {"art": "preis", "wert": w.replace(",-", ",00").replace(".-", ".00"), "waehrung": "CHF" if re.search(r"\.ch(/|$)", url) else "EUR", "zeile": " | ".join(kopf + [Z[k]] + werte[:2])[:160]})
                    break
            else: continue
            break
    return out

def stand_datum(zeilen):
    """„Stand: 12.03.2026“ / „aktualisiert am …“ auf der Seite"""
    for z in zeilen:
        m = re.search(r"(stand|aktualisiert|zuletzt geändert|last update[d]?|aggiornato)\s*(am|:|il|on)?\s*:?\s*(\d{1,2})\.\s?(\d{1,2})\.\s?(20\d{2})", z, re.I)
        if m:
            try: return datetime.date(int(m.group(5)), int(m.group(4)), int(m.group(3))).isoformat()
            except ValueError: pass
    return None
