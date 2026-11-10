"""
Circuitpython sketch for the sensor node that uses the EspNowManager and Sensor_Node classes to format and send the data packets
"""

import asyncio
import rtc
from espnow_manager import ESPNowManager

espnow_manager = ESPNowManager()
myRTC = rtc.RTC()

# TODO: Sync sensor node RTC with hub over espNOW?


async def main():
    # Set up as sensor node and wait for hub pair message
    print("Waiting to pair with hub...")
    hub_mac = await espnow_manager.init_sensor_node()
    print(f"Paired with hub: {espnow_manager.mac_to_str(hub_mac)}")

    while True:
        # Check for incoming messages from the hub
        message = await espnow_manager.read_message(hub_mac, timeout_ms=100)
        if message:
            sender_mac, data = message
            print(f"Received from hub: {data}")

            if espnow_manager.is_message_type(data, espnow_manager.REQUEST_MESSAGE):
                # Placeholder sensor data packet (to be populated by Sensor_Node class)
                sensor_data = {
                    # e.g., "temp": ..., "humidity": ...
                }
                print("Sending sensor data to hub...")
                await espnow_manager.send_message(hub_mac, sensor_data)

        await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(main())
