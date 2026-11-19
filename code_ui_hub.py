"""
CircuitPython entry point for the ESP32-S3 UI hub.

Connects to Wi-Fi, synchronizes the RTC via NTP, loads configured sensor node
MAC addresses from ThingSpeak, pairs with sensor nodes over ESP-NOW, and
continuously polls them while automatically re-pairing disconnected nodes.
"""

import os
import asyncio
import rtc
import adafruit_ticks
from wifi_manager import WifiManager
from espnow_manager import ESPNowManager
from ThingSpeak import ThingSpeak
from UI_Hub import UIHub

# ThingSpeak configuration and ESP-NOW polling/re-pairing intervals
SENSOR_MAC_FIELD = 8             # ThingSpeak field storing registered sensor MACs
POLL_INTERVAL_SEC = 5            # Delay between sensor poll cycles
REQUEST_REPLY_TIMEOUT_MS = 2000  # Max wait for a sensor's data packet after ACK
MAX_MISSED_POLLS = 2             # Consecutive failed polls before dropping a peer
REPAIR_TIMEOUT_SEC = 16          # Broadcast window when other sensors are still online
REPAIR_RETRY_INTERVAL_MS = 30000 # Periodic retry cadence for still-missing sensors
EMPTY_REPAIR_DELAY_SEC = 2       # Short pause between retries when 0 sensors are paired

THINGSPEAK_CHANNEL_ID = os.getenv("THINGSPEAK_CHANNEL_ID")
THINGSPEAK_READ_API_KEY = os.getenv("THINGSPEAK_READ_API_KEY")
THINGSPEAK_WRITE_API_KEY = os.getenv("THINGSPEAK_WRITE_API_KEY")

wifi_manager = WifiManager()
espnow_manager = ESPNowManager()
myRTC = rtc.RTC()


def get_sensor_macs(ts, field_id=SENSOR_MAC_FIELD):
    """
    Fetch ``field_id`` from ThingSpeak and return unique, non-empty MAC strings.

    Args:
        ts: Initialized ``ThingSpeak`` client.
        field_id: ThingSpeak channel field number (1-8) containing sensor MACs.

    Returns:
        list[str]: Deduplicated MAC address strings in discovery order.
    """
    data = ts.read_channel_field(field_id)
    field_key = f"field{field_id}"
    macs = []
    seen = set()

    for entry in data.get("feeds", []):
        mac = entry.get(field_key)
        if mac is not None:
            mac = str(mac).strip()
            # Normalize delimiters and case so equivalent MAC formats are deduplicated
            normalized = mac.replace(":", "").replace("-", "").replace(" ", "").lower()
            if mac and normalized not in seen:
                seen.add(normalized)
                macs.append(mac)

    return macs


# Sync onboard RTC to NTP when Wi-Fi is available
if wifi_manager.connected:
    try:
        myRTC.datetime = wifi_manager.utc_time
        print("RTC synced with NTP time")
    except Exception as e:
        print("RTC Sync Error:", e)

# Build peer MAC list from ThingSpeak field 8
thingspeak = ThingSpeak(THINGSPEAK_READ_API_KEY, THINGSPEAK_WRITE_API_KEY, THINGSPEAK_CHANNEL_ID)
SENSOR_MACS = get_sensor_macs(thingspeak)
print(f"Loaded sensor MACs from ThingSpeak field {SENSOR_MAC_FIELD}: {SENSOR_MACS}")

# Setup UIHub display controller
ui_hub = UIHub()


async def main():
    """Pair with sensor nodes and run the continuous polling and re-pairing loop."""
    # Initialize ESP-NOW as hub and pair with configured sensor nodes
    print("Initializing ESP-NOW hub...")
    expected_count = len(SENSOR_MACS)
    paired_sensors = await espnow_manager.init_hub(SENSOR_MACS)
    print(f"Paired sensors: {[espnow_manager.mac_to_str(m) for m in paired_sensors]}")

    missed_polls = {mac: 0 for mac in paired_sensors}
    last_repair_ms = adafruit_ticks.ticks_ms()

    while True:
        needs_immediate_repair = False

        # If Wi-Fi switched channels, previously paired sensors are on the old channel
        if espnow_manager.sync_hub_channel() and paired_sensors:
            print("[!] Wi-Fi channel changed, clearing peers to re-pair...")
            for mac in list(paired_sensors):
                espnow_manager.remove_peer(mac)
            paired_sensors = []
            missed_polls.clear()
            needs_immediate_repair = True

        # Request readings from each paired sensor node
        for mac in list(paired_sensors):
            mac_str = espnow_manager.mac_to_str(mac)
            print(f"Requesting data from {mac_str}...")
            poll_ok = False

            if await espnow_manager.send_message(mac, espnow_manager.REQUEST_MESSAGE):
                reply = await espnow_manager.read_message(mac, timeout_ms=REQUEST_REPLY_TIMEOUT_MS)
                if reply:
                    sender_mac, data = reply
                    print(f"Received from {espnow_manager.mac_to_str(sender_mac)}: {data}")
                    poll_ok = True
                else:
                    print(f"No response from {mac_str}")
            else:
                print(f"Failed to send request to {mac_str}")

            if poll_ok:
                missed_polls[mac] = 0
            else:
                missed_polls[mac] = missed_polls.get(mac, 0) + 1
                print(f"[!] Missed poll for {mac_str} ({missed_polls[mac]}/{MAX_MISSED_POLLS})")
                if missed_polls[mac] >= MAX_MISSED_POLLS:
                    print(f"[!] Sensor {mac_str} disconnected, removing peer to re-pair...")
                    espnow_manager.remove_peer(mac)
                    paired_sensors.remove(mac)
                    missed_polls.pop(mac, None)
                    needs_immediate_repair = True

        # Re-pair if any configured sensors are missing or disconnected
        if len(paired_sensors) < expected_count:
            all_disconnected = len(paired_sensors) == 0
            repair_due = (
                all_disconnected
                or needs_immediate_repair
                or adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), last_repair_ms) >= REPAIR_RETRY_INTERVAL_MS
            )
            if repair_due:
                timeout_sec = espnow_manager.PAIR_TIMEOUT_SEC if all_disconnected else REPAIR_TIMEOUT_SEC
                print(
                    f"[*] Attempting to re-pair missing sensors "
                    f"({len(paired_sensors)}/{expected_count} currently paired)..."
                )
                paired_sensors = await espnow_manager.repair_hub(SENSOR_MACS, timeout_sec=timeout_sec)
                last_repair_ms = adafruit_ticks.ticks_ms()
                for mac in paired_sensors:
                    missed_polls.setdefault(mac, 0)
                print(f"Paired sensors: {[espnow_manager.mac_to_str(m) for m in paired_sensors]}")

        # Retry faster when no sensors are paired; otherwise wait the normal poll interval
        if expected_count > 0 and len(paired_sensors) == 0:
            await asyncio.sleep(EMPTY_REPAIR_DELAY_SEC)
        else:
            await asyncio.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    asyncio.run(main())
