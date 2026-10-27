#!/usr/bin/env python3
"""Hütten und Almen im ganzen DACH-Raum (Österreich, Deutschland, Schweiz, Liechtenstein, Südtirol):
Bestand aus OpenStreetMap, Texte der Hütten-Webseiten holen und vorsichtig auswerten – ohne KI.

Endprodukt wie bei Prompt 1 (Tabelle gb_huetten, gleiche Felder). Regeln:
- Nur lesen (robots.txt, höchstens 1 Anfrage pro Sekunde und Seite), nur Fakten, keine fremden Texte.
- Nichts überschreiben: neue Einträge nur anlegen, maschinell Gelesenes nur bei Einträgen mit recherche='offen' oder 'auto'.
- Maschinell gelesen = sicherheit 'unklar', daten.recherche='auto' (App zeigt „ohne Gewähr“). Nur eindeutige
  Treffer werden eingetragen, alles andere kommt auf die Prüfliste für Claude (daten/dach/pruef-<Land>-<Region>.md).
- OSM-Öffnungszeiten zählen nicht als Quelle.

Aufrufe:
  huetten_dach.py regionen <grenzen.osm.pbf> <regionen.wkb.json>     Bundesländer/Kantone (admin_level 4) als Umrisse
  huetten_dach.py inventar <land> <regionen.wkb.json> <aus.jsonl> <datei.osm.pbf>...
  huetten_dach.py zusammen <inventar.jsonl> <neu.json>                doppelte weg, Zeilen für die Datenbank
  huetten_dach.py sammeln  <neu.json> <auto.json> <pruef-ordner> [quellen.json]   alle Quellen je Hütte lesen (siehe quellen.py)
  huetten_dach.py db-neu   <neu.json>      neue Einträge anlegen (vorhandene bleiben)
  huetten_dach.py db-auto  <auto.json>     eindeutige Treffer eintragen (nur recherche offen/auto)
Datenbank schreiben braucht das GitHub-Secret SUPABASE_SERVICE_KEY; ohne Schlüssel bleiben nur die Dateien im Repo.
"""
import json, os, re, sys, time, urllib.request, urllib.parse, concurrent.futures as cf
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sammler   # holen(), text_und_links(), auszug()

HEUTE = time.strftime("%Y-%m-%d")
JAHR = int(time.strftime("%Y"))

# gleiche Auswahl wie die Hütten-Zellen (karte/zellen.py), für Bayern zusätzlich „…alm“
R_HUT_T = re.compile(r"^(alpine_hut|wilderness_hut)$")
R_PLACE = re.compile(r"^(locality|isolated_dwelling|hamlet|farm)$")
R_PNAME = re.compile(r"^(Alpe?|Alp|Alm|Maisäß|Vorsäß)( |$)|(alpe|alm|alp)$", re.I)
R_AM = re.compile(r"^(restaurant|cafe|biergarten|pub)$")
R_ANAME = re.compile(r"(alpe?( |$)|^alp |hütte|huette|maisäß|vorsäß|alm( |$)|alm$|berggasth|alpengasth|bergrestaurant|berghaus)", re.I)

def art(t):
    n = t.get("name", "")
    if re.search(r"vorsäß|maisäß", n, re.I): return "vorsaess"
    if re.search(r"alpengasth|alpgasth|alpwirtschaft", n, re.I): return "alpgasthaus"
    if re.search(r"berggasth|berghaus|bergrest|berghof|gipfelrest|panoramarest", n, re.I): return "berggasthaus"
    if t.get("tourism") == "wilderness_hut": return "biwak"
    if t.get("tourism") == "alpine_hut" or re.search(r"hütte|huette|hüsli", n, re.I): return "huette"
    if re.search(r"alm\b|alm$", n, re.I): return "alm"
    if re.search(r"alp", n, re.I): return "alpe"
    return "sonstiges"

def ist_huette(t):
    if "name" not in t: return False
    if R_HUT_T.match(t.get("tourism", "")): return True
    if R_PLACE.match(t.get("place", "")) and R_PNAME.search(t["name"]): return True
    if R_AM.match(t.get("amenity", "")) and R_ANAME.search(t["name"]): return True
    return False

