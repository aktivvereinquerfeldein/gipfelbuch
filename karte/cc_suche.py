#!/usr/bin/env python3
"""Eigene Suche nach Hüttenseiten im offenen Web-Archiv Common Crawl (frei, ohne Konto, ohne Suchmaschine).
Common Crawl veröffentlicht jeden Monat einen Index aller gecrawlten Seiten (Parquet, nach Adresse sortiert). Daraus holen wir
alle Adressen der Länder-Endungen (.at, .de, .it, .ch, .tirol …) und suchen:
1. eigene Webseiten: Domain = Hüttenname (zistelalm.at, lucknerhuette.at); das Hüttenwort steht wie im Namen am Ende.
2. Detailseiten seriöser Herausgeber: letzter Teil der Adresse = Hüttenname (…/huette/rockfeldalm/), nur bei Gemeinden,
   Tourismusverbänden, Alpenverein/Naturfreunden, Forsten, Nationalparks und Seiten, die erkennbar Hüttenverzeichnisse sind.
Jeder Treffer wird danach selbst abgerufen (robots.txt, eigener Name des Sammlers) und gilt nur, wenn die Seite den Namen
der Hütte und einen Ort aus der Umgebung (Gemeinde bis 25 km) nennt.
Aufruf: cc_suche.py <neu.json> <quellen-cc.json>     (braucht duckdb: pip install duckdb)
Umgebung: CC_CRAWL (Komma-Liste) oder CC_CRAWLS (Anzahl der neuesten, Standard 3), CC_TLDS (Standard at,de,it,ch,li,tirol,wien,eu,info)"""
import gzip, json, math, os, re, sys, time, urllib.parse, urllib.request
import concurrent.futures as cf
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sammler, quellen

BASIS = "https://data.commoncrawl.org/"
FREMDSPRACHE = re.compile(r"/(en|it|nl|fr|cs|pl|hu|da|sv|sk|sl|ru|es|uk|zh|ja)(/|$)", re.I)
# Seitenarten, die nicht von der Hütte handeln oder nichts belegen
PFAD_NEIN = re.compile(r"/(tag|tags|category|kategorie|author|autor|blog|news|aktuelles|presse|galerie|gallery|bilder|fotos|foto|"
                       r"webcam|webcams|video|videos|unterkunft|unterkuenfte|zimmer|hotel|hoefe|ferienwohnung|appartement|angebot|"
                       r"pauschale|veranstaltung|veranstaltungen|events?|termine|veranstaltungsort|jobs?|stellen|karriere|shop|"
                       r"produkt|immobilien|strom|wetter|ngg_tag|wp-content|feed|kommentar|forum|archiv)(/|$)|\?", re.I)
# seriöse Herausgeber (zusätzlich: Domains aus verzeichnisse.txt und die Webseiten der Gemeinden)
SERIOES = re.compile(r"(tourism|tourismus|gemeinde|marktgemeinde|stadt-|alpenverein|naturfreunde|\bdav\b|dav-|sektion|oeav|"
                     r"wanderdoerfer|bergsteigerdoerfer|landesforste|bundesforste|nationalpark|naturpark|biosphaer|genussregion|"
                     r"tirol\.at$|salzkammergut|oberoesterreich|niederoesterreich|steiermark|kaernten|salzburgerland|vorarlberg|"
                     r"wieneralpen|mostviertel|waldviertel|weinviertel|burgenland|osttirol|\.gv\.at$|\.tirol$|almwirtschaft|"
                     r"almen-|almwandern|bergbahn|seilbahn)", re.I)
# Blogs, Tourenberichte, Vermietung: keine Quelle für Öffnungszeiten
NICHT_SERIOES = re.compile(r"(hoehenrausch|tourentagebuch|freizeitmonster|httenmax|huettenmax|gruppenhaus|blog|reise|travel|"
                           r"urlaubambauernhof|restauranttester|tripadvisor|falstaff|wetter|webcam|megacam|skischul|veranstalter|"
                           r"dolni-rakousko|tourentipp|wandertipp|mtb|bergtour-online)", re.I)
