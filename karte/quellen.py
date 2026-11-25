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
    titel = re.split(r"\s[|–—-]\s", re.sub(r"\s+", " ", sammler.html.unescape(titel).replace("\u200b", " ")).strip())[0]
    lat = lon = None
    m = R_LATLON.search(raw) or R_LONLAT.search(raw)   # schema.org / Kartenattribute der Tourismusportale
    if m:
        a, b = (float(m.group(1)), float(m.group(2))) if m.re is R_LATLON else (float(m.group(2)), float(m.group(1)))
        if 45 <= a <= 55.5 and 5 <= b <= 17.5: lat, lon = a, b
    if lat is None:
        for m in R_COORD.finditer(raw):
            a, b = float(m.group(1)), float(m.group(2))
            if 5 <= b <= 17.5: lat, lon = a, b; break
    return {"url": url, "titel": titel, "lat": lat, "lon": lon, "ext": fremde_links(raw, url), "int": eigene_links(raw, url)}

R_LATLON = re.compile(r"""["']?(?:latitude|lat)["']?\s*[:=]\s*["']?(4[5-9]\.\d+|5[0-5]\.\d+)["']?\s*,\s*["']?(?:longitude|lng|lon)["']?\s*[:=]\s*["']?(\d{1,2}\.\d+)""", re.I)
R_LONLAT = re.compile(r"""["']?(?:longitude|lng|lon)["']?\s*[:=]\s*["']?(\d{1,2}\.\d+)["']?\s*,\s*["']?(?:latitude|lat)["']?\s*[:=]\s*["']?(4[5-9]\.\d+|5[0-5]\.\d+)""", re.I)

# ---------- Verlinkte eigene Webseiten (Tourismus- und Gemeindeseiten verlinken die Seite der Hütte) ----------
PORTALE = re.compile(r"(facebook|instagram|youtube|twitter|tiktok|pinterest|linkedin|whatsapp|wa\.me|google|apple|booking|tripadvisor|"
                     r"holidaycheck|expedia|airbnb|trivago|hrs\.|alpenverein|naturfreunde|bergfex|komoot|outdooractive|wikipedia|wikimedia|"
                     r"feratel|tirol\.at|salzburgerland|vorarlberg|steiermark|kaernten|austria\.info|oesterreich|\.gv\.at|gemeinde|"
                     r"bergwelten|alpenvereinaktiv|strava|maps\.|openstreetmap|cookiebot|jquery|gstatic|cloudflare|doubleclick|"
                     r"tourismus|tourism|region|bergbahn|skigebiet|seilbahn|lift)", re.I)
R_HREF = re.compile(r"""href\s*=\s*["'](https?://[^"'#\s>]+)""", re.I)
LINK_ALLG = set("""huette huetten alm almen alpe alpen alp haus berghaus berggasthof berggasthaus gasthof gasthaus alpengasthof
schutzhaus schutzhuette naturfreundehaus restaurant bergrestaurant jausenstation stube stueberl hof wirtshaus hotel pension lodge
cafe berg neue alte obere untere hintere vordere mittlere grosse kleine skihuette panorama sport alpin almhuette""".split())

def fremde_links(raw, url):
    eigen = urllib.parse.urlparse(url).netloc.removeprefix("www.")
    out = []
    for h in R_HREF.findall(raw or ""):
        h = sammler.html.unescape(h)
        d = urllib.parse.urlparse(h).netloc.lower().removeprefix("www.")
        if d and d != eigen and not d.endswith("." + eigen) and not PORTALE.search(d): out.append(h)
    return list(dict.fromkeys(out))[:80]

R_HREF_ALLE = re.compile(r"""href\s*=\s*["']([^"'#\s>]+)""", re.I)

def eigene_links(raw, url):
    eigen = urllib.parse.urlparse(url).netloc
    out = []
    for h in R_HREF_ALLE.findall(raw or ""):
        u = urllib.parse.urljoin(url, sammler.html.unescape(h))
        if urllib.parse.urlparse(u).netloc == eigen and u.startswith("http") and u != url: out.append(u)
    return list(dict.fromkeys(out))[:600]

