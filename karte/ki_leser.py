#!/usr/bin/env python3
"""KI-Leser für Hütten-Webseiten: ein kleines Claude-Modell liest den kurzen Auszug einer Hütten-Webseite und gibt
Öffnungszeiten als festes Format zurück. Nur Werte, die wörtlich im Text stehen, jeder Wert mit Belegstelle.
Das Skript prüft danach selbst, dass jede Belegstelle wirklich im Auszug steht und die Daten zur Belegstelle passen –
sonst wird der Wert verworfen. Braucht das GitHub-Secret ANTHROPIC_API_KEY (eigener API-Schlüssel, belastet kein Chat-Limit).
"""
import json, os, re, time, unicodedata, urllib.request

MODELL = os.environ.get("KI_MODELL", "claude-haiku-4-5")
URL = "https://api.anthropic.com/v1/messages"
WT = ["mo", "di", "mi", "do", "fr", "sa", "so"]
MON = {"jän": 1, "jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "may": 5, "jun": 6, "jul": 7, "aug": 8,
       "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12}

ANWEISUNG = """Du liest den Textauszug der Webseite einer Berghütte, Alm, Alpe oder eines Berggasthauses.
Gib NUR ein JSON-Objekt zurück, nichts sonst. Regeln:
- Nur Angaben, die wörtlich im Text stehen und sich auf DIESE Hütte beziehen (nicht auf andere Betriebe, Lifte, Events).
- Nichts schätzen, nichts aus Allgemeinwissen ergänzen. Im Zweifel weglassen.
- Schließzeiten, Betriebsurlaub, Feiertage, einzelne Termine oder Veranstaltungen sind KEINE Saison.
- Eine Saison ist ein durchgehender Öffnungszeitraum (z. B. "20. Juni bis 5. September", "Mitte Mai bis Ende Oktober").
  Bei ungefähren Angaben wie "Mitte Mai" oder "Ende Oktober" setze ungefaehr=true und nimm 15. bzw. letzten Tag.
  Bei nur Monaten ("Juni bis September") ungefaehr=true, von = 1. des ersten Monats, bis = letzter Tag des letzten Monats.
- ruhetage nur, wenn ein ganzer Wochentag ohne Bedingung geschlossen ist. Bedingte Angaben ("außer im Juli", "ab 17 Uhr")
  gehören als Text in "zeiten", nicht in ruhetage.
- "zeiten": kurze sachliche Zusammenfassung der täglichen Öffnungszeiten/Öffnungstage in eigenen Worten, max. 80 Zeichen, sonst "".
- verpflegung: "ja" (Essen), "nur_getraenke", "nein" (Selbstversorger/unbewirtschaftet) oder "unbekannt".
- jahr: das Jahr, für das die Saison gilt, nur wenn im Text genannt, sonst null.
- Für jede Angabe ein Beleg: ein exaktes, zusammenhängendes Zitat aus dem Text (max. 120 Zeichen), Zeichen für Zeichen kopiert.
Format:
{"verpflegung":"...","beleg_verpflegung":"...",
 "saisons":[{"art":"sommer|winter","von":"MM-TT","bis":"MM-TT","jahr":2027,"ungefaehr":false,"beleg":"..."}],
 "ganzjaehrig":false,"beleg_ganzjaehrig":"",
 "ruhetage":["mo"],"beleg_ruhetage":"",
 "zeiten":"","beleg_zeiten":""}"""

def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("–", "-").replace("—", "-").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()

def steht_drin(beleg, text):
    b = norm(beleg)
    return len(b) >= 4 and b in norm(text)

def anfrage(text, name, versuche=4):
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    body = {"model": MODELL, "max_tokens": 500, "temperature": 0, "system": ANWEISUNG,
            "messages": [{"role": "user", "content": f"Hütte: {name}\n\nTextauszug:\n{text}"}]}
    for i in range(versuche):
        try:
            req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
                "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.load(r)
            t = "".join(c.get("text", "") for c in d.get("content", []))
            m = re.search(r"\{.*\}", t, re.S)
            return json.loads(m.group(0)) if m else None, d.get("usage", {})
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529): time.sleep(5 * (i + 1)); continue
            raise
        except (json.JSONDecodeError, TimeoutError, urllib.error.URLError):
            time.sleep(3 * (i + 1))
    return None, {}

def _tm(s):
    m = re.match(r"^(\d{2})-(\d{2})$", s or "")
    if not m: return None
    mo, ta = int(m.group(1)), int(m.group(2))
    return (mo, ta) if 1 <= mo <= 12 and 1 <= ta <= 31 else None

