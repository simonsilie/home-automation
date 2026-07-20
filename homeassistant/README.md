# Home Assistant

This directory mirrors a complete Home Assistant config directory: the versioned
`configuration.yaml` together with `automations.yaml`, `scripts.yaml`, `scenes.yaml`,
`climate.yaml` and `influxdb.yaml` can be used as-is. The packages live in
[packages/](packages/), the YAML dashboard in [dashboards/](dashboards/).

## Setup

Packages are loaded via `configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Copy the desired files from [packages/](packages/) into the `packages/` folder of the
active config directory, then reload or restart Home Assistant.

One secret is required in the active `secrets.yaml`: `waste_ics_url` (ICS calendar URL)
for [packages/waste_collection.yaml](packages/waste_collection.yaml).

## Energy system context

Battery charge/discharge is handled by a **Victron MultiPlus-II 3000** with a
**Pylontech US5000**, managed by the Cerbo GX (Venus OS / ESS). Home Assistant only
observes grid/PV/battery telemetry; it does not set battery charge current or inverter
power demand.

Related integration points:

- Grid meter: ESPHome IR head → MQTT → Cerbo `mqtt-grid-meter` (see [../venus-os/](../venus-os/))
- Balcony PV: OpenDTU → Cerbo `dbus-opendtu` as Victron PV inverter
- Battery / MultiPlus: Cerbo GX with Pylontech BMS (entities such as
  `sensor.gx_device_dc_batterieladung`, `sensor.gx_device_dc_batterieleistung`)

Active packages related to energy:

- [packages/energy_meter_common.yaml](packages/energy_meter_common.yaml) —
  `sensor.grid_power_average` from the IR meter
- [packages/ir_heizung_kinderzimmer2_control.yaml](packages/ir_heizung_kinderzimmer2_control.yaml) —
  IR heater on surplus export when battery is full and not discharging
  (SOC ≥ 98.9 %, `sensor.gx_device_dc_batterieleistung` ≤ 0.1 W, export ≥ 300 W;
  turns off below SOC 97 % / on discharge, with hysteresis)

### Retired packages (RD6030 / Soyosource)

These packages controlled the previous setup and are **not used** with the MultiPlus-II
+ Pylontech system. Keep them only as historical reference; do not load them in the
active config unless the old hardware is restored.

| Package | Former role |
| --- | --- |
| [packages/rd6030_battery_surplus_charge.yaml](packages/rd6030_battery_surplus_charge.yaml) | Surplus charging of the battery via RD6030W into a Victron MPPT |
| [packages/soyosource_feed_in_control.yaml](packages/soyosource_feed_in_control.yaml) | Manual-mode feed-in control for a Soyosource GTN |

Former mutual exclusion: RD6030 charged on meter surplus; Soyosource only fed in when
not charging. That loop is obsolete — the MultiPlus ESS now manages battery flux under
Venus OS.

## Dashboard as YAML

The example dashboard lives in
[dashboards/dashboard_energy_control.yaml](dashboards/dashboard_energy_control.yaml).

For Home Assistant to load it as config-as-code, a YAML dashboard must be registered in
the active `configuration.yaml`:

```yaml
lovelace:
  dashboards:
    energie-steuerung:
      mode: yaml
      title: Energie Steuerung
      icon: mdi:transmission-tower
      show_in_sidebar: true
      filename: /config/dashboards/dashboard_energy_control.yaml
```

Then copy the `dashboards/` folder into the active Home Assistant config directory and
reload or restart Home Assistant.

The dashboard shows in particular:

- current grid power via `sensor.electric_meter_ir_active_power` (and L1–L3)
- current PV power via `sensor.opendtu_91fd98_ac_power` (the `91fd98` suffix is the
  OpenDTU inverter serial and must match the inverter configured in OpenDTU; rename it
  if your inverter has a different serial)
- battery power and SOC from the Cerbo GX
- battery temperature via `sensor.temperature_battery_temperatur`
