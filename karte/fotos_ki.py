#!/usr/bin/env python3
"""Gipfelbuch – Fotos prüfen (KI-Einordnung, Ablauf „Fotos prüfen“)

Schritte je Region (z. B. Vorarlberg):
  osm      <region.pbf> <region> ziele.json   – Gipfel, Hütten/Alpen und Wege aus OpenStreetMap
  sammeln  <region> ziele.json kand.jsonl     – Kandidaten: Wikimedia Commons (mit Ort), Bilder der Gipfel-/Hütten-Artikel
                                                (Wikidata/Wikipedia), Panoramax (nur auf Wegen), Fotos der Mitglieder
  pruefen  <region> ziele.json kand.jsonl bericht.md – Bilder klein laden, KI ordnet ein (fotos_klassen.py),
                                                Gipfel/Hütte zuordnen, in die Tabelle gb_fotos schreiben

Regeln (Marcel, 27.10.2026):
  - Gipfel: nur der Berg selbst und die Aussicht vom Gipfel; das erste Bild ist immer der Berg.
  - Hütten: nur die Hütte selbst, eventuell die Aussicht direkt dort (kein anderer Berg wie der Panülerkopf bei der Oberzalimhütte).
  - Trailview: nur Bilder vom Weg, keine Aussicht von diesem Punkt.
  - Aussortieren: Füße, nahe Gesichter, Nahaufnahmen von Blumen (auch Essen, Schilder/Karten).
  - Community-Fotos von Touren (öffentlich) werden genutzt; zeigt ein Foto nahe einem Gipfel das Gipfelkreuz, gehört es zu diesem Gipfel.
Nur lesen (Commons, Wikidata, Panoramax), 1 Anfrage pro Sekunde je API, eigener User-Agent. Nichts wird gelöscht:
entfernte Mitglieder-Fotos werden nur als „entfernt“ markiert.
"""
import json, os, re, sys, time, math, unicodedata, urllib.request, urllib.parse, concurrent.futures as cf, datetime

UA = "Gipfelbuch-Fotopruefung/1.0 (Aktivverein quer.feld.ein; https://gipfelbuch.quer-feld-ein.workers.dev)"
SB_URL = os.environ.get("SB_URL", "https://vehrlrjegxlhxibrxqxm.supabase.co")
GB = os.environ.get("GB_ORIGIN", "https://gipfelbuch.quer-feld-ein.workers.dev")
MAX_BILDER = int(os.environ.get("MAX_BILDER", "20000"))
HEUTE = datetime.date.today().isoformat()

REGIONEN = {  # W, S, E, N (grob, Überlappung ist egal – jedes Foto gibt es nur einmal)
    "Vorarlberg": (9.53, 46.84, 10.24, 47.60),
    "Tirol": (10.09, 46.65, 12.97, 47.75),
    "Salzburg": (12.07, 46.94, 13.99, 48.04),
    "Kärnten": (12.65, 46.37, 15.07, 47.13),
    "Südtirol": (10.38, 46.22, 12.48, 47.09),
    "Steiermark": (13.56, 46.61, 16.17, 47.83),
    "Oberösterreich": (12.74, 47.46, 14.99, 48.77),
    "Niederösterreich": (14.45, 47.42, 17.07, 49.02),
    "Wien": (16.18, 48.11, 16.58, 48.33),
    "Burgenland": (16.00, 46.83, 17.17, 48.12),
}

# ---------- Helfer ----------
_last = {}
def get(url, host_pause=1.0, tries=4, roh=False, timeout=60):
    h = urllib.parse.urlparse(url).netloc
    for t in range(tries):
        w = _last.get(h, 0) + host_pause - time.time()
        if w > 0: time.sleep(w)
        _last[h] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
                return b if roh else json.loads(b)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503): time.sleep(int(e.headers.get("Retry-After") or 20)); continue
            if e.code == 404: return None
            time.sleep(3)
        except Exception:
            time.sleep(3)
    return None

def bild(url):
    """Vorschaubild ohne Tempo-Bremse je Anfrage (läuft in mehreren Fäden)"""
    for t in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            time.sleep(5 * (t + 1))
        except Exception:
            time.sleep(3)
    return None

