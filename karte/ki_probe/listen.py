#!/usr/bin/env python3
"""Listen bewirtschafteter Alpen (Broschüren der Regionen, Alp-Verbände) als Textstellen für die KI-Probe.
Jede Liste wird in Einträge je Alpe geteilt, der Eintrag wird unserer Hütte zugeordnet:
- mit Lage (Datenblock der Seite): Name passt und höchstens 1,5 km entfernt
- ohne Lage (PDF): Name passt genau und ist im Gebiet der Liste eindeutig
Ergebnis wird an treffer.json und lage.json angehängt (Einträge der Listen haben Adressen „<liste>#<nr>“).
Aufruf: listen.py <alle.json> <treffer.json> <lage.json>"""
import os, sys, re, json, html, io, difflib, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import sammler, quellen

HIER = os.path.dirname(os.path.abspath(__file__))
KOPF = re.compile(r"^(Alpe?|Alm|Vorsäß|Maisäß|Sennalpe|Berghütte|Hütte|Alpmuseum|Berggasthof|Berghaus)\s+\S.{0,45}$|^.{3,55}\s\d[\d.]{2,4}\s?m$")


def holen_gross(url, n=60_000_000):
    """wie sammler.holen (robots.txt, 1 Anfrage/s, eigener Name), aber ohne 3-MB-Grenze"""
    if not sammler.erlaubt(url): return None
    req = urllib.request.Request(url, headers={"User-Agent": sammler.UA, "Accept-Language": "de"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(n)


def pdf_text(daten):
    try:
        import subprocess
        r = subprocess.run(["pdftotext", "-q", "-", "-"], input=daten, capture_output=True, timeout=120)
        if r.returncode == 0 and r.stdout.strip(): return r.stdout.decode("utf-8", "replace")
    except Exception: pass
    import pypdf
    return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(daten)).pages)


def kern(n):
    """Name ohne Höhe und ohne „Alpe/Alp/Alm/Vorsäß“ vorne oder hinten: „Alpe Uga“ = „Ugaalpe“"""
    n = re.sub(r"\(.*?\)|\d[\d.,]*\s*m\b|\bim (sommer|winter)\b|^bio[- ]", " ", n or "", flags=re.I)
    k = quellen.norm(n)
    for _ in range(2): k = re.sub(r"^(jausenstation|biosennalpe|sennalpe|alpe|alp|alm|vorsaess|maisaess)", "", k)
    k = re.sub(r"(alpe|alp|alm|vorsaess|maisaess)$", "", k)
    return k


def ll(r):
    g = r.get("geom"); g = json.loads(g) if isinstance(g, str) else g
    return g["coordinates"] if g and g.get("type") == "Point" else None


def html_text(s):
    s = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", "\n", s or "", flags=re.I)
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def eintraege_pdf(text):
    """(Kopfzeile, Text) je Eintrag: Eintrag beginnt bei einer Kopfzeile und endet bei der nächsten"""
    zeilen = [re.sub(r"\s+", " ", z).strip() for z in text.split("\n")]
    kopf = [i for i, z in enumerate(zeilen) if z and KOPF.match(z) and "—" not in z and " – " not in z]
    out = []
    for a, b in zip(kopf, kopf[1:] + [len(zeilen)]):
        t = "\n".join(z for z in zeilen[a:b] if z)
        t = re.sub(r"-\n(?=[a-zäöü])", "", re.sub(r" -\n", "", t))   # Trennstriche am Zeilenende
        if len(t) > 120: out.append((zeilen[a], t[:1800]))
    return out


