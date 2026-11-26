#!/bin/bash
# HF310 boot-time Wi-Fi reconnect: try saved station profiles before captive portal.
set -u
connected() { nmcli -t -f GENERAL.STATE device show wlan0 2>/dev/null | grep -q '100 (connected)'; }
for attempt in $(seq 1 12); do
  connected && exit 0
  names=$(nmcli -t -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY connection show 2>/dev/null \
    | awk -F: '$2=="802-11-wireless" && $3=="yes" && $1!="hotspot" {print $4"|"$1}' \
    | sort -rn | cut -d'|' -f2-)
  for name in $names; do
    connected && exit 0
    nmcli connection up "$name" >/dev/null 2>&1
  done
  sleep 8
done
exit 0
