#!/usr/bin/env python3
"""Hütten-Sammler: holt die Texte der Hütten-Quellen und schneidet nur die Zeilen heraus, die für
Öffnungszeiten wichtig sind. Claude liest danach nur noch diese kurze Datei statt im Netz zu suchen
(spart den Großteil der Token). Nur lesen: keine Anmeldung, keine Formulare, robots.txt wird beachtet,
höchstens eine Anfrage pro Sekunde und Seite.

Aufrufe:
  sammler.py quellen  <aus-ordner>   – alle Quell-Adressen aus gb_huetten (fertig recherchiert) prüfen;
                                        in aenderungen.md landen nur Seiten, deren relevanter Text sich geändert hat
  sammler.py listen   <aus-ordner> <region>
                                      – Sammelquellen aus karte/sammelquellen.txt für eine Region holen
                                        (inkl. verlinkter Detailseiten derselben Seite), Auszüge nach <region>.md
Ergebnis-Ordner (im Repo): daten/sammler/  stand.json (Prüfsummen), aenderungen.md, <region>.md
"""
import hashlib, html, json, os, re, sys, time, urllib.parse, urllib.request, urllib.robotparser
from html.parser import HTMLParser

SB_URL = os.environ.get("SB_URL", "https://vehrlrjegxlhxibrxqxm.supabase.co")
SB_KEY = os.environ.get("SB_KEY", "sb_publishable_5sO5VJ5W232hdV_gAbzplA_A4UgzW6M")   # öffentlicher Leseschlüssel
UA = "Gipfelbuch-Sammler/1.0 (+https://aktivvereinquerfeldein.github.io/gipfelbuch/; nur lesen)"
HIER = os.path.dirname(os.path.abspath(__file__))
# Zeilen, die für Öffnungszeiten zählen
WICHTIG = re.compile(r"(geöffnet|geoeffnet|öffnungszeit|oeffnungszeit|saison|ruhetag|geschlossen|bewirt|einkehr|jause|ausschank|"
                     r"hüttenwirt|alpzeit|sommerbetrieb|winterbetrieb|open|closed|season|\b\d{1,2}\.\s?\d{1,2}\.(\d{2,4})?\b|"
                     r"\b(mo|di|mi|do|fr|sa|so)\b.{0,6}\d{1,2}[:.]\d{2}|\d{1,2}[:.]\d{2}\s?[–-]\s?\d{1,2}[:.]\d{2})", re.I)
DETAIL = re.compile(r"(hütte|huette|alpe|alp-|alm|vorsäß|vorsaess|gasthaus|gasthof|berg(gast|haus|restaurant)|stube|stüble|jausen|hut|poi)", re.I)

class Text(HTMLParser):
    def __init__(s):
        super().__init__(); s.out = []; s.links = []; s.skip = 0
    def handle_starttag(s, t, a):
        if t in ("script", "style", "noscript", "svg"): s.skip += 1
        if t in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "td", "dt", "dd"): s.out.append("\n")
        if t == "a":
            h = dict(a).get("href")
            if h: s.links.append(h)
    def handle_endtag(s, t):
        if t in ("script", "style", "noscript", "svg") and s.skip: s.skip -= 1
    def handle_data(s, d):
        if not s.skip: s.out.append(d)

def text_und_links(raw):
    p = Text(); p.feed(raw)
    zeilen = [re.sub(r"\s+", " ", html.unescape(z)).strip() for z in "".join(p.out).split("\n")]
    return [z for z in zeilen if z], p.links

def auszug(zeilen, maxlen=900):
    """nur relevante Zeilen (mit je einer Zeile davor als Überschrift), Doppeltes weg"""
    nimm = set()
    for i, z in enumerate(zeilen):
        if len(z) < 300 and WICHTIG.search(z): nimm.update({i - 1, i})
    out, seen = [], set()
    for i in sorted(x for x in nimm if 0 <= x < len(zeilen)):
        z = zeilen[i][:200]
        if z not in seen: seen.add(z); out.append(z)
    s = " | ".join(out)
    return s[:maxlen]

_robots, _zuletzt = {}, {}
def holen(url, timeout=25):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https"): return None, "keine Webadresse"
    host = u.scheme + "://" + u.netloc
    if host not in _robots:
        rp = urllib.robotparser.RobotFileParser(); rp.set_url(host + "/robots.txt")
        try: rp.read()
        except Exception: rp = None
        _robots[host] = rp
    rp = _robots[host]
    if rp is not None and not rp.can_fetch(UA, url): return None, "robots.txt verbietet"
    w = 1.0 - (time.time() - _zuletzt.get(host, 0))
    if w > 0: time.sleep(w)
    _zuletzt[host] = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "de,en;q=0.5"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if "pdf" in (r.headers.get("Content-Type") or ""): return None, "PDF (nicht ausgewertet)"
            return r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace"), None
    except Exception as e:
        return None, str(e)[:80]

