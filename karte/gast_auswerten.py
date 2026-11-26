#!/usr/bin/env python3
"""Ergebnisse des Ablaufs „Gaststätten Österreich“ zusammenführen und Berichte schreiben.
Aufruf: gast_auswerten.py <artefakte-ordner> <bericht-ordner> <arbeits-ordner>
Schreibt: <arbeit>/auto.json, daten/gast/ki-cache.json, <bericht>/messung.md (Regeln gegen KI), stichprobe.md (KI-Ergebnisse
mit Zitat zum Nachprüfen), webseiten.md (neu gefundene Webseiten, OSM-Webseiten, die nicht passen), pruef-*.md"""
import ast, glob, json, os, random, re, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gaststaetten as g

def main(a, rep, w):
    os.makedirs(rep, exist_ok=True); os.makedirs(w, exist_ok=True)
    ok = []
    for p in sorted(glob.glob(f"{a}/gelesen-*/auto-*.json")): ok += json.load(open(p, encoding="utf-8"))
    cache = {}
    if os.path.exists("daten/gast/ki-cache.json"): cache = json.load(open("daten/gast/ki-cache.json", encoding="utf-8"))
    for p in glob.glob(f"{a}/gelesen-*/pruef/ki-cache.json"): cache.update(json.load(open(p, encoding="utf-8")))
    # Nachprüfung: KI-Ergebnisse mit der aktuellen Zitat-Prüfung (Tag für Tag) noch einmal kontrollieren.
    # Was nicht besteht und nicht durch eine zweite Quelle bestätigt ist, kommt auf die Prüfliste statt in die Karte.
    nach = Counter()
    for i, e in enumerate(ok):
        if e.get("art") != "ki" or not e.get("woche"): continue
        ant = (cache.get(e.get("url")) or {}).get("a")
        grund = "KI: Antwort nicht mehr vorhanden" if not isinstance(ant, dict) else g.ki_pruefen(ant, str(ant.get("beleg") or ""))[4]
        if not grund: nach["ok"] += 1; continue
        if e.get("sicherheit") == "bestaetigt": nach["bestaetigt_behalten"] += 1; continue
        nach["verworfen"] += 1
        ok[i] = {k: v for k, v in e.items() if k in ("t", "id", "webseite", "webseite_art", "webseite_beleg", "osm_webseite_falsch")} | {"zurueck": grund[:200]}
    print(f"Nachprüfung KI: {dict(nach)}")
    json.dump(ok, open(f"{w}/auto.json", "w", encoding="utf-8"), ensure_ascii=False)
    if cache:
        os.makedirs("daten/gast", exist_ok=True)
        json.dump(cache, open("daten/gast/ki-cache.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    for f in glob.glob(f"{rep}/pruef-*.md"): os.remove(f)
    pr = defaultdict(list)
    for p in glob.glob(f"{a}/gelesen-*/pruef/pruef-*.md"):
        t = open(p, encoding="utf-8").read().split("\n\n", 2); pr[os.path.basename(p)].append(t[2] if len(t) > 2 else "")
    for k, v in pr.items():
        open(f"{rep}/{k}", "w", encoding="utf-8").write(f"# Prüfliste {k[6:-3]} ({g.HEUTE})\n\nNicht eindeutig lesbar (auch mit KI nicht).\n\n" + "\n".join(v))
    # Messung: Zähler aller Teile
    st = Counter()
    for p in glob.glob(f"{a}/gelesen-*/sammeln-*.txt"):
        for l in open(p, encoding="utf-8"):
            m = re.search(r"Abläufe: (\{.*\})", l)
            if m: st.update(ast.literal_eval(m.group(1)))
    inv = {(x["t"], x["id"]): x for x in json.load(open(glob.glob(f"{a}/bestand/neu.json")[0], encoding="utf-8"))}
    mz = st["mess_gleich"] + st["mess_anders"] + st["mess_ki_nichts"]
    with open(f"{rep}/messung.md", "w", encoding="utf-8") as fo:
        fo.write(f"# Gaststätten Österreich – Messung ({g.HEUTE})\n\nAuf Seiten, die die festen Regeln eindeutig lesen, wurde zusätzlich die KI gefragt.\n\n")
        fo.write(f"- verglichen: {mz}\n- KI gleich wie Regeln: {st['mess_gleich']}" + (f" ({100 * st['mess_gleich'] // mz} %)" if mz else "") +
                 f"\n- KI anders: {st['mess_anders']}\n- KI ohne Ergebnis (verworfen oder nichts): {st['mess_ki_nichts']}\n\n")
        fo.write("## Zähler aller Teile\n\n" + "\n".join(f"- {k}: {v}" for k, v in sorted(st.items())) + "\n")
    ki = [e for e in ok if e.get("art") == "ki" and e.get("woche")]
    random.seed(7)
    with open(f"{rep}/stichprobe.md", "w", encoding="utf-8") as fo:
        fo.write(f"# Stichprobe KI-gelesener Gaststätten ({g.HEUTE})\n\n{len(ki)} von der KI gelesen; zufällig 60 zum Nachprüfen.\n\n")
        for e in random.sample(ki, min(60, len(ki))):
            x = inv.get((e["t"], e["id"]), {})
            fo.write(f"## {x.get('n')} ({e['t']}/{e['id']})\n{e['url']}\n**{g.text_kurz(e['woche'], e['ruhetage'])}** ({e['sicherheit']})\n> {e['belege'][0][:400]}\n\n")
    neu = [e for e in ok if e.get("webseite_art") in ("mail", "cc", "wikidata")]
    falsch = [e for e in ok if e.get("osm_webseite_falsch")]
    with open(f"{rep}/webseiten.md", "w", encoding="utf-8") as fo:
        fo.write(f"# Gaststätten Österreich – Webseiten ({g.HEUTE})\n\n{len(neu)} Webseiten neu gefunden (nicht in OpenStreetMap), "
                 f"{len(falsch)} OSM-Webseiten passen nicht zum Lokal.\n\n## Neu gefunden\n\n")
        for e in sorted(neu, key=lambda e: inv.get((e["t"], e["id"]), {}).get("n", "")):
            x = inv.get((e["t"], e["id"]), {})
            fo.write(f"- {x.get('n')} ({x.get('plz') or ''} {x.get('ort') or ''}): {e['webseite']} – {e['webseite_art']}, Beleg {e.get('webseite_beleg')}\n")
        fo.write("\n## OSM-Webseite passt nicht\n\n")
        for e in falsch:
            x = inv.get((e["t"], e["id"]), {})
            fo.write(f"- {x.get('n')} ({e['t']}/{e['id']}): {x.get('w')} – {e['osm_webseite_falsch']}\n")
    print(f"{len(ok)} Einträge, {sum(1 for e in ok if e.get('woche'))} mit Zeiten ({len(ki)} durch KI), {len(neu)} neue Webseiten, "
          f"{len(falsch)} OSM-Webseiten passen nicht, Messung {st['mess_gleich']}/{mz} gleich")

if __name__ == "__main__":
    main(*sys.argv[1:4])
