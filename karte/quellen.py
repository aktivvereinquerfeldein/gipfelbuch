#!/usr/bin/env python3
"""Weitere Quellen je Hütte finden – bevor eine KI etwas liest.
1. Wikidata: hat die Hütte in OSM eine Wikidata-Nummer, wird dort die offizielle Webseite (P856) geholt.
2. Verzeichnisse (karte/verzeichnisse.txt): Hütten-Verzeichnisse von Alpenvereinen, Naturfreunden, Tourismusverbänden.
   Alle Detailseiten werden gelesen (Sitemap oder Liste), Name und Koordinaten der Seite werden mit unseren Hütten
   verglichen. Nur eindeutige Treffer (Name sehr ähnlich und, wenn die Seite Koordinaten hat, höchstens 2 km entfernt).
Nur lesen: robots.txt, 1 Anfrage pro Sekunde und Seite, keine Anmeldung, keine Formulare.
Ergebnis: quellen.json {"node/123": [{"url": ..., "art": "webseite|verzeichnis", "name": ...}], ...}
"""
import difflib, json, math, os, re, sys, unicodedata, urllib.parse, urllib.request
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sammler

HIER = os.path.dirname(os.path.abspath(__file__))
UA = sammler.UA

def schluessel(x): return f"{x['t']}/{x['id']}"

# ---------- Wikidata ----------
def wikidata_webseiten(huts):
    ids = sorted({x["wd"] for x in huts if re.match(r"^Q\d+$", x.get("wd") or "")})
    web = {}
    for i in range(0, len(ids), 50):
        url = "https://www.wikidata.org/w/api.php?action=wbgetentities&props=claims&format=json&ids=" + "|".join(ids[i:i + 50])
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r: d = json.load(r)
        except Exception as e:
            print("Wikidata-Fehler", str(e)[:80]); continue
        for q, ent in (d.get("entities") or {}).items():
            for c in (ent.get("claims") or {}).get("P856", []):
                v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                if isinstance(v, str) and v.startswith("http"): web[q] = v; break
    return web

WD_SPARQL = """SELECT ?item ?itemLabel ?web ?coord WHERE {
  VALUES ?typ { wd:Q182676 wd:Q339969 wd:Q1970292 wd:Q21523355 }
  ?item wdt:P31 ?typ ; wdt:P856 ?web ; wdt:P625 ?coord ; wdt:P17 ?land .
  VALUES ?land { wd:Q40 wd:Q183 wd:Q39 wd:Q347 wd:Q38 }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "de,en,it,fr". }
}"""

def wikidata_suche():
    """Hütten aus Wikidata mit offizieller Webseite und Koordinaten (für Hütten, deren OSM-Eintrag keine Wikidata-Nummer
    hat). Zuordnung wie bei den Verzeichnissen: Name sehr ähnlich und höchstens 2 km entfernt. Fehler -> leer."""
    import time
    u = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(WD_SPARQL)
    for versuch in range(3):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
            with urllib.request.urlopen(req, timeout=180) as r: b = json.load(r)["results"]["bindings"]
            break
        except Exception as e:
            print("Wikidata-Suche:", str(e)[:100]); time.sleep(70)
    else: return []
    out = []
    for x in b:
        m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", x["coord"]["value"])
        w = x["web"]["value"]
        if m and w.startswith("http"):
            out.append({"url": w, "titel": x["itemLabel"]["value"], "lat": float(m.group(2)), "lon": float(m.group(1))})
    return out

# ---------- Verzeichnisse ----------
def verzeichnisse():
    out = []
    p = os.path.join(HIER, "verzeichnisse.txt")
    for l in open(p, encoding="utf-8"):
        l = l.strip()
        if not l or l.startswith("#"): continue
        teile = [x.strip() for x in l.split(";")]
        if len(teile) >= 4: out.append({"name": teile[0], "typ": teile[1], "url": teile[2], "filter": teile[3]})
    return out

def sitemap_urls(url, filt, tiefe=0):
    raw, err = sammler.holen(url)
    if err or not raw: return []
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", raw)
    index = "<sitemapindex" in raw[:3000]   # Sitemap-Verzeichnis: alle Einträge sind weitere Sitemaps (auch „?sitemap=pois“ o. ä.)
    out = []
    for l in locs:
        l = sammler.html.unescape(l)
        if tiefe < 3 and (index or ("sitemap" in l and (l.endswith(".xml") or re.search(r"sitemap\.xml\?page=\d+$", l)))):
            out += sitemap_urls(l, filt, tiefe + 1)
        elif re.search(filt, l): out.append(l)
    return out

