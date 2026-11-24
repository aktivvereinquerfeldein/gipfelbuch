import os, json, re, sys, collections
rows = json.load(open("alle.json")); texte = json.load(open(sys.argv[1]))
BB = (9.5, 46.82, 10.25, 47.6)
def ll(r):
    g = r.get("geom"); g = json.loads(g) if isinstance(g, str) else g
    return g["coordinates"] if g and g.get("type") == "Point" else None
hs = []
for r in rows:
    p = ll(r); d = r.get("daten") or {}
    if not p or not (BB[0] <= p[0] <= BB[2] and BB[1] <= p[1] <= BB[3]): continue
    if d.get("recherche") in ("dublette", "keine_huette"): continue
    n = (r["name"] or "").strip()
    if len(n) < 6: continue
    hs.append(r)
print(len(hs), "Hütten im Gebiet")
EINK = re.compile(r"einkehr|bewirt|jause|jausen|essen|speis|küche|getränk|kaffee|kuchen|gastst|gasthaus|restaurant|stärkung|geöffnet|geschlossen|ruhetag|selbstversorg|unbewirt|sennerei|käse|alpbetrieb|bewirtschaft", re.I)
out = collections.defaultdict(list)
namen = collections.Counter(r["name"].strip().lower() for r in hs)
low = {u: t.lower() for u, t in texte.items()}
for r in hs:
    pat = re.compile(r"(?<![\wäöü])" + re.escape(r["name"].strip()) + r"(?![\wäöü])", re.I)
    nl = r["name"].strip().lower()
    for u, t in texte.items():
        if nl not in low[u]: continue
        bis = -1
        for m in pat.finditer(t):
            if m.start() < bis: continue
            a, b = max(0, m.start() - 350), min(len(t), m.end() + 350)
            s = t[a:b]
            if EINK.search(s):
                out[r["id"]].append({"url": u, "text": s, "mehrdeutig": namen[nl] > 1}); bis = b

json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False)
gt = {r["id"]: r for r in hs}
c = collections.Counter(((gt[i].get("daten") or {}).get("recherche"), gt[i]["verpflegung"]) for i in out)
print(len(out), "Hütten mit Erwähnung + Einkehr-Wort"); print(c.most_common())
print("insgesamt im Gebiet:", collections.Counter(((r.get("daten") or {}).get("recherche"), r["verpflegung"]) for r in hs).most_common())
