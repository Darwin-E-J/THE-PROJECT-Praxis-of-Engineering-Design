"""
ESP-NOW Per-Channel Ping-Pong Packet Loss Test — Sensor Node.

Copy this file to the Sensor Node's CIRCUITPY drive as ``code.py`` alongside
``espnow_manager.py``. Pairs with the UI Hub on each Wi-Fi channel (1-11),
replies with a ``pong`` to every ``ping``, follows the hub to the next
channel, and prints per-channel results as CSV over serial.
"""

import asyncio
import wifi
from espnow_manager import ESPNowManager

HUB_TIMEOUT_MS = 4000  # Re-scan if hub goes silent (e.g. missed 'next' packet)
CSV_HEADER = "channel,hub_mac,pings_received,pongs_acked,status"

espnow_manager = ESPNowManager()
espnow_manager.PAIR_CHANNEL_WAIT_MS = 400  # Fast channel-hop sweep (~4.4s for ch 1-11)


async def main():
    # Disconnect from any Wi-Fi AP so channels 1-11 can be tuned freely
    if wifi.radio.connected:
        wifi.radio.enabled = False
        wifi.radio.enabled = True

    print(CSV_HEADER)

    while True:
        # Use normal retries while pairing on the current channel
        espnow_manager.MAX_RETRIES = ESPNowManager.MAX_RETRIES
        espnow_manager.ACK_TIMEOUT_MS = ESPNowManager.ACK_TIMEOUT_MS
        hub_mac = await espnow_manager.init_sensor_node()
        ch = espnow_manager._channel
        ch_str = "" if ch is None else ch
        hub_mac_str = espnow_manager.mac_to_str(hub_mac)

        # Disable retries during ping-pong so real packet loss is measured
        espnow_manager.MAX_RETRIES = 0
        espnow_manager.ACK_TIMEOUT_MS = 50
        pings_received = 0
        pongs_acked = 0

        while True:
            msg = await espnow_manager.read_message(hub_mac, timeout_ms=100)
            if msg:
                _, data = msg
                msg_type = data.get("type")

                if msg_type == "ping":
                    pings_received += 1
                    if await espnow_manager.send_message(
                        hub_mac, {"type": "pong", "seq": data.get("seq")}
                    ):
                        pongs_acked += 1

                elif msg_type == "next":
                    print(f"{ch_str},{hub_mac_str},{pings_received},{pongs_acked},done")
                    next_ch = data.get("next_ch")
                    espnow_manager._channel = (
                        next_ch if next_ch is not None else espnow_manager.MIN_CHANNEL
                    )
                    break

            since_rx = espnow_manager.time_since_last_rx_ms(hub_mac)
            if since_rx is not None and since_rx >= HUB_TIMEOUT_MS:
                print(f"{ch_str},{hub_mac_str},{pings_received},{pongs_acked},hub_timeout")
                espnow_manager._channel = (
                    espnow_manager.MIN_CHANNEL
                    if ch is None or ch >= espnow_manager.MAX_CHANNEL
                    else ch + 1
                )
                break


if __name__ == "__main__":
    asyncio.run(main())