def _link_kern(name):
    """kennzeichnende Namensteile für den Vergleich mit einer Adresse, z. B. „Burgl Hütte“ -> ['burgl']"""
    out = []
    for w in re.split(r"[\s\-/,.()'’`]+", name or ""):
        w = norm(w)
        if not w or w in LINK_ALLG: continue
        w2 = re.sub(r"(huette|alm|alpe|haus|hof|stube|stueberl)$", "", w)
        if len(w2) >= 4 and w2 not in LINK_ALLG: out.append(w2)
    return out

TYP = r"(huetten|huette|hutte|almen|alm|alpe|alp|berggasthof|berggasthaus|gasthof|gasthaus|wirtshaus|haus|restaurant|jausenstation|stueberl|stube|hof|schutzhaus)"

TYP_GRUPPE = {"huetten": "hh", "huette": "h", "hutte": "h", "almen": "aa", "alm": "a", "alpe": "a", "alp": "a", "berggasthof": "hof",
              "gasthof": "hof", "hof": "hof", "berggasthaus": "gh", "gasthaus": "gh", "wirtshaus": "gh", "haus": "haus", "schutzhaus": "haus",
              "restaurant": "r", "jausenstation": "j", "stueberl": "s", "stube": "s"}

def _typen(t):
    return {TYP_GRUPPE[m] for m in re.findall(TYP, t)}

def link_index(huts):
    idx = defaultdict(list)
    for x in huts:
        k = _link_kern(x["n"])
        # sehr kurze Namen („Adler“) nur bei Hütte/Alm, sonst zu allgemein (gasthof-adler.at gibt es oft)
        if k and (sum(map(len, k)) >= 7 or (sum(map(len, k)) >= 5 and _typen(norm(x["n"])) & {"h", "a"})): idx[tuple(k)].append(x)
    return idx

TYP_RAND = r"(huetten|huette|hutte|almen|alm|alpe|alp|schutzhaus|berghaus|haus|stueberl|stube|jausenstation)"
ERLAUBT_GLOBAL = {"h", "a", "s", "j", "haus"}   # Gasthof/Restaurant + Nachname gibt es zu oft (gasthof-pichler.at)

def _rand_typ(t):
    """Hüttenwort am Anfang oder Ende abziehen: ('spiess', 'a') für spiessalm, ('widderstein', 'h') für widderstein-huette"""
    m = re.fullmatch(r"(.*?)" + TYP_RAND, t)   # nur am Ende, wie im Namen (hausdachstein.at ist nicht das Dachsteinhaus)
    if not m: return t, set()
    return m.group(1), {TYP_GRUPPE.get(m.group(2), "haus" if m.group(2) == "berghaus" else m.group(2))}

def link_schnell(huts):
    """Nachschlagetabelle für sehr viele Adressen (Common Crawl): Name ohne das Hüttenwort am Ende -> Hütten"""
    tab = defaultdict(list)
    for x in huts:
        k = _link_kern(x["n"])
        if not k: continue
        n = norm(x["n"])
        m = re.search(TYP_RAND + r"$", n)
        if not m: continue   # nur Namen, die mit Hütte/Alm/Alpe/Haus/Stube enden
        typ = TYP_GRUPPE.get(m.group(1), "haus" if m.group(1) == "berghaus" else m.group(1))
        if typ not in ERLAUBT_GLOBAL: continue
        kern_ = "".join(k)
        if len(kern_) < 5: continue
        tab[kern_].append((x, typ))
    return tab

def link_huette_schnell(link, tab, nur=None):
    d = urllib.parse.urlparse(link).netloc.lower().removeprefix("www.")
    dom = re.sub(r"[^a-z0-9]", "", norm(".".join(d.split(".")[:-1])))
    if len(dom) < 6: return None
    kern_, typen = _rand_typ(dom)
    if not typen: return None
    kand = {schluessel(x): x for x, t in tab.get(kern_, []) if t in typen}
    if nur is not None: kand = {k: v for k, v in kand.items() if k in nur}
    return next(iter(kand)) if len(kand) == 1 else None

