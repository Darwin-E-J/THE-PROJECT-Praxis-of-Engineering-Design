"""
ESP-NOW Per-Channel Ping-Pong Packet Loss Test — UI Hub.

Copy this file to the UI Hub's CIRCUITPY drive as ``code.py`` alongside
``espnow_manager.py``. For each Wi-Fi channel (1-11), pairs with the Sensor
Node, sends a user-specified number of single-shot ping-pong packets, outputs
packet loss as CSV over serial, tunes to the next channel, and repeats.
"""

import asyncio
import wifi
from espnow_manager import ESPNowManager

# How many packet tests per wifi channel
PACKET_COUNT = 1000

CHANNELS = tuple(range(ESPNowManager.MIN_CHANNEL, ESPNowManager.MAX_CHANNEL + 1))
PAIR_TIMEOUT_SEC = 15
PING_TIMEOUT_MS = 200
PING_DELAY_SEC = 0.02
CSV_HEADER = "channel,paired,sensor_mac,sent,received,lost,loss_pct"

espnow_manager = ESPNowManager()


def format_csv_row(
    ch: int, paired: bool, sensor_mac: str, sent: int, received: int, lost: int
) -> str:
    """Format a single channel test result as a CSV line."""
    loss_pct = (lost / sent) * 100.0 if sent else 0.0
    return f"{ch},{paired},{sensor_mac},{sent},{received},{lost},{loss_pct:.2f}"


async def test_channel(
    ch: int, next_ch, packet_count: int
) -> tuple[bool, str, int, int]:
    """Tune to ``ch``, pair, run ``packet_count`` ping-pongs, and return results."""
    # Reset peer state and tune radio to the target channel
    for mac in list(espnow_manager._peers):
        espnow_manager.remove_peer(mac)
    espnow_manager._acked_packets.clear()
    espnow_manager._reset_espnow_on_channel(ch)

    # Pair with the sensor node using normal retries
    espnow_manager.MAX_RETRIES = ESPNowManager.MAX_RETRIES
    espnow_manager.ACK_TIMEOUT_MS = ESPNowManager.ACK_TIMEOUT_MS
    paired = await espnow_manager.init_hub([""], timeout_sec=PAIR_TIMEOUT_SEC)

    if not paired:
        print(format_csv_row(ch, False, "", packet_count, 0, packet_count))
        return False, "", 0, packet_count

    sensor_mac = paired[0]
    sensor_mac_str = espnow_manager.mac_to_str(sensor_mac)

    # Single-shot ping-pong (no retries) to measure actual packet loss
    espnow_manager.MAX_RETRIES = 0
    espnow_manager.ACK_TIMEOUT_MS = PING_TIMEOUT_MS

    received = 0
    for seq in range(packet_count):
        espnow_manager._inbox.clear()
        if await espnow_manager.send_message(sensor_mac, {"type": "ping", "seq": seq}):
            reply = await espnow_manager.read_message(sensor_mac, timeout_ms=PING_TIMEOUT_MS)
            if reply and reply[1].get("type") == "pong" and reply[1].get("seq") == seq:
                received += 1
        await asyncio.sleep(PING_DELAY_SEC)

    lost = packet_count - received
    print(format_csv_row(ch, True, sensor_mac_str, packet_count, received, lost))

    # Tell sensor node to tune to the next channel
    espnow_manager.MAX_RETRIES = ESPNowManager.MAX_RETRIES
    espnow_manager.ACK_TIMEOUT_MS = ESPNowManager.ACK_TIMEOUT_MS
    await espnow_manager.send_message(sensor_mac, {"type": "next", "next_ch": next_ch})

    return True, sensor_mac_str, received, lost


async def main():
    # Disconnect from any Wi-Fi AP so channels 1-11 can be tuned freely
    if wifi.radio.connected:
        wifi.radio.enabled = False
        wifi.radio.enabled = True

    espnow_manager.start()
    print(CSV_HEADER)

    results = []
    for i, ch in enumerate(CHANNELS):
        next_ch = CHANNELS[i + 1] if i + 1 < len(CHANNELS) else None
        paired, sensor_mac_str, received, lost = await test_channel(ch, next_ch, PACKET_COUNT)
        results.append((ch, paired, sensor_mac_str, PACKET_COUNT, received, lost))

    espnow_manager.stop()

    print("\n--- CSV START ---")
    print(CSV_HEADER)
    for row in results:
        print(format_csv_row(*row))
    print("--- CSV END ---")


if __name__ == "__main__":
    asyncio.run(main())
