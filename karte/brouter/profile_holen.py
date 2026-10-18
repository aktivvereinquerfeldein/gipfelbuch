#!/usr/bin/env python3
"""Die eigenen BRouter-Profile (gb-hike, gb-bike, gb-road) direkt aus der App (index.html auf GitHub) holen,
damit Server und App immer dieselben Profile haben. Aufruf: profile_holen.py <ziel-ordner>"""
import re, json, sys, urllib.request
URL = "https://raw.githubusercontent.com/aktivvereinquerfeldein/gipfelbuch/main/index.html"
html = urllib.request.urlopen(URL, timeout=60).read().decode("utf-8")
for var, name in (("GB_BIKE_BRF", "gb-bike"), ("GB_HIKE_BRF", "gb-hike"), ("GB_ROAD_BRF", "gb-road")):
    m = re.search(r'const ' + var + r'=("(?:[^"\\]|\\.)*");', html)
    if not m: sys.exit("Profil fehlt in index.html: " + var)
    open(f"{sys.argv[1]}/{name}.brf", "w").write(json.loads(m.group(1)))
    print("Profil", name, "aktualisiert")