def sb(method, path, body=None, prefer=None):
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer: h["Prefer"] = prefer
    req = urllib.request.Request(SB_URL + "/rest/v1/" + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r: return r.read()

def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c)).replace("ß", "ss")
    return [w for w in re.split(r"[^a-z0-9]+", s) if len(w) > 2 and w not in ("der", "die", "das", "von", "und", "mit", "file", "jpg", "jpeg", "png")]

def hav(a, b):
    R = 6371000; la1, la2 = math.radians(a[1]), math.radians(b[1])
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(d))

def strip(s):
    return re.sub(r"<[^>]+>", "", s or "").replace("&nbsp;", " ").strip()

def monat_aus(s):
    m = re.search(r"(19|20)\d\d[-:/.](\d\d)", s or "")
    if m and 1 <= int(m.group(2)) <= 12: return int(m.group(2)), m.group(0)[:4] + "-" + m.group(2)
    return None, None

def datei_key(titel):
    f = re.sub(r"^(File|Datei):", "", titel).replace(" ", "_")
    return f.lower()

# ---------- 1. OSM: Gipfel, Hütten/Alpen, Wege ----------
def osm(pbf, region, aus):
    import osmium
    W, S, E, N = REGIONEN[region]
    gipfel, huetten, wege = [], [], []
    WEG = {"path", "footway", "track", "bridleway", "steps", "via_ferrata", "cycleway"}
    class H(osmium.SimpleHandler):
        def node(s, n):
            t = {x.k: x.v for x in n.tags}
            if not t.get("name"): return
            lon, lat = n.location.lon, n.location.lat
            if not (W <= lon <= E and S <= lat <= N): return
            if t.get("natural") in ("peak", "volcano"):
                gipfel.append({"id": n.id, "n": t.get("name:de") or t["name"], "lon": lon, "lat": lat, "ele": t.get("ele"), "wd": t.get("wikidata", ""), "wp": t.get("wikipedia", "")})
            elif t.get("tourism") in ("alpine_hut", "wilderness_hut") or (t.get("amenity") in ("restaurant", "cafe") and re.search(r"alp|alm|hütte|huette|vorsäß", t["name"], re.I)):
                huetten.append({"id": n.id, "n": t.get("name:de") or t["name"], "lon": lon, "lat": lat, "wd": t.get("wikidata", ""), "wp": t.get("wikipedia", "")})
        def way(s, w):
            t = {x.k: x.v for x in w.tags}
            if t.get("tourism") in ("alpine_hut", "wilderness_hut") and t.get("name"):
                try:
                    pts = [(n.lon, n.lat) for n in w.nodes]
                    lon = sum(p[0] for p in pts) / len(pts); lat = sum(p[1] for p in pts) / len(pts)
                    if W <= lon <= E and S <= lat <= N:
                        huetten.append({"id": w.id, "n": t.get("name:de") or t["name"], "lon": lon, "lat": lat, "wd": t.get("wikidata", ""), "wp": t.get("wikipedia", "")})
                except Exception: pass
                return
            if t.get("highway") not in WEG: return
            try:
                pts = [(round(n.lon, 6), round(n.lat, 6)) for n in w.nodes]
            except Exception: return
            if len(pts) > 1 and any(W <= p[0] <= E and S <= p[1] <= N for p in pts): wege.append(pts)
    H().apply_file(pbf, locations=True, idx="flex_mem")
    json.dump({"gipfel": gipfel, "huetten": huetten, "wege": wege}, open(aus, "w"))
    print(f"{region}: {len(gipfel)} Gipfel, {len(huetten)} Hütten/Alpen, {len(wege)} Wege")

