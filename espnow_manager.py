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
    BROADCAST_MAC = b"\xff\xff\xff\xff\xff\xff"

    def __init__(self):
        self._espnow = None
        self._packet_number = 0
        self._expected_packet = {}
        self._acked_packets = set()
        self._paired_sensors = []
        self._expected_pair_count = 0
        self._accept_pair_responses = False
        self._inbox = []
        self._auto_add_peers = False
        self._hub_mac = None
        self._hub_channel = None
        self._receive_task = None
        self._peers = {}
        self._channel = None
        self._sending_pair_success = False
        self._sensor_paired = False
        self._hub_pair_acked = False

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
        Continuously sends pair messages until expected_count sensors reply with
        SUCCESSFUL_PAIR_MESSAGE (accepting any sensor MAC without validation) or
        timeout_sec expires.
        """
        self.start()
        expected_count = len(sensor_macs)
        self._paired_sensors = []
        if expected_count == 0:
            return []

        channel = wifi.radio.ap_info.channel if wifi.radio.ap_info is not None else self._channel
        if channel is not None:
            self._channel = channel
        peer_channel = self._channel if self._channel is not None else 0

        added_broadcast = self.BROADCAST_MAC not in self._peers
        self.add_peer(self.BROADCAST_MAC, channel=peer_channel)
        if self.BROADCAST_MAC not in self._peers:
            return []

        pair_payload = dict(self.PAIR_MESSAGE)
        if channel is not None:
            pair_payload["ch"] = channel

        packet_id = self._packet_number
        packet = self._add_packet_id(pair_payload)
        self._packet_number += 1
        pair_bytes = json.dumps(packet).encode("utf-8")

        start_time = adafruit_ticks.ticks_ms()
        timeout_ms = int(timeout_sec * 1000)

        ch_label = channel if channel is not None else "unknown"
        print(
            f"[*] Hub discovery active on Wi-Fi Channel {ch_label} "
            f"(expecting {expected_count} sensor(s), timeout: {timeout_sec}s)..."
        )

        self._expected_pair_count = expected_count
        self._accept_pair_responses = True
        try:
            while (
                len(self._paired_sensors) < expected_count
                and adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start_time) < timeout_ms
            ):
                try:
                    self._espnow.send(pair_bytes, self._peers[self.BROADCAST_MAC])
                except Exception as e:
                    print(f"[!] Pair broadcast error: {e}")

                round_start = adafruit_ticks.ticks_ms()
                while (
                    len(self._paired_sensors) < expected_count
                    and adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), round_start) < self.PAIR_SEND_INTERVAL_MS
                    and adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start_time) < timeout_ms
                ):
                    await asyncio.sleep(0.01)
        finally:
            self._accept_pair_responses = False
            self._expected_pair_count = 0
            self._acked_packets.discard(packet_id)
            if added_broadcast:
                self.remove_peer(self.BROADCAST_MAC)

        if len(self._paired_sensors) < expected_count:
            print(
                f"[!] Pairing timed out ({len(self._paired_sensors)}/{expected_count} sensors paired)"
            )

        print(f"[*] Hub paired with {len(self._paired_sensors)}/{expected_count} sensors")
        return list(self._paired_sensors)

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
            acked = await self.send_message(mac_bytes, payload)
            if acked:
                self._hub_pair_acked = True
            return acked
        finally:
            self._sending_pair_success = False

    def _reset_espnow_on_channel(self, channel: int) -> None:
        """
        Re-create ESPNow after AP start/stop so STA RX restarts on channel,
        preserving any previously added peers.
        """
        if self._espnow is not None:
            try:
                self._espnow.deinit()
            except Exception as e:
                print(f"[!] ESPNow deinit warning: {e}")
            self._espnow = None

        if self._set_channel(channel):
            self._channel = channel
        self._espnow = espnow.ESPNow()

        peer_channel = self._channel if self._channel is not None else None
        for mac_bytes, peer in list(self._peers.items()):
            try:
                ch = peer_channel if peer_channel is not None else peer.channel
                new_peer = espnow.Peer(mac=mac_bytes, channel=ch)
                self._espnow.peers.append(new_peer)
                self._peers[mac_bytes] = new_peer
            except Exception as e:
                print(f"[!] Failed to restore peer {self.mac_to_str(mac_bytes)}: {e}")

    async def init_sensor_node(self) -> bytes:
        self._auto_add_peers = True
        self._hub_channel = None
        self._sensor_paired = False
        self._hub_pair_acked = False
        if not wifi.radio.enabled:
            wifi.radio.enabled = True

        print(f"[*] Waiting for Hub pair message (channel hopping {self.MIN_CHANNEL}-{self.MAX_CHANNEL})...")
        channel = self.MIN_CHANNEL

        while self._hub_mac is None:
            self._reset_espnow_on_channel(channel)

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

        if self._hub_channel is not None and self._hub_channel != self._channel:
            print(
                f"[!] Hub reported Wi-Fi Channel {self._hub_channel} "
                f"(sensor was on {self._channel}), syncing channel..."
            )
            self._reset_espnow_on_channel(self._hub_channel)

        print(f"[*] Found Hub {self.mac_to_str(self._hub_mac)} on channel {self._channel}, sending pairing complete...")
        if await self._send_successful_pair(self._hub_mac):
            self._hub_pair_acked = True
            print(f"[✓] Paired with Hub {self.mac_to_str(self._hub_mac)} on channel {self._channel}")
        else:
            print(f"[!] Hub {self.mac_to_str(self._hub_mac)} did not ACK pairing complete")
        self._sensor_paired = True
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
        has_fail_counter = hasattr(self._espnow, "send_failure")
        start_failures = self._espnow.send_failure if has_fail_counter else 0

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

        if has_fail_counter:
            fail_delta = self._espnow.send_failure - start_failures
            print(
                f"[!] Send to {self.mac_to_str(mac_bytes)} timed out "
                f"(packet_id={packet_id}, tx_failures={fail_delta})"
            )
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
            try:
                self._drain_rx()
            except Exception as e:
                print(f"[!] RX loop error: {e}")
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

        if not isinstance(packet_id, int) or isinstance(packet_id, bool):
            return None

        if data.get(self.ACK_KEY):
            self._acked_packets.add(packet_id)
            return None

        if self.is_message_type(data, self.PAIR_MESSAGE):
            hub_ch = data.get("ch")
            if self._auto_add_peers and self._hub_mac is None:
                self.add_peer(mac, channel=self._channel if self._channel is not None else 0)
                if mac in self._peers:
                    self._hub_mac = mac
                    if isinstance(hub_ch, int) and self.MIN_CHANNEL <= hub_ch <= self.MAX_CHANNEL:
                        self._hub_channel = hub_ch
                    self._auto_add_peers = False
                    self._send_ack(mac, packet_id)
                    self._expected_packet[mac] = packet_id + 1
                return None
            elif mac in self._peers:
                if isinstance(hub_ch, int) and self.MIN_CHANNEL <= hub_ch <= self.MAX_CHANNEL:
                    self._hub_channel = hub_ch
                self._send_ack(mac, packet_id)
                self._expected_packet[mac] = packet_id + 1
                if self._sensor_paired and not self._hub_pair_acked and not self._sending_pair_success:
                    asyncio.create_task(self._send_successful_pair(mac))
                return None
            else:
                return None

        if self.is_message_type(data, self.SUCCESSFUL_PAIR_MESSAGE):
            if self._accept_pair_responses:
                if mac not in self._paired_sensors and len(self._paired_sensors) < self._expected_pair_count:
                    if mac not in self._peers:
                        self.add_peer(mac, channel=self._channel if self._channel is not None else 0)
                    if mac in self._peers:
                        self._send_ack(mac, packet_id)
                        self._expected_packet[mac] = packet_id + 1
                        self._paired_sensors.append(mac)
                        print(f"[✓] Successfully paired with {self.mac_to_str(mac)}")
                elif mac in self._paired_sensors:
                    self._send_ack(mac, packet_id)
                    self._expected_packet[mac] = packet_id + 1
            elif mac in self._peers:
                self._send_ack(mac, packet_id)
                self._expected_packet[mac] = packet_id + 1
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