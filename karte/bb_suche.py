#!/usr/bin/env python3
"""Webseite für Bergbahnen ohne Quelle suchen (Teil von karte/bergbahnen.py, Schritt „quellen“).

Jede Bahn hat eine Webseite – nur steht sie oft nicht in den Kartendaten. Gesucht wird ohne Suchmaschine und ohne KI:
  1. Wikipedia-Artikel zur Bahn (Suche „Name Gemeinde“) -> offizielle Webseite aus Wikidata oder Weblink im Artikel
  2. Objekte mit Webseite rund um Tal- und Bergstation (Kartendaten über Overpass: Station, Gasthaus, Parkplatz, Skigebiet …)
  3. naheliegende Adressen aus dem Namen (z. B. untersbergbahn.at, schoecklseilbahn.at, kasberg.at)
Jeder Fund wird geöffnet und nur genommen, wenn die Seite die Bahn (oder ihren Berg/Ort zusammen mit „Bahn/Lift“) nennt.
Ergebnis: daten/bergbahnen/quellen-gesucht.json {key: {"datum", "url", "woher", "sicher"}}; Fehlschläge werden nach 30 Tagen
erneut versucht.
"""
import json, re, time, datetime, urllib.parse, urllib.request
import concurrent.futures as cf
import bb_lesen as L

UA = None            # wird von bergbahnen.py gesetzt
holen = None         # bergbahnen.holen (robots.txt, Pausen je Webseite, Cookie-Abfragen)
NICHT = None         # bergbahnen.NICHT_QUELLE
OVERPASS = "https://gipfelbuch.quer-feld-ein.workers.dev/overpass"
BAHNWORT = re.compile(r"(bahn|lift|seilbahn|gondel|sessel|bergbahn|skigebiet|funivia|cabinovia|seggiovia|standseilbahn|zahnrad)", re.I)
SAMMEL = re.compile(r"(bergfex|outdooractive|komoot|skiresort|skiinfo|snow-?online|wikipedia|wikimedia|wikidata|facebook|instagram|youtube|google|tripadvisor|"
                    r"booking|holidaycheck|alpenvereinaktiv|alpenverein|openstreetmap|mapy|wetter|seilbahn\.net|lift-world|remontees|bergbahnen\.org)", re.I)


# Anlagen, die keine öffentlichen Bahnen sind (Werks-, Boots-, Schanzen-, Hotelaufzüge)
KEINE = re.compile(r"(boot|schanze|pumpwerk|kraftwerk|schrägaufzug|schraegaufzug|material|werk\b|hotel|privat|baustelle|versorgung)", re.I)


def _api(u, timeout=40):
    for v in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=timeout) as r: return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503): time.sleep(3 + 4 * v); continue
            return None
        except Exception: time.sleep(3 + 5 * v)
    return None


def kern_tokens(b):
    """Namensteile der Bahn, die eine Webseite nennen sollte („untersberg“, „schoeckl“)"""
    t = [x for x in L.tokens(b["name"] or "") if x not in ("sommer", "winter", "fahrplanfeld", "slip", "schienen", "tenuta", "casa")]
    out = set(t)
    for x in t:
        s = re.sub(r"(seilbahn|sesselbahn|sessellift|gondelbahn|kabinenbahn|standseilbahn|bergbahn|bahnen|bahn|lift|express)$", "", x)
        if len(s) >= 4: out.add(s)
    return out


def wikipedia(b):
    """-> [(url, woher)]"""
    if not b["name"]: return []
    q = f'{b["name"]} {b.get("gemeinde") or ""}'.strip()
    lang = "it" if b["land"] == "IT" and not re.search(r"[äöüß]|bahn|lift", b["name"], re.I) else "de"
    u = (f"https://{lang}.wikipedia.org/w/api.php?action=query&format=json&generator=search&gsrlimit=4&gsrsearch=" + urllib.parse.quote(q) +
         "&prop=pageprops|extlinks&ppprop=wikibase_item&ellimit=60")
    j = _api(u)
    if not j: return []
    kt = kern_tokens(b)
    out, qids = [], []
    for p in sorted((j.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 9)):
        tn = L.norm(p.get("title", ""))
        if not any(t in tn.replace(" ", "") for t in kt): continue
        qid = (p.get("pageprops") or {}).get("wikibase_item")
        if qid: qids.append((qid, p["title"]))
        for el in p.get("extlinks", []):
            w = el.get("*") or el.get("url") or ""
            d = urllib.parse.urlparse(w).netloc.lower().removeprefix("www.")
            if d and not SAMMEL.search(d) and any(t in L.norm(d).replace(" ", "") for t in kt):
                out.append((w, f"Wikipedia: {p['title']}"))
    if qids:
        j = _api("https://www.wikidata.org/w/api.php?action=wbgetentities&props=claims&format=json&ids=" + "|".join(q for q, _ in qids[:5]))
        for q_, t_ in qids:
            e = ((j or {}).get("entities") or {}).get(q_) or {}
            for c in (e.get("claims") or {}).get("P856", []):
                w = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                if w and c.get("rank") != "deprecated": out.insert(0, (w, f"Wikidata zu Wikipedia „{t_}“"))
    return out


