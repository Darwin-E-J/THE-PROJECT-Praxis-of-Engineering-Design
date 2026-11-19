"""
CircuitPython entry point for the ESP32-S3 air-quality sensor node.

Initializes the SI7021 (climate), ENS160 (gas/TVOC/eCO2/AQI), and PMSA003I
(particulate matter) I2C sensors, pairs with the UI hub over ESP-NOW, replies
to hub data requests, and automatically re-pairs if the hub link is lost.
"""

import time
import json
import board
import busio
import wifi
import espnow

# Hardware sensor drivers contained in CIRCUITPY/lib/
import adafruit_si7021
import adafruit_ens160
from adafruit_pm25.i2c import PM25_I2C

import asyncio
import rtc
from espnow_manager import ESPNowManager

espnow_manager = ESPNowManager()
myRTC = rtc.RTC()

# TODO: Sync sensor node RTC with hub over ESP-NOW


class ClimateSensor:
    """Interfaces with the SI7021 temperature and relative humidity sensor (I2C 0x40)."""

    def __init__(self, i2c: busio.I2C, address: int = 0x40):
        """Initialize the SI7021 on ``i2c`` and record whether the sensor is available."""
        self.available = False
        try:
            self.sensor = adafruit_si7021.SI7021(i2c, address=address)
            self.available = True
            print(f"[+] Successfully initialized Climate Sensor(SI7021) at 0x40")
        except Exception as err:
            print(f"[!] Climate Sensor init error: {err}")

    def read_climate(self) -> tuple[float, float, bool]:
        """
        Read temperature (Fahrenheit) and relative humidity (%RH).

        Returns:
            tuple[float, float, bool]: ``(temp_f, humidity_rh, success_flag)``.
        """
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
    """Interfaces with the ENS160 MOX gas sensor for TVOC, eCO2, and AQI (I2C 0x53)."""

    def __init__(self, i2c: busio.I2C, address: int = 0x53):
        """Initialize the ENS160 on ``i2c`` and record whether the sensor is available."""
        self.available = False
        try:
            self.sensor = adafruit_ens160.ENS160(i2c, address=address)
            self.available = True
            print(f"[+] Successfully initialized GasSensor (ENS160) at 0x53")
        except Exception as err:
            print(f"[!] Gas Sensor init error: {err}")

    def calibrate_and_read(self, temp_f: float, humidity_rh: float) -> tuple[float, float, int, bool]:
        """
        Apply temperature/humidity compensation and read gas metrics.

        Args:
            temp_f: Ambient temperature in Fahrenheit for sensor compensation.
            humidity_rh: Ambient relative humidity (%RH) for sensor compensation.

        Returns:
            tuple[float, float, int, bool]: ``(tvoc_ppb, eco2_ppm, aqi, success_flag)``.
        """
        if not self.available:
            return 0.0, 0.0, 0, False
        try:
            # Convert Fahrenheit to Celsius for the ENS160 compensation registers
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


class ParticleSensor:
    """Interfaces with the PMSA003I optical particle counter (I2C 0x12)."""

    def __init__(self, i2c: busio.I2C, address: int = 0x12):
        """Initialize the PMSA003I on ``i2c`` and record whether the sensor is available."""
        self.available = False
        try:
            self.sensor = PM25_I2C(i2c, reset_pin=None, address=address)
            self.available = True
            print(f"[+] Successfully initialized Particle Sensor (PMSA003I) at 0x12")
        except Exception as err:
            print(f"[!] Particle Sensor init error: {err}")

    def read_particles(self) -> tuple[dict, bool]:
        """
        Read a particulate-matter measurement frame from the PMSA003I.

        Returns:
            tuple[dict, bool]: ``(pm_data_dict, success_flag)``.
        """
        if not self.available:
            return {}, False
        try:
            reading = self.sensor.read()
            return reading, True
        except Exception as err:
            print(f"[!] Particle Sensor read error: {err}")
            return {}, False