def eintraege_eopoi(raw):
    i = raw.find("eopoi-app-widget-initial-data"); j = raw.find("{", i); k = raw.find("<", j)
    s = html.unescape(raw[j:k]).strip()
    dec, pos, out = json.JSONDecoder(), 1, []
    while True:
        m = re.compile(r'\s*"(\d+)":').match(s, pos)
        if not m: break
        e, pos = dec.raw_decode(s, m.end())
        if s[pos:pos + 1] == ",": pos += 1
        if e.get("deleted") or not e.get("active", True): continue
        teile = [e.get("title", ""), e.get("subTitle", ""),
                 html_text((e.get("shortDescription") or {}).get("description")),
                 html_text((e.get("description") or {}).get("description")),
                 html_text(((e.get("openingHours") or {}).get("description") or {}).get("description"))]
        g = e.get("geo") or {}
        out.append((e.get("title", ""), "\n".join(x for x in teile if x)[:1800],
                    [round(g["lng"], 5), round(g["lat"], 5)] if g.get("lat") else None, e.get("slug")))
    return out


if __name__ == "__main__":
    rows = json.load(open(sys.argv[1])); treffer = json.load(open(sys.argv[2])); lage = json.load(open(sys.argv[3]))
    for u in [u for u in lage if "#" in u]: del lage[u]   # alte Listeneinträge neu bilden
    for h in list(treffer):
        treffer[h] = [s for s in treffer[h] if "#" not in s["url"]]
        if not treffer[h]: del treffer[h]
    for l in open(os.path.join(HIER, "listen.txt"), encoding="utf-8"):
        l = l.strip()
        if not l or l.startswith("#"): continue
        name, typ, url, geb = [x.strip() for x in l.split(";")]
        bb = [float(x) for x in geb.split(",")]
        hs = []
        for r in rows:
            p = ll(r); d = r.get("daten") or {}
            if p and bb[0] <= p[0] <= bb[2] and bb[1] <= p[1] <= bb[3] and d.get("recherche") not in ("dublette", "keine_huette"):
                hs.append((r, p, kern(r["name"])))
        nach = {}
        for r, p, k in hs: nach.setdefault(k, []).append((r, p))
        daten = holen_gross(url)
        if not daten: print(name, ": nicht lesbar"); continue
        if typ == "pdf": eintr = [(k, t, None, None) for k, t in eintraege_pdf(pdf_text(daten))]
        else: eintr = eintraege_eopoi(daten.decode("utf-8", "replace"))
        ok, fehlt = 0, []
        for nr, (kopf, text, punkt, slug) in enumerate(eintr):
            k = kern(kopf)
            if len(k) < 3: continue
            kand = nach.get(k, [])
            if punkt:
                if not kand:
                    kand = [x for n in difflib.get_close_matches(k, list(nach), n=3, cutoff=0.85) for x in nach[n]]
                if not kand:   # Name etwas anders („Sennalpe Laufbichl“ / „Laufbichlalpe“): nur ganz nah und Namensteil gleich
                    kand = [(r, p) for r, p, kk in hs if quellen.km(punkt[1], punkt[0], p[1], p[0]) <= 0.3 and len(kk) >= 4 and (kk in k or k in kk)]
                kand = sorted(((quellen.km(punkt[1], punkt[0], p[1], p[0]), r["id"], r, p) for r, p in kand))
                kand = [(r, p) for d, _, r, p in kand if d <= 1.5][:1]   # gleicher Name zweimal nah beisammen = Doppeleintrag, der nähere zählt
            if len(kand) > 1 and max(quellen.km(a[1][1], a[1][0], b[1][1], b[1][0]) for a in kand for b in kand) <= 1.0:
                kand = sorted(kand, key=lambda x: x[0]["id"])[:1]   # derselbe Ort doppelt in der Datenbank
            if len(kand) != 1:
                fehlt.append(f"{kopf} ({len(kand)})"); continue
            r = kand[0][0]; a = f"{url}#{slug or nr}"
            treffer.setdefault(str(r["id"]), []).insert(0, {"url": a, "text": text, "titel": True, "mehrdeutig": False, "liste": name})
            lage[a] = {"punkt": punkt, "track": []} if punkt else None
            ok += 1
        print(f"{name}: {len(eintr)} Einträge, {ok} zugeordnet", flush=True)
        print("  ohne Zuordnung:", "; ".join(fehlt))
    json.dump(treffer, open(sys.argv[2], "w"), ensure_ascii=False)
    json.dump(lage, open(sys.argv[3], "w"))
