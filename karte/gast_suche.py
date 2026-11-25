#!/usr/bin/env python3
"""Webseiten der Gaststätten finden und sicher zuordnen (vor dem Lesen der Öffnungszeiten).

Quellen je Gaststätte (Reihenfolge = Vorrang):
1. OSM-Webseite – wird geprüft: gehört sie wirklich zu diesem Lokal? (Name und/oder Adresse/PLZ/Telefon auf der Seite,
   keine geparkte Domain, keine Plattform). Passt sie nicht, kommt sie in den Bericht und wird nicht gelesen.
2. E-Mail-Domain aus OSM (info@gasthof-krone.at -> gasthof-krone.at), nicht bei Gratis-Anbietern (gmx, aon, gmail …).
3. Wikidata (offizielle Webseite P856), wenn das Lokal eine Wikidata-Nummer hat.
4. Common Crawl (offenes Web-Archiv, ohne Suchmaschine): Domain = Lokalname (gasthof-krone.at, krone-hittisau.at,
   cafe-sacher.at). Mehrere Lokale mit gleichem Namen sind normal – deshalb zählt eine gefundene Seite nur, wenn darauf
   der Name UND die Adresse (PLZ, Straße oder Telefonnummer) des Lokals stehen (auch im Impressum/Kontakt derselben Seite).
Nur lesen: robots.txt, 1 Anfrage pro Sekunde und Seite, keine Anmeldung, keine Formulare.

Aufruf: gast_suche.py finden <neu.json> <quellen.json>     Umgebung QUELLEN_TEIL = osm:i/n | cc
        gast_suche.py zusammen <quellen.json> <teil.json>...
        gast_suche.py test <neu.json> <osm-id> <url>          eine Seite gegen ein Lokal prüfen
Ergebnis: {"node/123": {"url": ..., "art": "osm|mail|wikidata|cc", "beleg": "name+plz"}}, dazu "_falsch": {"node/9": grund}
"""
import json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
import concurrent.futures as cf
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sammler

HIER = os.path.dirname(os.path.abspath(__file__))

def schluessel(x): return f"{x['t']}/{x['id']}"

def norm(n):
    n = unicodedata.normalize("NFKD", (n or "").lower())
    n = n.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", n)

def woerter(n):
    return [norm(w) for w in re.split(r"[\s\-/,.()'’`&+|:]+", n or "") if norm(w)]

# Wörter, die nichts über das einzelne Lokal sagen
TYPWORT = {"gasthof", "gasthaus", "landgasthof", "landgasthaus", "wirtshaus", "dorfwirtshaus", "gaststaette", "gastwirtschaft",
           "restaurant", "ristorante", "pizzeria", "trattoria", "osteria", "cafe", "caffe", "kaffee", "kaffeehaus", "konditorei",
           "cafekonditorei", "bar", "pub", "beisl", "beisel", "heuriger", "heurigen", "buschenschank", "buschenschenke", "stueberl",
           "stube", "imbiss", "wirt", "hotel", "pension", "bistro", "brasserie", "lounge", "grill", "steakhouse", "gastro",
           "weinbar", "weinstube", "bierstube", "braeu", "brauhaus", "biergarten", "eisdiele", "eissalon", "baeckerei",
           "zur", "zum", "zu", "beim", "bei", "der", "die", "das", "am", "im", "an", "und", "s", "the", "da", "la", "il", "al"}
GENERISCH = {"post", "krone", "adler", "hirschen", "loewen", "sonne", "rose", "linde", "stern", "engel", "baeren", "ochsen",
             "kreuz", "schwan", "lamm", "rössle", "roessle", "traube", "mohren", "hirsch", "alpenrose", "edelweiss", "central",
             "zentral", "dorf", "dorfplatz", "kirchenwirt", "brueckenwirt", "seewirt", "platzl", "linden", "bahnhof", "schloss",
             "rathaus", "stadt", "markt", "berg", "see", "alte", "alt", "neue", "neu", "goldener", "goldene", "goldenen",
             "weisses", "weisse", "schwarzer", "roter", "gruener", "blauer"}

def kern(name):
    return [w for w in woerter(name) if w not in TYPWORT and len(w) >= 2]

