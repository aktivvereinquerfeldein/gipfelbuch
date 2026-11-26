#!/usr/bin/env bash
# qfe-active – ganze Welt rechnen, auf einem gemieteten Server (Hetzner), gestartet vom Ablauf „Welt Server“ (.github/workflows/welt-server.yml).
# Gleiche Rechnung wie Österreich (karte-aktualisieren.yml), nur für alle Länder:
#   1. Grundkarte der ganzen Welt mit Planetiler (gleiche Version und Einstellungen wie Österreich)
#   2. jede Region (Geofabrik-Ausschnitt, höchstens ca. 1,2 GB) wie Österreich: Kartenstücke, Wege, Lifte, Gebäudenamen (karte/welt/region.sh)
#   3. Wege aller Regionen zusammen (doppelte an den Grenzen raus) -> wege.pmtiles mit tippecanoe (gleiche Einstellungen)
#   4. Kartenstücke aller Regionen je Ebene in eine Datei (karte/welt/zellen_pack.py)
#   5. Kartenpaket Welt = Grundkarte + Wege + eingebackene Ebenen (karte/paket.mjs in vielen Stücken gleichzeitig, dann paket_zusammen.mjs)
#   6. Hochladen nach R2 karte/welt/<Stand>/welt/ und (nur Modus voll) in karte/welt/aktuell.json als Region „welt“ HINTER den bisherigen
#      Regionen eintragen. Die laufende Karte (karte/aktuell.json) bleibt unverändert; die App nutzt das Paket nur im Testmodus ?welt=1.
#   7. Server löscht sich selbst (bei Fehler nach 2 Stunden, damit Claude nachsehen kann).
# Einstellungen kommen aus /root/welt.env: MODUS (probe|voll), S (Stand), R2-Schlüssel, HCLOUD_TOKEN, SERVER_ID.
set -euo pipefail
source /root/welt.env
export S MODUS
K=/root/gipfelbuch/karte; export ARBEIT=/data/w; mkdir -p $ARBEIT; cd $ARBEIT
export TMPDIR=$ARBEIT/tmp; mkdir -p $TMPDIR
export RCLONE_CONFIG_R2_TYPE=s3 RCLONE_CONFIG_R2_PROVIDER=Cloudflare RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true \
  RCLONE_CONFIG_R2_ENDPOINT=https://fbcade196ec6ad17a597c7ae5d75b31b.r2.cloudflarestorage.com \
  RCLONE_CONFIG_R2_ACCESS_KEY_ID RCLONE_CONFIG_R2_SECRET_ACCESS_KEY
BUCKET=qfeaktivfotos; ZIEL="karte/welt/$S/welt"; [ "$MODUS" = probe ] && ZIEL="karte/welt/$S-probe/welt"
CPU=$(nproc); RAM=$(free -g | awk '/Mem:/{print $2}')
PLANETILER=0.9.1
schritt() { echo; echo "=== $(date -u +%H:%M) $*"; echo "$(date -u +%FT%TZ) $*" >> $ARBEIT/schritte.txt; }
loeschen() { curl -s -X DELETE -H "Authorization: Bearer $HCLOUD_TOKEN" "https://api.hetzner.cloud/v1/servers/$SERVER_ID" > /dev/null || true; }
melden() { rclone copyto /root/welt.log "r2:$BUCKET/karte/welt/lauf/$S.log" -q 2>/dev/null || true; rclone copyto $ARBEIT/schritte.txt "r2:$BUCKET/karte/welt/lauf/$S.schritte.txt" -q 2>/dev/null || true; }
fehler() { echo "FEHLER in Zeile $1"; echo "FEHLER $(date -u +%FT%TZ) Zeile $1" >> $ARBEIT/schritte.txt; melden; sleep 7200; loeschen; }
trap 'fehler $LINENO' ERR
( while sleep 600; do melden; done ) & MELDER=$!

schritt "Start Modus $MODUS, Stand $S, $CPU Kerne, $RAM GB Speicher, $(df -h /data | tail -1 | awk '{print $4}') frei"

