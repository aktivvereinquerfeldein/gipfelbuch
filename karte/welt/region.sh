#!/usr/bin/env bash
# Welt in Regionen: EINE Region genau wie Österreich rechnen (gleiche Skripte wie „Karte aktualisieren“):
#   Kartenstücke (zellen.py) -> Wege-Vektorkacheln-Rohdaten (wegekarte.mjs, wegenden.py) -> Lifte -> Gebäudenamen -> Zellen verschlanken.
# Ergebnis in $ARBEIT/r/<name>/: zellen/, wege-niedrig.tsv.gz, wege-hoch.tsv.gz, lifte.tsv.gz, geb.tsv.gz
# (eine Zeile je Objekt: Schlüssel<TAB>GeoJSON; der Schlüssel entfernt beim Zusammenfügen doppelte Objekte an den Grenzen).
# Aufruf: region.sh <name> <pbf-adresse>
set -euo pipefail
N=$1; U=$2; K=$(cd "$(dirname "$0")/.." && pwd); D="$ARBEIT/r/$N"
[ -f "$D/fertig" ] && { echo "$N schon fertig"; exit 0; }
rm -rf "$D"; mkdir -p "$D"; cd "$D"
t0=$(date +%s)
for v in 1 2 3 4 5; do curl -sSfL --retry 3 -o r.pbf "$U" && break; echo "$N: Download Versuch $v fehlgeschlagen"; sleep $((v*30)); done
[ -s r.pbf ] || { echo "$N: keine Daten"; exit 1; }
python3 "$K/zellen.py" r.pbf zellen --version "$S" > zellen.log 2>&1 || { tail -20 zellen.log; exit 1; }
osmium tags-filter r.pbf w/highway -o hw.pbf --overwrite
python3 "$K/wegenden.py" hw.pbf wegenden.txt > /dev/null && rm -f hw.pbf
NODE_OPTIONS=--max-old-space-size=${NODE_GB:-16}000 node "$K/wegekarte.mjs" "$K/../index.html" zellen wege wegenden.txt > wege.log 2>&1 || { tail -20 wege.log; exit 1; }
rm -f wegenden.txt
# Lifte (Ebene l) und Gebäudenamen (Ebene g) – gleiche Auswahl wie in karte-aktualisieren.yml, mit OSM-Nummer für das Entdoppeln
osmium tags-filter r.pbf w/aerialway -o lifte.pbf --overwrite
osmium export lifte.pbf -f geojsonseq -o lifte.geojsonl --overwrite --geometry-types=linestring -a type,id
osmium tags-filter r.pbf w/building -o geb1.pbf --overwrite
osmium tags-filter geb1.pbf w/name -o geb2.pbf --overwrite
osmium export geb2.pbf -f geojsonseq -o geb.geojsonl --overwrite --geometry-types=polygon -a type,id
rm -f r.pbf lifte.pbf geb1.pbf geb2.pbf
python3 "$K/welt/schluessel.py" "$D"
python3 "$K/zellen_schlank.py" zellen > /dev/null
rm -f wege-niedrig.geojsonl wege-hoch.geojsonl lifte.geojsonl geb.geojsonl
echo "$(( $(date +%s) - t0 ))" > fertig
echo "$N fertig in $(( $(date +%s) - t0 )) s, $(du -sh . | cut -f1)"
