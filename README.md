# Home Automation

Konfigurationen für mein Smart-Home-Setup rund um Stromzähler, Balkonkraftwerk-Limiter,
Solarladeregler und überschussbasiertes Laden eines Labornetzteils.

## Überblick

Das Repo ist in zwei Bereiche aufgeteilt:

- [esphome/](esphome/) – ESPHome-Firmware-YAMLs für die ESP-Geräte
- [homeassistant/](homeassistant/) – Home Assistant-Konfiguration (als Packages)

### Einspeiseregelung (Soyosource)

```mermaid
flowchart TD
    Batterie[(Batterie)] -->|DC| Soyosource[Soyosource GTN\nWechselrichter]
    Soyosource -->|AC| PCC[Hausanschluss PCC]
    PCC -->|IR-Lesekopf| ESP8266[ESP8266\nelectric_meter_ir]
    ESP8266 -->|Wirkleistung| HA[Home Assistant]
    HA -->|Sollleistung| ESP32[ESP32\nsoyosource-victron]
    ESP32 -->|RS485| Soyosource
```

### Ueberschussladen (RD6030W)

```mermaid
flowchart TD
    PCC[Hausanschluss PCC] -->|AC| RD6030W[RD6030W\nNetzteil]
    RD6030W -->|DC| MPPT[Victron MPPT]
    MPPT -->|DC laden| Batterie[(Batterie)]
    PCC -->|IR-Lesekopf| ESP8266[ESP8266\nelectric_meter_ir]
    ESP8266 -->|Wirkleistung| HA[Home Assistant]
    HA -->|Modbus/TCP| RD6030W
    MPPT -->|VE.Direct| ESP32[ESP32\nsoyosource-victron]
    ESP32 -->|Telemetrie| HA
```

Der Soyosource GTN ist direkt an der Batterie (DC) angeschlossen und speist über seinen
AC-Ausgang ins Hausnetz ein. Das RD6030W bezieht AC vom Hausanschluss und gibt DC an den
PV-Eingang des Victron-MPPT — das Netzteil emuliert einen PV-String, der MPPT lädt damit
die Batterie regelkonform.

Die beiden Modi schließen sich gegenseitig aus: Wenn das Balkonkraftwerk mehr erzeugt als
verbraucht wird (Überschuss am Zähler), lädt der RD6030W die Batterie — der Soyosource
speist in diesem Fall nicht ein. Erst wenn die Batterie geladen ist oder kein Überschuss
mehr vorhanden ist, kann der Soyosource wieder aus der Batterie ins Netz einspeisen.
Beide gleichzeitig aktiv würden einen sinnlosen Kreislauf erzeugen (Netz → RD6030W →
Batterie → Soyosource → Netz).

Der Victron wird vom ESP32 nur per VE.Direct mitgelesen (Telemetrie), nicht aktiv gesteuert.

## ESPHome

| Datei | Hardware | Zweck |
| --- | --- | --- |
| [esphome/electric_meter_ir.yaml](esphome/electric_meter_ir.yaml) | ESP8266 (D1 mini) + Hichi IR-Lesekopf | SML-Zähler über UART auslesen, OBIS-Werte als HA-Sensoren |
| [esphome/soyosource-victron-esp32.yaml](esphome/soyosource-victron-esp32.yaml) | ESP32 + MAX485 + VE.Direct | Soyosource GTN Limiter (RS485) und Victron MPPT (VE.Direct) auf einem Gerät |

Genutzte externe Komponenten:

- [syssi/esphome-soyosource-gtn-virtual-meter](https://github.com/syssi/esphome-soyosource-gtn-virtual-meter)
- [KinDR007/VictronMPPT-ESPHOME](https://github.com/KinDR007/VictronMPPT-ESPHOME)

### Secrets

`esphome/secrets.yaml` aus [esphome/secrets.yaml.example](esphome/secrets.yaml.example) erzeugen
und ausfüllen (WLAN, OTA, API-Key).

### Flashen

```sh
cd esphome
esphome run electric_meter_ir.yaml
esphome run soyosource-victron-esp32.yaml
```

## Home Assistant

| Datei | Zweck |
| --- | --- |
| [homeassistant/packages/rd6030_device.yaml](homeassistant/packages/rd6030_device.yaml) | RD6030W Modbus/TCP-Entities |
| [homeassistant/packages/rd6030_battery_surplus_charge.yaml](homeassistant/packages/rd6030_battery_surplus_charge.yaml) | Überschussladung der Batterie über den RD6030W |
| [homeassistant/packages/soyosource_feed_in_control.yaml](homeassistant/packages/soyosource_feed_in_control.yaml) | Einspeiseregelung für den Soyosource im Handbetrieb |

### WLAN-Dongle des RD6030W

Der Modbus/TCP-Zugriff setzt voraus, dass der interne WLAN-Dongle mit einer alternativen
Firmware betrieben wird. Die originale Riden-Firmware unterstützt kein Modbus TCP.

Empfohlen: **[morgendagen/riden-dongle](https://github.com/morgendagen/riden-dongle)** –
PlatformIO-Firmware (ESP8266/ESP-12F), die Modbus TCP, SCPI und ein Web-Interface bereitstellt.

> **Hinweis Hardware:** Neuere Dongles verwenden einen ESP8684-Chip mit verschlüsseltem
> Bootloader (nicht flashbar). Diese müssen erst auf einen ESP-12F umgerüstet werden, bevor
> riden-dongle aufgespielt werden kann. Details siehe README des Projekts.

### Einbinden

In `configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages/
```

Dann den Package-Ordner mit dem Inhalt aus [homeassistant/packages/](homeassistant/packages/)
befüllen und `homeassistant/secrets.yaml` aus
[homeassistant/secrets.yaml.example](homeassistant/secrets.yaml.example) anlegen
(`rd6030w_host` u. ä.).

## Sicherheitshinweise

- 230 V-Verkabelung von Soyosource und RD6030W gehört in qualifizierte Hände.
- RD6030W → MPPT-PV-Eingang: Ausgangsspannung innerhalb des erlaubten PV-Eingangsfensters
  des MPPT halten (Vmax beachten) und Strom auf das Modell-Limit begrenzen. Echte PV-Module
  und der RD6030 dürfen am selben MPPT-Eingang nicht ohne Entkopplung parallel betrieben
  werden (Rückspeisung in das Netzteil vermeiden — z. B. Sperrdiode oder Umschaltung).
- Die eigentliche Ladeschluss-Regelung übernimmt der Victron-MPPT inkl. BMS-Abschaltung;
  diese Automatisierung steuert nur die zugeführte Leistung und ersetzt keine
  Hardware-Schutzmaßnahmen.
- Steuerwerte (max. Ladestrom, Sollleistung) konservativ einstellen und im Live-Betrieb
  beobachten, bevor Limits hochgezogen werden.

## Lizenz

[MIT](LICENSE)