# ---------- 1. Werkzeuge ----------
schritt "Werkzeuge"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq osmium-tool rclone tippecanoe openjdk-21-jre-headless aria2 python3-pip pigz > /dev/null
pip install -q --break-system-packages osmium shapely pmtiles numpy pillow mapbox-vector-tile
if ! node -v 2>/dev/null | grep -q '^v22'; then curl -sSfL https://nodejs.org/dist/v22.11.0/node-v22.11.0-linux-x64.tar.xz | tar -xJ -C /usr/local --strip-components=1; fi
# rclone direkt vom Hersteller (die Ubuntu-Version bekommt von R2 „NotImplemented“)
curl -fsSL --retry 3 -o /tmp/rclone.zip https://downloads.rclone.org/rclone-current-linux-amd64.zip && apt-get install -y -qq unzip > /dev/null && unzip -q -o /tmp/rclone.zip -d /tmp/rclone && install -m 755 /tmp/rclone/rclone-*-linux-amd64/rclone /usr/local/bin/rclone && hash -r
node -v; java -version 2>&1 | head -1; tippecanoe -v 2>&1 | head -1; rclone version | head -1

# ---------- 2. Regionen und Weltdatei ----------
schritt "Regionen bestimmen"
if [ "$MODUS" = probe ]; then python3 $K/welt/regionen.py regionen.tsv --nur liechtenstein,andorra
else python3 $K/welt/regionen.py regionen.tsv --max-gb 1.2; fi
cat regionen.tsv | head -5; wc -l regionen.tsv

schritt "Zusatzdaten Grundkarte (Meer, Natural Earth, Seenamen)"
mkdir -p data/sources
hole() { f=$1; u=$2; for i in 1 2 3 4 5; do curl -sSfL --retry 3 -o "data/sources/$f" "$u" && return 0; sleep 30; done; rclone copyto "r2:$BUCKET/karte/quellen/$f" "data/sources/$f"; }
hole water-polygons-split-3857.zip https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip
hole natural_earth_vector.sqlite.zip https://naciscdn.org/naturalearth/packages/natural_earth_vector.sqlite.zip
hole lake_centerline.shp.zip https://github.com/acalcutt/osm-lakelines/releases/download/v12/lake_centerline.shp.zip
curl -sSfL -o planetiler.jar https://github.com/onthegomap/planetiler/releases/download/v$PLANETILER/planetiler.jar

if [ "$MODUS" = probe ]; then
  schritt "Probe: Grundkarte aus den Probe-Regionen"
  i=0; while IFS=$'\t' read -r n u b; do curl -sSfL -o "probe-$i.pbf" "$u"; i=$((i+1)); done < regionen.tsv
  osmium merge probe-*.pbf -o planet.osm.pbf --overwrite; rm -f probe-*.pbf
else
  schritt "Weltdatei OSM holen (ca. 85 GB)"
  aria2c -q -x 8 -s 16 -k 64M --file-allocation=none -d . -o planet.osm.pbf \
    https://ftp5.gwdg.de/pub/misc/openstreetmap/planet.openstreetmap.org/pbf/planet-latest.osm.pbf \
    https://ftp.fau.de/osm-planet/pbf/planet-latest.osm.pbf \
    https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf
fi
ls -la planet.osm.pbf

# ---------- 3. Grundkarte (Planetiler, wie Österreich) – gleichzeitig laden die Regionen ----------
schritt "Grundkarte Welt (Planetiler)"
mkdir -p r
( # Regionen schon laden, solange die Grundkarte rechnet (nur Netz, kaum Rechenzeit)
  mkdir -p pbf; while IFS=$'\t' read -r n u b; do echo "$n $u"; done < regionen.tsv | xargs -P 3 -n 2 sh -c '[ -s "pbf/$0.pbf" ] || curl -sSfL --retry 5 -o "pbf/$0.pbf.tmp" "$1" && mv "pbf/$0.pbf.tmp" "pbf/$0.pbf" || echo "Download $0 fehlgeschlagen"'
) > laden.log 2>&1 &
LADER=$!
XMX=$(( RAM * 55 / 100 )); STOR=mmap   # wie die Planetiler-Anleitung für die ganze Welt (Array + mmap)
java -Xmx${XMX}g -jar planetiler.jar --osm-path=planet.osm.pbf --output=grundkarte.pmtiles --force --maxzoom=14 \
  --nodemap-type=array --storage=$STOR --tmpdir=$ARBEIT/pt-tmp \
  --water_polygons_path=data/sources/water-polygons-split-3857.zip --natural_earth_path=data/sources/natural_earth_vector.sqlite.zip \
  --lake_centerlines_path=data/sources/lake_centerline.shp.zip > planetiler.log 2>&1 || { tail -60 planetiler.log; false; }