def link_huette(link, idx, nur=None):
    """eigene Webseite, wenn die Adresse alle kennzeichnenden Namensteile genau einer Hütte trägt
    (burglhuette.at -> Burgl Hütte); nur = erlaubte Schlüssel (Hütten in der Nähe der Seite)"""
    d = urllib.parse.urlparse(link).netloc.lower().removeprefix("www.")
    dom = re.sub(r"[^a-z0-9]", "", norm(".".join(d.split(".")[:-1])))
    if len(dom) < 5: return None
    kand = {}
    for k, xs in idx.items():
        if not all(w in dom for w in k): continue
        rest = dom
        for w in sorted(k, key=len, reverse=True): rest = rest.replace(w, "", 1)
        typ_dom = _typen(rest)
        rest = re.sub(TYP + r"|bei|zur|zum|der|die|das|auf", "", rest)
        if rest: continue   # Adresse besteht nicht nur aus dem Namen (tmkoberalm.at = TMK Oberalm) (golling.info, festspiele, intersport-…)
        for x in xs:
            # trägt der Name ein Hüttenwort („Gollinghütte“), muss auch die Adresse eins tragen (golling.info ist der Ort)
            # und zwar dieselbe Art: „Dolomitenhütte“ ist nicht dolomitenhof.at, „St. Martiner Hütte“ nicht martinerhof.at
            tn = _typen(norm(x["n"]))
            if tn and not (tn & typ_dom): continue
            kand[schluessel(x)] = x
    if nur is not None: kand = {k: v for k, v in kand.items() if k in nur}
    if len(kand) != 1: return None
    return next(iter(kand))

R_SEITE = re.compile(r"[?&](page|seite|p|pg|offset|start)=\d+", re.I)
UEBERSICHT = re.compile(r"(huett|hütt|almen|alpen|einkehr|kulinar|gastro|jausen|wanderung|essen|genuss|restaurant)", re.I)

def webseite_dazu(quellen, k, url, name):
    d = urllib.parse.urlparse(url).netloc.removeprefix("www.")
    if any(urllib.parse.urlparse(q["url"]).netloc.removeprefix("www.") == d for q in quellen[k]): return 0
    quellen[k].append({"url": url, "art": "webseite", "name": name}); return 1

