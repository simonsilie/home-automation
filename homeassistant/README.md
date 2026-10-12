# Home Assistant

This directory mirrors a complete Home Assistant config directory: the versioned
`configuration.yaml` together with `automations.yaml`, `scripts.yaml`, `scenes.yaml`,
`climate.yaml` and `influxdb.yaml` can be used as-is. The packages live in
[packages/](packages/), the YAML dashboard in [dashboards/](dashboards/).

## Setup

Active packages are explicitly loaded via `configuration.yaml`:

```yaml
homeassistant:
  packages:
    energy_meter_common: !include packages/energy_meter_common.yaml
    solar_pv: !include packages/solar_pv.yaml
    ir_heizung_kinderzimmer2_control: !include packages/ir_heizung_kinderzimmer2_control.yaml
    waste_collection: !include packages/waste_collection.yaml
```

Copy these files from [packages/](packages/) into the `packages/` folder of the
active config directory and use the explicit includes above, then restart Home Assistant.
The retired RD6030 and Soyosource packages can remain in the folder for reference;
they are not loaded unless explicitly included. Add an include when installing a new package.

The package uses the `triggers:`/`trigger:` and `actions:`/`action:` automation
syntax, which requires Home Assistant 2024.10 or newer.

One secret is required in the active `secrets.yaml`: `waste_ics_url` (ICS calendar URL)
for [packages/waste_collection.yaml](packages/waste_collection.yaml).
The waste package retains the recommended nested `sources` and `sensors` under
`waste_collection_schedule`. The originally flagged nesting concern is **not a
schema defect** in inspected upstream **v2.35.0**:
[installation docs](https://github.com/mampfes/hacs_waste_collection_schedule/blob/v2.35.0/doc/installation.md#configuring-sensors)
and [YAML source](https://github.com/mampfes/hacs_waste_collection_schedule/blob/v2.35.0/custom_components/waste_collection_schedule/init_yaml.py)
show `CONFIG_SCHEMA` accepting nested `sensors` via `SENSOR_CONFIG`, which requires
`name` and defaults optional `source_index` to `0` (the first source). Thus `muell`
needs no explicit index with the sole ICS source. No waste schema migration is
needed; the original configuration and secret are unchanged. The inspected tag
is an evidence reference, not an installed-version assertion or requirement;
the installed HACS version and deployed Home Assistant configuration were not validated.

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
- [packages/solar_pv.yaml](packages/solar_pv.yaml) —
  Combined PV power (`sensor.solar_power_total`) aggregating OpenDTU and the APsystems EZ1-M.
  Both readings must be numeric and finite (`is_number`), otherwise the total is
  unavailable rather than silently partial. Numeric zero at night remains valid;
  valid readings are summed and rounded to one decimal place.
- [packages/ir_heizung_kinderzimmer2_control.yaml](packages/ir_heizung_kinderzimmer2_control.yaml) —
  Opt-in IR heater control with sustained export, explicit HVAC modes, and hysteresis

### IR heater surplus control

1. Set `input_number.ir_kz2_heater_power` to the heater's actual rated watts.
   A zero or unavailable value blocks automatic heating.
2. Enable `input_boolean.ir_kz2_surplus_control_enabled`. It starts disabled on
   every Home Assistant restart, and the nursery thermostat starts in HVAC `off`.
3. With the thermostat off, all prerequisites must hold continuously for 60 seconds:
   export exceeds the configured heater power plus a 300 W reserve, SOC is above
   98.9 %, battery power is ≤ 0 W, and the room temperature is numeric.
4. The controller then selects HVAC `heat` with a 21.5 °C target. While active,
   it selects HVAC `off` when export falls to 100 W or less, SOC drops below 97 %,
   battery power becomes positive, or a required reading becomes non-numeric.
   Shutdown no longer relies on a 7 °C frost-protection target.

The larger startup threshold accounts for the heater's own consumption; the
separate shutdown threshold avoids immediately undoing that startup. Disabling
surplus control switches the thermostat off once, then leaves manual mode and
temperature changes alone. No automatic frost protection runs while HVAC is off.

The control switch and rated-power helper are exposed in the energy dashboard's
"Energie Diagnose" card. Deploy both the updated package and `climate.yaml`,
then restart Home Assistant to remove the previous standalone shutdown automation.

**Battery polarity still requires verification:** this package preserves the
existing assumption that positive battery power means discharge. Confirm the HA
entity's convention before enabling the controller. Numeric-state checks do not
prove sensor freshness or provide an actuator watchdog during a Home Assistant
outage; independent heater safety remains necessary.

### Offline HA regression checks

From the repository root, install the locked development tools and run:

```sh
uv sync --locked --python 3.12
uv run --locked python -m unittest discover -s homeassistant/tests -v
uv run --locked yamllint homeassistant
```

`uv run --locked pytest` also runs the HA tests alongside the bridge/config tests.

These tests render the actual package templates and check PV availability/sums,
recommended nested waste YAML layout, heater action selection, thresholds, configured dwell time,
and opt-in defaults. The waste test preserves `!secret` as a placeholder without
reading secrets or fetching the calendar. They do not simulate the Home Assistant
scheduler, custom integrations, or physical thermostat/relay.

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
      filename: dashboards/dashboard_energy_control.yaml
```

Then copy the `dashboards/` folder into the active Home Assistant config directory and
reload or restart Home Assistant.

The dashboard shows in particular:

- current grid power via `sensor.electric_meter_ir_active_power` (and L1–L3)
- combined PV generation via `sensor.solar_power_total`
- individual PV power via `sensor.opendtu_91fd98_ac_power` (OpenDTU / Hoymiles) and `sensor.ez1_total_power` (APsystems EZ1-M)
- battery power and SOC from the Cerbo GX
- battery temperature via `sensor.temperature_battery_temperatur`