FREEMAIL = re.compile(r"@(gmx\.|aon\.|a1\.net|gmail\.|googlemail\.|hotmail\.|outlook\.|live\.|yahoo\.|icloud\.|me\.com|web\.de|"
                      r"t-online\.|chello\.|utanet\.|telering\.|tele2\.|inode\.|liwest\.|sbg\.at|vol\.at|tirol\.com|kabsi\.|"
                      r"kabelplus\.|magenta\.|drei\.at|speed\.at|cnh\.at|eunet\.|netway\.|aon\.cc|gmx\.net|bluewin\.|hispeed\.|"
                      r"salzburg\.co\.at|kstp\.at|nextra\.|direkt\.at|kabelsignal|ainet\.|wvnet\.|stmk\.net|silverserver|"
                      r"mail\.|posteo\.|proton|protonmail|freenet\.|libero\.|virgilio\.|tin\.it|email\.it|hotmail)", re.I)
PLATTFORM = re.compile(r"(facebook\.|instagram\.|tripadvisor\.|google\.|goo\.gl|yelp\.|thefork\.|lieferando\.|foursquare\.|"
                       r"falstaff\.|wikipedia\.|booking\.com|holidaycheck|linktr\.ee|tiktok\.|herold\.at|firmen\.|wko\.at|"
                       r"speisekarte\.|restaurantguru|mjam\.|foodora\.|wolt\.)", re.I)
GEPARKT = re.compile(r"(domain (ist |is )?(zu verkaufen|for sale|kaufen)|diese domain|this domain (is|may be)|sedo|parkingcrew|"
                     r"domain parking|bodis\.com|dan\.com|hier entsteht|under construction|website coming soon|"
                     r"seite (ist )?(in|im) (bau|aufbau)|webhosting|default web site page|welcome to nginx|it works!)", re.I)

def ziffern(s): return re.sub(r"\D", "", s or "")

def adress_belege(x, text):
    """welche Adressangaben des Lokals stehen im Text? (PLZ, Straße, Telefon)"""
    b = []
    t = " " + text.lower() + " "
    if x.get("plz") and re.search(r"(?<!\d)" + re.escape(x["plz"].strip()) + r"(?!\d)", t): b.append("plz")
    st = norm(re.sub(r"(straße|strasse|str\.|gasse|weg|platz|allee|ring)$", "", (x.get("str") or "").strip(), flags=re.I))
    if len(st) >= 4 and st in norm(text): b.append("strasse")
    tel = ziffern(x.get("tel"))
    if len(tel) >= 7 and tel[-6:] in ziffern(text): b.append("telefon")
    return b

def name_belegt(x, text):
    tn = norm(text); k = kern(x["n"])
    if k and all(w in tn for w in k): return True
    return len(norm(x["n"])) >= 6 and norm(x["n"]) in tn

def domain_ist_name(x, url):
    d = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    label = norm(".".join(d.split(".")[:-1]))
    k = "".join(kern(x["n"]))
    return len(k) >= 4 and (k in label or norm(x["n"]) in label)

KONTAKT = re.compile(r"(impressum|kontakt|contact|anfahrt|anreise|lage|about|ueber-uns|über-uns|imprint)", re.I)

def seite_pruefen(x, url, streng=True):
    """gehört die Seite zu diesem Lokal? gibt (ok, grund/beleg). streng = Name UND Adresse (gefundene Seiten),
    sonst Name ODER Adresse (Webseite aus OSM, dort von Hand eingetragen)"""
    raw, err = sammler.holen(url)
    if err or not raw: return False, "nicht lesbar"
    z, links = sammler.text_haupt(raw)
    text = " ".join(z)
    if len(text) < 300 and GEPARKT.search(text) or GEPARKT.search(text[:1500]) and len(text) < 3000:
        return False, "Domain geparkt/im Aufbau"
    if len(text) < 150: return (True, "Adresse = Name (Seite lädt per Skript)") if not streng and domain_ist_name(x, url) else (False, "kaum Text (Seite lädt per Skript)")
    nb, ab = name_belegt(x, text), adress_belege(x, text)
    if not streng and domain_ist_name(x, url) and not nb: nb = True   # OSM-Webseite mit dem Namen als Adresse (kaffeealtwien.at)
    if not (nb and ab) and (streng or not (nb or ab)):
        host = urllib.parse.urlparse(url).netloc
        weiter = []
        for l in links:
            href, txt = (l[0], l[1]) if isinstance(l, tuple) else (l, "")
            u = urllib.parse.urljoin(url, href or "")
            if urllib.parse.urlparse(u).netloc == host and KONTAKT.search((href or "") + " " + (txt or "")): weiter.append(u.split("#")[0])
        for u in list(dict.fromkeys(weiter))[:2]:
            r2, _ = sammler.holen(u)
            if not r2: continue
            t2 = " ".join(sammler.text_haupt(r2)[0])
            nb = nb or name_belegt(x, t2); ab = sorted(set(ab) | set(adress_belege(x, t2)))
            if nb and ab: break
    if nb and ab: return True, "name+" + "+".join(ab)
    if not streng and (nb or ab): return True, "name" if nb else "+".join(ab)
    if nb and not (x.get("plz") or x.get("str") or x.get("tel")):
        return (True, "name (keine Adresse in OSM)") if not streng else (False, "nur Name, keine Adresse zum Vergleich")
    return False, "Name fehlt" if not nb else "Adresse fehlt"

