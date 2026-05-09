# Home Assistant

Die Packages aus diesem Repository liegen in [packages/](packages/).

Damit Home Assistant sie lädt, muss in der aktiven `configuration.yaml` stehen:

```yaml
homeassistant:
  packages: !include_dir_named packages/
```

Danach die gewünschte Datei nach `packages/` legen und Home Assistant neu laden oder neu starten.

Beispiel:

- [packages/soyosource_feed_in_control.yaml](packages/soyosource_feed_in_control.yaml)