VERZ_PFAD = re.compile(r"/(huetten?|alm|almen|alpen?|einkehr|gastronomie|gastro|kulinarik|poi|pois|info|ausflugsziele?|"
                       r"betriebe|essen-trinken|restaurants?|jausenstationen?|schutzhuetten|bergrestaurants?)(/|$)", re.I)


def km(a, b, c, d):
    return math.hypot((a - c) * 111.2, (b - d) * 111.2 * math.cos(math.radians(a)))


def crawls_neueste(n):
    with urllib.request.urlopen(urllib.request.Request("https://index.commoncrawl.org/collinfo.json",
                                                       headers={"User-Agent": sammler.UA}), timeout=60) as r:
        return [c["id"] for c in json.load(r)[:n]]


def dateien(crawl, tlds):
    """Index-Dateien, deren Adressbereich die Endungen enthält (Mindest-/Höchstwert aus den Parquet-Metadaten)"""
    import duckdb
    with urllib.request.urlopen(urllib.request.Request(f"{BASIS}crawl-data/{crawl}/cc-index-table.paths.gz",
                                                       headers={"User-Agent": sammler.UA}), timeout=120) as r:
        paths = [l for l in gzip.decompress(r.read()).decode().split() if "subset=warc" in l]
    def mm(p):
        con = duckdb.connect(); con.execute("INSTALL httpfs; LOAD httpfs;")
        for _ in range(4):
            try:
                return p, con.execute(f"select min(stats_min_value), max(stats_max_value) from parquet_metadata('{BASIS}{p}') "
                                      f"where path_in_schema='url_surtkey'").fetchone()
            except Exception: time.sleep(3)
        return p, None
    with cf.ThreadPoolExecutor(8) as ex: rng = list(ex.map(mm, paths))
    out = defaultdict(list)
    for t in tlds:
        a, b = f"{t},", f"{t}-"   # SURT: „at,zistelalm)/…“; „-“ folgt direkt auf „,“
        out[t] = [p for p, m in rng if m and m[0] < b and m[1] >= a]
    return out


def adressen(crawls, tlds, aus):
    """alle HTML-Adressen der Endungen mit Hütten-Wörtern im Pfad und alle Domains -> Parquet (mehrere Crawls vereint:
    jeder Crawl erfasst nur einen Teil des Netzes)"""
    import duckdb
    con = duckdb.connect(); con.execute("INSTALL httpfs; LOAD httpfs;")
    # Common Crawl bremst zu viele gleichzeitige Abrufe (HTTP 403/503): wenige Threads, Wiederholungen, jeder Teil einzeln
    con.execute("SET threads=4; SET http_retries=8; SET http_retry_wait_ms=3000; SET http_retry_backoff=2;")
    ordner = aus + ".teile"; os.makedirs(ordner, exist_ok=True)
    for crawl in crawls:
        for t, ps in dateien(crawl, tlds).items():
            if not ps: continue
            ziel = os.path.join(ordner, f"{crawl}-{t}.parquet")
            if os.path.exists(ziel): continue
            lst = ",".join(f"'{BASIS}{p}'" for p in ps)
            for versuch in range(4):
                try:
                    con.execute(f"""copy (select url, url_host_registered_domain d, url_path p from read_parquet([{lst}])
                        where url_surtkey >= '{t},' and url_surtkey < '{t}-' and fetch_status = 200 and content_mime_detected = 'text/html'
                        and (url_path = '/' or url_path = '' or regexp_matches(lower(url_path),
                        '(alm|alpe|alp[-_/.]|h(ue|%c3%bc|u)tte|haus|jausen|stub|gasth|hof|beisl|wirt)')))
                        to '{ziel}.tmp' (format parquet)""")
                    os.replace(ziel + ".tmp", ziel); print(f"{crawl} .{t}: {len(ps)} Index-Dateien gelesen", flush=True); break
                except Exception as e:
                    print(f"{crawl} .{t}: Versuch {versuch + 1} gescheitert ({str(e)[:80]})", flush=True); time.sleep(60 * (versuch + 1))
    con.execute(f"copy (select distinct url, d, p from '{ordner}/*.parquet') to '{aus}.tmp' (format parquet)")
    os.replace(aus + ".tmp", aus)   # erst fertig, dann unter dem echten Namen (ein Abbruch hinterlässt keine halbe Datei)
    n = con.execute(f"select count(*), count(distinct d) from '{aus}'").fetchone()
    print(f"Common Crawl {', '.join(crawls)}: {n[0]} Adressen von {n[1]} Domains", flush=True)


