"""
ESP-NOW Manager with fixes for channel alignment and pairing timeouts.
"""

import json
import wifi
import espnow

try:
    import asyncio
except ImportError:
    raise ImportError("Requires asyncio library from Adafruit Bundle.")

try:
    import adafruit_ticks
except ImportError:
    raise ImportError("Requires adafruit_ticks library from Adafruit Bundle.")


class ESPNowManager:
    PACKET_ID_KEY = "id"
    ACK_KEY = "ack"
    ACK_TIMEOUT_MS = 500
    MAX_RETRIES = 5

    MIN_CHANNEL = 1
    MAX_CHANNEL = 11
    PAIR_CHANNEL_WAIT_MS = 1000

    PAIR_MESSAGE = {"type": "pair"}
    REQUEST_MESSAGE = {"type": "request"}

    MESSAGE_TYPES = [PAIR_MESSAGE, REQUEST_MESSAGE]
    message_types = MESSAGE_TYPES
    MAX_INBOX = 20

    def __init__(self):
        self._espnow = None
        self._packet_number = 0
        self._expected_packet = {}
        self._acked_packets = set()
        self._inbox = []
        self._auto_add_peers = False
        self._hub_mac = None
        self._receive_task = None
        self._peers = {}

    @property
    def hub_mac(self):
        return self._hub_mac

    @staticmethod
    def mac_to_bytes(mac):
        if isinstance(mac, bytes):
            return mac
        mac = mac.replace(":", "").replace("-", "").replace(" ", "")
        return bytes.fromhex(mac)

    @staticmethod
    def mac_to_str(mac_bytes):
        return ":".join(f"{b:02x}" for b in mac_bytes)

    @staticmethod
    def is_message_type(packet_data: dict, message_type: dict) -> bool:
        if not isinstance(packet_data, dict) or not isinstance(message_type, dict):
            return False
        return all(packet_data.get(k) == v for k, v in message_type.items())

    def start(self):
        if self._espnow is None:
            wifi.radio.enabled = True
            self._espnow = espnow.ESPNow()
            print("[+] ESP-NOW subsystem started")
        if self._receive_task is None:
            self._receive_task = asyncio.create_task(self.receive_messages())

    def stop(self):
        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None
        if self._espnow:
            self._espnow.deinit()
            self._espnow = None
            print("[-] ESP-NOW stopped")

    def add_peer(self, mac):
        mac_bytes = self.mac_to_bytes(mac)
        if mac_bytes in self._peers:
            return
        try:
            peer = espnow.Peer(mac=mac_bytes)
            self._espnow.peers.append(peer)
            self._peers[mac_bytes] = peer
            print(f"[+] Peer {self.mac_to_str(mac_bytes)} added")
        except Exception as e:
            print(f"[!] Failed to add peer: {e}")

    async def init_hub(self, sensor_macs: list, timeout_sec: int = 15) -> list:
        """
        Continuously broadcasts pair messages across a specified duration
        to allow channel-hopping nodes time to align channels.
        """
        self.start()
        paired = []
        target_macs = [self.mac_to_bytes(m) for m in sensor_macs]

        for mac_bytes in target_macs:
            self.add_peer(mac_bytes)

        start_time = adafruit_ticks.ticks_ms()
        timeout_ms = timeout_sec * 1000

        print(f"[*] Hub discovery active on Wi-Fi Channel {wifi.radio.channel}...")

        while len(paired) < len(target_macs):
            if adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start_time) > timeout_ms:
                break

            for mac_bytes in target_macs:
                if mac_bytes not in paired:
                    # Attempt pair send with reduced retry count per loop iteration
                    if await self.send_message(mac_bytes, self.PAIR_MESSAGE):
                        paired.append(mac_bytes)
                        print(f"[✓] Successfully paired with {self.mac_to_str(mac_bytes)}")

            await asyncio.sleep(0.2)

        return paired

    def _set_channel(self, channel: int) -> bool:
        try:
            wifi.radio.start_ap(" ", "", channel=channel, max_connections=0)
            wifi.radio.stop_ap()
            return True
        except Exception:
            return False

    async def init_sensor_node(self) -> bytes:
        self.start()
        self._auto_add_peers = True

        print(f"[*] Listening for Hub pair message on active channel ({wifi.radio.channel})...")
        channel = self.MIN_CHANNEL

        while self._hub_mac is None:
            # Hop channels only if pairing is taking too long
            self._set_channel(channel)
            start = adafruit_ticks.ticks_ms()
            
            while self._hub_mac is None and adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start) < self.PAIR_CHANNEL_WAIT_MS:
                await asyncio.sleep(0.01)

            channel = self.MIN_CHANNEL if channel >= self.MAX_CHANNEL else channel + 1

        return self._hub_mac

    async def send_message(self, mac: str | bytes, message: dict) -> bool:
        mac_bytes = self.mac_to_bytes(mac)
        if mac_bytes not in self._peers:
            return False

        packet_id = self._packet_number
        packet = self._add_packet_id(message)
        self._packet_number += 1

        data = json.dumps(packet).encode("utf-8")
        peer = self._peers[mac_bytes]

        retries = 0
        while retries <= self.MAX_RETRIES:
            try:
                self._espnow.send(data, peer)
            except Exception as e:
                print(f"[!] Send error: {e}")

            if await self._wait_for_ack(packet_id):
                return True

            retries += 1
            await asyncio.sleep(0.02)

        return False

    async def read_message(self, mac: str | bytes | None = None, timeout_ms: int = 0) -> tuple | None:
        mac_bytes = self.mac_to_bytes(mac) if mac is not None else None
        start = adafruit_ticks.ticks_ms()

        while True:
            for i, (sender, data) in enumerate(self._inbox):
                if mac_bytes is None or sender == mac_bytes:
                    return self._inbox.pop(i)

            if adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start) >= timeout_ms:
                return None
            await asyncio.sleep(0.01)

    async def receive_messages(self):
        """Continuously reads incoming packets with low latency (10ms poll rate)."""
        while True:
            if self._espnow:
                packet = self._espnow.read()
                if packet:
                    mac = packet.mac
                    msg = packet.msg
                    data = self._handle_packet(mac, msg)

                    if data is not None:
                        if len(self._inbox) >= self.MAX_INBOX:
                            self._inbox.pop(0)
                        self._inbox.append((mac, data))
            # Reduced sleep interval to fix ACK timeout lag
            await asyncio.sleep(0.01)

    def _add_packet_id(self, message: dict) -> dict:
        packet = {self.PACKET_ID_KEY: self._packet_number}
        if isinstance(message, dict):
            packet.update(message)
        else:
            packet["type"] = str(message)
        return packet

    async def _wait_for_ack(self, packet_id: int) -> bool:
        start = adafruit_ticks.ticks_ms()
        while adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start) < self.ACK_TIMEOUT_MS:
            if packet_id in self._acked_packets:
                self._acked_packets.remove(packet_id)
                return True
            await asyncio.sleep(0.01)
        return False

    def _send_ack(self, mac_bytes: bytes, packet_id: int) -> None:
        if mac_bytes not in self._peers:
            return
        ack = {self.PACKET_ID_KEY: packet_id, self.ACK_KEY: True}
        try:
            self._espnow.send(json.dumps(ack).encode("utf-8"), self._peers[mac_bytes])
        except Exception:
            pass

    def _handle_packet(self, mac: bytes, msg: bytes) -> dict | None:
        try:
            data = json.loads(msg.decode("utf-8"))
            packet_id = data[self.PACKET_ID_KEY]
        except (ValueError, KeyError, TypeError):
            return None

        if data.get(self.ACK_KEY):
            self._acked_packets.add(packet_id)
            return None

        if self.is_message_type(data, self.PAIR_MESSAGE):
            if self._auto_add_peers and self._hub_mac is None:
                self.add_peer(mac)
                self._hub_mac = mac
                self._auto_add_peers = False
                self._send_ack(mac, packet_id)
                self._expected_packet[mac] = packet_id + 1
                return None
            elif mac in self._peers:
                self._send_ack(mac, packet_id)
                return None
            else:
                return None

        if mac not in self._peers:
            return None

        self._send_ack(mac, packet_id)
        expected = self._expected_packet.get(mac)
        peer_restarted = packet_id == 0 and expected != 1

        if expected is not None and not peer_restarted:
            if packet_id < expected:
                return None

        self._expected_packet[mac] = packet_id + 1
        return data