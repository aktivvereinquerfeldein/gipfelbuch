#!/usr/bin/env python3
"""Textstellen suchen, in denen eine Hütte zusammen mit einem Einkehr-Wort erwähnt wird.
Seiten, die direkt von der Hütte handeln (Name im Titel), kommen zuerst und liefern den Seitenanfang.
Aufruf: treffer.py <texte.json> <treffer.json> [alle.json]"""
import os, json, re, sys, collections
rows = json.load(open(sys.argv[3] if len(sys.argv) > 3 else "alle.json")); texte = json.load(open(sys.argv[1]))
BB = (9.5, 46.82, 10.25, 47.6)   # Probe: Vorarlberg und Umgebung


def ll(r):
    g = r.get("geom"); g = json.loads(g) if isinstance(g, str) else g
    return g["coordinates"] if g and g.get("type") == "Point" else None


hs = []
for r in rows:
    p = ll(r); d = r.get("daten") or {}
    if not p or not (BB[0] <= p[0] <= BB[2] and BB[1] <= p[1] <= BB[3]): continue
    if d.get("recherche") in ("dublette", "keine_huette"): continue
    if len((r["name"] or "").strip()) < 5: continue
    hs.append(r)
print(len(hs), "Hütten im Gebiet")
EINK = re.compile(r"einkehr|bewirt|jause|jausen|essen|speis|küche|getränk|kaffee|kuchen|gastst|gasthaus|restaurant|stärkung|geöffnet|geschlossen|ruhetag|selbstversorg|unbewirt|sennerei|käse|alpbetrieb|bewirtschaft", re.I)
namen = collections.Counter(r["name"].strip().lower() for r in hs)
low = {u: t.lower() for u, t in texte.items()}
out = {}
for r in hs:
    name = r["name"].strip(); nl = name.lower()
    pat = re.compile(r"(?<![\wäöü])" + re.escape(name) + r"(?![\wäöü])", re.I)
    funde = []
    for u, t in texte.items():
        if nl not in low[u]: continue
        titel = low[u].split("\n", 1)[0]
        if pat.search(titel) and EINK.search(t[:2500]):
            funde.append((0, {"url": u, "text": t[:1800], "titel": True}))   # Seite handelt von der Hütte
            continue
        bis = -1
        for m in pat.finditer(t):
            if m.start() < bis: continue
            a, b = max(0, m.start() - 350), min(len(t), m.end() + 350)
            s = t[a:b]
            if EINK.search(s):
                funde.append((1, {"url": u, "text": s, "titel": False})); bis = b
    if funde:
        funde.sort(key=lambda x: x[0])
        out[str(r["id"])] = [dict(f, mehrdeutig=namen[nl] > 1) for _, f in funde[:6]]
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False)
gt = {str(r["id"]): r for r in hs}
c = collections.Counter(((gt[i].get("daten") or {}).get("recherche"), gt[i]["verpflegung"]) for i in out)
print(len(out), "Hütten mit Erwähnung + Einkehr-Wort"); print(c.most_common())
