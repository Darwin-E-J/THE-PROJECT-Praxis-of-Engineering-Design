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
    PAIR_CHANNEL_WAIT_MS = 1200
    PAIR_SEND_INTERVAL_MS = 100
    PAIR_TIMEOUT_SEC = 30

    PAIR_MESSAGE = {"type": "pair"}
    SUCCESSFUL_PAIR_MESSAGE = {"type": "successful_pair"}
    REQUEST_MESSAGE = {"type": "request"}

    MESSAGE_TYPES = [PAIR_MESSAGE, SUCCESSFUL_PAIR_MESSAGE, REQUEST_MESSAGE]
    message_types = MESSAGE_TYPES
    MAX_INBOX = 20

    def __init__(self):
        self._espnow = None
        self._packet_number = 0
        self._expected_packet = {}
        self._acked_packets = set()
        self._paired_sensors = set()
        self._inbox = []
        self._auto_add_peers = False
        self._hub_mac = None
        self._receive_task = None
        self._peers = {}
        self._channel = None
        self._sending_pair_success = False

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
            if not wifi.radio.enabled:
                wifi.radio.enabled = True
            self._espnow = espnow.ESPNow()
            print("[+] ESP-NOW subsystem started")
        if self._receive_task is None:
            self._receive_task = asyncio.create_task(self.receive_messages())

    def stop(self):
        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None
        if self._espnow is not None:
            self._espnow.deinit()
            self._espnow = None
            print("[-] ESP-NOW stopped")

    def add_peer(self, mac, channel=0):
        mac_bytes = self.mac_to_bytes(mac)
        if mac_bytes in self._peers:
            return
        try:
            peer = espnow.Peer(mac=mac_bytes, channel=channel)
            self._espnow.peers.append(peer)
            self._peers[mac_bytes] = peer
            print(f"[+] Peer {self.mac_to_str(mac_bytes)} added (ch {channel})")
        except Exception as e:
            print(f"[!] Failed to add peer: {e}")

    def remove_peer(self, mac):
        mac_bytes = self.mac_to_bytes(mac)
        if mac_bytes not in self._peers:
            return
        try:
            self._espnow.peers.remove(self._peers[mac_bytes])
            del self._peers[mac_bytes]
            self._expected_packet.pop(mac_bytes, None)
            print(f"[-] Peer {self.mac_to_str(mac_bytes)} removed")
        except Exception as e:
            print(f"[!] Failed to remove peer: {e}")

    async def init_hub(self, sensor_macs: list, timeout_sec: int = PAIR_TIMEOUT_SEC) -> list:
        """
        Continuously sends pair messages to all unpaired sensors until every
        expected sensor sends SUCCESSFUL_PAIR_MESSAGE or timeout_sec expires.
        """
        self.start()
        self._paired_sensors.clear()
        channel = wifi.radio.ap_info.channel if wifi.radio.ap_info is not None else self._channel

        pending = {}  # mac_bytes -> (packet_id, encoded_pair_bytes)
        for mac in sensor_macs:
            mac_bytes = self.mac_to_bytes(mac)
            if mac_bytes in pending:
                continue
            self.add_peer(mac_bytes)
            if mac_bytes not in self._peers:
                continue
            packet_id = self._packet_number
            packet = self._add_packet_id(self.PAIR_MESSAGE)
            self._packet_number += 1
            pending[mac_bytes] = (packet_id, json.dumps(packet).encode("utf-8"))

        total_sensors = len(pending)
        paired = []
        start_time = adafruit_ticks.ticks_ms()
        timeout_ms = int(timeout_sec * 1000)

        ch_label = channel if channel is not None else "unknown"
        print(f"[*] Hub discovery active on Wi-Fi Channel {ch_label} (timeout: {timeout_sec}s)...")

        while pending and adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start_time) < timeout_ms:
            for mac_bytes, (_, data) in pending.items():
                try:
                    self._espnow.send(data, self._peers[mac_bytes])
                except Exception as e:
                    print(f"[!] Pair send error to {self.mac_to_str(mac_bytes)}: {e}")

            round_start = adafruit_ticks.ticks_ms()
            while pending and adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), round_start) < self.PAIR_SEND_INTERVAL_MS:
                for mac_bytes in list(pending):
                    packet_id, _ = pending[mac_bytes]
                    if mac_bytes in self._paired_sensors:
                        self._acked_packets.discard(packet_id)
                        del pending[mac_bytes]
                        paired.append(mac_bytes)
                        print(f"[✓] Successfully paired with {self.mac_to_str(mac_bytes)}")

                if not pending or adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start_time) >= timeout_ms:
                    break
                await asyncio.sleep(0.01)

        for mac_bytes in pending:
            print(f"[!] Timed out pairing with {self.mac_to_str(mac_bytes)}")

        print(f"[*] Hub paired with {len(paired)}/{total_sensors} sensors")
        return paired

    def _set_channel(self, channel: int) -> bool:
        try:
            wifi.radio.start_ap(" ", "", channel=channel, max_connections=0)
            wifi.radio.stop_ap()
            return True
        except Exception as e:
            print(f"[!] Failed to set channel {channel}: {e}")
            return False

    def _drain_rx(self) -> None:
        if self._espnow is None:
            return
        while self._espnow:
            packet = self._espnow.read()
            if packet:
                mac = packet.mac
                msg = packet.msg
                data = self._handle_packet(mac, msg)

                if data is not None:
                    if len(self._inbox) >= self.MAX_INBOX:
                        self._inbox.pop(0)
                    self._inbox.append((mac, data))

    async def _send_successful_pair(self, mac_bytes: bytes) -> bool:
        if self._sending_pair_success:
            return False
        self._sending_pair_success = True
        try:
            payload = dict(self.SUCCESSFUL_PAIR_MESSAGE)
            payload["mac"] = self.mac_to_str(wifi.radio.mac_address)
            return await self.send_message(mac_bytes, payload)
        finally:
            self._sending_pair_success = False

    async def init_sensor_node(self) -> bytes:
        self._auto_add_peers = True
        if not wifi.radio.enabled:
            wifi.radio.enabled = True

        print(f"[*] Waiting for Hub pair message (channel hopping {self.MIN_CHANNEL}-{self.MAX_CHANNEL})...")
        channel = self.MIN_CHANNEL

        while self._hub_mac is None:
            # Re-create ESPNow after AP start/stop so the radio restarts STA RX on the new channel.
            if self._espnow is not None:
                try:
                    self._espnow.deinit()
                except Exception as e:
                    print(f"[!] ESPNow deinit warning: {e}")
                self._espnow = None

            if self._set_channel(channel):
                self._channel = channel
            self._espnow = espnow.ESPNow()

            if self._receive_task is None:
                self._receive_task = asyncio.create_task(self.receive_messages())

            print(f"[*] Listening for Hub pair message on channel {channel}...")
            start = adafruit_ticks.ticks_ms()
            while (
                self._hub_mac is None
                and adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start) < self.PAIR_CHANNEL_WAIT_MS
            ):
                await asyncio.sleep(0.01)

            # Drain any late-arriving pair packet before hopping away from this channel.
            if self._hub_mac is None:
                self._drain_rx()
            if self._hub_mac is not None:
                break

            channel = self.MIN_CHANNEL if channel >= self.MAX_CHANNEL else channel + 1

        print(f"[*] Found Hub {self.mac_to_str(self._hub_mac)} on channel {self._channel}, sending pairing complete...")
        if await self._send_successful_pair(self._hub_mac):
            print(f"[✓] Paired with Hub {self.mac_to_str(self._hub_mac)} on channel {self._channel}")
        else:
            print(f"[!] Hub {self.mac_to_str(self._hub_mac)} did not ACK pairing complete")
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
            self._drain_rx()
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
        except Exception as e:
            print(f"[!] Failed to send ack: {e}")

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
                if mac in self._peers:
                    self._hub_mac = mac
                    self._auto_add_peers = False
                    self._send_ack(mac, packet_id)
                    self._expected_packet[mac] = packet_id + 1
                return None
            elif mac in self._peers:
                self._send_ack(mac, packet_id)
                self._expected_packet[mac] = packet_id + 1
                if not self._sending_pair_success:
                    asyncio.create_task(self._send_successful_pair(mac))
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

        if self.is_message_type(data, self.SUCCESSFUL_PAIR_MESSAGE):
            sensor_mac = mac
            if "mac" in data:
                try:
                    payload_mac = self.mac_to_bytes(data["mac"])
                    if payload_mac == mac:
                        sensor_mac = payload_mac
                except Exception:
                    pass
            self._paired_sensors.add(sensor_mac)
            return None

        return data