# ---------- Regionen (Bundesland / Kanton) ----------
def regionen(pbf, aus):
    import osmium
    from osmium.geom import WKBFactory
    wkb = WKBFactory(); out = []
    class H(osmium.SimpleHandler):
        def area(s, a):
            t = {x.k: x.v for x in a.tags}
            iso = t.get("ISO3166-2") or ""
            ok = (t.get("boundary") == "administrative" and t.get("admin_level") == "4" and iso[:3] in ("AT-", "DE-", "CH-")) \
                 or iso == "IT-BZ" or (t.get("admin_level") == "2" and t.get("ISO3166-1") == "LI")
            if ok:
                try: out.append({"name": t.get("name:de") or t.get("name"), "iso": iso or "LI", "wkb": wkb.create_multipolygon(a)})
                except Exception: pass
    H().apply_file(pbf, locations=True, idx="flex_mem")
    alt = json.load(open(aus)) if os.path.exists(aus) else []
    json.dump(alt + out, open(aus, "w")); print(f"{len(out)} Regionen")

class Finder:
    def __init__(s, path):
        from shapely import wkb as W
        from shapely.strtree import STRtree
        s.r = [(x["name"], x["iso"], W.loads(x["wkb"], hex=True)) for x in json.load(open(path))]
        s.tree = STRtree([g for _, _, g in s.r])
    def __call__(s, lon, lat):
        from shapely.geometry import Point
        p = Point(lon, lat)
        for i in s.tree.query(p):
            n, iso, g = s.r[int(i)]
            if g.contains(p): return n, iso
        return None, None

# ---------- Bestand ----------
def inventar(land, regfile, aus, pbfs):
    import osmium
    fnd = Finder(regfile); n = 0
    LAND = land
    with open(aus, "a", encoding="utf-8") as f:
        class H(osmium.SimpleHandler):
            def emit(s, typ, oid, lon, lat, t):
                nonlocal n
                reg, iso = fnd(lon, lat)
                if not reg: return   # außerhalb des Gebiets (Randstreifen)
                if iso[:2] != LAND and not (LAND == "LI" and iso == "LI"): return   # Randstreifen gehört zum Nachbarland
                keep = {k: v for k, v in t.items() if k in ("name", "tourism", "amenity", "place", "ele", "website", "contact:website", "url", "phone", "contact:phone", "operator", "wikidata")}
                f.write(json.dumps({"t": typ, "id": oid, "lon": round(lon, 6), "lat": round(lat, 6), "land": LAND, "region": reg, "tags": keep}, ensure_ascii=False) + "\n"); n += 1
            def node(s, o):
                t = {x.k: x.v for x in o.tags}
                if ist_huette(t): s.emit("node", o.id, o.location.lon, o.location.lat, t)
            def way(s, o):
                t = {x.k: x.v for x in o.tags}
                if not ist_huette(t): return
                try: pts = [(nd.lon, nd.lat) for nd in o.nodes if nd.location.valid()]
                except Exception: return
                if pts: s.emit("way", o.id, sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts), t)
        h = H()
        for p in pbfs: h.apply_file(p, locations=True, idx="flex_mem")
    print(f"{land}: {n} Hütten/Almen")

# ---------- Zusammenführen (doppelte aus Randstreifen der Länder-Dateien weg) ----------
def lese(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]