def listen_urls(url, filt):
    raw, err = sammler.holen(url)
    if err or not raw: return []
    _, links = sammler.text_und_links(raw)
    out = []
    for h in links:
        u = urllib.parse.urljoin(url, h).split("#")[0]
        if urllib.parse.urlparse(u).netloc == urllib.parse.urlparse(url).netloc and re.search(filt, u) and u != url: out.append(u)
    return list(dict.fromkeys(out))

R_COORD = re.compile(r"(4[5-9]\.\d{3,8}|5[0-5]\.\d{3,8})\s*[,;/ ]\s*N?\s*(\d{1,2}\.\d{3,8})")
R_TITEL = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
R_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

def seite_info(url):
    raw, err = sammler.holen(url)
    if err or not raw: return None
    t = R_TITEL.search(raw) or R_TITLE.search(raw)
    titel = re.sub(r"<[^>]+>", " ", t.group(1)) if t else ""
    titel = re.split(r"\s[|–—-]\s", re.sub(r"\s+", " ", sammler.html.unescape(titel)).strip())[0]
    lat = lon = None
    m = R_LATLON.search(raw) or R_LONLAT.search(raw)   # schema.org / Kartenattribute der Tourismusportale
    if m:
        a, b = (float(m.group(1)), float(m.group(2))) if m.re is R_LATLON else (float(m.group(2)), float(m.group(1)))
        if 45 <= a <= 55.5 and 5 <= b <= 17.5: lat, lon = a, b
    if lat is None:
        for m in R_COORD.finditer(raw):
            a, b = float(m.group(1)), float(m.group(2))
            if 5 <= b <= 17.5: lat, lon = a, b; break
    return {"url": url, "titel": titel, "lat": lat, "lon": lon}

R_LATLON = re.compile(r"""["']?(?:latitude|lat)["']?\s*[:=]\s*["']?(4[5-9]\.\d+|5[0-5]\.\d+)["']?\s*,\s*["']?(?:longitude|lng|lon)["']?\s*[:=]\s*["']?(\d{1,2}\.\d+)""", re.I)
R_LONLAT = re.compile(r"""["']?(?:longitude|lng|lon)["']?\s*[:=]\s*["']?(\d{1,2}\.\d+)["']?\s*,\s*["']?(?:latitude|lat)["']?\s*[:=]\s*["']?(4[5-9]\.\d+|5[0-5]\.\d+)""", re.I)

# Wörter, die allein nichts über die Hütte sagen (für die Vorauswahl der Detailseiten nach der Adresse)
GENERISCH_Q = {"huette", "alpe", "alpen", "haus", "berghaus", "gasthaus", "gasthof", "berggasthof", "berggasthaus", "restaurant",
               "hotel", "skihuette", "schutzhaus", "schutzhuette", "almhuette", "jausenstation", "bergrestaurant", "alpengasthof",
               "naturfreundehaus", "wanderheim", "unterkunft", "pension", "stube", "beizli", "baita", "malga", "rifugio",
               "capanna", "cabane", "refuge", "sennhuette", "hof", "panorama", "sport", "alpin", "berg"}

def schluessel_woerter(huts):
    """Namen der Hütten als Suchwörter: ganzer Name und einzelne aussagekräftige Wörter"""
    k = set()
    for x in huts:
        n = norm(x["n"])
        if len(n) >= 6: k.add(n)
        for w in re.split(r"[\s\-/,.()']+", x["n"] or ""):
            w = norm(w)
            if len(w) >= 6 and w not in GENERISCH_Q: k.add(w)
    return k

def adresse_passt(url, keys):
    """Vorauswahl: Detailseite nur laden, wenn ihre Adresse einen Hüttennamen enthält (spart tausende Abrufe)"""
    slug = urllib.parse.unquote(urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1])
    slug = re.sub(r"(_vc\d+|\.html?|\.php)$", "", slug)
    teile = [norm(t) for t in re.split(r"[-_+ ]+", slug) if t]
    for i in range(len(teile)):
        for j in range(i + 1, min(len(teile), i + 4) + 1):
            if "".join(teile[i:j]) in keys: return True
    return False