grep -E "Finished|archive|features" planetiler.log | tail -8
rm -rf pt-tmp planet.osm.pbf; ls -la grundkarte.pmtiles
rclone copyto grundkarte.pmtiles "r2:$BUCKET/$ZIEL/grundkarte.pmtiles" --s3-chunk-size 128M --s3-upload-concurrency 16 &
HOCH1=$!

# ---------- 4. Regionen rechnen (wie Österreich) ----------
schritt "Regionen rechnen ($(wc -l < regionen.tsv) Stück)"
wait $LADER || true
PR=$(( RAM / 12 )); [ $PR -gt $CPU ] && PR=$CPU; [ $PR -lt 1 ] && PR=1
echo "gleichzeitig: $PR"
# Adresse durch die schon geladene Datei ersetzen (region.sh lädt sonst selbst)
while IFS=$'\t' read -r n u b; do f="$ARBEIT/pbf/$n.pbf"; [ -s "$f" ] && u="file://$f"; echo "$n $u"; done < regionen.tsv \
  | xargs -P $PR -n 2 sh -c 'bash '"$K"'/welt/region.sh "$0" "$1" >> "'"$ARBEIT"'/r/$0.log" 2>&1 && rm -f "'"$ARBEIT"'/pbf/$0.pbf" && echo "ok $0" || echo "FEHLT $0"' | tee regionen.log
OK=$(grep -c '^ok ' regionen.log || true); FEHLT=$(grep -c '^FEHLT ' regionen.log || true)
echo "Regionen fertig: $OK, fehlgeschlagen: $FEHLT"
# zweiter Versuch für fehlgeschlagene (allein, mit mehr Speicher)
if [ "$FEHLT" -gt 0 ]; then
  grep '^FEHLT ' regionen.log | cut -d' ' -f2 | while read -r n; do u=$(awk -F'\t' -v n="$n" '$1==n{print $2}' regionen.tsv); f="$ARBEIT/pbf/$n.pbf"; [ -s "$f" ] && u="file://$f"
    NODE_GB=$(( RAM / 2 )) bash $K/welt/region.sh "$n" "$u" >> r/$n.log 2>&1 && echo "ok2 $n" || echo "FEHLT2 $n"; done | tee -a regionen.log
fi
rm -rf pbf

# ---------- 5. Wege der ganzen Welt ----------
schritt "Wege zusammenfügen und Vektorkacheln (tippecanoe)"
eins() { # doppelte Objekte (gleicher Schlüssel) entfernen
  find r -name "$1.tsv.gz" -print0 | xargs -0 cat | pigz -dc | LC_ALL=C sort -u -t$'\t' -k1,1 -S $(( RAM / 3 ))G --parallel=$CPU -T $TMPDIR | cut -f2- > "$1.geojsonl"
  echo "$1: $(wc -l < "$1.geojsonl") Objekte"; }
eins wege-niedrig; eins wege-hoch; eins lifte; eins geb
find r -name "*.tsv.gz" -delete
tippecanoe -o wege-niedrig.pmtiles -l w -Z10 -z12 -r1 --no-feature-limit --no-tile-size-limit --force -q -P wege-niedrig.geojsonl
tippecanoe -o wege-hoch.pmtiles -l w -Z11 -z14 -r1 --no-feature-limit --no-tile-size-limit --force -q -P wege-hoch.geojsonl
tippecanoe -o lifte.pmtiles -l l -Z10 -z14 -r1 --no-feature-limit --no-tile-size-limit --force -q -P lifte.geojsonl
tippecanoe -o geb.pmtiles -l g -Z13 -z14 -r1 --no-feature-limit --no-tile-size-limit --force -q -P geb.geojsonl
tile-join -o wege.pmtiles --no-tile-size-limit --force -q wege-niedrig.pmtiles wege-hoch.pmtiles lifte.pmtiles geb.pmtiles 2>&1 | grep -v mismatched || true
rm -f wege-*.geojsonl lifte.geojsonl geb.geojsonl wege-niedrig.pmtiles wege-hoch.pmtiles lifte.pmtiles geb.pmtiles
ls -la wege.pmtiles
rclone copyto wege.pmtiles "r2:$BUCKET/$ZIEL/wege.pmtiles" --s3-chunk-size 128M --s3-upload-concurrency 16 &
HOCH2=$!

