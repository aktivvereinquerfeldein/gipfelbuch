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
    huts = json.load(open(neu, encoding="utf-8"))
    quellen = defaultdict(list)
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
    # 2. Verzeichnisse (Alpenvereine, Naturfreunde, Tourismusportale)
    keys = schluessel_woerter(huts)
    for v in verzeichnisse():
        urls = sitemap_urls(v["url"], v["filter"]) if v["typ"] == "sitemap" else listen_urls(v["url"], v["filter"])
        urls = list(dict.fromkeys(urls))
        if len(urls) > 400:   # große Portale: nur Seiten, deren Adresse einen Hüttennamen trägt
            alle = len(urls); urls = [u for u in urls if adresse_passt(u, keys)]
            print(f"{v['name']}: {alle} Seiten in der Sitemap, {len(urls)} mit Hüttennamen in der Adresse")
        urls = urls[:3000]
        infos = [i for i in map(seite_info, urls) if i]   # nacheinander: 1 Anfrage pro Sekunde je Verzeichnis
        tr = zuordnen(huts, infos)
        for k, lst in tr.items():
            for inf in lst:
                if all(q["url"] != inf["url"] for q in quellen[k]):
                    quellen[k].append({"url": inf["url"], "art": "verzeichnis", "name": v["name"]})
        print(f"{v['name']}: {len(urls)} Seiten, {len(infos)} gelesen, {len(tr)} Hütten zugeordnet")
    json.dump(quellen, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    mehr = sum(1 for v in quellen.values() if len(v) >= 2)
    print(f"Hütten mit mindestens einer Quelle: {len(quellen)}, mit zwei oder mehr: {mehr}")

if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "finden": finden(sys.argv[2], sys.argv[3])
    else: print(__doc__); sys.exit(2)
