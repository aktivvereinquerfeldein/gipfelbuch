#!/usr/bin/env python3
"""Probelauf: lokale KI (Ollama) liest Textstellen, in denen eine Hütte erwähnt wird, und sagt, ob man dort essen/trinken kann.
Jede Angabe braucht ein wörtliches Zitat; das Skript prüft, dass es im Text steht, nahe am Hüttennamen liegt und ein Einkehr-Wort enthält.
Aufruf: ki_probe.py <treffer.json> <alle.json> <modell1,modell2> <ergebnis.json>"""
import json, re, sys, time, unicodedata, urllib.request, collections
OLLAMA = "http://127.0.0.1:11434/api/chat"
ANW = """Du liest einen kurzen Ausschnitt einer Webseite (Tourismusportal, Tourenbeschreibung). Darin wird eine Hütte/Alpe/Alm genannt.
Frage: Steht im Text ausdrücklich, ob man in GENAU DIESER Hütte essen oder trinken kann?
Regeln:
- Nur was wörtlich dasteht. Nichts aus Allgemeinwissen, nichts vermuten.
- Bezieht sich die Einkehr auf einen anderen Ort oder Gasthof im Text, dann "unbekannt".
- "ja": man kann dort essen (Einkehr, Jause, Küche, bewirtet, Gasthaus, Restaurant, Mittagessen, Kuchen).
- "nur_getraenke": ausdrücklich nur Getränke.
- "nein": unbewirtschaftet, Selbstversorger, keine Bewirtung.
- "geschlossen", "derzeit geschlossen" oder Öffnungszeiten heißen NICHT "nein" (das ist nur der aktuelle Zustand).
- Auch vorsichtige Formulierungen zählen als "ja": "je nach Öffnungszeiten einkehren", "lädt zur Einkehr ein",
  "Jause genießen", "bietet Schmankerl", "bewirtschaftet".
- sonst "unbekannt".
Beispiele:
  "Die Alpe Uga lädt zu einer Brettljause ein." -> {"verpflegung":"ja","beleg":"Die Alpe Uga lädt zu einer Brettljause ein."}
  "Vorbei an der Alpe Uga geht es zum Gipfel." -> {"verpflegung":"unbekannt","beleg":""}
  "Die Alpe Uga ist eine Selbstversorgerhütte." -> {"verpflegung":"nein","beleg":"Die Alpe Uga ist eine Selbstversorgerhütte."}
  "Alpe Uga: derzeit geschlossen" -> {"verpflegung":"unbekannt","beleg":""}
- beleg: ein exaktes zusammenhängendes Zitat aus dem Text (max. 160 Zeichen), Zeichen für Zeichen kopiert, das die Antwort zeigt.
Antworte NUR mit JSON: {"verpflegung":"ja|nur_getraenke|nein|unbekannt","beleg":"..."}"""
EINK = re.compile(r"einkehr|bewirt|jause|jausen|essen|speis|küche|getränk|kaffee|kuchen|gastst|gasthaus|restaurant|stärkung|sennerei|käse|bewirtschaft|stube|schmankerl|gericht|imbiss", re.I)
# "geschlossen" allein heißt nur: gerade zu (Winterstatus der Portale) – das ist KEIN "nein"
NEIN = re.compile(r"unbewirt|selbstversorg|keine (bewirt|einkehr|verpflegung)|keine einkehrmöglichkeit|nicht bewirt|ohne bewirt|kein ausschank", re.I)
POS = re.compile(r"jause|schmankerl|kuchen|speisen|küche|gerichte|imbiss|kaffee|mittagessen|einkehr(?!möglichkeit)|bewirtet|ist bewirtschaftet", re.I)
EVENT = re.compile(r"einladung|veranstaltung|weihnachtsfeier|feier\b|konzert|workshop|spielenachmittag|kurs\b|markt\b|festival|tickets", re.I)


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower().replace("–", "-").replace("—", "-").replace(" ", " ")
    return re.sub(r"\s+", " ", s.replace("|", " ")).strip()


ALP = re.compile(r"alp|hütte|huette|haus|alm|stüb|stub|älpele|hof|säss|maisäß|berg|bahn|jausen", re.I)


def km(a, b):
    import math
    return math.hypot((a[0] - b[0]) * 75.5, (a[1] - b[1]) * 111.2)


def lage_ok(hut, info):
    """True: Seite liegt bei der Hütte, False: woanders, None: Seite hat keine Lage"""
    if not info: return None
    if info.get("track"): return min(km(hut, p) for p in info["track"]) <= 1.0
    if info.get("punkt"): return km(hut, info["punkt"]) <= 1.5
    return None


def frage(modell, name, text):
    body = {"model": modell, "stream": False, "format": "json", "think": False, "options": {"temperature": 0, "num_ctx": 2048},
            "messages": [{"role": "system", "content": ANW}, {"role": "user", "content": f"Hütte: {name}\n\nText:\n{text}"}]}
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    try:
        return json.loads(d["message"]["content"])
    except Exception:
        return None


