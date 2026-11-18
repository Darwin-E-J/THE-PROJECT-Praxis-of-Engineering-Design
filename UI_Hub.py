"""
This is the UI_Hub class which controls all of the data formatting and display.

"""


import time
import board
import displayio
import terminalio
from adafruit_display_text import label
from adafruit_display_shapes.rect import Rect
from adafruit_st7789 import ST7789

try:
    from fourwire import FourWire
except ImportError:
    from displayio import FourWire

# --- UPDATE THESE 3 PINS TO MATCH FREE GPIOS FROM dir(board) ---
CS_PIN = board.D6   # Do NOT use board.D5 (reserved for 24-LED NeoPixel ring)
DC_PIN = board.D9
RST_PIN = board.D10


class DisplayManager:
    BG_COLOR = 0x0F172A
    CARD_BG = 0x1E293B
    TEXT_PRIMARY = 0xF8FAFC
    TEXT_MUTED = 0x94A3B8
    STATUS_GOOD = 0x166534
    STATUS_MOD = 0xB45309
    STATUS_BAD = 0x991B1B

    def __init__(self, spi_bus, cs, dc, rst, rotation=90):
        displayio.release_displays()
        display_bus = FourWire(spi_bus, command=dc, chip_select=cs, reset=rst)
        self.display = ST7789(display_bus, width=320, height=240, rotation=rotation)

        # Allocate displayio groups/labels once at startup to prevent MemoryError
        self.root = displayio.Group()
        self.display.root_group = self.root

        self.root.append(Rect(0, 0, 320, 240, fill=self.BG_COLOR))
        self.root.append(Rect(0, 0, 320, 34, fill=self.CARD_BG))

        self.room_label = label.Label(
            terminalio.FONT, text="LIVING ROOM", color=self.TEXT_PRIMARY, scale=2, x=10, y=16
        )
        self.status_label = label.Label(
            terminalio.FONT, text="ONLINE", color=self.TEXT_MUTED, scale=1, x=255, y=16
        )
        self.root.append(self.room_label)
        self.root.append(self.status_label)

        self.banner_rect = Rect(6, 38, 308, 46, fill=self.STATUS_GOOD)
        self.banner_title = label.Label(
            terminalio.FONT, text="AIR QUALITY: GOOD", color=self.TEXT_PRIMARY, scale=1, x=16, y=50
        )
        self.prompt_label = label.Label(
            terminalio.FONT, text="Air is clean - no action needed", color=self.TEXT_PRIMARY, scale=1, x=16, y=68
        )
        self.root.append(self.banner_rect)
        self.root.append(self.banner_title)
        self.root.append(self.prompt_label)

        self.temp_val = self._make_card(6, 90, "TEMP (F)")
        self.hum_val = self._make_card(164, 90, "HUMIDITY (%RH)")
        self.tvoc_val = self._make_card(6, 164, "TVOC (ppb)")
        self.pm25_val = self._make_card(164, 164, "PM2.5 (ug/m3)")

    def _make_card(self, x, y, title):
        self.root.append(Rect(x, y, 150, 68, fill=self.CARD_BG))
        self.root.append(
            label.Label(terminalio.FONT, text=title, color=self.TEXT_MUTED, scale=1, x=x + 10, y=y + 14)
        )
        val = label.Label(terminalio.FONT, text="--", color=self.TEXT_PRIMARY, scale=2, x=x + 10, y=y + 42)
        self.root.append(val)
        return val

    def render_dashboard(self, location, metrics):
        self.room_label.text = location.upper()
        self.temp_val.text = f"{metrics.get('Temp (F)', 0.0):.1f} F"
        self.hum_val.text = f"{metrics.get('Humidity (%RH)', 0.0):.1f} %"
        tvoc_ppb = metrics.get("TVOC (ppb)", 0.0)
        self.tvoc_val.text = f"{tvoc_ppb:.0f}"
        self.pm25_val.text = f"{metrics.get('PM 2.5 (ug/m3)', 0.0):.1f}"

        tvoc_ppm = tvoc_ppb / 1000.0
        if tvoc_ppm >= 0.66:
            self.banner_rect.fill = self.STATUS_BAD
            self.banner_title.text = "AIR QUALITY: BAD"
            self.prompt_label.text = "Open a window & turn on fan!"
        elif tvoc_ppm > 0.22:
            self.banner_rect.fill = self.STATUS_MOD
            self.banner_title.text = "AIR QUALITY: MODERATE"
            self.prompt_label.text = "Consider opening a window"
        else:
            self.banner_rect.fill = self.STATUS_GOOD
            self.banner_title.text = "AIR QUALITY: GOOD"
            self.prompt_label.text = "Air is clean - no action needed"


# --- Bring-Up Verification ---
spi = board.SPI()
ui = DisplayManager(spi, cs=CS_PIN, dc=DC_PIN, rst=RST_PIN, rotation=90)

ui.render_dashboard(
    "Living Room",
    {"Temp (F)": 72.4, "Humidity (%RH)": 44.1, "TVOC (ppb)": 18.0, "PM 2.5 (ug/m3)": 4.2},
)

while True:
    time.sleep(1)
