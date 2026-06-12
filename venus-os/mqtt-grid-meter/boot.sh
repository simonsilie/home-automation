#!/bin/sh
set -eu

SERVICE=/service/mqtt-grid-meter
TARGET=/data/etc/mqtt-grid-meter/service

if [ ! -d "$TARGET" ]; then
  echo "mqtt-grid-meter target service directory missing: $TARGET"
  exit 1
fi

if [ ! -L "$SERVICE" ] || [ "$(readlink "$SERVICE" 2>/dev/null || true)" != "$TARGET" ]; then
  rm -rf "$SERVICE"
  ln -s "$TARGET" "$SERVICE"
fi

svc -u "$SERVICE" 2>/dev/null || true