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



# ======================================================================
# TEST EXECUTION LOOP
# ======================================================================
def main():
    print("\n--- Initializing I2C Bus & Testing First Two Classes ---")
    i2c = busio.I2C(board.SCL, board.SDA)

    climate = ClimateSensor(i2c)
    gas = GasSensor(i2c)

    print("\nStarting test read loop (press Ctrl+C to stop)...\n")

    sample_count = 0
    while True:
        sample_count += 1
        print(f"--- Reading #{sample_count} ---")

        # Step 1: Read Climate Sensor
        temp_f, humidity, climate_ok = climate.read_climate()
        if climate_ok:
            print(f"Climate  | Temp: {temp_f} °F | Humidity: {humidity} %RH")
            calib_temp, calib_rh = temp_f, humidity
        else:
            print("Climate  | Reading Failed! Using fallback calibration (68°F, 50%RH)")
            calib_temp, calib_rh = 68.0, 50.0

        # Step 2: Calibrate & Read Gas Sensor using climate data
        tvoc, eco2, aqi, gas_ok = gas.calibrate_and_read(calib_temp, calib_rh)
        if gas_ok:
            print(f"Gas      | TVOC: {tvoc} ppb | eCO2: {eco2} ppm | AQI: {aqi}")
        else:
            print("Gas      | Reading Failed!")

        print("-" * 40)
        time.sleep(3.0)

if __name__ == "__main__":
    main()







# class ParticleSensor:
#     """Interfaces with PMSA003I Optical Particle Counter (0x12)"""
#     def __init__(self, i2c:busio.I2C, address: int = 0x12):
#         self.available = False
#         try:
#             self.sensor = PM
        
        
# # Format into json
# # Send with espnow manager
