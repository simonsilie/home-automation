# Venus OS / Cerbo GX

This directory contains services and notes for the Cerbo GX.

## Hardware context

The live ESS on this GX is:

- **Victron MultiPlus-II 3000** (inverter/charger)
- **Pylontech US5000** (battery + BMS on the GX)
- IR grid meter and OpenDTU/Hoymiles as external integrations (see below)

Charge, discharge, and ESS setpoints are owned by Venus OS. The retired RD6030W surplus
charger and Soyosource GTN limiter are no longer part of this system.

## Services

| Path | Purpose |
| --- | --- |
| [mqtt-grid-meter/](mqtt-grid-meter/) | Expose the ESPHome IR meter as a Victron grid meter on D-Bus via MQTT |
| [dbus-opendtu-config.example](dbus-opendtu-config.example) | Example configuration for `henne49/dbus-opendtu` to expose OpenDTU/Hoymiles as a PV inverter in Venus OS |
| [dbus-apsystems-ez1-config.example](dbus-apsystems-ez1-config.example) | Example configuration for `domizei385/dbus-apsystems-ez1` to expose APsystems EZ1-M as a PV inverter in Venus OS |

## OpenDTU as PV Inverter

For OpenDTU, [henne49/dbus-opendtu](https://github.com/henne49/dbus-opendtu) is the right fit. The service runs directly on the Cerbo GX, polls OpenDTU through the REST API, and exposes the Hoymiles inverter as a Victron D-Bus service, usually as `com.victronenergy.pvinverter`.

This is a better model than adding another grid meter: the IR meter remains the grid measurement point, while OpenDTU is shown as AC PV generation.

### Data Flow

```text
OpenDTU -> REST API -> Cerbo GX dbus-opendtu -> Venus OS / VRM PV-Inverter
```

### Pre-Check

From the Cerbo or another machine on the same network:

```sh
curl http://OPENDTU_IP/api/livedata/status
```

If OpenDTU authentication is enabled, either set the username/password in `config.ini` or allow unauthenticated access to the status endpoint in OpenDTU.

### Install on the Cerbo

The upstream documentation often uses `/data/dbus-opendtu`. Installing under `/data/etc/dbus-opendtu` is also fine and keeps it next to the local [mqtt-grid-meter/](mqtt-grid-meter/) service. The `dbus-opendtu` installer uses its own directory as the base path, so the important part is that the final directory is named `dbus-opendtu`.

```sh
ssh root@CERBO_IP
mkdir -p /data/etc
cd /data/etc
wget -O main.zip https://github.com/henne49/dbus-opendtu/archive/refs/tags/$(curl -s https://api.github.com/repos/henne49/dbus-opendtu/releases/latest | grep "tag_name" | cut -d '"' -f 4).zip
mkdir dbus-opendtu-tmp
unzip main.zip -d dbus-opendtu-tmp
mv dbus-opendtu-tmp/* dbus-opendtu
rm -r dbus-opendtu-tmp main.zip
cd /data/etc/dbus-opendtu
cp config.example config.ini
vi config.ini
```

The most important values are shown in [dbus-opendtu-config.example](dbus-opendtu-config.example). Then install and start the service:

```sh
chmod a+x /data/etc/dbus-opendtu/*.sh
/data/etc/dbus-opendtu/install.sh
/data/etc/dbus-opendtu/restart.sh
```

### Important Settings

- `DTU=opendtu`
- `Host=OPENDTU_IP`
- `NumberOfTemplates=0` to avoid creating extra template devices such as generator blocks from the upstream example config.
- `NumberOfInvertersToQuery=2` for the two OpenDTU microinverters, HM800 and HM600.
- `DeviceInstance` must be unique. The local MQTT grid meter already uses `40`, so use something like `50`, `51`, ... for OpenDTU.
- `Servicename=com.victronenergy.pvinverter`
- `Phase=L1`, `L2`, `L3`, or `3P`. For a single-phase Hoymiles inverter, set the actual phase. For three-phase HMT devices, use `3P`.
- `AcPosition=0` if the inverter is connected on the grid/AC-input side of the Victron system; `AcPosition=1` if it is connected on the AC-out/backup-load side.

The local example config defines the HM800 as `[INVERTER0]` with `DeviceInstance=50` and the HM600 as `[INVERTER1]` with `DeviceInstance=51`. If both microinverters are on the same house phase, both sections can use the same `Phase`, for example `L1`.

### Polling

For OpenDTU, `dbus-opendtu` currently polls every 5 seconds. The main loop wakes every second and updates each service when its own polling interval has elapsed. In the upstream code, OpenDTU uses a fixed `5000` ms interval.

The `ESP8266PollingIntervall` option in `config.ini` only applies to AhoyDTU running on ESP8266. Template devices use their own `CUST_POLLING` setting. There is no OpenDTU-specific polling interval option in the current upstream release.

### Checks

```sh
svstat /service/dbus-opendtu
tail -F /var/log/dbus-opendtu/current | tai64nlocal
dbus-spy
```

The Cerbo device list should then show a PV inverter. In VRM it will be counted as AC PV generation, while the existing IR meter continues to provide grid import and export.

## APsystems EZ1-M as PV Inverter

The APsystems EZ1-M microinverter communicates directly over Wi-Fi/Bluetooth without needing an ECU gateway. When **Local Mode** is enabled and set to **Continuously** in the AP EasyPower / APsystems mobile app, it exposes a local REST API on port `8050`.

To integrate it on the Cerbo GX as an additional PV inverter on D-Bus, [domizei385/dbus-apsystems-ez1](https://github.com/domizei385/dbus-apsystems-ez1) can be deployed alongside `dbus-opendtu`.

### Data Flow

```text
APsystems EZ1-M -> local REST API (:8050) -> Cerbo GX dbus-apsystems-ez1 -> Venus OS / VRM PV-Inverter
```

### Pre-Check

From the Cerbo or another computer on the local network:

```sh
curl --fail --max-time 5 http://EZ1_IP:8050/getOutputData
```

Ensure a valid JSON response with `p1`, `p2`, `te1`, and `te2` is returned.

### Install on the Cerbo

```sh
ssh root@CERBO_IP
cd /data
wget https://github.com/domizei385/dbus-apsystems-ez1/archive/refs/heads/main.zip
unzip main.zip && mv dbus-apsystems-ez1-main dbus-apsystems-ez1 && rm main.zip
cd /data/dbus-apsystems-ez1
cp /path/to/dbus-apsystems-ez1-config.example config.ini # or configure manually based on dbus-apsystems-ez1-config.example
vi config.ini
chmod a+x install.sh
./install.sh
```

### Configuration Notes

- `Address`: IP address of the EZ1-M (assign a static DHCP reservation in your router).
- `Port`: `8050`.
- `Deviceinstance`: Set to `52` (must not collide with `40` for grid meter, or `50`/`51` for OpenDTU).
- `Phase`: Set to the physical phase (`L1`, `L2`, or `L3`) where the microinverter feeds in.
- `Position`: Set `0` for AC-input / grid side (standard).
- `CustomName`: `APsystems EZ1-M`.

### Checks

```sh
svstat /service/dbus-apsystems-ez1
tail -F /data/dbus-apsystems-ez1/current.log
dbus-spy
```
