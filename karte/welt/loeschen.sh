#!/bin/sh
# Welt-Server: Zusatzplatte (Hetzner-Volume) und diesen Server löschen. Aufgerufen am Ende von welt.sh, bei Fehler nach 2 Stunden
# und vom Wachhund (systemd, nach max_stunden). Übrig gebliebenes räumt der Ablauf „Welt Aufräumen“ weg.
. /root/welt.env
A="Authorization: Bearer $HCLOUD_TOKEN"; API=https://api.hetzner.cloud/v1
if [ -n "$VOLUME_ID" ]; then
  sync; umount -l "/mnt/HC_Volume_$VOLUME_ID" 2>/dev/null
  curl -s -X POST -H "$A" "$API/volumes/$VOLUME_ID/actions/detach" > /dev/null
  for i in $(seq 1 30); do
    curl -s -H "$A" "$API/volumes/$VOLUME_ID" | grep -q '"server": *null' && break; sleep 4
  done
  curl -s -X DELETE -H "$A" "$API/volumes/$VOLUME_ID" > /dev/null
fi
curl -s -X DELETE -H "$A" "$API/servers/$SERVER_ID" > /dev/null
