#!/usr/bin/env python3
"""Wanderkarte (Prompt 3): Inventar je Kartenversion und genaue Änderungsliste – ohne KI.

Das Inventar ist eine kleine Liste aller Wege, Hütten, Parkplätze, Bergbahnen und Klettersteige mit den Merkmalen,
die für den Bericht zählen (SAC-Skala, Markierung, Zutritt/Sperre, Downhill …). Es liegt je Version in R2
(karte/<Version>/inventar.tsv.gz). Der Vergleich zweier Inventare ergibt den Änderungsbericht laut Prompt 3,
den Claude danach nur noch lesen muss (daten/karte/<Version>.md im Repo).

Aufrufe:
  inventar.py erzeugen  <gebiet.osm.pbf> <gem.json> <inventar.tsv.gz>
  inventar.py vergleich <alt.tsv.gz oder -> <neu.tsv.gz> <bericht.md> <aenderungen.json> [--version V --vorher V0]
       Exit 0 = in Ordnung, 3 = auffällig (in einem Bundesland über 2 % weniger Wege → nicht freischalten)
  inventar.py db        <aenderungen.json> <bericht.md>   neue Hütten (und Bergbahnen/Klettersteige, sobald diese Tabellen
       befüllt sind) eintragen, verschwundene als „nicht_mehr_gefunden“ markieren (nie löschen), Lauf in gb_abgleich_laeufe
       festhalten. Braucht SUPABASE_SERVICE_KEY. Sperren aus den Kartendaten kommen nur auf die Liste im Bericht
       (OSM allein ist nie Quelle für eine neue Sperre, Prompt 6).

gem.json kommt von parkplaetze.py grenzen (Bundesländer, Bezirke, Gemeinden).
"""
import gzip, json, math, os, re, sys, time, urllib.parse
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HEUTE = time.strftime("%Y-%m-%d")
GRENZE = 0.02          # über 2 % weniger Wege in einem Bundesland = auffällig
MIN_WEGE = 1000        # kleinere Gebiete nicht bewerten
FELDER = ["kat", "typ", "id", "land", "bezirk", "lon", "lat", "m", "name", "hw", "sac", "vis", "mark", "mtb", "zug", "vfs", "art"]
WEG_HW = {"path", "footway", "bridleway", "steps", "track", "via_ferrata", "cycleway"}
BAHN = {"cable_car", "gondola", "chair_lift", "mixed_lift", "drag_lift", "t-bar", "j-bar", "platter", "rope_tow", "magic_carpet", "funicular"}
ZUG_KEYS = ("access", "foot", "bicycle", "mtb", "motor_vehicle", "access:conditional", "foot:conditional", "bicycle:conditional", "mtb:conditional")
R_SPERR_NOTE = re.compile(r"gesperrt|sperre", re.I)
R_DOWNHILL = re.compile(r"downhill|bikepark|flow ?trail|freeride|singletrail", re.I)
SAC_KURZ = {"hiking": "T1", "mountain_hiking": "T2", "demanding_mountain_hiking": "T3", "alpine_hiking": "T4",
            "demanding_alpine_hiking": "T5", "difficult_alpine_hiking": "T6"}

def osm_link(typ, oid): return f"https://www.openstreetmap.org/{ {'n': 'node', 'w': 'way', 'r': 'relation'}[typ] }/{oid}"
def clean(v): return (v or "").replace("\t", " ").replace("\n", " ").strip()