class Ziele:
    def __init__(s, path):
        from shapely.geometry import LineString, Point
        from shapely.strtree import STRtree
        z = json.load(open(path))
        s.gipfel, s.huetten = z["gipfel"], z["huetten"]
        s.Point = Point
        s.gt = STRtree([Point(g["lon"], g["lat"]) for g in s.gipfel]) if s.gipfel else None
        s.ht = STRtree([Point(h["lon"], h["lat"]) for h in s.huetten]) if s.huetten else None
        s.wl = [LineString(w) for w in z["wege"]]
        s.wt = STRtree(s.wl) if s.wl else None
    def nahe(s, tree, liste, lon, lat, m):
        if tree is None: return []
        d = m / 111320.0
        idx = tree.query(s.Point(lon, lat).buffer(d / max(0.2, math.cos(math.radians(lat)))))
        out = [(hav((lon, lat), (liste[i]["lon"], liste[i]["lat"])), liste[i]) for i in idx]
        return sorted([x for x in out if x[0] <= m], key=lambda x: x[0])
    def weg_m(s, lon, lat, maxm=60):
        """Abstand zum nächsten Weg/Pfad/Forstweg in Metern (bis maxm, sonst None)"""
        if s.wt is None: return None
        k = math.cos(math.radians(lat)); p = s.Point(lon * k, lat)
        best = None
        for i in s.wt.query(s.Point(lon, lat).buffer(maxm / 111320.0 / max(0.2, k))):
            from shapely.affinity import scale
            g = scale(s.wl[i], xfact=k, yfact=1, origin=(0, 0))
            d = g.distance(p) * 111320.0
            if best is None or d < best: best = d
        return round(best, 1) if best is not None and best <= maxm else None

# ---------- 2. Kandidaten sammeln ----------
API = "https://commons.wikimedia.org/w/api.php"
IIFILT = "Artist|LicenseShortName|DateTimeOriginal|ObjectName"

def commons_info(pageids=None, titles=None):
    out = {}
    lst = pageids or titles
    for i in range(0, len(lst), 50):
        chunk = lst[i:i + 50]
        q = {"action": "query", "format": "json", "prop": "imageinfo|coordinates", "iiprop": "url|extmetadata|mime", "iiurlwidth": "480", "iiextmetadatafilter": IIFILT}
        if pageids: q["pageids"] = "|".join(str(x) for x in chunk)
        else: q["titles"] = "|".join(chunk)
        j = get(API + "?" + urllib.parse.urlencode(q)) or {}
        for p in (j.get("query", {}).get("pages", {}) or {}).values():
            ii = (p.get("imageinfo") or [None])[0]
            if not ii or not re.search(r"jpeg|png|webp", ii.get("mime", "")) or "pageid" not in p: continue
            md = ii.get("extmetadata", {})
            c = (p.get("coordinates") or [{}])[0]
            dt = strip(md.get("DateTimeOriginal", {}).get("value"))
            mo, ym = monat_aus(dt)
            out[p["title"]] = {"id": "c%d" % p["pageid"], "quelle": "commons", "lat": c.get("lat"), "lng": c.get("lon"),
                               "titel": strip(md.get("ObjectName", {}).get("value")) or re.sub(r"^File:|\.[a-z]+$", "", p["title"], flags=re.I),
                               "src": ii.get("thumburl"), "big": (ii.get("thumburl") or "").replace("/480px-", "/1600px-"),
                               "autor": strip(md.get("Artist", {}).get("value"))[:200], "lizenz": md.get("LicenseShortName", {}).get("value", ""),
                               "link": ii.get("descriptionurl"), "datum": dt[:10] if re.match(r"\d{4}-\d\d-\d\d", dt) else None, "monat": mo,
                               "datei": datei_key(p["title"])}
    return out

def commons_region(W, S, E, N, tiefe=0):
    """alle Commons-Dateien mit Ort im Rechteck (Rechteck wird geteilt, wenn es zu viele sind)"""
    q = {"action": "query", "format": "json", "list": "geosearch", "gsbbox": f"{N}|{W}|{S}|{E}", "gsnamespace": "6", "gslimit": "500"}
    j = get(API + "?" + urllib.parse.urlencode(q)) or {}
    r = j.get("query", {}).get("geosearch", [])
    if len(r) >= 500 and tiefe < 6:
        mx, my = (W + E) / 2, (S + N) / 2
        out = []
        for b in ((W, S, mx, my), (mx, S, E, my), (W, my, mx, N), (mx, my, E, N)): out += commons_region(*b, tiefe + 1)
        return out
    return [x["pageid"] for x in r]

