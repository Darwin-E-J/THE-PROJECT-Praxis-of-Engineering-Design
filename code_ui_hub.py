"""
Circuitpython sketch for the UI hub that uses the WIfi_Manager, EspNowManager, and UI_Hub classes
"""

import os
import asyncio
import rtc
from wifi_manager import WifiManager
from espnow_manager import ESPNowManager
from ThingSpeak import ThingSpeak
from UI_Hub import UIHub

SENSOR_MAC_FIELD = 8
THINGSPEAK_CHANNEL_ID = os.getenv("THINGSPEAK_CHANNEL_ID")
THINGSPEAK_READ_API_KEY = os.getenv("THINGSPEAK_READ_API_KEY")
THINGSPEAK_WRITE_API_KEY = os.getenv("THINGSPEAK_WRITE_API_KEY")

wifi_manager = WifiManager()
espnow_manager = ESPNowManager()
myRTC = rtc.RTC()


def get_sensor_macs(ts, field_id=SENSOR_MAC_FIELD):
    """Pull field_id from ThingSpeak and return all unique non-None MAC addresses."""
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

# Setup UIHub
ui_hub = UIHub()


async def main():
    # Initialize ESP-NOW as hub and pair with configured sensor nodes
    print("Initializing ESP-NOW hub...")
    paired_sensors = await espnow_manager.init_hub(SENSOR_MACS)
    print(f"Paired sensors: {[espnow_manager.mac_to_str(m) for m in paired_sensors]}")

    while True:
        # Request readings from each paired sensor node
        for mac in paired_sensors:
            print(f"Requesting data from {espnow_manager.mac_to_str(mac)}...")
            if await espnow_manager.send_message(mac, espnow_manager.REQUEST_MESSAGE):
                reply = await espnow_manager.read_message(mac, timeout_ms=2000)
                if reply:
                    sender_mac, data = reply
                    print(f"Received from {espnow_manager.mac_to_str(sender_mac)}: {data}")
                else:
                    print(f"No response from {espnow_manager.mac_to_str(mac)}")
            else:
                print(f"Failed to send request to {espnow_manager.mac_to_str(mac)}")

        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
