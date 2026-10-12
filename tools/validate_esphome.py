"""Validate firmware using public dummy credentials, never local secrets or hardware."""

import argparse
import base64
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEVICES = ("electric_meter_ir.yaml", "riden-psu.yaml", "soyosource-victron-esp32.yaml")


def validate(devices: Sequence[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="esphome-check-") as directory:
        fixture = Path(directory)
        (fixture / "common").mkdir()
        shutil.copyfile(ROOT / "esphome/common/base.yaml", fixture / "common/base.yaml")
        secrets = yaml.safe_load((ROOT / "esphome/secrets.yaml.example").read_text())
        secrets["api_encryption_key"] = base64.b64encode(bytes(range(32))).decode("ascii")
        # This public fixture is only for validation and must never be flashed.
        (fixture / "secrets.yaml").write_text(yaml.safe_dump(secrets))
        for device in devices:
            shutil.copyfile(ROOT / "esphome" / device, fixture / device)
            result = subprocess.run(
                [sys.executable, "-m", "esphome", "config", str(fixture / device)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                print(result.stdout, end="")
                print(result.stderr, end="", file=sys.stderr)
                return result.returncode
            print("Validated {} with dummy credentials".format(device))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("devices", nargs="*", help="Device YAML filenames; defaults to the active IR meter")
    args = parser.parse_args()
    devices = args.devices or ["electric_meter_ir.yaml"]
    if any(device not in DEVICES for device in devices):
        parser.error("Only tracked device YAML filenames are accepted")
    return validate(devices)


if __name__ == "__main__":
    raise SystemExit(main())