def norm(n):
    n = unicodedata.normalize("NFKD", (n or "").lower())
    n = n.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"\b(die|der|das|neue|alte|the|rifugio|capanna|cabane|biwak|bivacco)\b", " ", n)
    n = re.sub(r"(hütte|huette|hutte)\b", "huette", n)
    n = re.sub(r"\(.*?\)|\d[\d.,]*\s*m\b", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)

def km(a, b, c, d):
    return math.hypot((a - c) * 111.2, (b - d) * 111.2 * math.cos(math.radians(a)))

def zuordnen(huts, infos):
    """gibt {schluessel: [info,...]} – nur eindeutige Treffer"""
    nach_name = defaultdict(list)
    for x in huts: nach_name[norm(x["n"])].append(x)
    namen = list(nach_name)
    treffer = defaultdict(list)
    for inf in infos:
        k = norm(inf["titel"])
        if len(k) < 5: continue
        kand = nach_name.get(k) or []
        if not kand and inf["lat"] is not None:   # ähnliche Namen nur mit Lageprüfung („Neubau“ ≠ „Neuburg“)
            nah = difflib.get_close_matches(k, namen, n=3, cutoff=0.88)
            kand = [x for n in nah for x in nach_name[n]]
        if inf["lat"] is not None:
            kand = [x for x in kand if km(inf["lat"], inf["lon"], x["lat"], x["lon"]) <= 2.0]
        if len(kand) == 1: treffer[schluessel(kand[0])].append(inf)
    return treffer