# ---------- Quellen ----------
def osm_und_mail(lokale, teil):
    nr, anz = teil
    jobs = [x for i, x in enumerate(sorted(lokale, key=schluessel)) if i % anz == nr]
    out, falsch, stat = {}, {}, Counter()
    def eins(x):
        k = schluessel(x)
        if x.get("w") and not x.get("skip") and not PLATTFORM.search(x["w"]):
            ok, g = seite_pruefen(x, x["w"], streng=False)
            if ok: return k, {"url": x["w"], "art": "osm", "beleg": g}, None
            fehler = g
        else: fehler = None
        m = x.get("mail") or ""
        if "@" in m and not FREEMAIL.search(m):
            dom = m.split("@", 1)[1].strip().lower().rstrip(".")
            if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", dom) and not PLATTFORM.search(dom):
                for u in (f"https://{dom}/", f"https://www.{dom}/"):
                    ok, g = seite_pruefen(x, u, streng=True)
                    if ok: return k, {"url": u, "art": "mail", "beleg": g}, fehler
                    if g != "nicht lesbar": break
        return k, None, fehler
    with cf.ThreadPoolExecutor(int(os.environ.get("THREADS", "24"))) as ex:
        for k, q, fehler in ex.map(eins, jobs):
            if q: out[k] = q; stat[q["art"]] += 1
            if fehler and fehler != "nicht lesbar" and (not q or q["art"] != "osm"): falsch[k] = fehler; stat["osm_passt_nicht"] += 1
            elif fehler == "nicht lesbar": stat["osm_nicht_lesbar"] += 1
    print(f"Teil {nr}/{anz}: {len(jobs)} Lokale, {dict(stat)}", flush=True)
    return out, falsch

def wikidata(lokale):
    ids = sorted({x["wd"] for x in lokale if re.match(r"^Q\d+$", x.get("wd") or "")})
    web = {}
    for i in range(0, len(ids), 50):
        try:
            req = urllib.request.Request("https://www.wikidata.org/w/api.php?action=wbgetentities&props=claims&format=json&ids=" + "|".join(ids[i:i + 50]),
                                         headers={"User-Agent": sammler.UA})
            with urllib.request.urlopen(req, timeout=60) as r: d = json.load(r)
        except Exception as e: print("Wikidata-Fehler", str(e)[:80]); continue
        for q, ent in (d.get("entities") or {}).items():
            for c in (ent.get("claims") or {}).get("P856", []):
                v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                if isinstance(v, str) and v.startswith("http"): web[q] = v; break
    return web

def varianten(x):
    """Domain-Namen, unter denen das Lokal seine Seite haben könnte (ohne Endung, nur a-z0-9)"""
    w = woerter(x["n"]); k = kern(x["n"]); ort = norm(x.get("ort") or "")
    voll, kk = "".join(w), "".join(k)
    out = set()
    if len(voll) >= 6 and voll != kk: out.add(voll)                       # gasthofkrone
    if kk and (len(kk) >= 9 or (len(k) >= 2 and len(kk) >= 7)) and not (len(k) == 1 and k[0] in GENERISCH): out.add(kk)   # stiegenwirt…
    if ort and kk and len(ort) >= 3:
        out |= {kk + ort, ort + kk, voll + ort}                            # kronehittisau, gasthofkronehittisau
    typ = [t for t in w if t in TYPWORT and len(t) >= 3]
    for t in typ[:1]:
        if kk: out |= {kk + t, t + kk}                                     # kronegasthof, pizzeriamario
    return {v for v in out if 6 <= len(v) <= 40}

