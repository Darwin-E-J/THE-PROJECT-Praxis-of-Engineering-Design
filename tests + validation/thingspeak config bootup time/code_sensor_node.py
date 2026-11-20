"""
ThingSpeak Config Bootup Time Test — Sensor Node.

Copy this file to the Sensor Node's CIRCUITPY drive as ``code.py`` alongside
``espnow_manager.py``. Measures the elapsed time from sketch start until
ESP-NOW pairing with the UI Hub completes, outputs the timing as CSV over
serial, and stays alive so repeated hub reboots re-pair automatically.
"""

import time

t_start = time.monotonic()

import asyncio
import wifi
from espnow_manager import ESPNowManager

t_imports = time.monotonic()
CSV_HEADER = "imports_s,espnow_pair_s,total_s,channel,hub_mac"
espnow_manager = ESPNowManager()


async def main():
    # Disconnect from any auto-connected Wi-Fi AP so channel hopping works freely
    if wifi.radio.connected:
        wifi.radio.enabled = False
        wifi.radio.enabled = True

    hub_mac = await espnow_manager.init_sensor_node()
    t_paired = time.monotonic()

    imports_s = t_imports - t_start
    espnow_pair_s = t_paired - t_imports
    total_s = t_paired - t_start
    ch = "" if espnow_manager._channel is None else espnow_manager._channel
    hub_mac_str = espnow_manager.mac_to_str(hub_mac)

    print("\n--- CSV START ---")
    print(CSV_HEADER)
    print(f"{imports_s:.3f},{espnow_pair_s:.3f},{total_s:.3f},{ch},{hub_mac_str}")
    print("--- CSV END ---")

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
