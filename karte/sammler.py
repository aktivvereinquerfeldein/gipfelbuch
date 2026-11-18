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

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
R_RAND = re.compile(r"(^|[\s_-])(nav|navbar|navigation|menu|menue|mainmenu|megamenu|offcanvas|footer|cookie|consent|breadcrumb|"
                    r"sidebar|sitemap|topbar|header-top|skip|search)([\s_-]|$)", re.I)

class TextHaupt(HTMLParser):
    """wie Text, aber ohne Menü, Kopf- und Fußzeile, Cookie-Hinweis, Seitenleiste; Links mit ihrem Linktext"""
    def __init__(s):
        super().__init__(); s.out = []; s.links = []; s.stack = []; s.skip_ab = None; s.skip = 0; s.a = None
    def handle_starttag(s, t, a):
        if t in VOID:
            if t == "br" and s.skip_ab is None: s.out.append("\n")
            return
        d = dict(a)
        s.stack.append(t)
        if s.skip_ab is None and (t in ("nav", "header", "footer", "aside", "form", "dialog")
                                  or R_RAND.search((d.get("class") or "") + " " + (d.get("id") or "") + " " + (d.get("role") or "").replace("navigation", "nav"))):
            s.skip_ab = len(s.stack)
        if t in ("script", "style", "noscript", "svg", "template"): s.skip += 1
        if t in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "td", "th", "dt", "dd", "section", "article", "table"): s.out.append("\n")
        if t == "a" and d.get("href"): s.a = [d["href"], ""]
    def handle_endtag(s, t):
        if t in VOID: return
        if t in ("script", "style", "noscript", "svg", "template") and s.skip: s.skip -= 1
        if t == "a" and s.a: s.links.append((s.a[0], re.sub(r"\s+", " ", html.unescape(s.a[1])).strip(), s.skip_ab is not None)); s.a = None
        if t in s.stack:   # bis zum passenden Tag schließen (fehlerhaftes HTML)
            while s.stack:
                x = s.stack.pop()
                if s.skip_ab is not None and len(s.stack) < s.skip_ab: s.skip_ab = None
                if x == t: break
        if t in ("p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "dd"): s.out.append("\n")
    def handle_data(s, d):
        if s.skip: return
        if s.a is not None: s.a[1] += d
        if s.skip_ab is None: s.out.append(d)

def text_haupt(raw):
    """Haupttext ohne Menü/Fußzeile; gibt (zeilen, [(href, linktext, im_menue)]). Fällt auf den ganzen Text zurück,
    wenn kaum etwas übrig bleibt (Seiten, die alles in ein „menu“-Element packen)."""
    p = TextHaupt()
    try: p.feed(raw); p.close()
    except Exception: pass
    unsichtbar = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\u00ad\ufeff"), None)   # z. B. „01.\u206005.“
    zeilen = [re.sub(r"\s+", " ", html.unescape(z).translate(unsichtbar)).strip() for z in "".join(p.out).split("\n")]
    zeilen = [z for z in zeilen if z]
    if sum(len(z) for z in zeilen) < 200:
        z2, _ = text_und_links(raw); zeilen = [z.translate(unsichtbar) for z in z2]
    return zeilen, p.links

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

class Robots:
    """robots.txt mit unserer Kennung holen (Pythons RobotFileParser holt sie ohne Kennung; viele Seiten antworten darauf
    mit 403, das galt dann als „alles verboten“) und nach RFC 9309 auswerten: * und $ in den Pfaden, längste Regel gewinnt,
    mehrere Gruppen für denselben Agenten zusammen."""
    def __init__(s, text, alles_verboten=False):
        s.verboten_alle, s.gruppen, s._maps = alles_verboten, [], []
        agents, regeln, in_regeln = [], [], False
        for z in (text or "").splitlines():
            z = z.split("#", 1)[0].strip()
            if ":" not in z: continue
            k, v = (x.strip() for x in z.split(":", 1)); k = k.lower()
            if k == "sitemap":
                if v: s._maps.append(v)
            elif k == "user-agent":
                if in_regeln: s.gruppen.append((agents, regeln)); agents, regeln, in_regeln = [], [], False
                agents.append(v.lower())
            elif k in ("allow", "disallow"):
                in_regeln = True
                if v: regeln.append((k == "allow", v))
        if agents: s.gruppen.append((agents, regeln))
    def site_maps(s): return s._maps or None
    @staticmethod
    def _passt(muster, pfad):
        rx = "^" + re.escape(muster).replace(r"\*", ".*")
        if rx.endswith(r"\$"): rx = rx[:-2] + "$"
        return re.match(rx, pfad) is not None
    def can_fetch(s, ua, url):
        if s.verboten_alle: return False
        u = urllib.parse.urlparse(url); pfad = (u.path or "/") + ("?" + u.query if u.query else "")
        name = ua.split("/")[0].lower()
        regeln = [r for a, r in s.gruppen if any(x != "*" and x in name for x in a)]
        if not regeln: regeln = [r for a, r in s.gruppen if "*" in a]
        best = None
        for allow, m in (x for r in regeln for x in r):
            if s._passt(m, pfad) and (best is None or len(m) > len(best[1]) or (len(m) == len(best[1]) and allow)): best = (allow, m)
        return True if best is None else best[0]

def robots_holen(host, ua=None):
    try:
        req = urllib.request.Request(host + "/robots.txt", headers={"User-Agent": ua or UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            return Robots(r.read(500_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return Robots("")   # 4xx (auch 403 „Invalid URL“ bei Daten-Schnittstellen): keine Regeln = erlaubt (RFC 9309); die Seite selbst kann trotzdem abweisen
    except Exception:
        return None

_robots, _zuletzt = {}, {}
def holen(url, timeout=25):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https"): return None, "keine Webadresse"
    host = u.scheme + "://" + u.netloc
    if host not in _robots:
        _robots[host] = robots_holen(host)
    rp = _robots[host]
    if rp is not None and not rp.can_fetch(UA, url): return None, "robots.txt verbietet"
    w = 1.0 - (time.time() - _zuletzt.get(host, 0))
    if w > 0: time.sleep(w)
    _zuletzt[host] = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "de,en;q=0.5"})
        with urllib.request.urlopen(req, timeout=abs(timeout)) as r:
            if "pdf" in (r.headers.get("Content-Type") or ""): return None, "PDF (nicht ausgewertet)"
            return r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location") if e.code in (307, 308) else None   # ältere Python-Versionen folgen 308 nicht
        if loc and timeout > 0: return holen(urllib.parse.urljoin(url, loc), -timeout)
        return None, str(e)[:80]
    except Exception as e:
        return None, str(e)[:80]

def erlaubt(url):
    """robots.txt erlaubt das Lesen? (auch für den Browser-Durchgang); wartet auf 1 Anfrage pro Sekunde und Seite"""
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https"): return False
    host = u.scheme + "://" + u.netloc
    if host not in _robots: _robots[host] = robots_holen(host)
    rp = _robots[host]
    if rp is not None and not rp.can_fetch(UA, url): return False
    w = 1.0 - (time.time() - _zuletzt.get(host, 0))
    if w > 0: time.sleep(w)
    _zuletzt[host] = time.time()
    return True

def pdf_text(daten):
    """Text aus PDF-Bytes: pdftotext (poppler-utils), sonst pypdf, sonst None"""
    import subprocess
    try:
        r = subprocess.run(["pdftotext", "-layout", "-q", "-", "-"], input=daten, capture_output=True, timeout=60)
        if r.returncode == 0 and r.stdout.strip(): return r.stdout.decode("utf-8", "replace")
    except Exception: pass
    try:
        import io, pypdf
        return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(daten)).pages[:20])
    except Exception: return None

def holen_pdf(url, timeout=40):
    """PDF holen (nur lesen, robots.txt, höchstens 10 MB) und als Text zurückgeben: (text, fehler)"""
    if not erlaubt(url): return None, "robots.txt verbietet"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "de,en;q=0.5"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            daten = r.read(10_000_000)
    except Exception as e:
        return None, str(e)[:80]
    if not daten.startswith(b"%PDF"): return None, "keine PDF"
    t = pdf_text(daten)
    return (t, None) if t and t.strip() else (None, "PDF ohne Text (Bild)")

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