def finden(neu, aus):
    """QUELLEN_TEIL (Umgebung) verteilt die Suche: web (eigene Webseiten, Wikidata), verz:i/n (Verzeichnisse), gem:i/n (Gemeinden);
    leer = alles. Teil-Ergebnisse werden mit „quellen.py zusammen“ vereint."""
    huts = json.load(open(neu, encoding="utf-8"))
    quellen = defaultdict(list)
    tl = os.environ.get("QUELLEN_TEIL", "")
    art_, nr, anz = (tl.split(":")[0], *(int(x) for x in tl.split(":")[1].split("/"))) if ":" in tl else (tl, 0, 1)
    if art_ == "verz": return _verz(huts, quellen, aus, nr, anz)
    if art_ == "gem":
        gemeinden(huts, quellen, teil=(nr, anz)); json.dump(quellen, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":")); return
    # 1. eigene Webseite aus OSM, sonst aus Wikidata
    wd = wikidata_webseiten(huts)
    n_wd = 0
    for x in huts:
        w = x.get("w")
        if not (w or "").startswith("http") and x.get("wd") in wd: w = wd[x["wd"]]; n_wd += 1
        if (w or "").startswith("http"): quellen[schluessel(x)].append({"url": w, "art": "webseite", "name": "Eigene Webseite"})
    print(f"Webseiten: {sum(1 for v in quellen.values() if v)} (davon {n_wd} neu über Wikidata)")
    # 1b. Wikidata-Suche nach Name und Lage (für Hütten ohne Wikidata-Nummer in OSM)
    wds = wikidata_suche(); n_wds = 0
    for k, lst in zuordnen(huts, wds).items():
        for inf in lst:
            if all(urllib.parse.urlparse(q["url"]).netloc.removeprefix("www.") != urllib.parse.urlparse(inf["url"]).netloc.removeprefix("www.") for q in quellen[k]):
                quellen[k].append({"url": inf["url"], "art": "webseite", "name": "Eigene Webseite (über Wikidata)"}); n_wds += 1
    print(f"Wikidata-Suche: {len(wds)} Hütten mit Webseite, {n_wds} neue Webseiten zugeordnet")
    if art_ == "web":
        json.dump(quellen, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":")); return
    _verz(huts, quellen, aus, 0, 1)
    if os.environ.get("GEMEINDEN") == "ja": gemeinden(huts, quellen)
    json.dump(quellen, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

def _verz(huts, quellen, aus, nr, anz):
    # 2. Verzeichnisse (Alpenvereine, Naturfreunde, Tourismusportale)
    keys = schluessel_woerter(huts)
    def ein_verzeichnis(v):
        if v["typ"] == "robots":   # alle Sitemaps, die die Seite in robots.txt nennt
            raw, _ = sammler.holen(v["url"].rstrip("/") + "/robots.txt")
            sms = re.findall(r"(?im)^sitemap:\s*(\S+)", raw or "") or [v["url"].rstrip("/") + "/sitemap.xml"]
            urls = [u for sm_ in sms[:8] for u in sitemap_urls(sm_, v["filter"])]
        else: urls = sitemap_urls(v["url"], v["filter"]) if v["typ"] == "sitemap" else listen_urls(v["url"], v["filter"])
        urls = list(dict.fromkeys(urls))
        if len(urls) > 400 or v["typ"] == "robots":   # große Portale: nur Seiten, deren Adresse einen Hüttennamen trägt
            alle = len(urls); urls = [u for u in urls if adresse_passt(u, keys)]
            print(f"{v['name']}: {alle} Seiten in der Sitemap, {len(urls)} mit Hüttennamen in der Adresse")
        urls = urls[:3000]
        infos = [i for i in map(seite_info, urls) if i]   # nacheinander: 1 Anfrage pro Sekunde je Verzeichnis
        return v, urls, infos
    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(12) as ex:   # verschiedene Seiten gleichzeitig, jede höchstens 1 Anfrage pro Sekunde
        for v, urls, infos in ex.map(ein_verzeichnis, [v for i, v in enumerate(verzeichnisse()) if i % anz == nr]):
            tr = zuordnen(huts, infos)
            for k, lst in tr.items():
                for inf in lst:
                    if all(q["url"] != inf["url"] for q in quellen[k]):
                        quellen[k].append({"url": inf["url"], "art": "verzeichnis", "name": v["name"]})
            print(f"{v['name']}: {len(urls)} Seiten, {len(infos)} gelesen, {len(tr)} Hütten zugeordnet", flush=True)
    json.dump(quellen, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    mehr = sum(1 for v in quellen.values() if len(v) >= 2)
    print(f"Hütten mit mindestens einer Quelle: {len(quellen)}, mit zwei oder mehr: {mehr}")

# ---------- Gemeinden (Wikidata: Gemeinde in Österreich mit offizieller Webseite) ----------
GEM_URL = re.compile(r"(alm|alpe|huett|hütt|freizeit|tourism|wander|gastro|einkehr|ausflug|sehensw|natur|berg|jausen|essen|kulinar|"
                     r"betriebe|wirtschaft|bewirt|sommer|erleben|urlaub|gasth)", re.I)
GEM_SPARQL = """SELECT ?g ?gLabel ?web ?coord WHERE { ?g wdt:P31 wd:Q667509; wdt:P856 ?web; wdt:P625 ?coord.
 SERVICE wikibase:label { bd:serviceParam wikibase:language "de". } }"""

def gemeinden_liste():
    u = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(GEM_SPARQL)
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=180) as r: d = json.load(r)
    out = {}
    for x in d["results"]["bindings"]:
        m = re.search(r"Point\(([\d.]+) ([\d.]+)\)", x["coord"]["value"])
        if m: out[x["g"]["value"]] = {"name": x["gLabel"]["value"], "web": x["web"]["value"], "lon": float(m.group(1)), "lat": float(m.group(2))}
    return list(out.values())

def _nn(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[^a-z0-9äöüß]+", " ", s).strip()

def gemeinde_seiten(g, huts_nah, max_seiten=150):
    """liest die Seiten einer Gemeinde, die nach Almen/Hütten/Freizeit klingen; gibt [(schluessel, url)]"""
    base = g["web"].rstrip("/")
    raw, err = sammler.holen(urllib.parse.urljoin(base + "/", "/robots.txt"))
    sm = re.findall(r"(?im)^sitemap:\s*(\S+)", raw or "") or [urllib.parse.urljoin(base + "/", "/sitemap.xml")]
    urls = []
    for x in sm[:4]: urls += sitemap_urls(x, r".")
    if not urls: urls = listen_urls(base, r".")
    # eigene Seite der Gemeinde und die Seite, auf die sie selbst verweist (Sitemap in robots.txt, z. B. Tourismusverband)
    hosts = {urllib.parse.urlparse(x).netloc.removeprefix("www.") for x in [base] + sm}
    urls = [u for u in dict.fromkeys(urls) if urllib.parse.urlparse(u).netloc.removeprefix("www.") in hosts and GEM_URL.search(urllib.parse.unquote(u))
            and not re.search(r"\.(pdf|jpe?g|png|docx?|xlsx?)$|/(en|it|nl|fr)/|unterk|ferienwohn|appartement|zimmer|hotel|pension|webcam|event|veranstalt|"
                     r"parkplatz|skipass|kasse|kassa|\bbar\b|-bar\b|lounge|grill|lift|bahn|amtstafel|kundmach|ausschreib|protokoll|verordnung|bescheid|cocktail|sport|shop|verleih|kletter|museum|muesum|schule|spielplatz|kinder", u, re.I)]
    urls = sorted(urls, key=lambda u: 0 if re.search(r"alm|alpe|huett|hütt|gastro|einkehr|jausen", u, re.I) else 1)[:max_seiten]
    namen = defaultdict(list)
    for x in huts_nah:
        n = _nn(x["n"])
        if len(n) >= 6 and len(n.split()) >= 1 and n not in GENERISCH_Q: namen[n].append(x)
    treffer = []
    for u in urls:
        raw, err = sammler.holen(u)
        if err or not raw: continue
        z, _ = sammler.text_haupt(raw)
        tt = R_TITEL.search(raw) or R_TITLE.search(raw)
        titel = _nn(re.split(r"\s[|–—-]\s", sammler.html.unescape(re.sub(r"<[^>]+>", " ", tt.group(1))))[0]) if tt else ""
        slug = _nn(re.sub(r"\.\w+$", "", urllib.parse.unquote(u.rstrip("/").rsplit("/", 1)[-1])).replace("-", " ").replace("_", " "))
        slug = slug.replace("ue", "ü").replace("ae", "ä").replace("oe", "ö")
        # nur Seiten, die von der Hütte handeln: Titel beginnt mit dem Namen, oder die Adresse ist (fast) nur der Name
        # („restaurant-jaegeralpe“ ja, „cocktailbar-oskars-jaegeralpe“ oder „parkplatz-…“ nein)
        for n, xs in namen.items():
            if len(xs) != 1: continue
            rest = f" {slug} ".replace(f" {n} ", " ").strip() if f" {n} " in f" {slug} " else None
            if (titel == n or titel.startswith(n + " ")) or (rest is not None and len(rest) <= 12):
                treffer.append((schluessel(xs[0]), u))
    return treffer

def gemeinden(huts, quellen, km_max=15.0, teil=(0, 1)):
    try: gs = gemeinden_liste()
    except Exception as e: print("Gemeinden: Wikidata nicht erreichbar", str(e)[:80]); return
    import concurrent.futures as cf
    jobs = []
    for g in gs:
        nah = [x for x in huts if abs(x["lat"] - g["lat"]) < 0.2 and abs(x["lon"] - g["lon"]) < 0.3 and km(x["lat"], x["lon"], g["lat"], g["lon"]) <= km_max]
        if nah: jobs.append((g, nah))
    jobs = [j for i, j in enumerate(sorted(jobs, key=lambda j: j[0]["name"])) if i % teil[1] == teil[0]]
    print(f"Gemeinden: {len(gs)} mit Webseite, {len(jobs)} mit Hütten im Umkreis von {km_max:.0f} km", flush=True)
    n = 0
    with cf.ThreadPoolExecutor(24) as ex:
        for (g, _), tr in zip(jobs, ex.map(lambda j: gemeinde_seiten(*j), jobs)):
            for k, u in tr:
                if all(q["url"] != u for q in quellen[k]):
                    quellen[k].append({"url": u, "art": "erwaehnung", "name": f"Gemeinde {g['name']}"}); n += 1
    print(f"Gemeinden: {n} Seiten mit Hüttennamen zugeordnet", flush=True)

def zusammen(aus, teile):
    """Teil-Ergebnisse vereinen, Reihenfolge je Hütte: eigene Webseite, Gemeinde, Verzeichnis (Tourismus, Alpenverein)"""
    q = defaultdict(list)
    for p in teile:
        for k, lst in json.load(open(p, encoding="utf-8")).items():
            for x in lst:
                if all(y["url"] != x["url"] for y in q[k]): q[k].append(x)
    rang = {"webseite": 0, "erwaehnung": 1, "verzeichnis": 2}
    for k in q: q[k].sort(key=lambda x: rang.get(x["art"], 3))
    json.dump(q, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    from collections import Counter
    print(f"Hütten mit Quelle: {len(q)}, Quellen: {dict(Counter(x['art'] for l in q.values() for x in l))}")

if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "zusammen": zusammen(sys.argv[2], sys.argv[3:])
    elif len(sys.argv) >= 4 and sys.argv[1] == "finden": finden(sys.argv[2], sys.argv[3])
    else: print(__doc__); sys.exit(2)