def zusammen(inv, aus):
    """schreibt die neuen Zeilen für gb_huetten als JSON """
    rows, seen = [], set()
    for x in lese(inv):
        if (x["t"], x["id"]) in seen: continue
        seen.add((x["t"], x["id"])); t = x["tags"]
        ele = re.match(r"^\d{3,4}", t.get("ele", "") or "")
        rows.append({"t": x["t"], "id": x["id"], "n": t["name"], "a": art(t), "lon": x["lon"], "lat": x["lat"],
                     "h": int(ele.group(0)) if ele else None, "l": x["land"], "r": x["region"],
                     "w": t.get("website") or t.get("contact:website") or t.get("url"), "tag": t.get("tourism") or t.get("amenity") or t.get("place"), "wd": t.get("wikidata")})
    json.dump(rows, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    n = defaultdict(int)
    for r in rows: n[r["l"] + " " + r["r"]] += 1
    print(f"{len(rows)} Hütten/Almen gesamt"); [print(f"  {k}: {v}") for k, v in sorted(n.items())]

# ---------- Webseiten lesen und vorsichtig auswerten ----------
MON = {"jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "dez": 12, "dec": 12}
WT = {"montag": "mo", "dienstag": "di", "mittwoch": "mi", "donnerstag": "do", "freitag": "fr", "samstag": "sa", "sonntag": "so"}
R_RANGE = re.compile(r"(\d{1,2})\.\s?(\d{1,2})\.(\d{2,4})?\s*(?:–|-|bis)\s*(\d{1,2})\.\s?(\d{1,2})\.(\d{2,4})")
MONATE = {"jänner": 1, "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7, "august": 8,
          "september": 9, "oktober": 10, "november": 11, "dezember": 12}
_M = "|".join(MONATE)
R_RANGE_M = re.compile(rf"(\d{{1,2}})\.\s*({_M})\s*(\d{{4}})?\s*(?:–|-|bis)\s*(\d{{1,2}})\.\s*({_M})\s*(\d{{4}})", re.I)

def zeitraeume(text):
    """alle Datumsbereiche mit Jahr am Ende: (start, ende, d1, m1, d2, m2, y2) – Ziffern- und Monatsnamen-Schreibweise"""
    out = []
    for m in R_RANGE.finditer(text):
        d1, m1, _, d2, m2, y2 = m.groups()
        out.append((m.start(), m.end(), d1, m1, d2, m2, y2))
    for m in R_RANGE_M.finditer(text):
        d1, m1, _, d2, m2, y2 = m.groups()
        out.append((m.start(), m.end(), d1, MONATE[m1.lower()], d2, MONATE[m2.lower()], y2))
    return sorted(out)
R_SAISONWORT = re.compile(r"(geöffnet|geoeffnet|öffnungszeit|oeffnungszeit|saison|bewirtschaftet|sommerbetrieb|winterbetrieb|sommer|winter)", re.I)
R_ZU = re.compile(r"(geschlossen|betriebsurlaub|urlaub|ruhezeit|pause|closed|revision|ferien|nicht geöffnet|außer|ausser|ausgenommen|umbau|renovierung)", re.I)
TAGE = "montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag"
R_RUHE = re.compile(r"ruhetage?\s*:?\s*((?:" + TAGE + r")(?:\s*(?:,|und|&|/)\s*(?:" + TAGE + r"))*)\s*$")
R_RUHE_BED = re.compile(r"(außer|ausser|ausgenommen|juli|august|hochsaison|nebensaison|schlecht|feiertag|ferien|nur |ab \d|bis \d|uhr|nachmittag|abend)")

def plausibel(a, m1, m2):
    if a == "sommer": return 4 <= m1 <= 7 and 8 <= m2 <= 11
    return m1 in (11, 12, 1) and 3 <= m2 <= 5

def auswerten(text, voll=None, gasthaus=False):
    """gibt (felder, grund) – felder nur, wenn eindeutig. text = Auszug, voll = ganzer Seitentext.
    Streng: lieber auf die Prüfliste als etwas Falsches eintragen."""
    low = text.lower(); vlow = (voll or text).lower()
    v = "unbekannt"
    selbst = re.search(r"selbstversorgerhütte|selbstversorgerhaus|selbstversorger-hütte|unbewirtschaftet|nicht bewirtschaftet", vlow)
    bew = re.search(r"(?<!un)(?<!nicht )bewirtschaftet|hüttenwirt|speisekarte|warme küche|warme speisen|bewirtung|selbstversorger", vlow)
    selbst_rein = selbst and not re.search(r"(?<!un)(?<!nicht )bewirtschaftet|hüttenwirt|speisekarte|warme küche|warme speisen|bewirtung", vlow)
    if selbst_rein:
        if gasthaus: return None, "Gasthaus, aber Seite nennt Selbstversorger"
        v = "nein"
    elif bew and not re.search(r"selbstversorger", vlow): v = "ja"
    elif bew: return None, "Verpflegung widersprüchlich (Selbstversorger und Bewirtung erwähnt)"
    saisons = set()
    for start, ende, d1, m1, d2, m2, y2 in zeitraeume(text):
        vor = text[max(0, start - 60):start]; nach = text[ende:ende + 40]
        if R_ZU.search(vor) or R_ZU.search(nach): return None, "Zeitraum mit Schließzeit/Ausnahme erwähnt"
        if not R_SAISONWORT.search(vor): continue
        y2 = int(y2) + (2000 if len(y2) == 2 else 0)
        if y2 not in (JAHR, JAHR + 1): continue
        try: d1, m1, d2, m2 = int(d1), int(m1), int(d2), int(m2)
        except ValueError: continue
        if not (1 <= m1 <= 12 and 1 <= m2 <= 12 and 1 <= d1 <= 31 and 1 <= d2 <= 31): continue
        a = "sommer" if 4 <= m1 <= 9 else "winter"
        tage = ((m2 - m1) * 30.4 + (d2 - d1)) % 365
        if tage < 45 or not plausibel(a, m1, m2): return None, f"ungewöhnlicher Zeitraum {d1}.{m1}.–{d2}.{m2}."
        saisons.add((a, f"{m1:02d}-{d1:02d}", f"{m2:02d}-{d2:02d}", y2))
    arten = defaultdict(list)
    for s in saisons: arten[s[0]].append(s)
    if any(len(x) > 1 for x in arten.values()): return None, "mehrere verschiedene Saisondaten"
    if len({x[3] for x in saisons}) > 1: return None, "Saisondaten aus verschiedenen Jahren"
    ruhe, ergebnisse = set(), set()
    for m in re.finditer(r"ruhetag", low):
        roh = low[m.start():m.start() + 90]
        stueck = re.split(r"[.|;\n(]", roh)[0]
        r = R_RUHE.search(stueck)
        if not r:
            if re.search(TAGE, stueck): return None, "Ruhetag nicht eindeutig"
            continue
        if R_RUHE_BED.search(roh[len(stueck):len(stueck) + 50]) or R_RUHE_BED.search(stueck): return None, "Ruhetag mit Bedingung"
        tage_ = frozenset(WT[w] for w in re.findall(TAGE, r.group(1)))
        ergebnisse.add(tage_); ruhe |= tage_
    for m in re.finditer(r"((?:" + TAGE + r")(?:\s*(?:,|und|&|/)\s*(?:" + TAGE + r"))*)\s+(?:ist\s+|sind\s+)?ruhetage?\b", low):
        a_ = max(low.rfind("|", 0, m.start()), low.rfind("\n", 0, m.start())) + 1
        e_ = min([x for x in (low.find("|", m.end()), low.find("\n", m.end())) if x >= 0] or [len(low)])
        umfeld = low[a_:e_]
        if R_RUHE_BED.search(umfeld): return None, "Ruhetag mit Bedingung"
        tage_ = frozenset(WT[w] for w in re.findall(TAGE, m.group(1)))
        ergebnisse.add(tage_); ruhe |= tage_
    if len(ergebnisse) > 1: return None, "verschiedene Ruhetage genannt"
    ruhe = sorted(ruhe, key=list(WT.values()).index)
    if v == "unbekannt" and not saisons: return None, "nichts Eindeutiges gefunden"
    f = {"verpflegung": v}
    if saisons:
        f["oeffnung"] = {"jahr": max(s[3] for s in saisons), "saisons": [{"art": a, "von": von, "bis": bis} for a, von, bis, _ in sorted(saisons)]}
        for a, von, bis, y in saisons:
            f["saison_" + a] = f"{von[3:]}.{von[:2]}.–{bis[3:]}.{bis[:2]}.{y}"
    if ruhe:
        f.setdefault("oeffnung", {})["ruhetage"] = ruhe; f["ruhetage"] = ", ".join(k.title() for k, x in WT.items() if x in ruhe)
    return f, None

FERTIG = [x.strip() for x in os.environ.get("FERTIG_REGIONEN", "Vorarlberg").split(",") if x.strip()]   # schon von Claude recherchiert

R_REL = re.compile(r"(geöffnet|geoeffnet|öffnungszeit|oeffnungszeit|saison|ruhetag|bewirt|selbstversorger|geschlossen|offen|opening|"
                   r"jänner|januar|februar|märz|april|mai|juni|juli|august|september|oktober|november|dezember|\d{1,2}\.\s?\d{1,2}\.)", re.I)
R_UNTER = re.compile(r"(oeffnungszeit|öffnungszeit|offnungszeit|opening|saison|oeffnung|öffnung|zeiten|kontakt|info|huette|hütte|alm|preise)", re.I)

def seite_lesen(w):
    """Startseite plus bis zu 2 passende Unterseiten derselben Seite; gibt (zeilen, fehler)"""
    raw, err = sammler.holen(w)
    if err: return None, err
    zeilen, links = sammler.text_und_links(raw)
    gesehen, n = {w}, 0
    for h in links:
        u = urllib.parse.urljoin(w, h).split("#")[0]
        if u in gesehen or urllib.parse.urlparse(u).netloc != urllib.parse.urlparse(w).netloc: continue
        pfad = urllib.parse.unquote(urllib.parse.urlparse(u).path)
        if not R_UNTER.search(pfad) or re.search(r"\.(jpe?g|png|gif|zip|docx?)$", pfad, re.I): continue
        gesehen.add(u)
        r2, _ = sammler.holen(u)
        if r2: zeilen += sammler.text_und_links(r2)[0]
        n += 1
        if n >= 2: break
    return zeilen, None

def kombinieren(ergebnisse):
    """mehrere Quellen zusammenführen: Widerspruch -> Prüfliste, Übereinstimmung von 2+ Quellen -> bestätigt"""
    v = {e["verpflegung"] for _, e in ergebnisse if e.get("verpflegung") not in (None, "unbekannt")}
    if len(v) > 1: return None, "Quellen widersprechen sich bei der Verpflegung", 0
    saisons, ruhe, zeiten, belege, quellen = {}, None, None, {}, []
    bestaetigt = 0
    for q, e in ergebnisse:
        quellen.append(q)
        o = e.get("oeffnung") or {}
        for s_ in o.get("saisons") or []:
            k = (s_["von"], s_["bis"])
            if s_["art"] in saisons and saisons[s_["art"]][0] != k: return None, f"Quellen widersprechen sich bei der {s_['art'].title()}saison", 0
            if s_["art"] in saisons: bestaetigt += 1
            saisons[s_["art"]] = (k, s_, o.get("jahr"))
        if o.get("ruhetage"):
            if ruhe is not None and ruhe != o["ruhetage"]: return None, "Quellen widersprechen sich bei den Ruhetagen", 0
            if ruhe is not None: bestaetigt += 1
            ruhe = o["ruhetage"]
        zeiten = zeiten or o.get("zeiten")
        if e.get("belege"): belege[q["url"]] = e["belege"]
    vv = [e["verpflegung"] for _, e in ergebnisse if e.get("verpflegung") not in (None, "unbekannt")]
    if len(vv) >= 2: bestaetigt += 1
    f = {"verpflegung": v.pop() if v else "unbekannt"}
    o = {}
    if saisons:
        o["saisons"] = [x[1] for x in saisons.values()]
        jahre = {x[2] for x in saisons.values() if x[2]}
        if len(jahre) == 1: o["jahr"] = jahre.pop()
        namen = {"sommer": "saison_sommer", "winter": "saison_winter"}
        for art_, (k, s_, j) in saisons.items():
            jj = str(o.get("jahr") or "")
            f[namen[art_]] = f"{'ca. ' if s_.get('ungefaehr') else ''}{k[0][3:]}.{k[0][:2]}.–{k[1][3:]}.{k[1][:2]}.{jj}"
    if ruhe:
        o["ruhetage"] = ruhe
        f["ruhetage"] = ", ".join(n.title() for n, x in WT.items() if x in ruhe)
    if zeiten: o["zeiten"] = zeiten; f["zeiten"] = zeiten
    if o: f["oeffnung"] = o
    f["belege"] = belege or None
    f["quellen"] = quellen
    return f, None, bestaetigt

def sammeln(neu, aus, pruefdir, quellen_datei=None):
    import hashlib, ki_leser
    os.makedirs(pruefdir, exist_ok=True)
    ki = bool(os.environ.get("ANTHROPIC_API_KEY")); max_ki = int(os.environ.get("MAX_KI", "5000"))
    if ki:
        try: ki_leser.anfrage("Geöffnet 1. Juni bis 30. September", "Test")
        except Exception as e:
            print(f"KI-Leser nicht nutzbar ({str(e)[:120]}) – Regel-Lesen wird verwendet"); ki = False
    cache_p = os.path.join(pruefdir, "ki-cache.json")
    cache = json.load(open(cache_p, encoding="utf-8")) if os.path.exists(cache_p) else {}
    qu = json.load(open(quellen_datei, encoding="utf-8")) if quellen_datei and os.path.exists(quellen_datei) else {}
    jobs = []
    for x in json.load(open(neu, encoding="utf-8")):
        if x["r"] in FERTIG: continue
        qs = qu.get(f"{x['t']}/{x['id']}") or ([{"url": x["w"], "art": "webseite", "name": "Eigene Webseite"}] if (x.get("w") or "").startswith("http") else [])
        if qs: jobs.append((x, qs))
    print(f"{len(jobs)} Hütten mit mindestens einer Quelle (ohne {', '.join(FERTIG)}), "
          f"{sum(1 for _, q in jobs if len(q) > 1)} mit mehreren, KI-Leser: {'an' if ki else 'aus'}")
    stat = defaultdict(int); tok = {"in": 0, "out": 0}; neu_cache = {}
    def eine_quelle(x, q, gast):
        if q["art"] == "webseite": zeilen, err = seite_lesen(q["url"])
        else:
            raw, err = sammler.holen(q["url"]); zeilen = sammler.text_und_links(raw)[0] if raw else None
        if err or not zeilen: return None, "nicht lesbar", ""
        voll = "\n".join(zeilen); txt = sammler.auszug(zeilen, 2500)
        if not ki:
            f, grund = auswerten(sammler.auszug(zeilen, 1500), voll[:200000], gast)
            stat["regel"] += 1
            return f, grund, txt
        if not R_REL.search(txt): return None, "keine Angaben", txt
        h = hashlib.sha1((txt + ki_leser.MODELL).encode()).hexdigest()[:16]
        alt = cache.get(q["url"])
        if alt and alt.get("h") == h: ant = alt.get("a"); stat["cache"] += 1
        else:
            if stat["_anfragen"] >= max_ki: return None, "KI-Obergrenze erreicht", txt
            stat["_anfragen"] += 1
            ant, usage = ki_leser.anfrage(txt, x["n"])
            tok["in"] += usage.get("input_tokens", 0); tok["out"] += usage.get("output_tokens", 0)
            if ant is not None: neu_cache[q["url"]] = {"h": h, "a": ant}
        f, grund = ki_leser.pruefen(ant, txt, gast)
        return f, grund, txt
    def huette(job):
        x, qs = job
        gast = x["a"] in ("berggasthaus", "alpgasthaus") or x["tag"] in ("restaurant", "cafe", "biergarten", "pub")
        ergebnisse, gruende, texte = [], [], []
        for q in qs[:4]:
            f, grund, txt = eine_quelle(x, q, gast)
            if f: ergebnisse.append((q, f))
            elif grund not in ("nicht lesbar", "keine Angaben"): gruende.append(f"{q['name']}: {grund}"); texte.append(f"[{q['url']}] {txt[:1200]}")
        if not ergebnisse: return x, None, "; ".join(gruende), "\n".join(texte), 0
        f, grund, best = kombinieren(ergebnisse)
        return x, f, grund, "\n".join(texte), best
    pruef, ok = defaultdict(list), []
    with cf.ThreadPoolExecutor(6 if ki else 16) as ex:
        for x, f, grund, txt, best in ex.map(huette, jobs):
            if f:
                sicher = "bestaetigt" if best >= 1 else ("eine_quelle" if ki else "unklar")
                ok.append({"t": x["t"], "id": x["id"], "url": f["quellen"][0]["url"], "art_lesen": "ki" if ki else "regel", "sicherheit": sicher, **f})
                stat["bestaetigt" if best >= 1 else "eine_quelle"] += 1
            elif grund:
                pruef[x["l"] + "-" + x["r"]].append(f"## {x['n']} ({x['t']}/{x['id']})\n{grund}\n{txt}\n")
    if ki: json.dump({**cache, **neu_cache}, open(cache_p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(ok, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    for f_ in os.listdir(pruefdir):
        if f_.startswith("pruef-"): os.remove(os.path.join(pruefdir, f_))
    for k, v in pruef.items():
        with open(os.path.join(pruefdir, f"pruef-{re.sub(r'[^A-Za-z0-9ÄÖÜäöüß-]+', '_', k)}.md"), "w", encoding="utf-8") as fo:
            fo.write(f"# Prüfliste {k} ({HEUTE})\n\nNicht eindeutig lesbar. Nur Fakten übernehmen, Regeln aus Prompt 1.\n\n" + "\n".join(v))
    print(f"gelesen: {len(ok)}, Prüfliste: {sum(len(v) for v in pruef.values())}, Abläufe: {dict(stat)}, KI-Token ein/aus: {tok['in']}/{tok['out']}")

# ---------- Datenbank (braucht das GitHub-Secret SUPABASE_SERVICE_KEY) ----------
SB_URL = os.environ.get("SB_URL", "https://vehrlrjegxlhxibrxqxm.supabase.co")
def sb(method, path, body=None, prefer=None):
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(SB_URL + "/rest/v1/" + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r: return r.read()

def db_neu(neu):
    """neue Einträge anlegen, vorhandene (z. B. Vorarlberg) bleiben unverändert"""
    rows = [{"osm_typ": x["t"], "osm_id": x["id"], "name": x["n"], "art": x["a"], "geom": f"SRID=4326;POINT({x['lon']} {x['lat']})",
             "hoehe": x["h"], "land": x["l"], "region": x["r"], "verpflegung": "unbekannt", "quelle": "OpenStreetMap",
             "quelle_url": f"https://www.openstreetmap.org/{x['t']}/{x['id']}", "pruef_datum": HEUTE, "sicherheit": "unklar", "status": "aktiv",
             "daten": {"lauf": "dach", "recherche": "offen", "osm_tag": x["tag"], "osm_webseite": x["w"]}} for x in json.load(open(neu, encoding="utf-8"))]
    for i in range(0, len(rows), 500):
        sb("POST", "gb_huetten?on_conflict=osm_typ,osm_id", rows[i:i + 500], "resolution=ignore-duplicates,return=minimal")
    print(f"{len(rows)} Einträge gemeldet (vorhandene bleiben unverändert)")

def db_auto(auto):
    """gelesene Werte eintragen – nur bei Einträgen, die noch niemand recherchiert hat (offen oder schon auto)"""
    n = 0
    for e in json.load(open(auto, encoding="utf-8")):
        flt = f"osm_typ=eq.{e['t']}&osm_id=eq.{e['id']}&daten->>recherche=in.(offen,auto)"
        try:
            cur = json.loads(sb("GET", f"gb_huetten?select=daten&{flt}"))
            if not cur: continue
            ki = e.get("art_lesen") == "ki"
            body = {k: e.get(k) for k in ("oeffnung", "saison_sommer", "saison_winter", "ruhetage", "zeiten")}
            qs = e.get("quellen") or [{"url": e["url"], "art": "webseite", "name": "Eigene Webseite"}]
            namen = ", ".join(dict.fromkeys(q["name"] for q in qs))
            web = next((q["url"] for q in qs if q["art"] == "webseite"), None)
            body.update({"verpflegung": e.get("verpflegung", "unbekannt"), "webseite": web,
                         "quelle": f"{namen} ({'automatisch gelesen, Zitat geprüft' if ki else 'maschinell gelesen'})",
                         "quelle_url": e["url"], "quelle_datum": HEUTE, "pruef_datum": HEUTE,
                         "sicherheit": e.get("sicherheit") or ("eine_quelle" if ki else "unklar"),
                         "daten": {**cur[0]["daten"], "recherche": "auto", "auto_datum": HEUTE, "auto_art": e.get("art_lesen", "regel"),
                                   "belege": e.get("belege"), "quellen_weitere": [q["url"] for q in qs[1:]] or None}})
            sb("PATCH", f"gb_huetten?{flt}", body, "return=minimal"); n += 1
        except Exception as ex:
            print("Fehler", e["id"], str(ex)[:80])
    print(f"{n} Einträge befüllt")

if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    if c == "regionen": regionen(sys.argv[2], sys.argv[3])
    elif c == "inventar": inventar(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:])
    elif c == "zusammen": zusammen(sys.argv[2], sys.argv[3])
    elif c == "sammeln": sammeln(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else None)
    elif c == "db-neu": db_neu(sys.argv[2])
    elif c == "db-auto": db_auto(sys.argv[2])
    else: print(__doc__); sys.exit(2)
