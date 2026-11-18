#!/usr/bin/env python3
"""Vorab berechnete Tourenvorschläge prüfen, nach R2 hochladen und freischalten (Ablauf „Vorschläge berechnen“).
Aufruf: vorschlaege_einspielen.py <Ordner mit Artefakten> <stand> <Regionen> [Test-Rechteck]
- Jede Datei <ix>_<iy>.json.gz ist ein fertig berechnetes Kartenstück. Fehlende Kartenstücke bleiben einfach weg (dort rechnet die App selbst).
- Der neue Stand übernimmt zuerst alle Kartenstücke des bisherigen Stands (andere Regionen bleiben erhalten), dann kommen die neuen darüber.
- Sicherung je Region (ab 15.11.2026): hat eine Region weniger als die Hälfte der Touren des letzten Laufs, mehr als 30 % Kartenstücke
  ohne Ergebnis oder fehlende Teile, bleibt für diese Region der bisherige Stand; die übrigen Regionen werden trotzdem freigeschaltet.
- Nachrechnen (NACH = Liste von Kartenstücken, ab 18.11.2026): nur diese Stücke kommen neu dazu, die Zahlen der Region werden zum
  bisherigen Stand addiert; die Prüfungen auf Touren-Anzahl und Anteil leerer Stücke entfallen (vorher gab es dort ja nichts).
- Nichts wird gelöscht; alte Stände bleiben liegen (wenige MB)."""
import glob, gzip, json, os, subprocess, sys, time
ART, STAND, REGIONEN = sys.argv[1], sys.argv[2], [r.strip() for r in sys.argv[3].split(",") if r.strip()]
TEST = (sys.argv[4] if len(sys.argv) > 4 else "").strip()
B = os.environ.get("BUCKET", "qfeaktivfotos")
NACH = bool(os.environ.get("NACH", "").strip()) and not TEST
def rc(*a, check=True): return subprocess.run(["rclone", *a], check=check, capture_output=True, text=True)
os.makedirs("daten/vorschlaege", exist_ok=True)
# alle Sportarten, die sgVorab liefert (v2083: Laufen, Klettersteig, Rad dazu). Vorher kannte die Prüfung nur die ersten drei und
# sortierte Kartenstücke aus, deren erste Tour z. B. Laufen war (Lauf 43948729357: 150 Dateien „unbrauchbar“).
SPORTARTEN = ("wandern", "bergsteigen", "bh", "laufen", "rad", "klettersteig")
regs = ["Test"] if TEST else REGIONEN
alt = {}
if not TEST:
    if rc("copyto", f"r2:{B}/karte/touren/aktuell.json", "alt.json", check=False).returncode == 0 and os.path.exists("alt.json"):
        try: alt = json.load(open("alt.json"))
        except Exception: alt = {}