# ---------- 6. Kartenstücke je Ebene in eine Datei ----------
schritt "Kartenstücke packen"
python3 $K/welt/zellen_pack.py r zp $(( CPU < 16 ? CPU : 16 ))
rclone copy zp "r2:$BUCKET/$ZIEL/" --transfers 16 --s3-chunk-size 64M &
HOCH3=$!

# ---------- 7. Kartenpaket Welt (viele Stücke gleichzeitig) ----------
schritt "Kartenpaket Welt"
(cd $K && npm i --no-save --silent pmtiles 2>/dev/null || true)
node -e '
let acc=0; const st=[]; for(let z=0;z<=15;z++){ st.push(acc); acc+=4**z; }
const teile={10:4,11:16,12:64,13:128,14:512}; const out=[];
out.push([0, st[10]]); // Zoom 0–9 in einem Stück
for (let z=10; z<=14; z++){ const n=teile[z], a=st[z], b=st[z+1], d=Math.ceil((b-a)/n); for(let i=0;i<n;i++){ const lo=a+i*d, hi=Math.min(b,a+(i+1)*d); if(lo<hi) out.push([lo,hi]); } }
console.log(out.map(([a,b],i)=>`${String(i).padStart(4,"0")} ${a} ${b}`).join("\n"));' > stuecke.txt
mkdir -p st
cat stuecke.txt | xargs -P $CPU -n 3 sh -c 'PAKET_MINZ=0 PAKET_VON=$1 PAKET_BIS=$2 node --max-old-space-size=4000 '"$K"'/paket.mjs '"$K"'/../index.html grundkarte.pmtiles wege.pmtiles st/$0.bin > st/$0.log 2>&1 || { echo "Stück $0 fehlgeschlagen"; tail -5 st/$0.log; exit 255; }'
ls st/*.bin | sort > st-liste.txt; wc -l st-liste.txt
PAKET_MINZ=0 node --max-old-space-size=$(( RAM * 1024 / 3 )) $K/welt/paket_zusammen.mjs paket.pmtiles grundkarte.pmtiles wege.pmtiles $(cat st-liste.txt)
ls -la paket.pmtiles

# ---------- 8. Prüfen und hochladen ----------
schritt "Prüfen"
python3 - <<'EOF' | tee pruefung.txt
from pmtiles.reader import Reader, MmapSource
import gzip, mapbox_vector_tile as mvt, os
probe = os.environ.get("MODUS") == "probe"
import math
def t(lon, lat, z, muss):
  n = 2 ** z; x = int((lon + 180) / 360 * n); y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n); return (z, x, y, muss)
# Probe: Liechtenstein (Malbun); voll: Brandnertal, Montafon, Innsbruck, Zermatt, Chamonix, Yosemite, Fuji, Patagonien, Meer
P = [t(9.61,47.10,14,['transportation','w']),t(9.55,47.15,12,['landcover','lcf_landcover']),t(9.55,47.15,10,['landcover'])] if probe else \
    [(14,8635,5753,['transportation','w']),(12,2159,1438,['landcover','lcf_landcover']),(10,539,359,['landcover','lcf_landcover']),(9,269,179,['water','wcf_water']),
     (13,4320,2878,['water','wnc_water_name']),t(7.75,46.02,14,['transportation','w']),t(6.87,45.92,14,['transportation','w']),
     t(-119.55,37.74,14,['transportation','w']),t(138.73,35.36,14,['transportation','w']),t(-73.0,-50.95,12,['landcover']),t(-30,40,5,['water'])]
ok = True
with open('paket.pmtiles','rb') as f:
  r = Reader(MmapSource(f))
  for z,x,y,muss in P:
    d = r.get(z,x,y)
    if not d: print(f"{z}/{x}/{y}: fehlt"); ok = False; continue
    t = mvt.decode(gzip.decompress(d)); fehlt = [m for m in muss if m not in t]
    print(f"{z}/{x}/{y}: Ebenen {', '.join(sorted(t))}" + (f" – FEHLT {fehlt}" if fehlt else ""))
    if fehlt: ok = False
print("PRUEFUNG", "OK" if ok else "FEHLER")
EOF
schritt "Hochladen"
rclone copyto paket.pmtiles "r2:$BUCKET/$ZIEL/paket.pmtiles" --s3-chunk-size 128M --s3-upload-concurrency 16
wait $HOCH1 $HOCH2 $HOCH3
{ echo "# Welt-Server $S (Modus $MODUS)"; echo; echo "- Ziel in R2: $ZIEL/"; echo "- Server: $CPU Kerne, $RAM GB"
  echo "- Regionen: $(wc -l < regionen.tsv), fertig $(grep -c '^ok' regionen.log || true), fehlgeschlagen: $(grep '^FEHLT2 ' regionen.log | cut -d' ' -f2 | tr '\n' ' ')"
  for f in grundkarte.pmtiles wege.pmtiles paket.pmtiles; do echo "- $f: $(( $(stat -c %s $f) / 1000000 )) MB"; done
  echo "- Kartenstücke: $(du -sh zp | cut -f1) in $(ls zp/*.pmtiles | wc -l) Dateien"
  echo; echo "## Schritte"; echo '```'; cat schritte.txt; echo '```'; echo; echo "## Prüfung"; echo '```'; cat pruefung.txt; echo '```'
  echo; echo "## Regionen (Sekunden)"; for d in r/*/; do n=$(basename $d); echo "$n $(cat $d/fertig 2>/dev/null || echo fehlt)"; done | sort -k2 -n -r | head -30; } > bericht.md
