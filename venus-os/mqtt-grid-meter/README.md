# Venus OS MQTT Grid Meter

Small Venus OS service that subscribes to ESPHome MQTT topics from the IR meter and exposes them as a Victron grid meter on D-Bus.

It is intended for this data flow:

```text
electric_meter_ir ESPHome -> Home Assistant Mosquitto -> Cerbo GX service -> Venus OS grid meter
```

No Node-RED and no Venus OS Large image are required. The Python bridge uses
only the standard library for MQTT and TLS and targets the Python 3.12 shipped with
Venus OS v3.81 (verified: Python 3.12.14 on a Cerbo GX).

## MQTT Transport and Broker Security

This bridge can use verified TLS for **Cerbo GX -> broker** connections. It does
not configure Mosquitto or change the ESPHome meter's transport. This is a
capability and deployment recommendation, not a claim that the deployed broker
or the ESP8266 connection is secure.

In `[mqtt]`:

- `tls = true` enables TLS, with a default port of `8883` if `port` is omitted.
  Use a broker DNS hostname matching its certificate's subjectAltName (as in the
  example config); an IP address works only if the certificate has a matching IP
  subjectAltName. The hostname is passed as `server_hostname` for verification
  and SNI. The verified handshake completes before MQTT credentials are sent.
- `ca_file` is an optional path to a PEM CA file **on the Cerbo GX**. Leave it
  empty or omit it to use the system CA trust store. For a private broker CA,
  provision the trusted CA file separately and set, for example,
  `ca_file = /data/etc/mqtt-grid-meter/broker-ca.pem`.
- TLS requires version 1.2 or newer, certificate validation, and hostname
  verification. There is no insecure certificate bypass. An unreadable/invalid
  CA, an untrusted/expired certificate, or a hostname/handshake failure fails the
  connection; there is no automatic fallback to plaintext. Failed TLS wrapping
  closes the raw socket. Ensure the Cerbo's clock is correct for certificate
  validity checks.
- `tls` defaults to `false` for compatibility with existing installations, and
  its default port remains `1883`. An explicit `port` is always preserved;
  changing a port alone does not enable TLS. New installations should use the
  TLS-enabled example after preparing a TLS listener.

For legacy hardware, explicitly select plaintext only on an isolated, trusted
LAN:

```ini
[mqtt]
host = HOME_ASSISTANT_LAN_IP
tls = false
port = 1883
```

This opt-in is **not encrypted or authenticated at the transport layer**:
MQTT credentials and meter readings can be observed or modified by an attacker
with access to that network. MQTT usernames/passwords and topic ACLs do not
remove this risk. Existing configs without `tls` keep this legacy behavior;
prefer making the choice explicit and migrating the bridge to TLS when possible.
Do not expose either MQTT listener to the internet or forward its ports.

### Trusted LAN and Mosquitto Recommendations

Configure and verify broker listeners separately; copying `config.example.ini`
does not create a TLS listener. Prefer a LAN-bound TLS listener on `8883` with a
server certificate/private key, the required certificate chain, anonymous access
disabled, and authenticated accounts with topic ACLs. Restrict listener access
with firewall/VLAN rules to the required devices. Keep broker private keys and
account passwords out of this repository; restrict the runtime config's file
permissions (for example, owner-only `chmod 600 config.ini`).

Provision a **dedicated read-only bridge account**, not an administrator or a
shared Home Assistant/meter account. A Mosquitto ACL entry for the example
username and topic prefix is:

```text
user venus-grid-meter
topic read electric-meter-ir/#
```

Install this entry in the broker's configured ACL file and ensure no other
matching rules grant this account write access or broader reads. If you change
`topic_prefix`, update the ACL accordingly; restricting reads to the individual
mapped sensor topics is also possible. The bridge subscribes and does not need
application-topic publish permission. Verify that reads of the required topics
succeed and publishing or reading unrelated topics is denied.

Recommend separate **per-device accounts** for the meter, bridge, and other
consumers, with unique credentials and only the required topic permissions.
Give the meter only the publish/subscribe permissions its actual firmware needs,
not access to all broker topics. Rotate credentials independently.

Review the ESP8266's actual firmware, ESPHome version/framework, TLS support,
certificate validation, and resource limits before changing its transport; do
**not assume ESPHome MQTT TLS is supported** on that build. If the meter cannot
use verified TLS, a separately restricted LAN-only Mosquitto plaintext listener
on `1883` can serve it while the Python bridge uses TLS on `8883`. That leaves the
meter-to-broker hop exposed to the plaintext risks above. Review Mosquitto's
`per_listener_settings` behavior and authentication/ACL coverage for **every**
listener; do not accidentally leave a legacy listener anonymous or broadly
reachable. Listener setup and hardware validation remain operator work, outside
this Python bridge change.

## Stale-Data Safety

The D-Bus meter is marked connected only after a fresh, non-retained
`active_power` MQTT message arrives. Broker connectivity alone does not mark
the meter as valid. Retained MQTT sensor values are ignored, and if total power
is not updated within `stale_seconds`, `/Connected` is set to `0` and all
instantaneous AC values are cleared. The default timeout is 20 seconds.

Phase power, voltage, current, and frequency also expire independently after
`stale_seconds`, even while total power keeps arriving. Energy counters are
preserved. Freshness is measured from MQTT receipt using a monotonic clock, so
system clock corrections and delayed main-loop callbacks cannot refresh old samples.