# ---------------- Inventar erzeugen ----------------
def erzeugen(pbf, gemfile, aus):
    import osmium
    import zellen
    from parkplaetze import Orte
    orte = Orte(gemfile); cache = {}
    def ort(lon, lat):
        k = (round(lon, 2), round(lat, 2))
        if k not in cache:
            l = orte.finde("land", lon, lat)
            land = None
            if l: land = "Südtirol" if l["iso"] == "IT-BZ" else {"AT-1": "Burgenland", "AT-2": "Kärnten", "AT-3": "Niederösterreich",
                     "AT-4": "Oberösterreich", "AT-5": "Salzburg", "AT-6": "Steiermark", "AT-7": "Tirol", "AT-8": "Vorarlberg", "AT-9": "Wien"}.get(l["iso"])
            b = orte.finde("bezirk", lon, lat) if land and land != "Südtirol" else None
            cache[k] = (land, (b or {}).get("name") or "")
        return cache[k]

    # 1. Durchgang: Wege in Wander-/Radrouten (Markierung)
    wander, rad = set(), set()
    class R(osmium.SimpleHandler):
        def relation(s, r):
            rt = r.tags.get("route")
            if rt in ("hiking", "foot"): ziel = wander
            elif rt in ("mtb", "bicycle"): ziel = rad
            else: return
            for mb in r.members:
                if mb.type == "w": ziel.add(mb.ref)
    R().apply_file(pbf)
    print(f"Routen: {len(wander)} Wege in Wanderrouten, {len(rad)} in Radrouten", flush=True)

    n = defaultdict(int)
    f = gzip.open(aus, "wt", encoding="utf-8", compresslevel=6)
    f.write("\t".join(FELDER) + "\n")
    def zeile(kat, typ, oid, lon, lat, meter, t, **x):
        land, bez = ort(lon, lat)
        if not land: return   # Randstreifen der Nachbarländer gehört nicht zum Inventar
        z = {"kat": kat, "typ": typ, "id": oid, "land": land, "bezirk": bez, "lon": f"{lon:.5f}", "lat": f"{lat:.5f}", "m": str(int(meter)),
             "name": clean(t.get("name")), "hw": t.get("highway", ""), "sac": SAC_KURZ.get(t.get("sac_scale", ""), t.get("sac_scale", "")),
             "vis": t.get("trail_visibility", ""), "mark": "", "mtb": t.get("mtb:scale", ""), "zug": "", "vfs": t.get("via_ferrata_scale", ""), "art": ""}
        z.update(x)
        f.write("\t".join(clean(str(z[k])) for k in FELDER) + "\n"); n[kat.split(",")[0]] += 1

    def zug(t):
        p = [f"{k}={t[k]}" for k in ZUG_KEYS if k in t]
        if R_SPERR_NOTE.search(t.get("note", "")): p.append("note=gesperrt")
        return ";".join(p)

    class H(osmium.SimpleHandler):
        def node(s, o):
            t = {x.k: x.v for x in o.tags}
            if not t: return
            lon, lat = o.location.lon, o.location.lat
            ks = zellen.keys_node(t)
            if "hut" in ks: zeile("hut", "n", o.id, lon, lat, 0, t, art=t.get("tourism") or t.get("amenity") or t.get("place", ""))
            if t.get("amenity") == "parking": zeile("park", "n", o.id, lon, lat, 0, t, art=t.get("parking", ""), zug=zug(t))
        def way(s, o):
            t = {x.k: x.v for x in o.tags}
            if not t: return
            hw, aw = t.get("highway"), t.get("aerialway")
            ist_weg = hw in WEG_HW
            ist_hut = "hut" in zellen.keys_way(t) if (t.get("tourism") or t.get("place") or t.get("amenity")) else False
            ist_park = t.get("amenity") == "parking"
            ist_bahn = aw in BAHN or t.get("railway") == "funicular"
            if not (ist_weg or ist_hut or ist_park or ist_bahn): return
            try: c = [(nd.lon, nd.lat) for nd in o.nodes if nd.location.valid()]
            except Exception: return
            if not c: return
            meter = 0.0
            for i in range(1, len(c)):
                kx = 111320 * math.cos(c[i][1] * math.pi / 180)
                meter += math.hypot((c[i][0] - c[i-1][0]) * kx, (c[i][1] - c[i-1][1]) * 110540)
            mid = c[len(c) // 2]
            if ist_weg:
                kat = ["weg"]
                if hw == "track": kat.append("forst")
                if hw == "via_ferrata" or "via_ferrata_scale" in t: kat.append("vf")
                if "mtb:type" in t or R_DOWNHILL.search(t.get("name", "")): kat.append("dh")
                if zellen.sperr_way(t): kat.append("sperre")
                mark = []
                if o.id in wander: mark.append("Wanderroute")
                if o.id in rad: mark.append("Radroute")
                if "osmc:symbol" in t: mark.append("Zeichen " + t["osmc:symbol"])
                if t.get("trailblazed") == "yes": mark.append("markiert")
                zeile(",".join(kat), "w", o.id, mid[0], mid[1], meter, t, mark=" + ".join(mark), zug=zug(t))
            if ist_hut: zeile("hut", "w", o.id, mid[0], mid[1], 0, t, art=t.get("tourism") or t.get("amenity") or t.get("place", ""))
            if ist_park: zeile("park", "w", o.id, mid[0], mid[1], 0, t, art=t.get("parking", ""), zug=zug(t))
            if ist_bahn: zeile("bahn", "w", o.id, c[0][0], c[0][1], meter, t, art=aw or "funicular")
    H().apply_file(pbf, locations=True, idx="flex_mem")
    f.close()
    print("Inventar:", dict(n), flush=True)

# ---------------- Vergleich ----------------
def lesen(p):
    d = {}
    with gzip.open(p, "rt", encoding="utf-8") as f:
        kopf = f.readline().rstrip("\n").split("\t")
        for line in f:
            v = line.rstrip("\n").split("\t")
            if len(v) != len(kopf): continue
            z = dict(zip(kopf, v)); z["kats"] = set(z["kat"].split(","))
            d[(z["typ"], z["id"])] = z
    return d

def km(m): return f"{m / 1000:,.1f}".replace(",", " ").replace(".", ",")
def wname(z): return z["name"] or {"track": "Forstweg", "path": "Pfad", "footway": "Fußweg", "steps": "Treppe", "via_ferrata": "Klettersteig",
                                   "bridleway": "Reitweg", "cycleway": "Radweg"}.get(z.get("hw"), z.get("art") or "ohne Namen")
def eintrag(z, extra=""):
    lage = f"{z['land']}{', ' + z['bezirk'] if z.get('bezirk') else ''}"
    meter = f", {int(z['m'])} m" if z.get("m") and int(z["m"]) > 0 and z["kat"] != "hut" else ""
    return f"- [{wname(z)}]({osm_link(z['typ'], z['id'])}) – {lage}{meter}{(' – ' + extra) if extra else ''}"

def liste(zeilen, rows, n_max, sortkey=None, extra=None):
    if not rows: zeilen.append("- keine"); return
    if sortkey: rows = sorted(rows, key=sortkey)
    for z in rows[:n_max]: zeilen.append(eintrag(z, extra(z) if extra else ""))
    if len(rows) > n_max: zeilen.append(f"- … und {len(rows) - n_max} weitere (vollständig in karte/<Version>/aenderungen.json.gz)")

def vergleich(alt, neu, bericht, aus, version="", vorher=""):
    N = lesen(neu)
    zeilen = [f"# Wanderkarte {version} – Änderungsbericht", "", f"Erzeugt vom Skript am {HEUTE}. Quelle: OpenStreetMap (Geofabrik), "
              f"Österreich und Südtirol. Prüfdatum {HEUTE}, Sicherheitsstufe der Kartendaten: „eine Quelle“ (OSM).", ""]
    zahl = defaultdict(lambda: defaultdict(float))
    for z in N.values():
        for k in z["kats"]:
            zahl[k][z["land"]] += 1
            if k in ("weg", "forst"): zahl[k + "_m"][z["land"]] += int(z["m"] or 0)
    erg = {"version": version, "vorher": vorher, "datum": HEUTE, "auffaellig": [], "neu": {}, "weg": {}, "geaendert": {}}
    if alt in ("-", "") or not os.path.exists(alt):
        zeilen += ["Erstes Inventar – Vergleich ab dem nächsten Lauf. Bestand:", "", "| Bundesland | Wege | km | Forststraßen km | Hütten/Almen | Parkplätze | Bergbahnen | Klettersteig-Abschnitte |",
                   "|---|---|---|---|---|---|---|---|"]
        for l in sorted(zahl["weg"]):
            zeilen.append(f"| {l} | {int(zahl['weg'][l])} | {km(zahl['weg_m'][l])} | {km(zahl['forst_m'][l])} | {int(zahl['hut'][l])} | {int(zahl['park'][l])} | {int(zahl['bahn'][l])} | {int(zahl['vf'][l])} |")
        open(bericht, "w").write("\n".join(zeilen) + "\n")
        json.dump(erg, open(aus, "w"), ensure_ascii=False); print("\n".join(zeilen)); return 0
    A = lesen(alt)
    zeilen.insert(2, f"Vorher: {vorher or 'letzte Version'}, jetzt: {version}.")
    def gruppe(k):
        neu_ = [z for key, z in N.items() if k in z["kats"] and (key not in A or k not in A[key]["kats"])]
        weg_ = [z for key, z in A.items() if k in z["kats"] and (key not in N or k not in N[key]["kats"])]
        return neu_, weg_
    def wirklich_weg(z): return (z["typ"], z["id"]) not in N   # ganz verschwunden (nicht nur umgewidmet)
    # Plausibilität je Bundesland
    za = defaultdict(int)
    for z in A.values():
        if "weg" in z["kats"]: za[z["land"]] += 1
    zeilen += ["", "## Plausibilität (Wege je Bundesland)", "", "| Bundesland | vorher | jetzt | Änderung |", "|---|---|---|---|"]
    for l in sorted(set(za) | set(zahl["weg"])):
        va, vn = za.get(l, 0), int(zahl["weg"].get(l, 0))
        p = (vn - va) / va * 100 if va else 0
        warn = va >= MIN_WEGE and (va - vn) / va > GRENZE
        if warn: erg["auffaellig"].append({"land": l, "vorher": va, "jetzt": vn, "prozent": round(p, 1)})
        zeilen.append(f"| {l} | {va} | {vn} | {vn - va:+d} ({p:+.1f} %){' **AUFFÄLLIG**' if warn else ''} |")
    if erg["auffaellig"]: zeilen += ["", "**Auffällig: in mindestens einem Bundesland fehlen über 2 % der Wege – nicht freigeschaltet, Marcel fragen.**"]

    # Wege
    wn, ww = gruppe("weg")
    ww = [z for z in ww if wirklich_weg(z)]
    sac = [(A[k], z) for k, z in N.items() if k in A and "weg" in z["kats"] and "weg" in A[k]["kats"] and z["sac"] != A[k]["sac"]]
    mark = [(A[k], z) for k, z in N.items() if k in A and "weg" in z["kats"] and "weg" in A[k]["kats"] and z["mark"] != A[k]["mark"]]
    fn, _ = gruppe("forst")
    zeilen += ["", "## Übersicht", "", "| Was | Anzahl |", "|---|---|",
               f"| neue Wege | {len(wn)} ({km(sum(int(z['m']) for z in wn))} km) |", f"| entfernte Wege | {len(ww)} ({km(sum(int(z['m']) for z in ww))} km) |",
               f"| SAC-Skala geändert | {len(sac)} |", f"| Markierung geändert | {len(mark)} |", f"| neue Forststraßen | {len(fn)} ({km(sum(int(z['m']) for z in fn))} km) |"]
    gr = {}
    for k in ("sperre", "dh", "vf", "hut", "park", "bahn"): gr[k] = gruppe(k)
    zug = [(A[k], z) for k, z in N.items() if k in A and "weg" in z["kats"] and z["zug"] != A[k]["zug"] and (z["zug"] or A[k]["zug"])]
    hutname = [(A[k], z) for k, z in N.items() if k in A and "hut" in z["kats"] and "hut" in A[k]["kats"] and z["name"] != A[k]["name"]]
    vfgrad = [(A[k], z) for k, z in N.items() if k in A and "vf" in z["kats"] and "vf" in A[k]["kats"] and z["vfs"] != A[k]["vfs"]]
    titel = {"sperre": "Sperren und Zutrittsverbote", "dh": "Downhill-/MTB-Strecken", "vf": "Klettersteig-Abschnitte", "hut": "Hütten und Almen",
             "park": "Parkplätze", "bahn": "Bergbahnen und Lifte"}
    for k, (a, b) in gr.items(): zeilen.append(f"| {titel[k]}: neu / weg | {len(a)} / {len([z for z in b if wirklich_weg(z)])} |")
    zeilen.append(f"| Zutrittsregeln an Wegen geändert | {len(zug)} |")

    # je Bundesland
    zeilen += ["", "## Je Bundesland", "", "| Bundesland | Wege neu | Wege weg | SAC geändert | Forst neu | Sperren neu | Hütten neu | Parkplätze neu | Bahnen neu |", "|---|---|---|---|---|---|---|---|---|"]
    def cnt(rows, l): return sum(1 for z in rows if (z[1] if isinstance(z, tuple) else z)["land"] == l)
    for l in sorted(zahl["weg"]):
        zeilen.append(f"| {l} | {cnt(wn,l)} | {cnt(ww,l)} | {cnt(sac,l)} | {cnt(fn,l)} | {cnt(gr['sperre'][0],l)} | {cnt(gr['hut'][0],l)} | {cnt(gr['park'][0],l)} | {cnt(gr['bahn'][0],l)} |")

    lang = lambda z: -int(z["m"] or 0)
    zeilen += ["", "## Neue Wege (die längsten)"]; liste(zeilen, wn, 25, lang, lambda z: z["sac"] or z["hw"])
    zeilen += ["", "## Entfernte Wege (die längsten)"]; liste(zeilen, ww, 25, lang, lambda z: z["sac"] or z["hw"])
    zeilen += ["", "## SAC-Skala geändert"]
    for a, z in sorted(sac, key=lambda p: -int(p[1]["m"] or 0))[:60]: zeilen.append(eintrag(z, f"{a['sac'] or 'ohne'} → {z['sac'] or 'ohne'}"))
    if len(sac) > 60: zeilen.append(f"- … und {len(sac) - 60} weitere")
    zeilen += ["", "## Markierung geändert"]
    for a, z in sorted(mark, key=lambda p: -int(p[1]["m"] or 0))[:40]: zeilen.append(eintrag(z, f"{a['mark'] or 'keine'} → {z['mark'] or 'keine'}"))
    if len(mark) > 40: zeilen.append(f"- … und {len(mark) - 40} weitere")
    zeilen += ["", "## Neue Forststraßen (die längsten)"]; liste(zeilen, fn, 25, lang)
    zeilen += ["", "## Sperren und Zutrittsverbote (aus den Kartendaten, nur Hinweis – keine alleinige Quelle)", "", "Neu:"]
    liste(zeilen, gr["sperre"][0], 150, lang, lambda z: z["zug"])
    zeilen += ["", "Geändert:"]
    for a, z in sorted(zug, key=lambda p: -int(p[1]["m"] or 0))[:150]: zeilen.append(eintrag(z, f"{a['zug'] or 'frei'} → {z['zug'] or 'frei'}"))
    if len(zug) > 150: zeilen.append(f"- … und {len(zug) - 150} weitere")
    for k in ("dh", "vf", "hut", "bahn", "park"):
        a, b = gr[k]; b = [z for z in b if wirklich_weg(z)]
        mx = 80 if k == "park" else 300
        zeilen += ["", f"## {titel[k]}", "", "Neu:"]; liste(zeilen, a, mx, lambda z: (z["land"], z["name"] == "", z["name"]))
        zeilen += ["", "Nicht mehr gefunden:"]; liste(zeilen, b, mx, lambda z: (z["land"], z["name"] == "", z["name"]))
    if vfgrad:
        zeilen += ["", "## Klettersteige: Schwierigkeit geändert"]
        for a, z in vfgrad: zeilen.append(eintrag(z, f"{a['vfs'] or '?'} → {z['vfs'] or '?'}"))
    if hutname:
        zeilen += ["", "## Hütten umbenannt"]
        for a, z in hutname: zeilen.append(eintrag(z, f"vorher „{a['name']}“"))

    def kurz(z): return {k: z[k] for k in FELDER if k != "kat"} | {"kat": z["kat"]}
    erg["neu"] = {k: [kurz(z) for z in gr[k][0]] for k in ("hut", "bahn", "vf", "sperre", "dh")}
    erg["weg"] = {k: [kurz(z) for z in gr[k][1] if wirklich_weg(z)] for k in ("hut", "bahn", "vf")}
    erg["zahlen"] = {"wege_neu": len(wn), "wege_weg": len(ww), "sac": len(sac), "markierung": len(mark), "forst_neu": len(fn), "zutritt": len(zug),
                     **{f"{k}_neu": len(gr[k][0]) for k in gr}, **{f"{k}_weg": len([z for z in gr[k][1] if wirklich_weg(z)]) for k in gr}}
    erg["voll"] = {"wege_neu": [kurz(z) for z in wn], "wege_weg": [kurz(z) for z in ww], "sac": [[a["sac"], kurz(z)] for a, z in sac],
                   "markierung": [[a["mark"], kurz(z)] for a, z in mark], "forst_neu": [kurz(z) for z in fn], "zutritt": [[a["zug"], kurz(z)] for a, z in zug],
                   "park_neu": [kurz(z) for z in gr["park"][0]], "park_weg": [kurz(z) for z in gr["park"][1] if wirklich_weg(z)]}
    open(bericht, "w").write("\n".join(zeilen) + "\n")
    json.dump(erg, open(aus, "w"), ensure_ascii=False)
    print("\n".join(zeilen[:60]))
    return 3 if erg["auffaellig"] else 0

# ---------------- Datenbank ----------------
def db(ausfile, bericht):
    from parkplaetze import sb
    if not os.environ.get("SUPABASE_SERVICE_KEY"): print("Kein SUPABASE_SERVICE_KEY – Datenbank bleibt unverändert"); return
    e = json.load(open(ausfile, encoding="utf-8")); zeilen = ["", "## Datenbank"]
    def geom(z): return f"SRID=4326;POINT({z['lon']} {z['lat']})"
    def link(z): return osm_link(z["typ"], z["id"])
    typ = {"n": "node", "w": "way", "r": "relation"}
    def anzahl(tab):
        r = json.loads(sb("GET", f"{tab}?select=id&limit=1"))
        return len(r)
    def eintragen(tab, rows):
        for i in range(0, len(rows), 500): sb("POST", f"{tab}?on_conflict=osm_typ,osm_id", rows[i:i + 500], "resolution=ignore-duplicates,return=minimal")
    def markieren(tab, rows):
        k = 0
        for z in rows:
            flt = f"osm_typ=eq.{typ[z['typ']]}&osm_id=eq.{z['id']}&status=eq.aktiv"
            r = json.loads(sb("PATCH", f"{tab}?{flt}", {"status": "nicht_mehr_gefunden", "pruef_datum": HEUTE}, "return=representation"))
            k += len(r)
        return k
    basis = lambda z: {"osm_typ": typ[z["typ"]], "osm_id": int(z["id"]), "name": z["name"] or None, "geom": geom(z), "land": "AT" if z["land"] != "Südtirol" else "IT",
                       "region": z["land"], "bezirk": z["bezirk"] or None, "quelle": "OpenStreetMap", "quelle_url": link(z), "pruef_datum": HEUTE,
                       "sicherheit": "unklar", "status": "aktiv"}
    h = [basis(z) | {"art": z["art"] or None, "verpflegung": "unbekannt", "daten": {"lauf": "karte", "recherche": "offen", "kartenversion": e["version"]}} for z in e["neu"]["hut"]]
    eintragen("gb_huetten", h); w = markieren("gb_huetten", e["weg"]["hut"])
    zeilen.append(f"- Hütten/Almen: {len(h)} neue gemeldet (recherche offen, vorhandene bleiben unverändert), {w} als nicht mehr gefunden markiert")
    if anzahl("gb_bergbahnen"):
        b = [basis(z) | {"art": z["art"] or None, "daten": {"lauf": "karte", "recherche": "offen"}} for z in e["neu"]["bahn"]]
        eintragen("gb_bergbahnen", b); w = markieren("gb_bergbahnen", e["weg"]["bahn"])
        zeilen.append(f"- Bergbahnen: {len(b)} neue gemeldet, {w} nicht mehr gefunden")
    else: zeilen.append("- Bergbahnen: Tabelle noch leer (Erstsuche Prompt 5 fehlt) – nur Liste oben")
    zeilen.append("- Klettersteige: nur Liste oben – die Tabelle gb_klettersteige pflegt der eigene Ablauf „Klettersteige“ (karte/klettersteige.py, 1. März)")
    zeilen.append("- Sperren: nur Liste oben (Kartendaten sind nie alleinige Quelle für eine neue Sperre); kommt in die Prüfliste von Prompt 6")
    z = e.get("zahlen", {})
    sb("POST", "gb_abgleich_laeufe", {"gruppe": "wanderkarte", "region": "Österreich, Südtirol", "gestartet": HEUTE, "beendet": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "fortschritt": 100, "status": "auffaellig" if e.get("auffaellig") else "fertig", "neu": z.get("wege_neu"), "geaendert": (z.get("sac") or 0) + (z.get("markierung") or 0) + (z.get("zutritt") or 0),
       "nicht_gefunden": z.get("wege_weg"), "bericht": f"daten/karte/{e['version']}.md", "daten": {"version": e["version"], "vorher": e.get("vorher"), "zahlen": z}}, "return=minimal")
    open(bericht, "a").write("\n".join(zeilen) + "\n"); print("\n".join(zeilen))

if __name__ == "__main__":
    a = sys.argv[1:]; c = a[0] if a else ""
    opt = {a[i][2:]: a[i + 1] for i in range(len(a) - 1) if a[i].startswith("--")}
    if c == "erzeugen": erzeugen(a[1], a[2], a[3])
    elif c == "vergleich": sys.exit(vergleich(a[1], a[2], a[3], a[4], opt.get("version", ""), opt.get("vorher", "")))
    elif c == "db": db(a[1], a[2])
    else: print(__doc__); sys.exit(2)
