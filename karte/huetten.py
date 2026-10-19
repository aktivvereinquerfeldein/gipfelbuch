#!/usr/bin/env python3
"""Öffnungszeiten und Verpflegung der Hütten/Almen (Supabase-Tabelle gb_huetten) in die Hütten-Zellen packen.

So kommen die Daten ohne Extra-Abruf in die App (wie bei bergfex): die App lädt die Hütten-Zellen ohnehin.
Die Original-Zellen der Kartenversion bleiben unverändert; die angereicherten Zellen liegen in einem eigenen Ordner
(karte/gb/<stand>/hut/…), aktuell.json zeigt mit dem Feld "gb" darauf. Der Worker nimmt sie nur, wenn sie zur
laufenden Kartenversion gehören, sonst die normalen Zellen.

Aufrufe:
  huetten.py daten  <daten.json>                         – Tabelle über die öffentliche Supabase-Schnittstelle holen (nur lesen)
  huetten.py anreichern <zellen-ordner> <aus-ordner> <daten.json>
                                                         – <zellen-ordner>/hut/*.json.gz lesen, Felder "gb" ergänzen,
                                                           neue Einträge ohne OSM-ID als Punkte dazu, nach <aus-ordner>/hut schreiben
  huetten.py gb-feld <aktuell.json> <neu.json> --pfad P --stand S --version V [--anzahl N]
                                                         – Feld "gb" in aktuell.json setzen (Rest bleibt gleich)

Das Feld "gb" an einer Hütte ist ein kurzer JSON-Text:
  v  Verpflegung (ja / nur_getraenke / nein / unbekannt)   o  Öffnung (Saisons MM-TT, Ruhetage, Zeiten)
  s  Sommersaison-Text   w Wintersaison-Text   r Ruhetage   z Zeiten   h Hinweise   ue Übernachtung (1/0)
  q  Quelle   u Quelle-Adresse   d Prüfdatum   k Sicherheit (bestaetigt / eine_quelle / unklar)   st Status   tel / web
Nur belegte Werte stehen drin (keine geschätzten Daten, keine OSM-Zeiten).
"""
import gzip, json, math, os, sys, time, urllib.request, argparse

SB_URL = os.environ.get("SB_URL", "https://vehrlrjegxlhxibrxqxm.supabase.co")
# öffentlicher App-Schlüssel (steht ohnehin in index.html); die Tabelle ist für alle lesbar, schreiben geht damit nicht
SB_KEY = os.environ.get("SB_KEY", "sb_publishable_5sO5VJ5W232hdV_gAbzplA_A4UgzW6M")
CELL_HUT = 0.4
FELDER = "id,osm_typ,osm_id,name,art,geom,hoehe,verpflegung,saison_sommer,saison_winter,ruhetage,zeiten,hinweise,uebernachtung,telefon,webseite,quelle,quelle_url,pruef_datum,sicherheit,status,oeffnung,daten"

