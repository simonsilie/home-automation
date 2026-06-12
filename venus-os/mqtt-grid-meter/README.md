# Venus OS MQTT Grid Meter

Small Venus OS service that subscribes to ESPHome MQTT topics from the IR meter and exposes them as a Victron grid meter on D-Bus.

It is intended for this data flow:

```text
electric_meter_ir ESPHome -> Home Assistant Mosquitto -> Cerbo GX service -> Venus OS grid meter
```

No Node-RED and no Venus OS Large image are required.

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

5. Set at least these values in `config.ini`:

   ```ini
   host = HOME_ASSISTANT_IP
   username = mqtt
   password = your_mqtt_password
   ```

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