def db_quellen():
    url = (f"{SB_URL}/rest/v1/gb_huetten?select=id,name,bezirk,quelle_url,webseite,daten"
           f"&daten->>recherche=eq.fertig&order=id&limit=5000")
    req = urllib.request.Request(url, headers={"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY})
    with urllib.request.urlopen(req, timeout=60) as r: rows = json.load(r)
    out = []
    for r in rows:
        urls = [r.get("quelle_url"), r.get("webseite"), (r.get("daten") or {}).get("quellen_weitere")]
        urls = [u for u in dict.fromkeys(u for u in urls if u and u.startswith("http") and "openstreetmap" not in u)]
        if urls: out.append((r["id"], r["name"], r.get("bezirk") or "", urls[:2]))
    return out

def quellen(aus):
    os.makedirs(aus, exist_ok=True)
    sp = os.path.join(aus, "stand.json")
    stand = json.load(open(sp)) if os.path.exists(sp) else {}
    neu, zeilen, fehler, gleich = {}, [], [], 0
    for hid, name, bez, urls in db_quellen():
        for u in urls:
            if u in neu: continue
            raw, err = holen(u)
            if err: fehler.append(f"- {hid} {name}: {u} ({err})"); neu[u] = stand.get(u, ""); continue
            a = auszug(text_und_links(raw)[0])
            h = hashlib.sha1(a.encode()).hexdigest()[:12]; neu[u] = h
            if stand.get(u) == h: gleich += 1; continue
            zeilen.append(f"## {hid} {name} ({bez})\n{u}\n{a or '(keine passenden Zeilen gefunden)'}\n")
    json.dump(neu, open(sp, "w"), ensure_ascii=False, indent=0, sort_keys=True)
    with open(os.path.join(aus, "aenderungen.md"), "w") as f:
        f.write(f"# Hütten-Quellen: Änderungen seit dem letzten Lauf ({time.strftime('%Y-%m-%d')})\n\n"
                f"Unverändert: {gleich} Seiten. Geändert oder neu: {len(zeilen)}. Nicht lesbar: {len(fehler)}.\n"
                "Nur Fakten übernehmen, Regeln aus Prompt 1 (Sicherheitsstufen, nichts schätzen).\n\n")
        f.write("\n".join(zeilen) + "\n## Nicht lesbar\n" + "\n".join(fehler) + "\n")
    print(f"unverändert {gleich}, geändert {len(zeilen)}, Fehler {len(fehler)}")

def listen(aus, region):
    os.makedirs(aus, exist_ok=True)
    qs = [l.split(";", 1)[1].strip() for l in open(os.path.join(HIER, "sammelquellen.txt"), encoding="utf-8")
          if l.strip() and not l.startswith("#") and l.split(";", 1)[0].strip().lower() == region.lower()]
    blocks, gesehen = [], set()
    for q in qs:
        raw, err = holen(q)
        if err: blocks.append(f"## Liste {q}\n({err})\n"); continue
        zl, links = text_und_links(raw)
        blocks.append(f"## Liste {q}\n{auszug(zl, 2500)}\n")
        base = urllib.parse.urlparse(q)
        det = []
        for h in links:
            u = urllib.parse.urljoin(q, h).split("#")[0]
            p = urllib.parse.urlparse(u)
            if p.netloc == base.netloc and u not in gesehen and u != q and DETAIL.search(urllib.parse.unquote(p.path)):
                gesehen.add(u); det.append(u)
        for u in det[:80]:   # höchstens 80 Detailseiten je Liste
            raw, err = holen(u)
            if err: continue
            a = auszug(text_und_links(raw)[0], 700)
            if a: blocks.append(f"### {u}\n{a}\n")
    with open(os.path.join(aus, f"{region}.md"), "w") as f:
        f.write(f"# Sammelquellen {region} ({time.strftime('%Y-%m-%d')})\n\nAuszüge, nur Zeilen zu Öffnungszeiten. "
                "Quelle je Abschnitt. Nur Fakten übernehmen, Regeln aus Prompt 1.\n\n" + "\n".join(blocks))
    print(f"{region}: {len(qs)} Listen, {len(gesehen)} Detailseiten")

if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "quellen": quellen(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "listen": listen(sys.argv[2], sys.argv[3])
    else: print(__doc__); sys.exit(2)