def umgebung(b):
    """Objekte mit Webseite rund um Tal- und Bergstation (600 m) -> [(url, woher)]"""
    teile = []
    for s in (b["tal"], b["berg"]):
        if s.get("lat"): teile.append(f'nwr(around:600,{s["lat"]:.5f},{s["lon"]:.5f})[~"^(website|contact:website|url)$"~"."];')
    if not teile: return []
    qry = "[out:json][timeout:25];(" + "".join(teile) + ");out tags center 60;"
    j = _api(OVERPASS + "?data=" + urllib.parse.quote(qry), timeout=60)
    if not j: return []
    kt = kern_tokens(b); out = []
    for e in j.get("elements", []):
        t = e.get("tags") or {}
        w = t.get("website") or t.get("contact:website") or t.get("url")
        if not w or not w.startswith("http"): continue
        txt = L.norm(" ".join([t.get("name", ""), t.get("operator", ""), w]))
        art = " ".join(t.get(x, "") for x in ("aerialway", "railway", "landuse", "tourism", "amenity", "site", "leisure", "public_transport"))
        rang = (2 if any(t_ in txt.replace(" ", "") for t_ in kt) else 0) + (1 if BAHNWORT.search(txt + " " + art) or "winter_sports" in art or "station" in art else 0)
        if rang >= 3 or (rang == 1 and ("station" in art or "winter_sports" in art or "aerialway" in t)): out.append((rang, w, f"Kartendaten in der Nähe: {t.get('name') or t.get('operator') or w}"))
    out.sort(key=lambda x: -x[0])
    return [(w, wo) for _, w, wo in out]


def raten(b):
    """naheliegende Adressen aus dem Namen -> [(url, woher)]: voller Name, Berg/Ort aus dem Namen, Name + „bahn/bergbahnen“"""
    if not kern_tokens(b): return []          # „Bergbahn“, „Sommer: Sessellift“ – kein Name, aus dem sich eine Adresse ergibt
    tld = {"AT": ["at", "com"], "DE": ["de", "com"], "CH": ["ch", "com"], "LI": ["li", "ch"], "IT": ["it", "com"]}.get(b["land"], ["at", "de", "com"])
    nm = L.norm(b["name"] or "")
    roh = [w for w in nm.split() if not re.fullmatch(r"\d+|i+|ii|iii|iv|sektion|teil", w)]
    stufen = [[], [], []]
    if roh: stufen[0] += ["".join(roh), "-".join(roh)]
    for t in sorted(kern_tokens(b), key=len, reverse=True):
        stufen[1].append(t)
        if not t.endswith(("bahn", "lift")):
            for e in ("bahn", "bahnen", "seilbahn", "bergbahn", "bergbahnen", "lifte"): stufen[2] += [t + e, t + "-" + e]
    out, gesehen = [], set()
    for st in stufen:
        for s_ in st:
            if 5 <= len(s_) <= 40 and s_ not in gesehen:
                gesehen.add(s_); out += [(f"https://www.{s_}.{t}/", "Adresse aus dem Namen") for t in tld]
    return out[:30]


def pruefen(b, url):
    """Seite öffnen: nennt sie die Bahn? -> (end_url, sicher) oder None"""
    try: st, ct, end, raw, _ = holen(url, 1_500_000, 20)
    except Exception: return None
    if st != 200 or not raw or ("html" not in ct and ct): return None
    d = urllib.parse.urlparse(end).netloc.lower().removeprefix("www.")
    if NICHT and NICHT.search(d) or SAMMEL.search(d): return None
    s = raw.decode("utf-8", "replace")
    txt = L.norm(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)))[:400_000]
    kompakt = txt.replace(" ", "")
    ganz = L.norm(b["name"] or "").replace(" ", "")
    kt = kern_tokens(b)
    if ganz and len(ganz) >= 6 and ganz in kompakt: return end, True
    # „Bergbahn Grünberg“, „Schöckl-Seilbahn“, „Kasberg … Sesselbahn“: Berg/Ort aus dem Namen direkt neben einem Bahnwort
    for t in kt:
        if re.search(r"(?:\b(?:seil|berg|sessel|gondel|kabinen|standseil|zahnrad)?bahn(?:en)?|\blifte?)\s?" + t + r"|" + t + r"\w{0,3}\s?(?:seil|berg|sessel|gondel|kabinen|standseil)?(?:bahn|lift)", txt):
            return end, len(t) >= 6
    return None


def suchen(bahnen, alt, heute):
    """bahnen: Bahnen ohne Quelle (dicts aus bahnen.json); alt: bisheriges quellen-gesucht.json -> neues dict"""
    H = heute.isoformat()
    neu = dict(alt)
    todo = []
    for b in bahnen:
        a = alt.get(b["key"]) or {}
        if a.get("url"): continue
        if a.get("datum") and (heute - datetime.date.fromisoformat(a["datum"])).days < 30: continue
        if not b["name"] or KEINE.search(b["name"]): continue
        todo.append(b)
    print(f"Webseitensuche: {len(todo)} Bahnen ohne Quelle")
    def eine(b):
        gesehen = set()
        for quelle in (raten, wikipedia, umgebung):
            try: kand = quelle(b)
            except Exception: kand = []
            for url, woher in kand:
                d = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
                if not d or d in gesehen or SAMMEL.search(d) or (NICHT and NICHT.search(d)): continue
                gesehen.add(d)
                r = pruefen(b, url)
                # geratene Adresse, die auf eine fremde Seite weiterleitet (Portal, Domainhändler, anderes Skigebiet): nicht nehmen
                if r and quelle is raten:
                    ed = L.norm(urllib.parse.urlparse(r[0]).netloc).replace(" ", "")
                    if not any(t in ed for t in kern_tokens(b)): r = None
                if r: return b["key"], {"datum": H, "url": r[0], "woher": woher, "sicher": r[1]}
        return b["key"], {"datum": H, "url": None}
    with cf.ThreadPoolExecutor(6) as ex:
        for k, v in ex.map(eine, todo): neu[k] = v
    gef = sum(1 for b in todo if (neu.get(b["key"]) or {}).get("url"))
    print(f"Webseitensuche: {gef} von {len(todo)} gefunden")
    return neu
