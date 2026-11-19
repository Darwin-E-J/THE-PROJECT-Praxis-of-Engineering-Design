"""
ThingSpeak Config Bootup Time Test — UI Hub.

Copy this file to the UI Hub's CIRCUITPY drive as ``code.py`` alongside
``wifi_manager.py``, ``ThingSpeak.py``, ``espnow_manager.py``, and
``settings.toml``. Measures the elapsed time from sketch start through Wi-Fi
connection, NTP RTC sync, ThingSpeak sensor MAC config load, and ESP-NOW
hub-sensor pairing.
"""

import time

t_start = time.monotonic()

import os
import asyncio
import rtc
from wifi_manager import WifiManager
from ThingSpeak import ThingSpeak
from espnow_manager import ESPNowManager

SENSOR_MAC_FIELD = 8  # ThingSpeak field storing registered sensor MACs

t_imports = time.monotonic()


def get_sensor_macs(ts, field_id=SENSOR_MAC_FIELD):
    """Fetch unique, non-empty sensor MAC strings from ThingSpeak ``field_id``."""
    data = ts.read_channel_field(field_id)
    field_key = f"field{field_id}"
    macs = []
    seen = set()

    for entry in data.get("feeds", []):
        mac = entry.get(field_key)
        if mac is not None:
            mac = str(mac).strip()
            normalized = mac.replace(":", "").replace("-", "").replace(" ", "").lower()
            if mac and normalized not in seen:
                seen.add(normalized)
                macs.append(mac)

    return macs


async def main():
    # 1. Connect to Wi-Fi
    wifi_manager = WifiManager()
    t_wifi = time.monotonic()

    # 2. Sync RTC via NTP
    my_rtc = rtc.RTC()
    if wifi_manager.connected:
        try:
            my_rtc.datetime = wifi_manager.utc_time
        except Exception as err:
            print(f"[!] RTC Sync Error: {err}")
    t_rtc = time.monotonic()

    # 3. Load configured sensor MACs from ThingSpeak
    thingspeak = ThingSpeak(
        os.getenv("THINGSPEAK_READ_API_KEY"),
        os.getenv("THINGSPEAK_WRITE_API_KEY"),
        os.getenv("THINGSPEAK_CHANNEL_ID"),
    )
    sensor_macs = get_sensor_macs(thingspeak)
    t_thingspeak = time.monotonic()
    print(f"Loaded {len(sensor_macs)} sensor MAC(s) from ThingSpeak field {SENSOR_MAC_FIELD}: {sensor_macs}")

    # 4. Pair with sensor node(s) over ESP-NOW
    espnow_manager = ESPNowManager()
    paired_sensors = await espnow_manager.init_hub(sensor_macs)
    t_paired = time.monotonic()

    ch = espnow_manager._channel
    paired_strs = [espnow_manager.mac_to_str(m) for m in paired_sensors]
    all_paired = len(sensor_macs) > 0 and len(paired_sensors) == len(sensor_macs)

    print("\n--- Bootup to Pair Timing (UI Hub) ---")
    print(f"Imports            : {t_imports - t_start:6.3f} s")
    print(f"Wi-Fi connect      : {t_wifi - t_imports:6.3f} s")
    print(f"RTC NTP sync       : {t_rtc - t_wifi:6.3f} s")
    print(f"ThingSpeak config  : {t_thingspeak - t_rtc:6.3f} s ({len(sensor_macs)} MACs)")
    print(f"ESP-NOW pairing    : {t_paired - t_thingspeak:6.3f} s ({len(paired_sensors)}/{len(sensor_macs)} paired on CH {ch})")
    print("--------------------------------------")
    print(f"TOTAL (start->pair): {t_paired - t_start:6.3f} s")
    print(f"Paired sensors     : {paired_strs}")
    if not all_paired:
        print("[!] WARNING: Not all configured sensors paired before timeout.")

    espnow_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