def seriose_domains():
    s = set()
    for v in quellen.verzeichnisse(): s.add(urllib.parse.urlparse(v["url"]).netloc.lower().removeprefix("www."))
    for g in quellen.gemeinden_liste(): s.add(urllib.parse.urlparse(g["web"]).netloc.lower().removeprefix("www."))
    return s


def slug(p):
    seg = urllib.parse.unquote(p.rstrip("/").rsplit("/", 1)[-1])
    seg = re.sub(r"(\.html?|\.php|\.aspx?)$", "", seg)
    seg = re.sub(r"(_vc\d+|[-_]\d+)$", "", seg)
    return quellen.norm(seg)


def kandidaten(huts, pq):
    import duckdb
    rows = duckdb.connect().execute(f"select url, d, p from '{pq}'").fetchall()
    tab = quellen.link_schnell(huts)
    nach = defaultdict(list)
    for h in huts:
        n = quellen.norm(h["n"])
        if len(n) >= 7: nach[n].append(h)
    ser = seriose_domains()
    web, seiten, je_dom = defaultdict(set), defaultdict(set), defaultdict(set)
    for u, d, p in rows:
        if p in ("", "/"):
            k = quellen.link_huette_schnell("https://" + d + "/", tab)
            if k: web[k].add(f"https://{d}/")
            continue
        if FREMDSPRACHE.search(p) or PFAD_NEIN.search(u): continue
        xs = nach.get(slug(p))
        if xs and len(xs) == 1:
            k = quellen.schluessel(xs[0]); seiten[k].add((u, d)); je_dom[d].add(k)
    # seriös: bekannter Herausgeber, oder ein Verzeichnis (viele verschiedene Hütten unter Verzeichnis-Pfaden)
    # Verzeichnis nur bei österreichischen Endungen (deutsche Tourenblogs, Reiseveranstalter sonst dabei)
    verz = {d for d, ks in je_dom.items() if len(ks) >= 5 and not NICHT_SERIOES.search(d) and re.search(r"\.(at|tirol|wien)$", d)}
    gut = defaultdict(list)
    for k, lst in seiten.items():
        for u, d in lst:
            pfad = urllib.parse.urlparse(u).path
            if NICHT_SERIOES.search(d): continue
            if d in ser or SERIOES.search(d) or (d in verz and VERZ_PFAD.search(pfad)): gut[k].append((u, d))
    return web, gut, verz


def orte_nah(gem, h, km_max=25):
    """Gemeinden im Umkreis, mit Kurzform („Neustift im Stubaital“ -> auch „Neustift“)"""
    out = set()
    for g in gem:
        if km(h["lat"], h["lon"], g["lat"], g["lon"]) > km_max: continue
        n = re.sub(r"[^a-zäöüß0-9]+", " ", g["name"].lower()).strip(); out.add(n)
        kurz = re.split(r" (im|am|an der|an|bei|in der|in|ob der|ob)\b ", n)[0].strip()
        if len(kurz) >= 5: out.add(kurz)
    return out