def heimseite(link):
    p = urllib.parse.urlparse(link)
    return f"{p.scheme}://{p.netloc}/"


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
    if art_ == "cc":   # eigene Suche im offenen Web-Archiv Common Crawl (karte/cc_suche.py)
        import cc_suche; return cc_suche.main(neu, aus)
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
        ueber = []
        if len(urls) > 400 or v["typ"] == "robots":   # große Portale: nur Seiten, deren Adresse einen Hüttennamen trägt
            alle = len(urls); rest = [u for u in urls if not adresse_passt(u, keys)]; urls = [u for u in urls if adresse_passt(u, keys)]
            # dazu Übersichtsseiten („Almen & Hütten“, „Einkehrmöglichkeiten“): sie verlinken oft die eigene Seite der Hütte
            ueber = [u for u in rest if UEBERSICHT.search(urllib.parse.unquote(u)) and not re.search(r"/(en|it|nl|fr|cs|pl|hu)/", u)][:150]
            print(f"{v['name']}: {alle} Seiten in der Sitemap, {len(urls)} mit Hüttennamen in der Adresse, {len(ueber)} Übersichtsseiten")
        urls = urls[:3000]
        infos = [i for i in map(seite_info, urls) if i]   # nacheinander: 1 Anfrage pro Sekunde je Verzeichnis
        uinfos = [i for i in map(seite_info, ueber) if i]
        # Blätter-Seiten der Übersichten (?page=2 …) und Detailseiten, die nur dort verlinkt sind (nicht in der Sitemap)
        gesehen = set(urls) | set(ueber)
        blaetter = [l for i in uinfos for l in i.get("int", []) if R_SEITE.search(l) and l not in gesehen][:200]
        gesehen |= set(blaetter)
        uinfos += [i for i in map(seite_info, blaetter) if i]
        mehr = [l for i in uinfos for l in i.get("int", []) if l not in gesehen and re.search(v["filter"], l) and adresse_passt(l, keys)
                and not re.search(r"\.(pdf|jpe?g|png|webp|svg|css|js)(\?|$)", l, re.I)]
        mehr = list(dict.fromkeys(mehr))[:1500]
        if mehr: print(f"{v['name']}: {len(mehr)} weitere Detailseiten über Übersichtsseiten", flush=True)
        infos += [i for i in map(seite_info, mehr) if i]; urls = urls + mehr
        # dieselbe Lage auf vielen Seiten = Standardwert der Seite (z. B. montafon.at: 46.859/16.442), keine echte Lage
        from collections import Counter
        haeufig = Counter((i["lat"], i["lon"]) for i in infos if i["lat"] is not None)
        for i in infos:
            if i["lat"] is not None and haeufig[(i["lat"], i["lon"])] >= max(3, len(infos) // 10): i["lat"] = i["lon"] = None
        return v, urls, infos, uinfos
    import concurrent.futures as cf
    idx = link_index(huts); n_link = 0
    with cf.ThreadPoolExecutor(12) as ex:   # verschiedene Seiten gleichzeitig, jede höchstens 1 Anfrage pro Sekunde
        for v, urls, infos, uinfos in ex.map(ein_verzeichnis, [v for i, v in enumerate(verzeichnisse()) if i % anz == nr]):
            tr = zuordnen(huts, infos)
            for k, lst in tr.items():
                for inf in lst:
                    if all(q["url"] != inf["url"] for q in quellen[k]):
                        quellen[k].append({"url": inf["url"], "art": "verzeichnis", "name": v["name"]})
            nl = 0
            for inf in infos + uinfos:
                for l in inf.get("ext") or []:
                    k = link_huette(l, idx)
                    if k: nl += webseite_dazu(quellen, k, heimseite(l), f"Eigene Webseite (verlinkt von {v['name']})")
            n_link += nl
            print(f"{v['name']}: {len(urls)} Seiten, {len(infos)} gelesen, {len(tr)} Hütten zugeordnet, {nl} eigene Webseiten verlinkt", flush=True)
    json.dump(quellen, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    mehr = sum(1 for v in quellen.values() if len(v) >= 2)
    print(f"Eigene Webseiten über Links von Verzeichnissen: {n_link}")
    print(f"Hütten mit mindestens einer Quelle: {len(quellen)}, mit zwei oder mehr: {mehr}")

# ---------- Gemeinden (Wikidata: Gemeinde in Österreich mit offizieller Webseite) ----------
GEM_URL = re.compile(r"(alm|alpe|huett|hütt|freizeit|tourism|wander|gastro|einkehr|ausflug|sehensw|natur|berg|jausen|essen|kulinar|"
                     r"betriebe|wirtschaft|bewirt|sommer|erleben|urlaub|gasth)", re.I)
GEM_SPARQL = """SELECT ?g ?gLabel ?web ?coord WHERE { ?g wdt:P31 wd:Q667509; wdt:P856 ?web; wdt:P625 ?coord.
 SERVICE wikibase:label { bd:serviceParam wikibase:language "de". } }"""

def gemeinden_liste():
    p = os.path.join(HIER, "gemeinden_at.json")   # Stand aus Wikidata (24.11.2026), damit der Lauf nicht von Wikidata abhängt
    if os.path.exists(p): return json.load(open(p, encoding="utf-8"))
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

# Alpen/Hütten-Schlüssel: „Gaßner Alpe“, „gassner-alpe“, „Gaßneralpe“ und „Alpe Gassner“ ergeben denselben Schlüssel
ALP_ENDE = [("huetten", "hh"), ("huette", "h"), ("hutte", "h"), ("almen", "aa"), ("alpen", "aa"), ("alpe", "a"), ("alp", "a"), ("alm", "a"),
            ("vorsaess", "v"), ("maisaess", "v"), ("jausenstation", "j"), ("schutzhaus", "s")]
ALP_VORNE = [("alpe", "a"), ("alp", "a"), ("alm", "a"), ("vorsaess", "v"), ("maisaess", "v")]

def alp_schluessel(t):
    n = norm(re.sub(r"(_vc\d+|\.html?|\.php)$", "", t or ""))
    for w, g in ALP_ENDE:
        if n.endswith(w) and len(n) - len(w) >= 4: return (n[:-len(w)], g)
    for w, g in ALP_VORNE:
        if n.startswith(w) and len(n) - len(w) >= 4 and not n.startswith(("alpen", "almen")): return (n[len(w):], g)
    return None

def _gem_slugs(name):
    n = unicodedata.normalize("NFKC", name).lower()
    aus = set()
    for v in (n, re.sub(r"^sankt\b", "st", n), re.sub(r"^sankt\b", "st.", n)):
        for u in (v.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"), v.replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")):
            t = re.sub(r"[^a-z0-9]+", "-", u).strip("-")
            if len(t) >= 4: aus.add(t); aus.add(t.replace("-", ""))
    return aus

TOUR_WORT = re.compile(r"wander|urlaub|unterk|ausflug|tourismus|alpen|hütten|huetten|gastronomie|freizeit|sehensw", re.I)

def tourismus_hosts(g, start_raw=None):
    """eigene Tourismusseite einer Gemeinde (raggal.net neben raggal.at): Adresse aus dem Gemeindenamen oder auf der
    Gemeindeseite verlinkt, und die Startseite nennt die Gemeinde und Tourismus-Wörter"""
    import socket
    base = urllib.parse.urlparse(g["web"]).netloc.lower().removeprefix("www.")
    slugs = _gem_slugs(g["name"])
    kand = set()
    for s in slugs:
        for tld in (".net", ".info", ".com", ".at"):
            d = s + tld
            if d != base: kand.add(d)
    for l in fremde_links(start_raw or "", g["web"]):
        d = urllib.parse.urlparse(l).netloc.lower().removeprefix("www.")
        if any(s.replace("-", "") in d.replace("-", "") for s in slugs) and d != base: kand.add(d)
    aus = []
    for d in sorted(kand):
        try: socket.getaddrinfo("www." + d, 443)
        except Exception:
            try: socket.getaddrinfo(d, 443)
            except Exception: continue
        for host in ("https://www." + d, "https://" + d):
            raw, err = sammler.holen(host + "/")
            if raw: break
        if not raw: continue
        z, _ = sammler.text_haupt(raw)
        txt = " ".join(z or []) + " " + re.sub(r"<[^>]+>", " ", raw[:200000])
        if re.search(re.escape(g["name"].split()[-1]), txt, re.I) and len(TOUR_WORT.findall(txt)) >= 3 and not PORTALE.search(d):
            aus.append(host)
    return aus

def seiten_einer_domain(host, max_listen=60, max_url=2000):
    """alle Adressen einer Domain: Sitemap (robots.txt), sonst Links bis 3 Ebenen tief über Seiten, die nach Alpen/Freizeit klingen"""
    raw, err = sammler.holen(urllib.parse.urljoin(host + "/", "/robots.txt"))
    sm = re.findall(r"(?im)^sitemap:\s*(\S+)", raw or "") or [urllib.parse.urljoin(host + "/", "/sitemap.xml")]
    urls = []
    for x in sm[:4]: urls += sitemap_urls(x, r".")
    netz = {urllib.parse.urlparse(host).netloc.removeprefix("www.")}
    urls = [u for u in urls if urllib.parse.urlparse(u).netloc.removeprefix("www.") in netz]
    if len(urls) >= 20: return urls
    gesehen, ebene, n = set(urls) | {host + "/"}, [host + "/"], 0
    for _ in range(3):
        naechste = []
        for u in ebene:
            if n >= max_listen: break
            r, e = sammler.holen(u); n += 1
            if not r: continue
            for l in eigene_links(r, u):
                l = l.split("#")[0].split("?")[0]
                if l in gesehen or re.search(r"\.(pdf|jpe?g|png|gif|svg|docx?|xlsx?|zip|css|js)$", l, re.I): continue
                gesehen.add(l)
                if GEM_URL.search(urllib.parse.unquote(l)): naechste.append(l)
        ebene = sorted(naechste, key=lambda u: 0 if re.search(r"alp|alm|huett|hütt|einkehr|jausen|gastro", u, re.I) else 1)
        if len(gesehen) > max_url: break
    return list(gesehen)

GEM_NEIN = re.compile(r"\.(pdf|jpe?g|png|docx?|xlsx?)$|/(en|it|nl|fr)/|unterk|ferienwohn|appartement|zimmer|hotel|pension|webcam|event|veranstalt|"
                      r"parkplatz|skipass|kasse|kassa|\bbar\b|-bar\b|lounge|grill|lift|bahn|amtstafel|kundmach|ausschreib|protokoll|verordnung|bescheid|"
                      r"cocktail|sport|shop|verleih|kletter|museum|muesum|schule|spielplatz|kinder|alpabtrieb|kapellenfest|fest-|-fest|/biken/|mountainbike|/mtb|e-bike", re.I)

def gemeinde_seiten(g, huts_nah, max_seiten=150):
    """liest die Seiten einer Gemeinde und ihrer Tourismusseite, die nach Almen/Hütten/Freizeit klingen.
    gibt (treffer [(schluessel, url, art)], fehlend [{name, url, ...}]) – fehlend = eigene Seite einer Alpe/Hütte, die wir nicht kennen"""
    base = g["web"].rstrip("/")
    start, _ = sammler.holen(base + "/")
    hosts = [base] + tourismus_hosts(g, start)
    urls = []
    for h in hosts: urls += [u for u in seiten_einer_domain(h) if u.rstrip("/") != h.rstrip("/")]
    urls = [u for u in dict.fromkeys(urls) if GEM_URL.search(urllib.parse.unquote(u)) and not GEM_NEIN.search(u)]
    urls = sorted(urls, key=lambda u: 0 if re.search(r"alm|alpe|huett|hütt|gastro|einkehr|jausen", u, re.I) else 1)[:max_seiten]
    namen = defaultdict(list); alp = defaultdict(list)
    for x in huts_nah:
        n = _nn(x["n"])
        if len(n) >= 6 and len(n.split()) >= 1 and n not in GENERISCH_Q: namen[n].append(x)
        a = alp_schluessel(x["n"])
        if a: alp[a].append(x)
    treffer, fehlend = [], []
    idx = link_index(huts_nah); nah_k = {schluessel(x) for x in huts_nah}
    for u in urls:
        raw, err = sammler.holen(u)
        if err or not raw: continue
        for l in fremde_links(raw, u):
            k = link_huette(l, idx, nah_k)
            if k: treffer.append((k, heimseite(l), "webseite"))
        tt = R_TITEL.search(raw) or R_TITLE.search(raw)
        titel_roh = sammler.html.unescape(re.sub(r"<[^>]+>", " ", tt.group(1))).strip() if tt else ""
        titel_roh = re.split(r"\s[|–—-]\s", titel_roh)[0].strip()
        titel = _nn(titel_roh)
        letzter = urllib.parse.unquote(u.rstrip("/").rsplit("/", 1)[-1])
        slug = _nn(re.sub(r"\.\w+$", "", letzter).replace("-", " ").replace("_", " "))
        slug = slug.replace("ue", "ü").replace("ae", "ä").replace("oe", "ö")
        # nur Seiten, die von der Hütte handeln: Titel beginnt mit dem Namen, oder die Adresse ist (fast) nur der Name
        # („restaurant-jaegeralpe“ ja, „cocktailbar-oskars-jaegeralpe“ oder „parkplatz-…“ nein)
        gef = False
        for n, xs in namen.items():
            if len(xs) != 1: continue
            rest = f" {slug} ".replace(f" {n} ", " ").strip() if f" {n} " in f" {slug} " else None
            if (titel == n or titel.startswith(n + " ")) or (rest is not None and len(rest) <= 12):
                treffer.append((schluessel(xs[0]), u, "erwaehnung")); gef = True
        # Alpen-Schlüssel: Adresse und Titel nennen dieselbe Alpe (gassner-alpe / „Gaßner Alpe“ = Gaßneralpe)
        ka = alp_schluessel(letzter)
        tl = R_TITLE.search(raw)
        titel2 = re.split(r"\s[|–—-]\s", sammler.html.unescape(re.sub(r"<[^>]+>", " ", tl.group(1))).strip())[0].strip() if tl else ""
        tk = [(t, alp_schluessel(t)) for t in (titel_roh, titel2) if t]
        tk = [(t, k) for t, k in tk if k == ka]
        if ka and tk:
            titel_roh = tk[0][0]
            xs = alp.get(ka) or []
            if len(xs) == 1:
                if not gef: treffer.append((schluessel(xs[0]), u, "erwaehnung"))
            elif not xs and not gef:
                fehlend.append({"name": titel_roh, "key": list(ka), "url": u, "gem": g["name"], "glat": g["lat"], "glon": g["lon"]})
    return treffer, fehlend

def _region_nah(huts, lat, lon):
    b = min(huts, key=lambda x: km(x["lat"], x["lon"], lat, lon)) if huts else None
    return (b["l"], b["r"]) if b else ("AT", None)

def fehlende_osm(fehlend, huts, km_max=15.0):
    """Alpen/Hütten, die eine Gemeinde- oder Tourismusseite beschreibt, die aber nicht im Bestand sind: Lage aus OSM
    (gleicher Name im Umkreis der Gemeinde, ein einziges Objekt) – die Seite ist die Quelle, OSM nur der Ort"""
    import huetten_dach
    bekannt = {schluessel(x) for x in huts}
    nach_gem = defaultdict(list)
    for f in fehlend: nach_gem[(f["gem"], f["glat"], f["glon"])].append(f)
    pfad = os.environ.get("OSM_NAMEN", "osm-namen.json")
    if not os.path.exists(pfad): print("  keine OSM-Namensliste", pfad); return []
    alle = json.load(open(pfad, encoding="utf-8"))
    for e in alle: e["type"] = e["t"]
    neu, gesehen = [], set()
    for (gem, la, lo), fs in nach_gem.items():
        el = [e for e in alle if abs(e["lat"] - la) < 0.2 and abs(e["lon"] - lo) < 0.3 and km(e["lat"], e["lon"], la, lo) <= km_max]
        for f in fs:
            k = tuple(f["key"])
            tr = [e for e in el if alp_schluessel(e.get("tags", {}).get("name")) == k and f"{e['type']}/{e['id']}" not in bekannt]
            if not tr: continue
            # mehrere Objekte (Gebäude, Flur): nur wenn alle beieinander liegen (1 km), dann das mit Hütten-/Gebäude-Merkmal
            pos = lambda e: (e.get("lat") or e["center"]["lat"], e.get("lon") or e["center"]["lon"])
            p0 = pos(tr[0])
            if any(km(*pos(e), *p0) > 1.0 for e in tr): continue
            rang = lambda e: 0 if e["tags"].get("tourism") or e["tags"].get("amenity") else 1 if e["tags"].get("building") else 2
            e = sorted(tr, key=rang)[0]
            s = f"{e['type']}/{e['id']}"
            if s in gesehen: continue
            gesehen.add(s); la2, lo2 = pos(e); t = e["tags"]
            ele = re.match(r"^\d{3,4}", t.get("ele", "") or "")
            l, r = _region_nah(huts, la2, lo2)
            neu.append({"t": e["type"], "id": e["id"], "n": t["name"], "a": huetten_dach.art(t), "lon": round(lo2, 6), "lat": round(la2, 6),
                        "h": int(ele.group(0)) if ele else None, "l": l, "r": r, "w": t.get("website") or t.get("contact:website"),
                        "tag": t.get("tourism") or t.get("amenity") or t.get("place") or ("building=" + t["building"] if t.get("building") else None),
                        "wd": t.get("wikidata"), "q_url": f["url"], "q_name": f"Gemeinde {f['gem']}", "q_titel": f["name"]})
    return neu

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
    n = nw = 0; fehlend = []
    with cf.ThreadPoolExecutor(24) as ex:
        def sicher(j):
            try: return gemeinde_seiten(*j)
            except Exception as e: print("  Gemeinde", j[0]["name"], "Fehler:", str(e)[:80], flush=True); return [], []
        for (g, _), (tr, fe) in zip(jobs, ex.map(sicher, jobs)):
            fehlend += fe
            for k, u, art in tr:
                if art == "webseite": nw += webseite_dazu(quellen, k, u, f"Eigene Webseite (verlinkt von Gemeinde {g['name']})"); continue
                if all(q["url"] != u for q in quellen[k]):
                    quellen[k].append({"url": u, "art": "erwaehnung", "name": f"Gemeinde {g['name']}"}); n += 1
    print(f"Gemeinden: {n} Seiten mit Hüttennamen zugeordnet, {nw} eigene Webseiten verlinkt", flush=True)
    neu = fehlende_osm(fehlend, huts) if fehlend else []
    for x in neu:
        quellen[schluessel(x)].append({"url": x["q_url"], "art": "erwaehnung", "name": x["q_name"]})
    if neu: quellen["_neu"] = neu
    print(f"Gemeinden: {len(fehlend)} Alpen/Hütten mit eigener Seite, aber nicht im Bestand, davon {len(neu)} mit Lage aus OSM nachgetragen", flush=True)
    for x in neu[:60]: print(f"  neu: {x['n']} ({x['t']}/{x['id']}) – {x['q_url']}")

def zusammen(aus, teile):
    """Teil-Ergebnisse vereinen, Reihenfolge je Hütte: eigene Webseite, Gemeinde, Verzeichnis (Tourismus, Alpenverein)"""
    q = defaultdict(list); neu = {}
    for p in teile:
        for k, lst in json.load(open(p, encoding="utf-8")).items():
            if k == "_neu":
                for x in lst: neu.setdefault(f"{x['t']}/{x['id']}", x)
                continue
            for x in lst:
                dom = lambda u: urllib.parse.urlparse(u).netloc.lower().removeprefix("www.")
                if x["art"] == "webseite" and any(y["art"] == "webseite" and dom(y["url"]) == dom(x["url"]) for y in q[k]): continue
                if all(y["url"] != x["url"] for y in q[k]): q[k].append(x)
    rang = {"webseite": 0, "erwaehnung": 1, "verzeichnis": 2}
    for k in q: q[k].sort(key=lambda x: rang.get(x["art"], 3))
    json.dump(q, open(aus, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    # nachgetragene Alpen/Hütten (nicht im Bestand, eigene Seite bei Gemeinde/Tourismus, Lage aus OSM) für Lesen und Eintragen
    json.dump(list(neu.values()), open(os.path.join(os.path.dirname(os.path.abspath(aus)), "neu-huetten.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(f"Nachgetragene Alpen/Hütten: {len(neu)}")
    from collections import Counter
    print(f"Hütten mit Quelle: {len(q)}, Quellen: {dict(Counter(x['art'] for l in q.values() for x in l))}")

if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "zusammen": zusammen(sys.argv[2], sys.argv[3:])
    elif len(sys.argv) >= 4 and sys.argv[1] == "finden": finden(sys.argv[2], sys.argv[3])
    else: print(__doc__); sys.exit(2)
