#!/usr/bin/env python3
"""karte/aktuell.json schreiben – damit schaltet der Worker auf die neue Version um (die alte bleibt im Speicher liegen).

Aufruf: freischalten.py --alt alt.json --neu ausgabe.json [--version V --zellen-index idx.json --abdeckung abd.json
                         --grundkarte grundkarte.pmtiles --gebiete "Österreich,Südtirol"] [--hang hang/] [--hoehenlinien karte/hoehenlinien.pmtiles] [--wege karte/V/wege.pmtiles]
Felder, die nicht angegeben sind, werden aus der alten Datei übernommen.
"""
import json, argparse, time

def vector_layers(pm):
    try:
        from pmtiles.reader import Reader, MmapSource
        with open(pm, "rb") as f:
            md = Reader(MmapSource(f)).metadata()
        return md.get("vector_layers", [])
    except Exception as e:
        print("Hinweis: vector_layers nicht gelesen:", e); return []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alt"); ap.add_argument("--neu", required=True); ap.add_argument("--version")
    ap.add_argument("--zellen-index"); ap.add_argument("--abdeckung"); ap.add_argument("--grundkarte"); ap.add_argument("--gebiete")
    ap.add_argument("--hang"); ap.add_argument("--hoehenlinien"); ap.add_argument("--wege")
    a = ap.parse_args()
    akt = {}
    if a.alt:
        try: akt = json.load(open(a.alt))
        except Exception: akt = {}
    if akt.get("version"): akt.setdefault("vorher", []).insert(0, akt["version"]); akt["vorher"] = akt["vorher"][:4]
    if a.version:
        akt["version"] = a.version; akt["basis"] = f"karte/{a.version}/"; akt["erzeugt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if a.zellen_index: akt["cell"] = json.load(open(a.zellen_index))["cell"]
    if a.abdeckung:
        ab = json.load(open(a.abdeckung)); akt["gebiet"] = ab["gebiet"]; akt["zellen"] = ab["zellen"]; akt["bounds"] = ab["bounds"]
    if a.grundkarte: akt["vector_layers"] = vector_layers(a.grundkarte)
    if a.gebiete: akt["gebiete"] = [g.strip() for g in a.gebiete.split(",")]
    if a.hang: akt["hang"] = a.hang
    if a.hoehenlinien: akt["hoehenlinien"] = a.hoehenlinien
    if a.wege: akt["wege"] = a.wege
    akt.setdefault("minzoomEigen", 8)
    json.dump(akt, open(a.neu, "w"), separators=(",", ":"), ensure_ascii=False)
    print("aktuell.json:", {k: (v if not isinstance(v, (list, dict)) or k in ("gebiete", "vorher") else "…") for k, v in akt.items()})

if __name__ == "__main__": main()