def _datum_im_beleg(mo, ta, beleg, ungefaehr):
    """steht der Tag/Monat wirklich im Beleg? (Ziffern oder Monatsname)"""
    b = norm(beleg)
    if re.search(rf"(?<!\d)0?{ta}\.\s?0?{mo}\.", b): return True
    namen = [k for k, v in MON.items() if v == mo]
    if any(n in b for n in namen):
        return ungefaehr or re.search(rf"(?<!\d)0?{ta}\.?\s*(?:{'|'.join(namen)})", b) is not None
    return False

def pruefen(ant, text, gasthaus=False):
    """übernimmt nur, was belegt ist; gibt (felder, grund)"""
    if not isinstance(ant, dict): return None, "KI-Antwort unlesbar"
    f, belege = {}, {}
    v = ant.get("verpflegung")
    if v in ("ja", "nur_getraenke", "nein") and steht_drin(ant.get("beleg_verpflegung"), text):
        if not (v == "nein" and gasthaus):
            f["verpflegung"] = v; belege["verpflegung"] = ant["beleg_verpflegung"]
    saisons, jahre = [], set()
    for s in ant.get("saisons") or []:
        if not isinstance(s, dict) or s.get("art") not in ("sommer", "winter"): continue
        a, b = _tm(s.get("von")), _tm(s.get("bis"))
        bel, ung = s.get("beleg") or "", bool(s.get("ungefaehr"))
        if not (a and b and steht_drin(bel, text)): continue
        if not (_datum_im_beleg(a[0], a[1], bel, ung) and _datum_im_beleg(b[0], b[1], bel, ung)): continue
        tage = ((b[0] - a[0]) * 30.4 + (b[1] - a[1])) % 365
        if tage < 30: continue
        if re.search(r"(geschlossen|betriebsurlaub|ruhezeit|closed|umbau)", norm(bel)): continue
        x = {"art": s["art"], "von": s["von"], "bis": s["bis"]}
        if ung: x["ungefaehr"] = True
        saisons.append(x); belege.setdefault("saisons", []).append(bel)
        if isinstance(s.get("jahr"), int) and str(s["jahr"]) in bel: jahre.add(s["jahr"])
    if len({x["art"] for x in saisons}) < len(saisons): return None, "mehrere Saisons derselben Art"
    o = {}
    if saisons:
        o["saisons"] = saisons
        if len(jahre) == 1: o["jahr"] = jahre.pop()
    if ant.get("ganzjaehrig") is True and steht_drin(ant.get("beleg_ganzjaehrig"), text) and not saisons:
        o["ganzjaehrig"] = True; belege["ganzjaehrig"] = ant["beleg_ganzjaehrig"]
    rt = [t for t in (ant.get("ruhetage") or []) if t in WT]
    if rt and steht_drin(ant.get("beleg_ruhetage"), text):
        lang = {"mo": "montag", "di": "dienstag", "mi": "mittwoch", "do": "donnerstag", "fr": "freitag", "sa": "samstag", "so": "sonntag"}
        bel = norm(ant["beleg_ruhetage"])
        if all(lang[t] in bel or re.search(rf"\b{t}\b", bel) for t in rt) and "ruhetag" in bel:
            o["ruhetage"] = sorted(set(rt), key=WT.index); belege["ruhetage"] = ant["beleg_ruhetage"]
    z = (ant.get("zeiten") or "").strip()[:80]
    if z and steht_drin(ant.get("beleg_zeiten"), text):
        o["zeiten"] = z; belege["zeiten"] = ant["beleg_zeiten"]
    if not f and not o: return None, "nichts Belegtes gefunden"
    if o: f["oeffnung"] = o
    f.setdefault("verpflegung", "unbekannt")
    namen = {"mo": "Montag", "di": "Dienstag", "mi": "Mittwoch", "do": "Donnerstag", "fr": "Freitag", "sa": "Samstag", "so": "Sonntag"}
    for s in saisons:
        j = str(o["jahr"]) if o.get("jahr") else ""
        txt = f"{'ca. ' if s.get('ungefaehr') else ''}{s['von'][3:]}.{s['von'][:2]}.–{s['bis'][3:]}.{s['bis'][:2]}.{j}"
        f["saison_" + s["art"]] = txt
    if o.get("ruhetage"): f["ruhetage"] = ", ".join(namen[t] for t in o["ruhetage"])
    if o.get("zeiten"): f["zeiten"] = o["zeiten"]
    f["belege"] = belege
    return f, None