def common_crawl(lokale, ordner):
    import cc_suche, duckdb
    crawls = os.environ["CC_CRAWL"].split(",") if os.environ.get("CC_CRAWL") else cc_suche.crawls_neueste(int(os.environ.get("CC_CRAWLS", "3")))
    tlds = os.environ.get("CC_TLDS", "at,wien,tirol,info,eu").split(",")
    pq = os.path.join(ordner, "cc_adressen.parquet")
    if not os.path.exists(pq): cc_suche.adressen(crawls, tlds, pq)
    doms = [d for (d,) in duckdb.connect().execute(f"select distinct d from '{pq}' where p in ('', '/')").fetchall() if d]
    tab = defaultdict(list)
    for x in lokale:
        for v in varianten(x): tab[v].append(x)
    jobs = []
    for d in doms:
        label = norm(".".join(d.lower().split(".")[:-1]).removeprefix("www."))
        for x in tab.get(label, []): jobs.append((x, f"https://{d}/"))
    print(f"Common Crawl: {len(doms)} Domains, {len(jobs)} Kandidaten für {len({schluessel(x) for x, _ in jobs})} Lokale", flush=True)
    out, stat = {}, Counter()
    def eins(j):
        x, u = j
        ok, g = seite_pruefen(x, u, streng=True)
        return x, u, ok, g
    treffer = defaultdict(list)
    with cf.ThreadPoolExecutor(int(os.environ.get("THREADS", "24"))) as ex:
        for x, u, ok, g in ex.map(eins, jobs):
            stat["ok" if ok else g] += 1
            if ok: treffer[schluessel(x)].append({"url": u, "art": "cc", "beleg": g})
    # eine Domain darf nur zu einem Lokal gehören (sonst Kette/Sammelseite -> nicht eindeutig)
    je_url = Counter(q["url"] for l in treffer.values() for q in l)
    for k, l in treffer.items():
        l = [q for q in l if je_url[q["url"]] == 1]
        if len({urllib.parse.urlparse(q["url"]).netloc for q in l}) == 1: out[k] = l[0]
    print(f"Common Crawl geprüft: {dict(stat)}, {len(out)} Lokale mit eindeutig zugeordneter Webseite", flush=True)
    return out

def finden(neu, aus):
    lokale = [x for x in json.load(open(neu, encoding="utf-8")) if not x.get("marke")]   # Ketten (brand) haben Sammelseiten
    tl = os.environ.get("QUELLEN_TEIL", "osm:0/1")
    out, falsch = {}, {}
    if tl.startswith("osm:"):
        nr, anz = map(int, tl[4:].split("/"))
        out, falsch = osm_und_mail(lokale, (nr, anz))
        if nr == 0:
            web = wikidata(lokale); n = 0
            for x in lokale:
                u = web.get(x.get("wd") or "")
                if u and schluessel(x) not in out and not PLATTFORM.search(u):
                    ok, g = seite_pruefen(x, u, streng=False)
                    if ok: out[schluessel(x)] = {"url": u, "art": "wikidata", "beleg": g}; n += 1
            print(f"Wikidata: {len(web)} Webseiten, {n} neu zugeordnet", flush=True)
    elif tl == "cc":
        ohne = [x for x in lokale if not x.get("w") or x.get("skip")]
        ohne += [x for x in lokale if x.get("w") and not x.get("skip")]   # auch mit OSM-Seite: falls die nicht passt
        out = common_crawl(ohne, os.path.dirname(os.path.abspath(aus)))
    json.dump({**out, "_falsch": falsch}, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

RANG = {"osm": 0, "mail": 1, "wikidata": 2, "cc": 3}

def zusammen(aus, teile):
    q, falsch = {}, {}
    for p in teile:
        try: d = json.load(open(p, encoding="utf-8"))
        except Exception: continue
        falsch.update(d.pop("_falsch", {}))
        for k, v in d.items():
            if k not in q or RANG[v["art"]] < RANG[q[k]["art"]]: q[k] = v
    json.dump({**q, "_falsch": {k: v for k, v in falsch.items() if k not in q or q[k]["art"] != "osm"}}, open(aus, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print(f"Webseiten zugeordnet: {len(q)} Lokale ({dict(Counter(v['art'] for v in q.values()))}), OSM-Webseite passt nicht: {len(falsch)}")

def test(neu, oid, url):
    x = next(x for x in json.load(open(neu, encoding="utf-8")) if str(x["id"]) == oid)
    print(x); print(seite_pruefen(x, url, streng=True)); print("Varianten:", sorted(varianten(x)))

if __name__ == "__main__":
    c, a = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if c == "finden": finden(*a)
    elif c == "zusammen": zusammen(a[0], a[1:])
    elif c == "test": test(*a)
    else: print(__doc__); sys.exit(2)
