#!/usr/bin/env python3
"""Vorab berechnete Tourenvorschläge prüfen, nach R2 hochladen und freischalten (Ablauf „Vorschläge berechnen“).
Aufruf: vorschlaege_einspielen.py <Ordner mit Artefakten> <stand> <Regionen> [Test-Rechteck]
- Jede Datei <ix>_<iy>.json.gz ist ein fertig berechnetes Kartenstück. Fehlende Kartenstücke bleiben einfach weg (dort rechnet die App selbst).
- Der neue Stand übernimmt zuerst alle Kartenstücke des bisherigen Stands (andere Regionen bleiben erhalten), dann kommen die neuen darüber.
- Sicherung: hat eine Region weniger als die Hälfte der Touren des letzten Laufs oder mehr als 30 % Kartenstücke ohne Ergebnis,
  wird nichts freigeschaltet (der bisherige Stand bleibt).
- Nichts wird gelöscht; alte Stände bleiben liegen (wenige MB)."""
import glob, gzip, json, os, subprocess, sys, time
ART, STAND, REGIONEN = sys.argv[1], sys.argv[2], [r.strip() for r in sys.argv[3].split(",") if r.strip()]
TEST = (sys.argv[4] if len(sys.argv) > 4 else "").strip()
B = os.environ.get("BUCKET", "qfeaktivfotos")
def rc(*a, check=True): return subprocess.run(["rclone", *a], check=check, capture_output=True, text=True)
os.makedirs("daten/vorschlaege", exist_ok=True)
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
                l = json.load(gzip.open(f[:-8] + "-l.json.gz")) if os.path.exists(f[:-8] + "-l.json.gz") else json.load(gzip.open("neu/" + os.path.basename(f)[:-8] + "-l.json.gz"))
                assert len(l["linien"]) == len(j["touren"]), "Linien passen nicht zur Tourenliste"
                for t in j["touren"][:3]: assert t["a"] in ("wandern", "bergsteigen", "bh") and t["t"]["n"] and t["s"]["n"] and t["h"] > 0
            os.replace(f, "neu/" + os.path.basename(f))
        except Exception as e:
            kaputt += 1; print(f"Datei unbrauchbar: {f} ({e})")
    vor = (alt.get("regionen") or {}).get(r, {})
    grund = None
    if not st: grund = "kein Rechner hat ein Ergebnis geliefert"
    elif fehlt_teile > 0 and fehlt_teile >= max(1, von // 3): grund = f"{fehlt_teile} von {von} Teilen fehlen"
    elif n_zellen and (n_zellen - fertig) > n_zellen * 0.3: grund = f"{n_zellen - fertig} von {n_zellen} Kartenstücken ohne Ergebnis"
    elif vor.get("touren") and touren < vor["touren"] * 0.5: grund = f"nur {touren} Touren, letztes Mal {vor['touren']}"
    elif touren == 0: grund = "keine einzige Tour"
    if grund: schlecht.append(f"{r}: {grund}")
    info[r] = {"stand": STAND, "zellen": fertig, "zellen_gesamt": n_zellen, "touren": touren, "je": je, "ohne_ergebnis": fehler, "fehlende_teile": fehlt_teile, "kaputt": kaputt,
               "ziele": sum(s["ziele"] for s in st), "ziele_ohne_tour": sum(s["ohne"] for s in st), "routen": sum(s.get("routen", 0) for s in st)}
    with open(f"daten/vorschlaege/{r}.md", "w") as f:
        i = info[r]
        f.write(f"# Vorschläge berechnen {r} ({STAND})\n\n")
        f.write(f"- Kartenstücke berechnet: {i['zellen']} von {i['zellen_gesamt']}" + (f", ohne Ergebnis: {', '.join(fehler[:40])}" if fehler else "") + "\n")
        f.write(f"- Ziele: {i['ziele']}, davon ohne Tour: {i['ziele_ohne_tour']}\n")
        f.write(f"- Touren: {touren} (" + ", ".join(f"{k}: {v}" for k, v in sorted(je.items())) + ")\n")
        f.write(f"- Routenberechnungen: {i['routen']}\n")
        if vor.get("touren"): f.write(f"- Letzter Lauf ({vor.get('stand')}): {vor['touren']} Touren\n")
        f.write(f"- Ergebnis: {'NICHT freigeschaltet – ' + grund if grund else ('Test, nicht freigeschaltet' if TEST else 'freigeschaltet')}\n")
    print(open(f"daten/vorschlaege/{r}.md").read())
if TEST: sys.exit(0)
if schlecht:
    print("::error::Nicht freigeschaltet: " + "; ".join(schlecht)); sys.exit(1)
P = f"karte/touren/{STAND}/"
if alt.get("pfad"):   # bisherige Kartenstücke übernehmen (andere Regionen), im Speicher kopiert
    rc("copy", f"r2:{B}/{alt['pfad']}", f"r2:{B}/{P}", "--transfers", "32")
rc("copy", "neu", f"r2:{B}/{P}", "--transfers", "32")
n = len(rc("lsf", f"r2:{B}/{P}").stdout.split())
if n < len(os.listdir("neu")): print("::error::Hochladen unvollständig"); sys.exit(1)
neu = {"pfad": P, "stand": STAND, "erzeugt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "zellen": n, "regionen": {**(alt.get("regionen") or {}), **{r: {k: v for k, v in info[r].items() if k not in ("ohne_ergebnis",)} for r in regs}}}
json.dump(neu, open("neu.json", "w"), ensure_ascii=False)
rc("copyto", "neu.json", f"r2:{B}/karte/touren/aktuell.json")
print(f"Freigeschaltet: {P} mit {n} Kartenstücken")
with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a") as f: f.write(f"Vorschläge {STAND} freigeschaltet: {n} Kartenstücke\n")
