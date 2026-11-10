"""
Circuitpython sketch for the UI hub that uses the WIfi_Manager, EspNowManager, and UI_Hub classes
"""

import asyncio
import rtc
from wifi_manager import WifiManager
from espnow_manager import ESPNowManager
from UI_Hub import UIHub

# List of sensor node MAC addresses to pair with (strings or bytes)
SENSOR_MACS = [
    # "24:6F:28:AB:CD:EF",
]

wifi_manager = WifiManager()
espnow_manager = ESPNowManager()
myRTC = rtc.RTC()

if wifi_manager.connected:
    try:
        myRTC.datetime = wifi_manager.utc_time
        print("RTC synced with NTP time")
    except Exception as e:
        print("RTC Sync Error:", e)

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
