"""
ThingSpeak Config Bootup Time Test — Sensor Node.

Copy this file to the Sensor Node's CIRCUITPY drive as ``code.py`` alongside
``espnow_manager.py``. Measures the elapsed time from sketch start until
ESP-NOW pairing with the UI Hub completes, then stays alive so repeated hub
reboots re-pair automatically.
"""

import time

t_start = time.monotonic()

import asyncio
import wifi
from espnow_manager import ESPNowManager

t_imports = time.monotonic()
espnow_manager = ESPNowManager()


async def main():
    # Disconnect from any auto-connected Wi-Fi AP so channel hopping works freely
    if wifi.radio.connected:
        wifi.radio.enabled = False
        wifi.radio.enabled = True

    hub_mac = await espnow_manager.init_sensor_node()
    t_paired = time.monotonic()

    print("\n--- Bootup to Pair Timing (Sensor Node) ---")
    print(f"Imports            : {t_imports - t_start:6.3f} s")
    print(f"ESP-NOW pairing    : {t_paired - t_imports:6.3f} s (CH {espnow_manager._channel})")
    print("-------------------------------------------")
    print(f"TOTAL (start->pair): {t_paired - t_start:6.3f} s")
    print(f"Paired hub         : {espnow_manager.mac_to_str(hub_mac)}")
    print("\nStaying online to re-pair if the UI Hub is rebooted for another trial...")

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
