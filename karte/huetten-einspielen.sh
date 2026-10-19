#!/usr/bin/env bash
# Hütten-Öffnungszeiten (gb_huetten) in die Hütten-Zellen der laufenden Kartenversion packen und freischalten.
# Benutzt von .github/workflows/huetten-einspielen.yml und am Ende von karte-aktualisieren.yml.
set -euo pipefail
K="$(cd "$(dirname "$0")" && pwd)"
mkdir -p gbw && cd gbw
rclone copyto "r2:$BUCKET/karte/aktuell.json" aktuell.json
V=$(python3 -c "import json;print(json.load(open('aktuell.json'))['version'])")
B=$(python3 -c "import json;print(json.load(open('aktuell.json'))['basis'])")
S=$(date -u +%Y-%m-%d-%H%M)
echo "Kartenversion $V, Hütten-Stand $S"
python3 "$K/huetten.py" daten daten.json
rclone copy "r2:$BUCKET/${B}zellen/hut" zin/hut --transfers 16
N=$(python3 "$K/huetten.py" anreichern zin zout daten.json | tee /dev/stderr | sed -n 's/.*geschrieben, \([0-9]*\) Hütten.*/\1/p')
[ "${N:-0}" -gt 0 ] || { echo "::error::keine Hütte mit Öffnungsdaten – nichts freigeschaltet"; exit 1; }
# Parkplatz-Gebühren (gb_parkplaetze) in die Parkplatz-Zellen derselben Kartenversion
E=hut; NP=0
python3 "$K/parkplaetze.py" daten park.json
python3 "$K/parkplaetze.py" zellen park.json > park-zellen.txt
if [ -s park-zellen.txt ]; then
  rclone copy "r2:$BUCKET/${B}zellen" zin --files-from park-zellen.txt --transfers 16 || true
  NP=$(python3 "$K/parkplaetze.py" anreichern zin zout park.json | tee /dev/stderr | sed -n 's/.*geschrieben, \([0-9]*\) Parkplatz.*/\1/p')
  [ "${NP:-0}" -gt 0 ] && E=hut,pka
fi
P="karte/gb/$S/"
rclone copy zout "r2:$BUCKET/$P" --transfers 16
# kurz vor dem Umschalten nochmal lesen (falls inzwischen eine neue Kartenversion freigeschaltet wurde)
rclone copyto "r2:$BUCKET/karte/aktuell.json" aktuell2.json
python3 "$K/huetten.py" gb-feld aktuell2.json neu.json --pfad "$P" --stand "$S" --version "$V" --anzahl "$N" --ebenen "$E" --park "${NP:-0}"
rclone copyto neu.json "r2:$BUCKET/karte/aktuell.json"
echo "Zusatzdaten $S freigeschaltet ($N Hütten/Alpen, ${NP:-0} Parkplätze)" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
# Test über den Worker (merkt sich aktuell.json bis 5 Minuten); bei Fehler alten Stand zurück
sleep 330
ok=1
curl -sf "$WORKER/karte/status" | grep -q "\"gb\":\"$S\"" || ok=0
curl -sf "$WORKER/karte/zellen/hut/24_117.json" | python3 -c "import json,sys;j=json.load(sys.stdin);assert sum(1 for e in j['elements'] if 'gb' in e.get('tags',{}))>5" || ok=0
if [ "${NP:-0}" -gt 0 ]; then   # Parkplatz-Gebühren: eine angereicherte Parkplatz-Zelle muss "gb" enthalten
  PZ=$(ls zout/pka | head -1 | sed 's/.json.gz//')
  curl -sf "$WORKER/karte/zellen/pka/$PZ.json?gb=$S" | python3 -c "import json,sys;j=json.load(sys.stdin);n=sum(1 for e in j['elements'] if 'gb' in e.get('tags',{}));print('Parkplatz-Zelle $PZ:',n,'mit Gebühren');assert n>0" || ok=0
fi
if [ $ok -ne 1 ]; then
  echo "::error::Test fehlgeschlagen – vorheriger Stand wird wieder eingesetzt"
  rclone copyto aktuell2.json "r2:$BUCKET/karte/aktuell.json"; exit 1
fi
echo "Test ok"
