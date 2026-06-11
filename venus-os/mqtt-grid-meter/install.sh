#!/bin/sh
set -eu

DEST=/data/etc/mqtt-grid-meter
SERVICE=/service/mqtt-grid-meter
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

mkdir -p "$DEST"
mkdir -p /data/log/mqtt-grid-meter

for file in mqtt_grid_meter.py simple_mqtt.py config.example.ini install.sh README.md; do
  if [ "$SOURCE/$file" != "$DEST/$file" ]; then
    cp "$SOURCE/$file" "$DEST/$file"
  fi
done

mkdir -p "$DEST/service"
if [ "$SOURCE/service/run" != "$DEST/service/run" ]; then
  cp "$SOURCE/service/run" "$DEST/service/run"
fi
mkdir -p "$DEST/service/log"
if [ "$SOURCE/service/log/run" != "$DEST/service/log/run" ]; then
  cp "$SOURCE/service/log/run" "$DEST/service/log/run"
fi
chmod +x "$DEST/install.sh" "$DEST/service/run" "$DEST/service/log/run"

if [ ! -f "$DEST/config.ini" ]; then
  cp "$DEST/config.example.ini" "$DEST/config.ini"
  echo "Created $DEST/config.ini. Edit MQTT settings before relying on the service."
fi

if [ -L "$SERVICE" ] || [ -e "$SERVICE" ]; then
  rm -f "$SERVICE"
fi

ln -s "$DEST/service" "$SERVICE"
svc -t "$SERVICE" 2>/dev/null || svc -u "$SERVICE" || true

echo "Installed mqtt-grid-meter. Log: tail -F /data/log/mqtt-grid-meter/current | tai64nlocal"