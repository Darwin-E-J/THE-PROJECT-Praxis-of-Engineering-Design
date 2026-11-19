"""
Simplified UI Hub sketch for demoing ESP-NOW communication on channel 0 without Wi-Fi.

Pairs with a static list of sensor node MAC addresses and periodically requests
sample sensor readings every 5 seconds.
"""

import asyncio
from espnow_manager import ESPNowManager

# List of sensor node MAC addresses to pair with (strings or bytes)
SENSOR_MACS = ["28-37-2F-8C-B7-E8"]

espnow_manager = ESPNowManager()


async def main():
    """Pair with configured demo sensor nodes and poll them every 5 seconds."""
    print("Initializing ESP-NOW hub on channel 0 (no WiFi)...")
    paired_sensors = await espnow_manager.init_hub(SENSOR_MACS)
    print(f"Paired sensors: {[espnow_manager.mac_to_str(m) for m in paired_sensors]}")

    while True:
        # Poll each paired sensor node and print its response
        for mac in paired_sensors:
            mac_str = espnow_manager.mac_to_str(mac)
            print(f"Requesting data from {mac_str}...")
            if await espnow_manager.send_message(mac, espnow_manager.REQUEST_MESSAGE):
                print(f"Request confirmed by {mac_str}, waiting for data reply...")
                reply = await espnow_manager.read_message(mac, timeout_ms=2000)
                if reply:
                    sender_mac, data = reply
                    print(f"Received from {espnow_manager.mac_to_str(sender_mac)}: {data}\n")
                else:
                    print(f"No response from {mac_str}\n")
            else:
                print(f"Failed to send request to {mac_str} (no ACK)\n")

        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
