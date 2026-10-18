#!/bin/bash
# Gipfelbuch – eigener Routen-Server (BRouter) auf einem kleinen gemieteten Linux-Server (Ubuntu 24.04).
# NUR nach Marcels Okay buchen. Konto und Bezahlung beim Anbieter macht Marcel; danach dieses Skript einmal als root ausführen:
#   curl -sSL https://raw.githubusercontent.com/aktivvereinquerfeldein/gipfelbuch/main/karte/brouter/einrichten.sh | bash
# Ergebnis: https://<IP>.sslip.io/brouter?... (gleiche Schnittstelle wie brouter.de, Profile gb-hike/gb-bike/gb-road fest eingebaut)
set -euo pipefail
BR_VERSION="1.7.10"                      # BRouter-Version (GitHub abrensch/brouter)
KACHELN="E5_N45 E10_N45 E15_N45"         # 5°-Kacheln: Österreich + Südtirol (später: Deutschland E5_N50 E10_N50, Schweiz E5_N45, Norwegen E5_N55 … )
D=/opt/brouter
apt-get update -qq && apt-get install -y -qq openjdk-17-jre-headless unzip curl python3 caddy ufw > /dev/null
mkdir -p $D/segments4 $D/profiles2
cd $D
curl -sSfL -o br.zip "https://github.com/abrensch/brouter/releases/download/v${BR_VERSION}/brouter-${BR_VERSION}.zip"
unzip -oq br.zip && cp -r brouter-${BR_VERSION}/profiles2/* profiles2/ && cp brouter-${BR_VERSION}/brouter-${BR_VERSION}-all.jar brouter.jar
curl -sSfL -o $D/profile_holen.py https://raw.githubusercontent.com/aktivvereinquerfeldein/gipfelbuch/main/karte/brouter/profile_holen.py
python3 $D/profile_holen.py $D/profiles2
cat > $D/segmente.sh <<EOS
#!/bin/bash
# Straßen-/Wegdaten (wöchentlich neu von brouter.de, dort aus OSM berechnet) und Profile aktualisieren
cd $D/segments4
for k in $KACHELN; do curl -sSfL -z \$k.rd5 -o \$k.rd5.neu "https://brouter.de/brouter/segments4/\$k.rd5" && [ -s \$k.rd5.neu ] && mv \$k.rd5.neu \$k.rd5 || rm -f \$k.rd5.neu; done
python3 $D/profile_holen.py $D/profiles2 || true
systemctl restart brouter
EOS
chmod +x $D/segmente.sh && $D/segmente.sh || true
cat > /etc/systemd/system/brouter.service <<EOS
[Unit]
Description=BRouter fuer Gipfelbuch
After=network.target
[Service]
WorkingDirectory=$D
ExecStart=/usr/bin/java -Xmx1500M -Xms512M -Xmn256M -DmaxRunningTime=60 -DuseRFCMimeType=false -cp $D/brouter.jar btools.server.RouteServer $D/segments4 $D/profiles2 $D/customprofiles 17777 4 127.0.0.1
Restart=always
[Install]
WantedBy=multi-user.target
EOS
mkdir -p $D/customprofiles
systemctl daemon-reload && systemctl enable --now brouter
IP=$(curl -s https://api.ipify.org)
cat > /etc/caddy/Caddyfile <<EOS
${IP//./-}.sslip.io {
  header Access-Control-Allow-Origin "*"
  reverse_proxy /brouter* 127.0.0.1:17777
  respond /status "ok" 200
}
EOS
systemctl restart caddy
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
( crontab -l 2>/dev/null; echo "23 4 * * 2 $D/segmente.sh > /var/log/brouter-segmente.log 2>&1" ) | crontab -
echo "Fertig: https://${IP//./-}.sslip.io/brouter  (Test: /brouter?lonlats=9.75,47.15|9.80,47.12&profile=gb-hike&format=geojson)"
