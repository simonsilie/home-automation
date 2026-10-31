#!/bin/sh
# Deploy the mqtt-grid-meter bridge to a Cerbo GX and verify it is live.
# Usage: tools/deploy-venus.sh root@<cerbo-ip>
set -eu

CERBO=${1:?usage: tools/deploy-venus.sh root@<cerbo-ip>}
SERVICE=com.victronenergy.grid.mqtt_ir_grid_meter
cd "$(dirname "$0")/.."

# Fail before touching the device if the bridge does not build.
python3 -B -m unittest discover -s venus-os/mqtt-grid-meter -p 'test_*.py' >/dev/null
echo "tests ok"

# Build the archive before stopping the meter: if mktemp or tar fails here, no
# device-side downtime has happened yet.
archive=$(mktemp)
trap 'rm -f "$archive"' EXIT

# Exclude the runtime config: an untracked local config.ini must never replace
# the Cerbo's credentials. The deployed config only exists on the device.
tar --no-xattrs --exclude __pycache__ --exclude ./config.ini \
    -C venus-os/mqtt-grid-meter -cf "$archive" .

# Stop the supervised service, then kill whatever survives: anything still
# running once the supervisor is held down is an orphan holding the D-Bus name.
ssh "$CERBO" '
    svc -d /service/mqtt-grid-meter 2>/dev/null || true
    sleep 2
    for pid in $(ps w | grep "[m]qtt_grid_meter.py" | awk "{print \$1}"); do
        kill $pid
    done
'

# A half-written or failed extraction must abort the remote block instead of
# restarting and verifying old code; hence set -e.
ssh "$CERBO" '
    set -e
    cd /data/etc/mqtt-grid-meter
    tar -xf -
    find . -name "._*" -delete
    chown -R root:root .
    chmod +x install.sh boot.sh service/run service/log/run
    svc -u /service/mqtt-grid-meter
' < "$archive"
echo "deployed"

# If the pid changes between two svstat reads the bridge is crash-looping.
pid1=$(ssh "$CERBO" 'svstat /service/mqtt-grid-meter' | sed -n 's/.*(\(pid [0-9]*\)).*/\1/p')
sleep 5
status2=$(ssh "$CERBO" 'svstat /service/mqtt-grid-meter')
echo "$status2"
pid2=$(echo "$status2" | sed -n 's/.*(\(pid [0-9]*\)).*/\1/p')
if [ "$pid1" != "$pid2" ]; then
    echo "service pid changed between checks - crash-looping; see /data/log/mqtt-grid-meter/current"
    exit 1
fi

# More than one supervisor for the service is the inode-swap debris that fights
# over the D-Bus name; only a reboot cleans this up reliably.
sup_count=$(ssh "$CERBO" 'ps w | grep "[s]upervise mqtt-grid-meter" | wc -l')
if [ "$sup_count" != "1" ]; then
    echo "found $sup_count supervise processes watching the service; supervisor debris - reboot the Cerbo"
    exit 1
fi

# /Connected must reach 1 before ESS will use this meter — but only after the
# first fresh (non-retained) publish following the restart, so poll for up to
# ~30s instead of sampling once seconds after svc -u. The version probe uses
# the same connection.
snippet="
import dbus
bus = dbus.SystemBus()
meter = bus.get_object(\"$SERVICE\", \"/Mgmt/ProcessVersion\")
print(\"version=%s\" % meter.GetValue())
"
out=$(printf '%s' "$snippet" | ssh "$CERBO" '
    DBUS_SYSTEM_BUS_ADDRESS=unix:path=/var/run/dbus/system_bus_socket python3 -')
echo "$out"
local_version=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' venus-os/mqtt-grid-meter/mqtt_grid_meter.py)
case $out in
    *"version=$local_version"*) ;;
    *) echo "deployed version does not match the repository (want $local_version): $out"; exit 1 ;;
esac

connected=""
for i in 1 2 3 4 5 6; do
    connected=$(printf '%s' "
import dbus
bus = dbus.SystemBus()
meter = bus.get_object(\"$SERVICE\", \"/Connected\")
print(meter.GetValue())
" | ssh "$CERBO" '
    DBUS_SYSTEM_BUS_ADDRESS=unix:path=/var/run/dbus/system_bus_socket python3 -' 2>/dev/null)
    if [ "$connected" = "1" ] || [ "$connected" = "1.0" ]; then
        connected=1
        break
    fi
    sleep 5
done
echo "connected=$connected"
if [ "$connected" != "1" ]; then
    echo "meter is not connected (stale data or MQTT problem); check the service log"
    exit 1
fi
echo "deploy verified"