def pruefen(a, name, text, titel=False):
    if not isinstance(a, dict): return "unbekannt", "unlesbar"
    v, b = a.get("verpflegung"), a.get("beleg") or ""
    if v not in ("ja", "nur_getraenke", "nein"): return "unbekannt", ""
    nt, nb, nn = norm(text), norm(b), norm(name)
    teile = [x.strip() for x in re.split(r"\.\.\.|…", nb) if x.strip()]
    if len(nb) < 8 or not teile or not all(len(x) >= 8 and x in nt for x in teile): return "unbekannt", "zitat steht nicht im text"
    nb = max(teile, key=len); pb = nt.find(nb)
    pn = [m.start() for m in re.finditer(re.escape(nn), nt)]
    if nn not in nb and (not pn or min(abs(p - pb) for p in pn) > 350): return "unbekannt", "zitat zu weit weg vom namen"
    if not titel and nn not in nb: return "unbekannt", "zitat nennt die hütte nicht"   # sonst Gefahr: Einkehr bei einer anderen Hütte im selben Satz
    ohne = nb.replace(nn, " ")   # Einkehr-Wort darf nicht nur im Namen stecken („Gasthaus Mühle“)
    if v == "nein" and not NEIN.search(ohne): return "unbekannt", "nein ohne beleg-wort"
    if v == "nein" and POS.search(re.sub(r"unbewirt\w*|keine \w+", " ", ohne)): return "unbekannt", "widerspruch"
    if v != "nein" and not EINK.search(ohne): return "unbekannt", "ohne einkehr-wort"
    if v != "nein" and NEIN.search(ohne): return "unbekannt", "widerspruch"
    return v, b


if __name__ == "__main__":
    snippets = json.load(open(sys.argv[1]))
    rows = {r["id"]: r for r in json.load(open(sys.argv[2]))}
    modelle = sys.argv[3].split(",")
    lage = json.load(open(sys.argv[5])) if len(sys.argv) > 5 else {}
    teil, teile_n = (int(x) for x in (sys.argv[6] if len(sys.argv) > 6 else "0/1").split("/"))
    snippets = {k: v for i, (k, v) in enumerate(sorted(snippets.items())) if i % teile_n == teil}
    erg = {}
    for m in modelle:
        t0 = time.time(); res, ende = {}, {}
        for hid, lst in snippets.items():
            r = rows[int(hid)]; name = r["name"]; best = ("unbekannt", "", "")
            g = r.get("geom"); g = json.loads(g) if isinstance(g, str) else g; hut = g["coordinates"]
            frei = []
            for s in lst:
                if EVENT.search(s["url"]) or EVENT.search(s["text"][:150]): continue   # Veranstaltung, keine Aussage über den Betrieb
                ok = lage_ok(hut, lage.get(s["url"]))
                if ok is False: continue   # Seite liegt woanders (andere Hütte gleichen Namens)
                if ok is None and (s.get("mehrdeutig") or not ALP.search(name)): continue   # Allerweltsname ohne Lage: zu unsicher
                frei.append(s)
            for s in frei[:3]:
                a = frage(m, name, s["text"]); v, b = pruefen(a, name, s["text"], s.get("titel"))
                res.setdefault(hid, []).append({"url": s["url"], "roh": a, "v": v, "b": b})
                if v != "unbekannt": best = (v, b, s["url"]); break
            ende[hid] = best
        dauer = time.time() - t0
        # Auswertung gegen schon geprüfte Hütten (fertig / auto mit bekannter Verpflegung)
        c = collections.Counter(); fehler, neu = [], []
        for hid in snippets:
            r = rows[int(hid)]; rec = (r.get("daten") or {}).get("recherche"); soll = r["verpflegung"]; v = ende[hid][0]
            if rec in ("fertig", "auto") and soll != "unbekannt":
                if v == "unbekannt": c["geprueft_keine_aussage"] += 1
                elif v == soll or (soll == "ja" and v == "nur_getraenke"): c["geprueft_richtig"] += 1
                else: c["geprueft_FALSCH"] += 1; fehler.append((r["name"], soll, v, ende[hid][1], ende[hid][2]))
            else:
                c["neu_" + v] += 1
                if v != "unbekannt": neu.append((r["name"], v, ende[hid][1], ende[hid][2]))
        erg[m] = {"dauer_s": round(dauer), "zahlen": dict(c), "fehler": fehler, "neu": neu, "res": res}
        print(f"== {m}: {round(dauer)} s, {dict(c)}", flush=True)
        for f in fehler: print("  FALSCH:", f)
        for f in neu: print("  NEU:", f)
    json.dump(erg, open(sys.argv[4], "w"), ensure_ascii=False, indent=1)
