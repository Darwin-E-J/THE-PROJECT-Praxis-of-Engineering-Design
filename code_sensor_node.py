"""
Circuitpython sketch for the sensor node that uses the EspNowManager and Sensor_Node classes to format and send the data packets
"""
import time
import json
import board
import busio
import wifi
import espnow

# Hardware sensor drivers contained in CIRCUITPY/LIB/
import adafruit_si7021
import adafruit_pm25
import adafruit_ens160

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
# Get sensor data from I2C

class ClimateSensor:
    """Interfaces with SI7021 Temperature & Humidity Sensor (0x40)"""
    def __init__(self, i2c: busio.I2C, address: int = 0x40):
        self.available = False
        try:
            self.sensor = adafruit_si7021.SI7021(i2c, address=address)
            self.available = True
            print(f"[+] Successfully initialized Climate Sensor(SI7021) at 0x40")
        except Exception as err:
            print(f"[!] Climate Sensor init error: {err}")

    def read_climate(self) -> tuple[float, float, bool]:
        """Returns (temp_f, humidity_rh, success_flag)"""
        if not self.available:
            return 0.0, 0.0, False
        try:
            temp_c = self.sensor.temperature
            temp_f = (temp_c * 9.0 / 5.0) + 32
            humidity = float(self.sensor.relative_humidity)
            return round(temp_f, 2), round(humidity, 2), True
        except Exception as err:
            print(f"[!] Climate Sensor read error: {err}")
            return 0.0, 0.0, False

class GasSensor:
    """Interfaces with ENS160 MOX Sensor (0x53)"""
    def __init__(self, i2c: busio.I2C, address: int = 0x53):
        self.available = False
        try:
            self.sensor = adafruit_ens160.ENS160(i2c, address=address)
            self.available = True
            print(f"[+] Successfully initialized GasSensor (ENS160) at 0x53")
        except Exception as err:
            print(f"[!] Gas Sensor init error: {err}")
            
    def calibrate_and_read(self, temp_f: float, humidity_rh: float) -> tuple[float, float, int, bool]:
        """Passes climate calibration values and returns (TVOC, eCO2, AQI, success_flag)."""
        if not self.available:
            return 0.0, 0.0, 0, False
        try:
            # Convert F to C for compensation registers
            temp_c = (temp_f - 32.0) * (5.0 / 9.0)
            self.sensor.temperature_compensation = temp_c
            self.sensor.humidity_compensation = humidity_rh

            tvoc = float(self.sensor.TVOC)
            eco2 = float(self.sensor.eCO2)
            aqi = int(self.sensor.AQI)
            return tvoc, eco2, aqi, True
        except Exception as err:
            print(f"[!] GasSensor read error: {err}")
            return 0.0, 0.0, 0, False
        
        
# Format into json
# Send with espnow manager