rclone copyto bericht.md "r2:$BUCKET/$ZIEL/bericht.md"

if [ "$MODUS" = voll ] && grep -q "PRUEFUNG OK" pruefung.txt; then
  schritt "In karte/welt/aktuell.json eintragen (Region welt hinter den bisherigen, laufende Karte unverändert)"
  rclone copyto "r2:$BUCKET/karte/welt/aktuell.json" welt-alt.json 2>/dev/null || echo '{}' > welt-alt.json
  python3 - <<EOF
import json
w = json.load(open('welt-alt.json')); p = json.load(open('paket.pmtiles.json')); z = json.load(open('zp/zellen-index.json'))
reg = [g for g in w.get('regionen', []) if g.get('name') != 'welt']
reg.append({"name": "welt", "paket": "$ZIEL/paket.pmtiles", "gebiet": [[[[-180,-85.06],[180,-85.06],[180,85.06],[-180,85.06],[-180,-85.06]]]],
            "bounds": [-180,-85.06,180,85.06], "minzoomEigen": 0, "huelle": p["huelle"], "kacheln": p["kacheln"], "bytes": p["bytes"],
            "zellen": "$ZIEL/", "cell": z["cell"], "pack": z["pack"], "osm": "planet $S"})
vl = w.get('vector_layers') or p['vector_layers']
json.dump({"stand": "$S", "erzeugt": "$S", "regionen": reg, "vector_layers": vl}, open('welt-neu.json', 'w'), ensure_ascii=False)
EOF
  rclone copyto welt-neu.json "r2:$BUCKET/karte/welt/aktuell.json"