def daten(out):
    rows, off = [], 0
    while True:
        url = f"{SB_URL}/rest/v1/gb_huetten?select={FELDER}&order=id&limit=1000&offset={off}"
        req = urllib.request.Request(url, headers={"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r: part = json.load(r)
        rows += part
        if len(part) < 1000: break
        off += 1000
    json.dump(rows, open(out, "w"), ensure_ascii=False)
    print(f"{len(rows)} Einträge aus gb_huetten geholt")

def kurz(r):
    d = r.get("daten") or {}
    if d.get("recherche") not in ("fertig", "auto") and r.get("status") == "aktiv": return None   # nur fertig recherchierte oder maschinell gelesene Einträge
    g = {"v": r.get("verpflegung") or "unbekannt"}
    o = r.get("oeffnung")
    if o:
        o = {k: v for k, v in o.items() if k in ("jahr", "saisons", "ruhetage", "zeiten", "ganzjaehrig", "wetter")}
        if "saisons" in o: o["saisons"] = [x for x in o["saisons"] if x.get("von") and x.get("bis")]   # unvollständige Saison weglassen
        if o.get("saisons") == []: o.pop("saisons")
        if o.get("ruhetage") == []: o.pop("ruhetage")
        if o: g["o"] = o
    for k, f in (("s", "saison_sommer"), ("w", "saison_winter"), ("r", "ruhetage"), ("z", "zeiten"), ("h", "hinweise"),
                 ("q", "quelle"), ("u", "quelle_url"), ("d", "pruef_datum"), ("k", "sicherheit"), ("tel", "telefon"), ("web", "webseite")):
        v = r.get(f)
        if v not in (None, ""): g[k] = v
    if r.get("uebernachtung") is not None: g["ue"] = 1 if r["uebernachtung"] else 0
    if r.get("status") and r["status"] != "aktiv": g["st"] = r["status"]
    return json.dumps(g, ensure_ascii=False, separators=(",", ":"))

def lonlat(r):
    g = r.get("geom")
    if isinstance(g, str):
        try: g = json.loads(g)
        except Exception: return None
    if isinstance(g, dict) and g.get("type") == "Point": return g["coordinates"][0], g["coordinates"][1]
    return None

def anreichern(zin, zout, dfile):
    rows = json.load(open(dfile))
    by, neu = {}, []
    T = {"node": "n", "way": "w", "relation": "r"}
    for r in rows:
        d = r.get("daten") or {}
        if d.get("recherche") == "dublette": continue
        g = kurz(r)
        if not g: continue
        if r.get("osm_typ") and r.get("osm_id"):
            by[T[r["osm_typ"]] + str(r["osm_id"])] = g
            for x in (d.get("osm_dubletten") or "").split(","):
                x = x.strip()
                if x and x not in by: by[x] = g
        else:
            ll = lonlat(r)
            if ll: neu.append((r, g, ll))
    src = os.path.join(zin, "hut"); dst = os.path.join(zout, "hut"); os.makedirs(dst, exist_ok=True)
    n_tr, n_z = 0, 0; cells = {}
    for fn in sorted(os.listdir(src)):
        if not fn.endswith(".json.gz"): continue
        with gzip.open(os.path.join(src, fn), "rt", encoding="utf-8") as f: j = json.load(f)
        for el in j.get("elements", []):
            g = by.get(el["type"][0] + str(el["id"]))
            if g: el.setdefault("tags", {})["gb"] = g; n_tr += 1
        cells[fn] = j
    # neue Einträge (in den Quellen gefunden, noch nicht in OpenStreetMap): als Punkt mit negativer ID
    for r, g, (lon, lat) in neu:
        fn = f"{math.floor(lon / CELL_HUT)}_{math.floor(lat / CELL_HUT)}.json.gz"
        j = cells.setdefault(fn, {"version": 0.6, "generator": "gipfelbuch-zellen", "elements": []})
        art = r.get("art") or "alpe"
        t = {"name": r["name"], "gb": g, "gb:neu": "1"}
        if art in ("huette", "berggasthaus", "alpgasthaus"): t["tourism"] = "alpine_hut"
        elif art == "biwak": t["tourism"] = "wilderness_hut"
        else: t["place"] = "locality"
        if r.get("hoehe"): t["ele"] = str(r["hoehe"])
        j["elements"].append({"type": "node", "id": -int(r["id"]), "lat": round(lat, 7), "lon": round(lon, 7), "tags": t})
    for fn, j in cells.items():
        with gzip.open(os.path.join(dst, fn), "wt", encoding="utf-8", compresslevel=9) as f:
            f.write(json.dumps(j, ensure_ascii=False, separators=(",", ":")))
        n_z += 1
    print(f"Hütten-Zellen: {n_z} geschrieben, {n_tr} Hütten/Alpen mit Öffnungsdaten, {len(neu)} neue Einträge")
    return n_tr

def gb_feld(alt, neu, pfad, stand, version, anzahl):
    a = json.load(open(alt))
    if a.get("version") != version:
        print(f"::warning::aktuell.json zeigt Version {a.get('version')}, Hütten-Daten gehören zu {version} – nichts geändert"); return 1
    a["gb"] = {"pfad": pfad, "stand": stand, "version": version, "ebenen": ["hut"], "anzahl": anzahl}
    json.dump(a, open(neu, "w"), separators=(",", ":"), ensure_ascii=False)
    print("gb:", a["gb"]); return 0

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd"); ap.add_argument("args", nargs="*")
    ap.add_argument("--pfad"); ap.add_argument("--stand"); ap.add_argument("--version"); ap.add_argument("--anzahl", type=int, default=0)
    a = ap.parse_args()
    if a.cmd == "daten": daten(a.args[0])
    elif a.cmd == "anreichern": anreichern(*a.args)
    elif a.cmd == "gb-feld": sys.exit(gb_feld(a.args[0], a.args[1], a.pfad, a.stand or time.strftime("%Y-%m-%d"), a.version, a.anzahl))
    else: print(__doc__); sys.exit(2)
