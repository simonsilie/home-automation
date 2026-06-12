#!/bin/sh
set -eu

DEST=/data/etc/mqtt-grid-meter
SERVICE=/service/mqtt-grid-meter
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

mkdir -p "$DEST"
mkdir -p /data/log/mqtt-grid-meter

for file in mqtt_grid_meter.py simple_mqtt.py config.example.ini install.sh boot.sh README.md; do
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
chmod +x "$DEST/install.sh" "$DEST/boot.sh" "$DEST/service/run" "$DEST/service/log/run"

if [ ! -f "$DEST/config.ini" ]; then
  cp "$DEST/config.example.ini" "$DEST/config.ini"
  echo "Created $DEST/config.ini. Edit MQTT settings before relying on the service."
fi

if [ ! -f /data/rc.local ]; then
  cat > /data/rc.local <<'EOF'
#!/bin/sh
EOF
fi

if ! grep -Fqx "$DEST/boot.sh" /data/rc.local; then
  cat >> /data/rc.local <<EOF

# Start mqtt-grid-meter custom Venus OS service.
$DEST/boot.sh
EOF
fi
chmod +x /data/rc.local

"$DEST/boot.sh"
svc -t "$SERVICE" 2>/dev/null || true

echo "Installed mqtt-grid-meter. Log: tail -F /data/log/mqtt-grid-meter/current | tai64nlocal"