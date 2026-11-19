"""
Simplified Sensor Node sketch for demoing ESP-NOW communication on channel 0 without Wi-Fi.

Waits to pair with the demo UI hub and replies to each incoming request packet
with a static sample JSON dictionary of sensor measurements.
"""

import asyncio
from espnow_manager import ESPNowManager

espnow_manager = ESPNowManager()

# Static mock sensor payload returned whenever the hub sends a REQUEST_MESSAGE
SAMPLE_SENSOR_DATA = {
    "type": "data",
    "temp_f": 72.5,
    "humidity": 45.2,
    "tvoc": 120.0,
    "eco2": 415.0,
    "aqi": 1,
}


async def main():
    """Pair with the demo hub on channel 0 and respond to incoming data requests."""
    print("Waiting to pair with hub on channel 0...")
    hub_mac = await espnow_manager.init_sensor_node()
    print(f"Paired with hub: {espnow_manager.mac_to_str(hub_mac)}")

    while True:
        # Poll inbox for requests from the paired hub
        message = await espnow_manager.read_message(hub_mac, timeout_ms=100)
        if message:
            sender_mac, data = message
            print(f"Received from hub ({espnow_manager.mac_to_str(sender_mac)}): {data}")

            if espnow_manager.is_message_type(data, espnow_manager.REQUEST_MESSAGE):
                response = dict(SAMPLE_SENSOR_DATA)
                print(f"Sending sample sensor data to hub: {response}")
                if await espnow_manager.send_message(hub_mac, response):
                    print("Sensor data confirmed by hub\n")
                else:
                    print("[!] Failed to send sensor data to hub (no ACK)\n")

        await asyncio.sleep(0.05)


if __name__ == "__main__":
    asyncio.run(main())