def _read_sensor_payload(
    climate: ClimateSensor | None,
    gas: GasSensor | None,
    particle: ParticleSensor | None,
) -> dict:
    """
    Sample all initialized sensors and build a JSON-serializable ESP-NOW data packet.

    Falls back to nominal room conditions (68 F, 50% RH) for ENS160 compensation
    if the climate sensor is unavailable, and encodes unavailable fields as None.
    """
    temp_f, humidity, climate_ok = climate.read_climate() if climate else (0.0, 0.0, False)
    # Use live climate data when available; otherwise fall back to room-temp defaults
    calib_temp, calib_rh = (temp_f, humidity) if climate_ok else (68.0, 50.0)
    tvoc, eco2, aqi, gas_ok = gas.calibrate_and_read(calib_temp, calib_rh) if gas else (0.0, 0.0, 0, False)
    pm_data, particle_ok = particle.read_particles() if particle else ({}, False)

    return {
        "type": "data",
        "temp_f": temp_f if climate_ok else None,
        "humidity": humidity if climate_ok else None,
        "tvoc": tvoc if gas_ok else None,
        "eco2": eco2 if gas_ok else None,
        "aqi": aqi if gas_ok else None,
        "pm1_0": float(pm_data.get("pm10 env", 0.0)) if particle_ok else None,
        "pm2_5": float(pm_data.get("pm25 env", 0.0)) if particle_ok else None,
        "pm10_0": float(pm_data.get("pm100 env", 0.0)) if particle_ok else None,
    }


# Disconnect detection thresholds for triggering automatic re-pairing
HUB_TIMEOUT_MS = 30000  # Re-pair if no valid hub traffic is received within 30s
MAX_SEND_FAILURES = 2   # Re-pair after 2 consecutive unacknowledged replies


async def main():
    """Initialize I2C sensors, pair with the UI hub, and serve data requests forever."""
    climate = None
    gas = None
    particle = None
    try:
        i2c = busio.I2C(board.SCL, board.SDA)
        climate = ClimateSensor(i2c)
        gas = GasSensor(i2c)
        particle = ParticleSensor(i2c)
    except Exception as err:
        print(f"[!] I2C init error: {err}")

    # Set up as sensor node and wait for hub pair message
    print("Waiting to pair with hub...")
    hub_mac = await espnow_manager.init_sensor_node()
    print(f"Paired with hub: {espnow_manager.mac_to_str(hub_mac)}")
    failed_sends = 0

    while True:
        # Only reply to the UI hub when a request packet is received
        message = await espnow_manager.read_message(hub_mac, timeout_ms=100)
        if message:
            sender_mac, data = message
            print(f"Received from hub: {data}")

            if espnow_manager.is_message_type(data, espnow_manager.REQUEST_MESSAGE):
                sensor_data = _read_sensor_payload(climate, gas, particle)
                print(f"Sending sensor data to hub: {sensor_data}")
                if await espnow_manager.send_message(hub_mac, sensor_data):
                    failed_sends = 0
                else:
                    failed_sends += 1
                    print(f"[!] Failed to send sensor data to hub ({failed_sends}/{MAX_SEND_FAILURES})")

        # Re-pair if the hub goes silent (e.g. rebooted on a new channel) or ACKs fail repeatedly
        since_rx_ms = espnow_manager.time_since_last_rx_ms(hub_mac)
        timed_out = since_rx_ms is not None and since_rx_ms >= HUB_TIMEOUT_MS

        if timed_out or failed_sends >= MAX_SEND_FAILURES:
            if timed_out:
                print(f"[!] No communication from hub for {HUB_TIMEOUT_MS // 1000}s, re-pairing...")
            else:
                print(f"[!] Lost connection to hub after {failed_sends} failed sends, re-pairing...")

            hub_mac = await espnow_manager.repair_sensor_node()
            print(f"Re-paired with hub: {espnow_manager.mac_to_str(hub_mac)}")
            failed_sends = 0

        await asyncio.sleep(0.05)


if __name__ == "__main__":
    asyncio.run(main())

