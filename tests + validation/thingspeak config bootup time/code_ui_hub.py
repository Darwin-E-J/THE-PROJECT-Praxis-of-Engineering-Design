"""
ThingSpeak Config Bootup Time Test — UI Hub.

Copy this file to the UI Hub's CIRCUITPY drive as ``code.py`` alongside
``wifi_manager.py``, ``ThingSpeak.py``, ``espnow_manager.py``, and
``settings.toml``. Measures the elapsed time from sketch start through Wi-Fi
connection, NTP RTC sync, ThingSpeak sensor MAC config load, and ESP-NOW
hub-sensor pairing, then outputs the results as CSV over serial.
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
CSV_HEADER = (
    "imports_s,wifi_connect_s,rtc_sync_s,thingspeak_config_s,"
    "espnow_pair_s,total_s,wifi_connected,rtc_synced,"
    "configured_macs,paired_count,all_paired,channel,paired_sensors"
)

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
    rtc_synced = False
    if wifi_manager.connected:
        try:
            my_rtc.datetime = wifi_manager.utc_time
            rtc_synced = True
        except Exception:
            rtc_synced = False
    t_rtc = time.monotonic()

    # 3. Load configured sensor MACs from ThingSpeak
    thingspeak = ThingSpeak(
        os.getenv("THINGSPEAK_READ_API_KEY"),
        os.getenv("THINGSPEAK_WRITE_API_KEY"),
        os.getenv("THINGSPEAK_CHANNEL_ID"),
    )
    sensor_macs = get_sensor_macs(thingspeak)
    t_thingspeak = time.monotonic()

    # 4. Pair with sensor node(s) over ESP-NOW
    espnow_manager = ESPNowManager()
    paired_sensors = await espnow_manager.init_hub(sensor_macs)
    t_paired = time.monotonic()

    ch = espnow_manager._channel
    ch_str = "" if ch is None else ch
    paired_strs = [espnow_manager.mac_to_str(m) for m in paired_sensors]
    all_paired = len(sensor_macs) > 0 and len(paired_sensors) == len(sensor_macs)

    espnow_manager.stop()

    imports_s = t_imports - t_start
    wifi_connect_s = t_wifi - t_imports
    rtc_sync_s = t_rtc - t_wifi
    thingspeak_config_s = t_thingspeak - t_rtc
    espnow_pair_s = t_paired - t_thingspeak
    total_s = t_paired - t_start

    print("\n--- CSV START ---")
    print(CSV_HEADER)
    print(
        f"{imports_s:.3f},{wifi_connect_s:.3f},{rtc_sync_s:.3f},"
        f"{thingspeak_config_s:.3f},{espnow_pair_s:.3f},{total_s:.3f},"
        f"{wifi_manager.connected},{rtc_synced},"
        f"{len(sensor_macs)},{len(paired_sensors)},{all_paired},"
        f"{ch_str},{';'.join(paired_strs)}"
    )
    print("--- CSV END ---")


if __name__ == "__main__":
    asyncio.run(main())