fi
# ---------- 9. Feines Relief (wie Vorarlberg) in den Gebirgen mit freien Laserdaten, dazu Relief weit für die Welt ----------
if [ "${RELIEF:-ja}" = ja ]; then
  schritt "Platz schaffen für das Relief"
  find "$ARBEIT" -maxdepth 1 \( -name r -o -name zp -o -name st -o -name pbf -o -name tmp -o -name '*.pmtiles' \) -exec rm -r {} + ; mkdir -p "$ARBEIT/tmp"; df -h /data | tail -1
  pip install -q --break-system-packages rasterio
  RZIEL="karte/welt/$S/relief1m"; [ "$MODUS" = probe ] && RZIEL="karte/welt/$S-probe/relief1m"
  Z16="${RELIEF_Z16:-ueberall}"; [ "$Z16" = alpen ] && export RELIEF_Z16_GEBIET="4.5,43.5,16.8,48.6"
  NUR=""; [ "$MODUS" = probe ] && NUR="--nur 6.75,45.75,7.1,46.05"
  schritt "Feines Relief: Blöcke bestimmen (Zoom 16: $Z16)"
  rclone copyto "r2:$BUCKET/karte/relief1m/aktuell.json" r1m.json 2>/dev/null || echo '{}' > r1m.json
  python3 $K/welt/relief_bloecke.py bloecke.txt --spanne 500 --usa-spanne 800 --ohne r1m.json $NUR
  split -l 4000 -d -a 3 bloecke.txt bl-
  PRF=$(( RAM / 3 )); [ $PRF -gt $CPU ] && PRF=$CPU
  for f in bl-*; do i=${f#bl-}
    schritt "Feines Relief Teil $i ($(wc -l < $f) Blöcke, $PRF gleichzeitig)"
    RELIEF_MH=1 RELIEF_GROB=0 python3 $K/relief1m.py rf-$i "@$f" $PRF 0/1 > rf-$i.log 2>&1 || { tail -20 rf-$i.log; false; }
    tail -1 rf-$i.log
    python3 $K/relief.py --pmtiles rf-$i t$i.pmtiles
    rclone copyto t$i.pmtiles "r2:$BUCKET/$RZIEL/t$i.pmtiles" --s3-chunk-size 128M --s3-upload-concurrency 16
    cp rf-$i/bloecke.json t$i.json; rclone copyto t$i.json "r2:$BUCKET/$RZIEL/t$i.json"
    echo "Teil $i: $(python3 -c "import json;print(len(json.load(open('t$i.json'))))") Blöcke, $(du -h t$i.pmtiles | cut -f1)"
    find "$ARBEIT" -maxdepth 1 \( -name "rf-$i" -o -name "t$i.pmtiles" \) -exec rm -r {} +
  done
  python3 - <<EOF
import json, glob, re
teile = []; teil = {}
for f in sorted(glob.glob("t*.json"), key=lambda f: int(re.findall(r"t(\d+)", f)[0])):
    i = len(teile); teile.append("$RZIEL/" + f[:-5] + ".pmtiles")
    for b in json.load(open(f)): teil[b] = i
json.dump({"stand": "$S", "teile": teile, "teil": teil, "bloecke": sorted(teil), "quelle": "Mapterhorn (offene Laserscan-Modelle der Länder)"}, open("relief1m-welt.json", "w"), separators=(",", ":"))
print(len(teile), "Teile,", len(teil), "Blöcke mit feinem Relief")
EOF
  # nur ablegen – die laufende Karte (karte/relief1m/aktuell.json) bleibt, bis der Bildvergleich passt
  rclone copyto relief1m-welt.json "r2:$BUCKET/$RZIEL/aktuell.json"

  schritt "Relief weit Welt (Zoom 0–12)"
  python3 $K/welt/relief_weit_gebiet.py land.txt berge.txt --spanne 800 $NUR
  [ "$MODUS" = probe ] || python3 $K/relief.py rw 0,1,2,3,4,5 "-180,-84,180,84" --fein=2,0.9 > rw-0.log 2>&1
  for st in "6,7,8 land 2" "9,10 land 2" "11 land 1" "12 berge 1"; do set -- $st
    seq 0 $((CPU - 1)) | xargs -P $CPU -I{} sh -c "python3 $K/relief.py rw $1 @$2.txt:{}/$CPU --fein=$3,0.9 > rw-$1-{}.log 2>&1 || tail -5 rw-$1-{}.log"
    echo "Zoom $1 fertig: $(find rw -name '*.webp' | wc -l) Bilder bisher"
  done
  python3 $K/relief.py --pmtiles rw reliefw.pmtiles
  rclone copyto reliefw.pmtiles "r2:$BUCKET/$RZIEL/reliefw.pmtiles" --s3-chunk-size 128M --s3-upload-concurrency 16
  { echo; echo "## Relief"; echo "- feines Relief: $(wc -l < bloecke.txt) Blöcke geplant, $(python3 -c "import json;print(len(json.load(open('relief1m-welt.json'))['bloecke']))") gerechnet, Zoom 16: $Z16"
    echo "- Relief weit: $(du -h reliefw.pmtiles | cut -f1), Land $(wc -l < land.txt) / Gebirge $(wc -l < berge.txt) Kacheln Zoom 8"; } >> bericht.md
  rclone copyto bericht.md "r2:$BUCKET/$ZIEL/bericht.md"
fi
schritt "Fertig"
kill $MELDER 2>/dev/null || true; melden
trap - ERR
loeschen
