#!/usr/bin/env python3
"""Hilfe für Claude: zeigt Aufbau einzelner Seiten (nur lesen, robots.txt). Aufruf: ks_probe.py <url>..."""
import re, sys, os, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sammler
KW = re.compile(r"(schwierig|länge|laenge|höhenmeter|hoehenmeter|hm\b|topo|gesperrt|sperre|saniert|sanierung|geschlossen|stand|aktualisiert|zeit|einstieg|koordinat|lat|lng|geo)", re.I)
for u in sys.argv[1:]:
    voll = u.startswith("voll:"); u = u[5:] if voll else u
    print("=" * 100); print("URL", u)
    try:
        req = urllib.request.Request(u, headers={"User-Agent": sammler.UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            ct = r.headers.get("Content-Type"); raw = r.read(4_000_000)
            print("STATUS", r.status, "TYP", ct, "ENDE", r.geturl(), "BYTES", len(raw))
    except Exception as e:
        print("FEHLER", str(e)[:200]); continue
    if "xml" in (ct or "") or u.endswith(".xml") or u.endswith(".txt"):
        print(raw.decode("utf-8", "replace")[:6000]); continue
    s = raw.decode("utf-8", "replace")
    m = re.search(r"<title>(.*?)</title>", s, re.S); print("TITEL", m and m.group(1).strip()[:200])
    zeilen, links = sammler.text_und_links(s)
    print("--- Zeilen" + (" (alle)" if voll else " mit Stichwörtern"))
    for z in zeilen[:1200]:
        if (voll or KW.search(z)) and len(z) < 300: print("  ", z)
    print("--- Links (Bild/PDF/topo)")
    for h in dict.fromkeys(links):
        if re.search(r"(\.jpe?g|\.png|\.webp|\.pdf|topo)", h, re.I): print("  ", h[:250])
    print("--- Bilder")
    if voll:
        for m in re.finditer(r"<(div|span|dt|dd|label)[^>]*class=\"([^\"]*field[^\"]*)\"", s): print("  KLASSE", m.group(2)[:120])
    for m in dict.fromkeys(re.findall(r'<img[^>]+(?:data-src|src)="([^"]+)"', s)):
        print("  ", m[:250])
    for k in ("Topo zur Tour", "topo", "Schwierigkeit", "ld+json", "data-lat", "latitude"):
        i = s.find(k)
        if i >= 0: print(f"--- HTML um '{k}'"); print(re.sub(r"\s+", " ", s[max(0, i - 600):i + 1500]))