def ort_drin(zeilen, orte):
    t = " " + re.sub(r"[^a-zäöüß0-9]+", " ", " ".join(zeilen).lower()) + " "
    return any(f" {o} " in t for o in orte)

def pruefen(huts, web, seiten, threads=16):
    """jede Seite selbst abrufen: Name der Hütte und ein Ort aus der Umgebung müssen draufstehen"""
    gem = [g for g in quellen.gemeinden_liste() if len(g["name"]) >= 4]
    hut = {quellen.schluessel(h): h for h in huts}
    jobs = [(k, u, "webseite") for k, us in web.items() for u in sorted(us)] + \
           [(k, u, "verzeichnis") for k, lst in seiten.items() for u, _ in sorted(lst)[:3]]
    def eins(j):
        k, u, art = j; h = hut[k]
        raw, err = sammler.holen(u)
        if err or not raw: return j, "nicht lesbar"
        z, links = sammler.text_haupt(raw)
        tn = quellen.norm(" ".join(z))
        kern = quellen._link_kern(h["n"])
        if not kern or not all(w in tn for w in kern): return j, "Name fehlt"
        orte = orte_nah(gem, h)
        if ort_drin(z, orte): return j, None
        # Adresse steht verlässlich im Impressum / Kontakt / Anreise derselben Seite
        weiter = [urllib.parse.urljoin(u, l[0]) for l in links if isinstance(l, tuple) and re.search(r"impressum|kontakt|anreise|anfahrt|lage|contact", (l[0] or "") + " " + (l[1] or ""), re.I)]
        for w in list(dict.fromkeys(x for x in weiter if urllib.parse.urlparse(x).netloc == urllib.parse.urlparse(u).netloc))[:2]:
            r2, e2 = sammler.holen(w)
            if r2 and ort_drin(sammler.text_haupt(r2)[0], orte): return j, None
        return j, "kein Ort aus der Umgebung"
    ok, grund = defaultdict(list), Counter()
    with cf.ThreadPoolExecutor(threads) as ex:
        for (k, u, art), g in ex.map(eins, jobs):
            grund[g or "ok"] += 1
            if not g: ok[k].append((u, art))
    print(f"Geprüft: {len(jobs)} Seiten, {dict(grund)}", flush=True)
    return ok


def main(neu, aus):
    huts = json.load(open(neu, encoding="utf-8"))
    crawls = os.environ["CC_CRAWL"].split(",") if os.environ.get("CC_CRAWL") else crawls_neueste(int(os.environ.get("CC_CRAWLS", "3")))
    tlds = os.environ.get("CC_TLDS", "at,de,it,ch,li,tirol,wien,eu,info").split(",")
    pq = os.path.join(os.path.dirname(os.path.abspath(aus)), "cc_adressen.parquet")
    if not os.path.exists(pq): adressen(crawls, tlds, pq)
    web, seiten, verz = kandidaten(huts, pq)
    print(f"Kandidaten: {len(web)} Hütten mit eigener Domain, {len(seiten)} mit Seite bei seriösem Herausgeber, "
          f"{len(verz)} Verzeichnis-Domains", flush=True)
    ok = pruefen(huts, web, seiten)
    q = defaultdict(list)
    for k, lst in ok.items():
        for u, art in lst:
            d = urllib.parse.urlparse(u).netloc.removeprefix("www.")
            q[k].append({"url": u, "art": art, "name": "Eigene Webseite (Common Crawl)" if art == "webseite" else d})
    json.dump(q, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"Common Crawl: {sum(1 for l in q.values() if any(x['art'] == 'webseite' for x in l))} Hütten mit eigener Webseite, "
          f"{sum(1 for l in q.values() if any(x['art'] == 'verzeichnis' for x in l))} mit Seite bei seriösem Herausgeber, "
          f"zusammen {len(q)} Hütten", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3: print(__doc__); sys.exit(2)
    main(sys.argv[1], sys.argv[2])