This prevents ESS from continuing to regulate against an old export value after
the ESPHome meter or MQTT connection fails.

An idle socket timeout leaves the connection open. A timeout midway through an
MQTT packet closes the connection and triggers reconnection rather than parsing
the remaining bytes as a new packet.

## Install On Cerbo GX

1. Enable SSH/root access on the Cerbo GX.
2. Copy this directory to the Cerbo, for example:

   ```sh
   scp -r venus-os/mqtt-grid-meter root@CERBO_IP:/data/etc/mqtt-grid-meter
   ```

3. Log in to the Cerbo:

   ```sh
   ssh root@CERBO_IP
   ```

4. Create the runtime config:

   ```sh
   cd /data/etc/mqtt-grid-meter
   cp config.example.ini config.ini
   vi config.ini
   ```

5. Set the broker's certificate DNS hostname, TLS listener, and a separately
   provisioned read-only bridge account in `[mqtt]`. Replace `CHANGE_ME` locally:

   ```ini
   host = mqtt.example.com
   tls = true
   port = 8883
   username = venus-grid-meter
   password = CHANGE_ME
   ```

   Set `ca_file` if the broker uses a private CA, then restrict config permissions:

   ```sh
   chmod 600 config.ini
   ```

   Prepare and verify the broker listener first; if deliberately retaining a
   legacy LAN-only plaintext connection, use `tls = false` and `port = 1883` as
   described above rather than copying the TLS settings blindly.

6. Install and start the service:

   ```sh
   chmod +x install.sh service/run
   ./install.sh
   ```

   The installer also adds `/data/etc/mqtt-grid-meter/boot.sh` to `/data/rc.local`.
   This is needed because Venus OS rebuilds `/service` during boot, so direct service
   links below `/service` do not survive a reboot.

7. Check the service log:

   ```sh
   tail -F /data/log/mqtt-grid-meter/current | tai64nlocal
   ```

The Cerbo device list should then show a grid meter named `MQTT IR Grid Meter`.

When upgrading an existing installation, copy both Python files and keep the
Cerbo's existing `config.ini` (do not overwrite it with the new TLS example).
Configs without `tls` remain plaintext on their configured port, or `1883` by
default; explicitly add `tls = false` if retaining that legacy LAN-only mode.
Migrate to `tls = true` only after checking the broker TLS listener, certificate
hostname, and CA trust. Set `stale_seconds = 20` and restart the service:

```sh
svc -t /service/mqtt-grid-meter
```

## ESPHome Topics

The default config expects the MQTT topic prefix from [../../esphome/electric_meter_ir.yaml](../../esphome/electric_meter_ir.yaml):

```text
electric-meter-ir
```

It maps these topics:

```text
electric-meter-ir/sensor/active_power/state
electric-meter-ir/sensor/active_power_l1/state
electric-meter-ir/sensor/active_power_l2/state
electric-meter-ir/sensor/active_power_l3/state
electric-meter-ir/sensor/total_energy_import/state
electric-meter-ir/sensor/total_energy_export/state
electric-meter-ir/sensor/voltage_l1/state
electric-meter-ir/sensor/voltage_l2/state
electric-meter-ir/sensor/voltage_l3/state
electric-meter-ir/sensor/current_l1/state
electric-meter-ir/sensor/current_l2/state
electric-meter-ir/sensor/current_l3/state
electric-meter-ir/sensor/grid_frequency/state
```

These mappings are the built-in defaults. They can be overridden per topic with
`[mapping.*]` sections in `config.ini` (each needs `topic`, `dbus_path`, and the
optional `converter` — `float` or `power`, where `power` applies
`power_multiplier`); any mapping section then replaces the whole default list.
See the comments and `DEFAULT_MAPPINGS` in `mqtt_grid_meter.py`.

## Power Sign

Victron grid meters normally use positive power for grid import and negative power for export. If the Cerbo shows the sign reversed, set this in `config.ini`:

```ini
power_multiplier = -1
```

## Service Control

```sh
svc -u /service/mqtt-grid-meter  # start
svc -d /service/mqtt-grid-meter  # stop
svc -t /service/mqtt-grid-meter  # restart
```

After a reboot, verify that the boot hook recreated the service link:

```sh
ls -l /service/mqtt-grid-meter
dbus -y com.victronenergy.grid.mqtt_ir_grid_meter /Connected GetValue
```

## Development Checks

From the repository root, use the pinned development environment:

```sh
uv sync --locked --python 3.12
uv run --locked ruff check venus-os/mqtt-grid-meter
uv run --locked pyrefly check
uv run --locked pytest
```

The bridge alone can also be tested without development dependencies:

```sh
python3 -B -m unittest discover -s venus-os/mqtt-grid-meter -p 'test_*.py' -v
```

The tests mock socket connections and TLS wrapping; they cover handshake-before-
credentials ordering, failure cleanup, verification requirements, TLS minimum,
config defaults/explicit ports, CA selection/errors, and the existing framing
and stale-data behavior. They do not contact a broker or prove any deployed
listener, ACL, certificate chain, or ESP8266 transport is secure. The real
standard-library context test checks verification defaults without networking.
Ruff and pyrefly target Python 3.12 via the repository configuration, matching the
Cerbo GX firmware; run the unittest command with a Python 3.12 interpreter when
validating that runtime. Ruff is development tooling, not a bridge runtime dependency.