os.makedirs("neu", exist_ok=True)
info, schlecht = {}, []
for r in regs:
    st = [json.load(open(f)) for f in sorted(glob.glob(f"{ART}/touren-{r}-*/stat-*.json"))]
    dateien = sorted(f for f in glob.glob(f"{ART}/touren-{r}-*/*.json.gz"))
    von = st[0]["von"] if st else 0
    n_zellen = sum(s["zellen"] for s in st); fertig = sum(s["fertig"] for s in st); fehler = sum((s["fehler"] for s in st), [])
    touren = sum(s["touren"] for s in st); je = {}
    for s in st:
        for k, v in s.get("je", {}).items(): je[k] = je.get(k, 0) + v
    fehlt_teile = von - len(st)
    # Stichprobe: jede Datei muss lesbar sein und zur Kennung passen
    kaputt = 0
    for f in dateien:
        try:
            j = json.load(gzip.open(f))
            if f.endswith("-l.json.gz"): assert isinstance(j.get("linien"), list)
            else:
                assert isinstance(j.get("touren"), list)
                lf = f[:-8] + "-l.json.gz"; l = json.load(gzip.open(lf if os.path.exists(lf) else f"reg/{r}/" + os.path.basename(lf)))
                assert len(l["linien"]) == len(j["touren"]), "Linien passen nicht zur Tourenliste"
                for t in j["touren"][:3]: assert t["a"] in SPORTARTEN, f"unbekannte Sportart {t.get('a')}"; assert t["t"]["n"] and t["s"]["n"] and t["h"] > 0
            os.makedirs(f"reg/{r}", exist_ok=True); os.replace(f, f"reg/{r}/" + os.path.basename(f))
        except Exception as e:
            kaputt += 1; print(f"Datei unbrauchbar: {f} ({e})")
    vor = (alt.get("regionen") or {}).get(r, {})
    grund = None
    if not st: grund = "kein Rechner hat ein Ergebnis geliefert"
    elif fehlt_teile > 0 and fehlt_teile >= max(1, von // 3): grund = f"{fehlt_teile} von {von} Teilen fehlen"
    elif NACH: pass
    elif n_zellen and (n_zellen - fertig) > n_zellen * 0.3: grund = f"{n_zellen - fertig} von {n_zellen} Kartenstücken ohne Ergebnis"
    elif vor.get("touren") and touren < vor["touren"] * 0.5: grund = f"nur {touren} Touren, letztes Mal {vor['touren']}"
    elif touren == 0: grund = "keine einzige Tour"
    if grund: schlecht.append(f"{r}: {grund}")
    info[r] = {"stand": STAND, "zellen": fertig, "zellen_gesamt": n_zellen, "touren": touren, "je": je, "ohne_ergebnis": fehler, "fehlende_teile": fehlt_teile, "kaputt": kaputt,
               "ziele": sum(s["ziele"] for s in st), "ziele_ohne_tour": sum(s["ohne"] for s in st), "routen": sum(s.get("routen", 0) for s in st)}
    if NACH and vor:   # Zahlen zum bisherigen Stand der Region dazuzählen
        neu_r = info[r]; info[r] = {**vor, "nachgerechnet": STAND}
        for k in ("zellen", "touren", "ziele", "ziele_ohne_tour", "routen"): info[r][k] = vor.get(k, 0) + neu_r[k]
        info[r]["je"] = {k: vor.get("je", {}).get(k, 0) + neu_r["je"].get(k, 0) for k in set(vor.get("je", {})) | set(neu_r["je"])}
        info[r]["ohne_ergebnis"] = fehler
    with open(f"daten/vorschlaege/{r}{'-nachgerechnet' if NACH else ''}.md", "w") as f:
        i = info[r]
        f.write(f"# Vorschläge {'nachgerechnet' if NACH else 'berechnen'} {r} ({STAND})\n\n")
        f.write(f"- Kartenstücke berechnet: {fertig} von {n_zellen}" + (f", ohne Ergebnis: {', '.join(fehler[:40])}" if fehler else "") + "\n")
        f.write(f"- Ziele: {i['ziele']}, davon ohne Tour: {i['ziele_ohne_tour']}\n")
        f.write(f"- Touren: {touren} (" + ", ".join(f"{k}: {v}" for k, v in sorted(je.items())) + ")\n")
        f.write(f"- Routenberechnungen: {i['routen']}\n")
        if vor.get("touren"): f.write(f"- Letzter Lauf ({vor.get('stand')}): {vor['touren']} Touren\n")
        f.write(f"- Ergebnis: {'NICHT freigeschaltet – ' + grund if grund else ('Test, nicht freigeschaltet' if TEST else 'freigeschaltet')}\n")
    print(open(f"daten/vorschlaege/{r}{'-nachgerechnet' if NACH else ''}.md").read())
if TEST: sys.exit(0)
gut = [r for r in regs if not any(x.startswith(r + ":") for x in schlecht)]
for r in gut:
    for f in glob.glob(f"reg/{r}/*"): os.replace(f, "neu/" + os.path.basename(f))
if not gut:
    print("::error::Nichts freigeschaltet: " + "; ".join(schlecht)); sys.exit(1)
P = f"karte/touren/{STAND}/"
if alt.get("pfad"):   # bisherige Kartenstücke übernehmen (andere Regionen), im Speicher kopiert
    rc("copy", f"r2:{B}/{alt['pfad']}", f"r2:{B}/{P}", "--transfers", "32")
rc("copy", "neu", f"r2:{B}/{P}", "--transfers", "32")
n = len(rc("lsf", f"r2:{B}/{P}").stdout.split())
if n < len(os.listdir("neu")): print("::error::Hochladen unvollständig"); sys.exit(1)
neu = {"pfad": P, "stand": STAND, "erzeugt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "zellen": n, "regionen": {**(alt.get("regionen") or {}), **{r: {k: v for k, v in info[r].items() if k not in ("ohne_ergebnis",)} for r in gut}}}
json.dump(neu, open("neu.json", "w"), ensure_ascii=False)
rc("copyto", "neu.json", f"r2:{B}/karte/touren/aktuell.json")
print(f"Freigeschaltet: {P} mit {n} Kartenstücken, Regionen: {', '.join(gut)}")
if schlecht: print("::error::Nicht freigeschaltet (bisheriger Stand bleibt): " + "; ".join(schlecht)); sys.exit(1)
with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a") as f: f.write(f"Vorschläge {STAND} freigeschaltet: {n} Kartenstücke\n")