def wiki_bilder(ziele):
    """Titelbilder der Gipfel/Hütten (Wikidata P18 und Wikipedia-Artikel) – das ist meist der Berg selbst"""
    titel = {}  # Dateiname -> (art, ziel)
    wd = [(x, "gipfel") for x in ziele.gipfel if x["wd"]] + [(x, "huette") for x in ziele.huetten if x["wd"]]
    for i in range(0, len(wd), 50):
        chunk = wd[i:i + 50]
        j = get("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode({"action": "wbgetentities", "format": "json", "props": "claims", "ids": "|".join(x["wd"] for x, _ in chunk)})) or {}
        for x, art in chunk:
            e = (j.get("entities") or {}).get(x["wd"]) or {}
            for cl in (e.get("claims") or {}).get("P18", [])[:2]:
                v = cl.get("mainsnak", {}).get("datavalue", {}).get("value")
                if v: titel.setdefault("File:" + v, (art, x))
    wp = {}
    for x, art in [(x, "gipfel") for x in ziele.gipfel if x["wp"]] + [(x, "huette") for x in ziele.huetten if x["wp"]]:
        m = re.match(r"^([a-z]{2}):(.+)$", x["wp"])
        if m: wp.setdefault(m.group(1), []).append((m.group(2), art, x))
    for lang, L in wp.items():
        for i in range(0, len(L), 50):
            chunk = L[i:i + 50]
            j = get(f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode({"action": "query", "format": "json", "prop": "pageimages", "piprop": "name", "redirects": "1", "titles": "|".join(t for t, _, _ in chunk)})) or {}
            red = {r["to"]: r["from"] for r in (j.get("query", {}).get("redirects") or [])}
            nmap = {n["to"]: n["from"] for n in (j.get("query", {}).get("normalized") or [])}
            for p in (j.get("query", {}).get("pages") or {}).values():
                if not p.get("pageimage"): continue
                t = red.get(p["title"], p["title"]); t = nmap.get(t, t)
                for tt, art, x in chunk:
                    if tt == t or tt.replace("_", " ") == t: titel.setdefault("File:" + p["pageimage"].replace("_", " "), (art, x))
    info = commons_info(titles=list(titel))
    out = []
    for t, (art, x) in titel.items():
        k = info.get(t) or info.get(t.replace("_", " "))
        if not k: continue
        k = dict(k); k["lead"] = art; k["ziel_id"] = x["id"]
        if k["lat"] is None: k["lat"], k["lng"] = x["lat"], x["lon"]; k["ohne_ort"] = 1
        out.append(k)
    return out

def panoramax_region(W, S, E, N, ziele, tiefe=0):
    """Panoramax-Bilder, die auf einem Weg/Pfad/Forstweg liegen (bis 20 m); Serienbilder auf 25 m ausgedünnt"""
    from shapely.geometry import box
    if ziele.wt is None or not len(ziele.wt.query(box(W, S, E, N))): return []
    j = get("https://api.panoramax.xyz/api/search?" + urllib.parse.urlencode({"bbox": f"{W},{S},{E},{N}", "limit": "1000"}), host_pause=0.5) or {}
    fs = j.get("features") or []
    if len(fs) >= 1000 and tiefe < 7:
        mx, my = (W + E) / 2, (S + N) / 2
        out = []
        for b in ((W, S, mx, my), (mx, S, E, my), (W, my, mx, N), (mx, my, E, N)): out += panoramax_region(*b, ziele, tiefe + 1)
        return out
    out = []
    for f in fs:
        th = (f.get("assets") or {}).get("thumb", {}).get("href")
        if not th: continue
        lon, lat = f["geometry"]["coordinates"]
        wm = ziele.weg_m(lon, lat, 20)
        if wm is None: continue
        dt = (f.get("properties") or {}).get("datetime") or ""
        mo, _ = monat_aus(dt)
        out.append({"id": "p" + f["id"], "quelle": "panoramax", "lat": lat, "lng": lon, "titel": "Trailview", "src": th,
                    "big": ((f["assets"].get("sd") or f["assets"].get("hd") or f["assets"]["thumb"])["href"]),
                    "autor": ((f.get("providers") or [{}])[0].get("name") or "")[:200], "lizenz": (f.get("properties") or {}).get("license") or "CC-BY-SA",
                    "link": f"https://api.panoramax.xyz/#focus=pic&pic={f['id']}", "datum": dt[:10] or None, "monat": mo, "weg_m": wm})
    return out

def duennen(liste, m=25):
    zell = {}
    out = []
    for x in liste:
        k = (round(x["lat"] * 2000), round(x["lng"] * 1400))  # ca. 55 m-Raster zum schnellen Vorsortieren
        nah = [y for dx in (-1, 0, 1) for dy in (-1, 0, 1) for y in zell.get((k[0] + dx, k[1] + dy), [])]
        if any(hav((x["lng"], x["lat"]), (y["lng"], y["lat"])) < m for y in nah): continue
        zell.setdefault(k, []).append(x); out.append(x)
    return out

def mitglieder(W, S, E, N):
    """Fotos aus Aktivitäten (öffentlich und Freunde). Nur öffentliche bekommen Ort und Bildadresse in gb_fotos."""
    if not os.environ.get("SUPABASE_SERVICE_KEY"): return [], set()
    sel = "id,title,visibility,done_at,photos,photo_pos,photo_time"
    rows = json.loads(sb("GET", f"activities?select={sel}&visibility=in.(public,friends)&max_lat=gte.{S}&min_lat=lte.{N}&max_lng=gte.{W}&min_lng=lte.{E}&limit=5000"))
    out, alle = [], set()
    for a in rows:
        pos, tim = a.get("photo_pos") or {}, a.get("photo_time") or {}
        for k in a.get("photos") or []:
            alle.add("g" + k)
            q = pos.get(k)
            t = tim.get(k)
            mo = datetime.datetime.utcfromtimestamp(t / 1000).month if isinstance(t, (int, float)) else None
            pub = a.get("visibility") == "public"
            out.append({"id": "g" + k, "quelle": "mitglied", "lat": q[1] if (q and pub) else None, "lng": q[0] if (q and pub) else None,
                        "_lat": q[1] if q else None, "_lng": q[0] if q else None,
                        "titel": a.get("title") or "", "src": GB + "/fotos/" + k if pub else None, "big": GB + "/fotos/" + k if pub else None,
                        "autor": "", "lizenz": "", "link": "", "monat": mo,
                        "datum": datetime.datetime.utcfromtimestamp(t / 1000).date().isoformat() if isinstance(t, (int, float)) else None,
                        "oeffentlich": pub, "_laden": GB + "/fotos/" + k})
    return out, alle

def vorhanden(W, S, E, N):
    if not os.environ.get("SUPABASE_SERVICE_KEY"): return set()
    ids, off = set(), 0
    while True:
        b = json.loads(sb("GET", f"gb_fotos?select=id&lat=gte.{S}&lat=lte.{N}&lng=gte.{W}&lng=lte.{E}&order=id&offset={off}&limit=1000"))
        ids |= {x["id"] for x in b}
        if len(b) < 1000: break
        off += 1000
    return ids

def sammeln(region, zpath, aus):
    W, S, E, N = REGIONEN[region]
    z = Ziele(zpath)
    alt = set() if os.environ.get("ALLE") == "1" else vorhanden(W, S, E, N)
    print(f"schon geprüft: {len(alt)}")
    nur_mg = os.environ.get("NUR_MITGLIEDER") == "1"
    # Commons in 0,1°-Feldern
    pids = []
    x = E if nur_mg else W
    while x < E:
        y = S
        while y < N:
            pids += commons_region(round(x, 4), round(y, 4), round(min(E, x + 0.1), 4), round(min(N, y + 0.1), 4))
            y += 0.1
        x += 0.1
    pids = sorted(set(p for p in pids if "c%d" % p not in alt))
    print(f"Commons neu: {len(pids)}")
    com = list(commons_info(pageids=pids).values())
    lead = [] if nur_mg else [k for k in wiki_bilder(z) if k["id"] not in alt or os.environ.get("ALLE") == "1"]
    print(f"Titelbilder Gipfel/Hütten: {len(lead)}")
    pm = []
    x = E if nur_mg else W
    while x < E:
        y = S
        while y < N:
            pm += panoramax_region(round(x, 4), round(y, 4), round(min(E, x + 0.05), 4), round(min(N, y + 0.05), 4), z)
            y += 0.05
        x += 0.05
    pm = duennen([p for p in pm if p["id"] not in alt])
    print(f"Panoramax auf Wegen (ausgedünnt): {len(pm)}")
    mg, alle_mg = mitglieder(W, S, E, N)
    mg = [m for m in mg if m["id"] not in alt]
    print(f"Mitglieder-Fotos neu: {len(mg)}")
    seen = set()
    with open(aus, "w") as f:
        for k in lead + mg + com + pm:   # Reihenfolge = Wichtigkeit (bei MAX_BILDER wird hinten abgeschnitten)
            if k["id"] in seen: continue
            seen.add(k["id"]); f.write(json.dumps(k, ensure_ascii=False) + "\n")
    json.dump(sorted(alle_mg), open(aus + ".mitglieder.json", "w"))
    print(f"Kandidaten gesamt: {len(seen)} (Grenze je Lauf: {MAX_BILDER})")

# ---------- 3. Prüfen, zuordnen, eintragen ----------
def zuordnen(k, z):
    """Gipfel/Hütte zuordnen (nur Fotos, die laut KI dazu passen)"""
    lon, lat = k.get("lng") if k.get("lng") is not None else k.get("_lng"), k.get("lat") if k.get("lat") is not None else k.get("_lat")
    kl, tt = k["klasse"], set(norm(k.get("titel", "")))
    g = h = None
    if k.get("lead") == "gipfel" and kl in ("gipfel", "aussicht", "huette", "ort"):
        g = next((x for x in z.gipfel if x["id"] == k["ziel_id"]), None)
        if g and kl != "gipfel": k["klasse"] = "gipfel"   # Titelbild des Artikels zeigt den Berg
    elif k.get("lead") == "huette" and kl in ("huette", "ort", "aussicht", "gipfel"):
        h = next((x for x in z.huetten if x["id"] == k["ziel_id"]), None)
        if h: k["klasse"] = "huette"
    elif lon is not None:
        if kl == "gipfel":
            nah = z.nahe(z.gt, z.gipfel, lon, lat, 300)
            if nah: g = nah[0][1]                                      # z. B. Gipfelkreuz am Rappenkopf
            else:                                                      # Berg von weiter weg, Name im Bildtitel
                for d, x in z.nahe(z.gt, z.gipfel, lon, lat, 6000):
                    n = norm(x["n"])
                    if n and all(w in tt for w in n): g = x; break
        elif kl == "aussicht":
            nah = z.nahe(z.gt, z.gipfel, lon, lat, 150)
            if nah: g = nah[0][1]                                      # Aussicht vom Gipfel
            else:
                nh = z.nahe(z.ht, z.huetten, lon, lat, 80)
                if nh: h = nh[0][1]                                    # Aussicht direkt bei der Hütte
        elif kl == "huette":
            nh = z.nahe(z.ht, z.huetten, lon, lat, 150)
            if nh: h = nh[0][1]
            else:
                for d, x in z.nahe(z.ht, z.huetten, lon, lat, 1500):
                    n = norm(x["n"])
                    if n and all(w in tt for w in n): h = x; break
    if g: k["gipfel_osm"], k["gipfel_name"] = g["id"], g["n"]
    if h: k["huette_osm"], k["huette_name"] = h["id"], h["n"]

FELDER = ("id", "quelle", "lat", "lng", "datum", "monat", "klasse", "ok", "werte", "gipfel_osm", "gipfel_name", "huette_osm", "huette_name",
          "weg_m", "titel", "src", "big", "autor", "lizenz", "link", "region", "oeffentlich", "datei")

def pruefen(region, zpath, kpath, bericht):
    from fotos_klassen import Pruefer
    z = Ziele(zpath)
    K = [json.loads(l) for l in open(kpath)][:MAX_BILDER]
    P = Pruefer()
    stat, fertig, fehler = {}, [], 0
    db = bool(os.environ.get("SUPABASE_SERVICE_KEY"))
    def laden(k): return bild(k.get("_laden") or k["src"])
    t0 = time.time()
    for i in range(0, len(K), 64):
        chunk = K[i:i + 64]
        with cf.ThreadPoolExecutor(6) as ex: bl = list(ex.map(laden, chunk))
        res = P.pruefe([b or b"" for b in bl])
        rows = []
        for k, r in zip(chunk, res):
            if r is None: fehler += 1; continue
            k.update(r); k["region"] = region; k.setdefault("oeffentlich", True)
            lon = k["lng"] if k.get("lng") is not None else k.get("_lng"); lat = k["lat"] if k.get("lat") is not None else k.get("_lat")
            if k.get("weg_m") is None and lon is not None and not k.get("ohne_ort"): k["weg_m"] = z.weg_m(lon, lat, 60)
            zuordnen(k, z)
            stat[k["klasse"]] = stat.get(k["klasse"], 0) + 1
            rows.append({f: k.get(f) for f in FELDER})
            fertig.append(k)
        if db and rows:
            sb("POST", "gb_fotos?on_conflict=id", rows, "resolution=merge-duplicates,return=minimal")
        if (i // 64) % 20 == 0: print(f"{i + len(chunk)}/{len(K)} geprüft, {round(time.time() - t0)} s", flush=True)
    # Mitglieder-Fotos, die es nicht mehr gibt: nur markieren, nicht löschen
    entfernt = 0
    mp = kpath + ".mitglieder.json"
    if db and os.path.exists(mp):
        alle = set(json.load(open(mp)))
        W, S, E, N = REGIONEN[region]
        b = json.loads(sb("GET", f"gb_fotos?select=id&quelle=eq.mitglied&region=eq.{urllib.parse.quote(region)}&klasse=neq.entfernt&limit=10000"))
        weg = [x["id"] for x in b if x["id"] not in alle]
        for i in range(0, len(weg), 100):
            sb("PATCH", "gb_fotos?id=in.(" + ",".join('"' + x + '"' for x in weg[i:i + 100]) + ")", {"klasse": "entfernt", "ok": False}, "return=minimal")
        entfernt = len(weg)
    gz = sum(1 for k in fertig if k.get("gipfel_osm")); hz = sum(1 for k in fertig if k.get("huette_osm"))
    with open(bericht, "w", encoding="utf-8") as f:
        f.write(f"# Fotos prüfen {region} ({HEUTE})\n\n")
        f.write(f"- Geprüft: {len(fertig)} Bilder (nicht ladbar: {fehler}), Kandidaten gesamt: {sum(1 for _ in open(kpath))}, Grenze je Lauf: {MAX_BILDER}\n")
        f.write(f"- Datenbank: {'eingetragen' if db else 'nicht eingetragen (kein SUPABASE_SERVICE_KEY)'}\n")
        f.write(f"- Einem Gipfel zugeordnet: {gz}, einer Hütte/Alpe zugeordnet: {hz}, entfernte Mitglieder-Fotos markiert: {entfernt}\n")
        f.write(f"- Modell: {os.environ.get('FOTO_MODELL', 'ViT-B-16-SigLIP2')}\n\n## Gruppen\n\n")
        for kl, n in sorted(stat.items(), key=lambda x: -x[1]): f.write(f"- {kl}: {n}\n")
        f.write("\n## Stichprobe (je Gruppe 8)\n\n")
        for kl in sorted(stat):
            f.write(f"### {kl}\n\n")
            for k in [x for x in fertig if x["klasse"] == kl][:8]:
                ziel = k.get("gipfel_name") or k.get("huette_name") or ""
                f.write(f"- [{k.get('titel') or k['id']}]({k.get('link') or k.get('src') or ''}) {('→ ' + ziel) if ziel else ''} {json.dumps(k.get('werte'))}\n")
            f.write("\n")
    print(f"fertig: {len(fertig)} geprüft, Gruppen {stat}, Gipfel {gz}, Hütten {hz}")

if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(1)
    if a[0] == "osm": osm(a[1], a[2], a[3])
    elif a[0] == "sammeln": sammeln(a[1], a[2], a[3])
    elif a[0] == "pruefen": pruefen(a[1], a[2], a[3], a[4])
    else: print(__doc__); sys.exit(1)
