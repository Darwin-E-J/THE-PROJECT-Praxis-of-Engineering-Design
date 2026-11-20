"""
ESP-NOW communication manager for CircuitPython (ESP32-S3).

Handles peer discovery, dynamic Wi-Fi channel alignment, ACK-based reliable
messaging with deduplication, and automatic re-pairing after disconnections
between a UI hub and one or more sensor nodes.
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
    """
    Manages ESP-NOW radio initialization, pairing, reliable JSON packet
    exchange, and link recovery for both hub and sensor node roles.
    """

    # Packet framing and reliability settings
    PACKET_ID_KEY = "id"
    ACK_KEY = "ack"
    ACK_TIMEOUT_MS = 500
    MAX_RETRIES = 5
    BUFFER_SIZE = 4096  # Larger RX ring buffer to avoid CircuitPython 'Invalid buffer' under bursts

    # Channel hopping and pairing timing parameters
    MIN_CHANNEL = 1
    MAX_CHANNEL = 11
    PAIR_CHANNEL_WAIT_MS = 1200
    PAIR_SEND_INTERVAL_MS = 100
    PAIR_TIMEOUT_SEC = 30
    PAIR_REACK_COOLDOWN_MS = 5000

    # Built-in protocol control messages
    PAIR_MESSAGE = {"type": "pair"}
    SUCCESSFUL_PAIR_MESSAGE = {"type": "successful_pair"}
    REQUEST_MESSAGE = {"type": "request"}

    MESSAGE_TYPES = [PAIR_MESSAGE, SUCCESSFUL_PAIR_MESSAGE, REQUEST_MESSAGE]
    message_types = MESSAGE_TYPES
    MAX_INBOX = 20
    BROADCAST_MAC = b"\xff\xff\xff\xff\xff\xff"

    def __init__(self):
        """Initialize internal ESP-NOW state, peer tables, and inbox queues."""
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
        self._pair_reply_task = None
        self._peers = {}
        self._channel = None
        self._sending_pair_success = False
        self._sensor_paired = False
        self._hub_pair_acked = False
        self._last_pair_packet_id = None
        self._last_pair_ack_ms = None
        self._last_rx_ms = {}

    @property
    def hub_mac(self):
        """Return the paired hub's 6-byte MAC address (sensor role), or None."""
        return self._hub_mac

    @property
    def paired_sensors(self):
        """Return a copy of currently paired sensor MAC addresses (hub role)."""
        return list(self._paired_sensors)

    def time_since_last_rx_ms(self, mac=None) -> int | None:
        """
        Return milliseconds elapsed since the last valid packet or ACK was
        received from ``mac`` (defaults to the paired hub), or None if unseen.
        """
        target = self.mac_to_bytes(mac) if mac is not None else self._hub_mac
        if target is None:
            return None
        last_ms = self._last_rx_ms.get(target)
        if last_ms is None:
            return None
        return adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), last_ms)

    @staticmethod
    def mac_to_bytes(mac):
        """Convert a MAC string (colon, dash, or space delimited) or bytes to 6-byte bytes."""
        if isinstance(mac, bytes):
            return mac
        mac = mac.replace(":", "").replace("-", "").replace(" ", "")
        return bytes.fromhex(mac)

    @staticmethod
    def mac_to_str(mac_bytes):
        """Format a 6-byte MAC address as a lowercase colon-separated hex string."""
        return ":".join(f"{b:02x}" for b in mac_bytes)

    @staticmethod
    def is_message_type(packet_data: dict, message_type: dict) -> bool:
        """Return True if ``packet_data`` contains all key-value pairs in ``message_type``."""
        if not isinstance(packet_data, dict) or not isinstance(message_type, dict):
            return False
        return all(packet_data.get(k) == v for k, v in message_type.items())

    def _create_espnow(self):
        """Instantiate ESPNow with an expanded RX buffer when supported."""
        try:
            return espnow.ESPNow(buffer_size=self.BUFFER_SIZE)
        except TypeError:
            return espnow.ESPNow()

    def start(self):
        """Enable the Wi-Fi radio, initialize ESP-NOW, and launch the background RX task."""
        if self._espnow is None:
            if not wifi.radio.enabled:
                wifi.radio.enabled = True
            self._espnow = self._create_espnow()
            print("[+] ESP-NOW subsystem started")
        if self._receive_task is None:
            self._receive_task = asyncio.create_task(self.receive_messages())

    def stop(self):
        """Cancel background tasks and deinitialize the ESP-NOW interface."""
        if self._pair_reply_task:
            try:
                self._pair_reply_task.cancel()
            except Exception:
                pass
            self._pair_reply_task = None
        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None
        if self._espnow is not None:
            self._espnow.deinit()
            self._espnow = None
            print("[-] ESP-NOW stopped")

    def add_peer(self, mac, channel=0):
        """Register an ESP-NOW peer on ``channel`` (0 uses the radio's current channel)."""
        mac_bytes = self.mac_to_bytes(mac)
        if self._espnow is None or mac_bytes in self._peers:
            return
        try:
            peer = espnow.Peer(mac=mac_bytes, channel=channel)
            self._espnow.peers.append(peer)
            self._peers[mac_bytes] = peer
            print(f"[+] Peer {self.mac_to_str(mac_bytes)} added (ch {channel})")
        except Exception as e:
            print(f"[!] Failed to add peer: {e}")

    def remove_peer(self, mac):
        """Remove a peer from ESP-NOW and purge all associated tracking and inbox state."""
        mac_bytes = self.mac_to_bytes(mac)
        if mac_bytes in self._peers:
            try:
                if self._espnow is not None:
                    self._espnow.peers.remove(self._peers[mac_bytes])
                del self._peers[mac_bytes]
                print(f"[-] Peer {self.mac_to_str(mac_bytes)} removed")
            except Exception as e:
                print(f"[!] Failed to remove peer: {e}")
        # Clear sequence, liveness, pairing, and queued message state for this MAC
        self._expected_packet.pop(mac_bytes, None)
        self._last_rx_ms.pop(mac_bytes, None)
        if mac_bytes in self._paired_sensors:
            self._paired_sensors.remove(mac_bytes)
        self._inbox = [(sender, data) for sender, data in self._inbox if sender != mac_bytes]

    def sync_hub_channel(self) -> bool:
        """
        Sync ESP-NOW channel and peer channels with the current Wi-Fi AP channel.

        Returns:
            bool: True if the AP channel changed from a previously recorded channel.
        """
        ap_info = wifi.radio.ap_info
        current_channel = ap_info.channel if ap_info is not None else None
        if current_channel is None:
            return False

        changed = self._channel is not None and self._channel != current_channel
        self._channel = current_channel

        if changed and self._espnow is not None:
            print(f"[!] Hub Wi-Fi channel changed to {current_channel}, updating peers...")
            for mac_bytes in list(self._peers.keys()):
                try:
                    self._espnow.peers.remove(self._peers[mac_bytes])
                    new_peer = espnow.Peer(mac=mac_bytes, channel=current_channel)
                    self._espnow.peers.append(new_peer)
                    self._peers[mac_bytes] = new_peer
                except Exception as e:
                    print(f"[!] Failed to update peer {self.mac_to_str(mac_bytes)} channel: {e}")

        return changed

    async def _pair_hub(
        self,
        sensor_macs: list,
        timeout_sec: int = PAIR_TIMEOUT_SEC,
        reset_paired: bool = True,
    ) -> list:
        """
        Broadcast PAIR_MESSAGE packets until ``len(sensor_macs)`` sensors are
        paired or ``timeout_sec`` expires.

        Args:
            sensor_macs: Configured sensor list (only its length is used as target count).
            timeout_sec: Maximum duration in seconds to broadcast discovery packets.
            reset_paired: If True, start with an empty paired list; otherwise keep
                currently paired active peers and only discover missing sensors.

        Returns:
            list: Paired sensor MAC addresses as bytes.
        """
        self.start()
        expected_count = len(sensor_macs)
        if reset_paired:
            self._paired_sensors = []
        else:
            self._paired_sensors = [m for m in self._paired_sensors if m in self._peers]

        if expected_count == 0 or len(self._paired_sensors) >= expected_count:
            return list(self._paired_sensors)

        self.sync_hub_channel()
        channel = self._channel
        peer_channel = channel if channel is not None else 0

        # Refresh the broadcast peer so it always matches the active Wi-Fi channel
        if self.BROADCAST_MAC in self._peers:
            self.remove_peer(self.BROADCAST_MAC)
        self.add_peer(self.BROADCAST_MAC, channel=peer_channel)
        if self.BROADCAST_MAC not in self._peers:
            return list(self._paired_sensors)

        # Include hub's Wi-Fi channel so sensors receiving adjacent-channel bleed can lock on
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
        mode_label = "discovery" if reset_paired else "re-pairing"
        print(
            f"[*] Hub {mode_label} active on Wi-Fi Channel {ch_label} "
            f"({len(self._paired_sensors)}/{expected_count} paired, timeout: {timeout_sec}s)..."
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

                # Wait between broadcasts while yielding frequently so RX handles replies promptly
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
            self.remove_peer(self.BROADCAST_MAC)

        if len(self._paired_sensors) < expected_count:
            print(
                f"[!] Pairing timed out ({len(self._paired_sensors)}/{expected_count} sensors paired)"
            )

        print(f"[*] Hub paired with {len(self._paired_sensors)}/{expected_count} sensors")
        return list(self._paired_sensors)

    async def init_hub(self, sensor_macs: list, timeout_sec: int = PAIR_TIMEOUT_SEC) -> list:
        """
        Perform initial hub discovery by broadcasting PAIR_MESSAGE until
        ``len(sensor_macs)`` sensors reply with SUCCESSFUL_PAIR_MESSAGE
        (accepting any sensor MAC without validation) or ``timeout_sec`` expires.
        """
        return await self._pair_hub(sensor_macs, timeout_sec=timeout_sec, reset_paired=True)

    async def repair_hub(self, sensor_macs: list, timeout_sec: int = PAIR_TIMEOUT_SEC) -> list:
        """
        Attempt to pair missing or disconnected sensors while preserving
        already-paired sensors.
        """
        return await self._pair_hub(sensor_macs, timeout_sec=timeout_sec, reset_paired=False)

    def _set_channel(self, channel: int) -> bool:
        """Tune the ESP32 radio to ``channel`` via a brief AP start/stop cycle."""
        try:
            wifi.radio.start_ap(" ", "", channel=channel, max_connections=0)
            wifi.radio.stop_ap()
            return True
        except Exception as e:
            print(f"[!] Failed to set channel {channel}: {e}")
            return False

    def _drain_rx(self) -> None:
        """Read and process all currently buffered incoming ESP-NOW packets."""
        if self._espnow is None:
            return
        while self._espnow:
            packet = self._espnow.read()
            if not packet:
                break
            mac = packet.mac
            msg = packet.msg
            data = self._handle_packet(mac, msg)

            if data is not None:
                # Drop oldest unread message if inbox reaches capacity
                if len(self._inbox) >= self.MAX_INBOX:
                    self._inbox.pop(0)
                self._inbox.append((mac, data))

    async def _send_successful_pair(self, mac_bytes: bytes) -> bool:
        """
        Send SUCCESSFUL_PAIR_MESSAGE to the hub, syncing the radio channel first
        if the hub reported a different Wi-Fi channel.
        """
        if self._sending_pair_success:
            return False
        self._sending_pair_success = True
        try:
            if (
                self._hub_channel is not None
                and self._channel is not None
                and self._hub_channel != self._channel
            ):
                print(
                    f"[!] Hub reported Wi-Fi Channel {self._hub_channel} "
                    f"(sensor was on {self._channel}), syncing channel..."
                )
                self._reset_espnow_on_channel(self._hub_channel)
            payload = dict(self.SUCCESSFUL_PAIR_MESSAGE)
            payload["mac"] = self.mac_to_str(wifi.radio.mac_address)
            acked = await self.send_message(mac_bytes, payload)
            if acked and self._hub_mac == mac_bytes:
                now = adafruit_ticks.ticks_ms()
                self._hub_pair_acked = True
                self._last_pair_ack_ms = now
                self._last_rx_ms[mac_bytes] = now
            return acked
        finally:
            self._sending_pair_success = False

    def _reset_espnow_on_channel(self, channel: int) -> None:
        """
        Re-create ESPNow after AP start/stop so STA RX restarts on ``channel``,
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
        self._espnow = self._create_espnow()

        peer_channel = self._channel if self._channel is not None else None
        for mac_bytes, peer in list(self._peers.items()):
            try:
                ch = peer_channel if peer_channel is not None else peer.channel
                new_peer = espnow.Peer(mac=mac_bytes, channel=ch)
                self._espnow.peers.append(new_peer)
                self._peers[mac_bytes] = new_peer
            except Exception as e:
                print(f"[!] Failed to restore peer {self.mac_to_str(mac_bytes)}: {e}")

    async def _reset_sensor_state(self) -> None:
        """Cancel any in-flight pair reply task and clear all prior sensor pairing state."""
        if self._pair_reply_task is not None:
            try:
                self._pair_reply_task.cancel()
            except Exception:
                pass
            self._pair_reply_task = None
            # Yield once so the cancelled task runs its finally block before ESPNow resets
            await asyncio.sleep(0)

        for mac_bytes in list(self._peers.keys()):
            self.remove_peer(mac_bytes)

        self._hub_mac = None
        self._hub_channel = None
        self._auto_add_peers = True
        self._sensor_paired = False
        self._hub_pair_acked = False
        self._sending_pair_success = False
        self._last_pair_packet_id = None
        self._last_pair_ack_ms = None
        self._inbox.clear()
        self._acked_packets.clear()
        self._expected_packet.clear()
        self._last_rx_ms.clear()

    async def init_sensor_node(self) -> bytes:
        """
        Channel-hop across Wi-Fi channels 1-11 until a hub PAIR_MESSAGE is
        received and SUCCESSFUL_PAIR_MESSAGE is acknowledged by the hub.

        Returns:
            bytes: The paired hub's 6-byte MAC address.
        """
        last_channel = self._channel
        await self._reset_sensor_state()
        if not wifi.radio.enabled:
            wifi.radio.enabled = True

        print(f"[*] Waiting for Hub pair message (channel hopping {self.MIN_CHANNEL}-{self.MAX_CHANNEL})...")
        # Prefer the last known hub channel first when re-pairing to recover faster
        channel = (
            last_channel
            if (last_channel is not None and self.MIN_CHANNEL <= last_channel <= self.MAX_CHANNEL)
            else self.MIN_CHANNEL
        )

        while True:
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

            # Sync to the exact channel advertised by the hub in case of adjacent-channel RX
            if self._hub_channel is not None and self._hub_channel != self._channel:
                print(
                    f"[!] Hub reported Wi-Fi Channel {self._hub_channel} "
                    f"(sensor was on {self._channel}), syncing channel..."
                )
                self._reset_espnow_on_channel(self._hub_channel)

            print(
                f"[*] Found Hub {self.mac_to_str(self._hub_mac)} on channel {self._channel}, "
                f"sending pairing complete..."
            )
            if await self._send_successful_pair(self._hub_mac):
                self._sensor_paired = True
                print(f"[+] Paired with Hub {self.mac_to_str(self._hub_mac)} on channel {self._channel}")
                return self._hub_mac

            # Hub never ACKed pairing completion; reset and resume channel hopping
            print(
                f"[!] Hub {self.mac_to_str(self._hub_mac)} did not ACK pairing complete, "
                f"retrying discovery..."
            )
            next_channel = (
                self._channel
                if (self._channel is not None and self.MIN_CHANNEL <= self._channel <= self.MAX_CHANNEL)
                else self.MIN_CHANNEL
            )
            await self._reset_sensor_state()
            channel = next_channel

    async def repair_sensor_node(self) -> bytes:
        """
        Clear current hub pairing state and re-run channel-hopping discovery
        until paired with a hub.
        """
        print("[*] Re-pairing sensor node with hub...")
        return await self.init_sensor_node()

    async def send_message(self, mac: str | bytes, message: dict) -> bool:
        """
        Send a JSON-encoded message to ``mac`` and wait for an application-level
        ACK, retrying up to ``MAX_RETRIES`` times on timeout.

        Returns:
            bool: True if the peer acknowledged the packet, False otherwise.
        """
        mac_bytes = self.mac_to_bytes(mac)
        if self._espnow is None or mac_bytes not in self._peers:
            return False

        packet_id = self._packet_number
        packet = self._add_packet_id(message)
        self._packet_number += 1

        data = json.dumps(packet).encode("utf-8")
        has_fail_counter = hasattr(self._espnow, "send_failure")
        start_failures = self._espnow.send_failure if has_fail_counter else 0

        retries = 0
        while retries <= self.MAX_RETRIES:
            if self._espnow is None or mac_bytes not in self._peers:
                return False
            peer = self._peers[mac_bytes]
            try:
                self._espnow.send(data, peer)
            except Exception as e:
                print(f"[!] Send error: {e}")

            if await self._wait_for_ack(packet_id):
                return True

            retries += 1
            await asyncio.sleep(0.02)

        if has_fail_counter and self._espnow is not None:
            fail_delta = self._espnow.send_failure - start_failures
            print(
                f"[!] Send to {self.mac_to_str(mac_bytes)} timed out "
                f"(packet_id={packet_id}, tx_failures={fail_delta})"
            )
        return False

    async def read_message(self, mac: str | bytes | None = None, timeout_ms: int = 0) -> tuple | None:
        """
        Pop and return the next queued ``(sender_mac, data_dict)`` from the inbox.

        Args:
            mac: Optional sender MAC filter; accepts any peer when None.
            timeout_ms: Maximum milliseconds to wait before returning None.
        """
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
        """Continuously drain incoming ESP-NOW packets at a 10ms poll interval."""
        while True:
            try:
                self._drain_rx()
            except Exception as e:
                print(f"[!] RX loop error: {e}")
            # Reduced sleep interval to fix ACK timeout lag
            await asyncio.sleep(0.01)

    def _add_packet_id(self, message: dict) -> dict:
        """Wrap ``message`` with the current monotonically increasing packet ID."""
        packet = {self.PACKET_ID_KEY: self._packet_number}
        if isinstance(message, dict):
            packet.update(message)
        else:
            packet["type"] = str(message)
        return packet

    async def _wait_for_ack(self, packet_id: int) -> bool:
        """Wait up to ``ACK_TIMEOUT_MS`` for ``packet_id`` to appear in ``_acked_packets``."""
        start = adafruit_ticks.ticks_ms()
        while adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start) < self.ACK_TIMEOUT_MS:
            if packet_id in self._acked_packets:
                self._acked_packets.remove(packet_id)
                return True
            await asyncio.sleep(0.01)
        return False

    def _send_ack(self, mac_bytes: bytes, packet_id: int) -> None:
        """Transmit an ACK packet for ``packet_id`` back to ``mac_bytes``."""
        if self._espnow is None or mac_bytes not in self._peers:
            return
        ack = {self.PACKET_ID_KEY: packet_id, self.ACK_KEY: True}
        try:
            self._espnow.send(json.dumps(ack).encode("utf-8"), self._peers[mac_bytes])
        except Exception as e:
            print(f"[!] Failed to send ack: {e}")

    def _handle_packet(self, mac: bytes, msg: bytes) -> dict | None:
        """
        Decode and dispatch a raw ESP-NOW payload.

        Processes ACKs, PAIR_MESSAGE, and SUCCESSFUL_PAIR_MESSAGE internally,
        and returns deduplicated application payloads from known peers.
        """
        try:
            data = json.loads(msg.decode("utf-8"))
            packet_id = data[self.PACKET_ID_KEY]
        except (ValueError, KeyError, TypeError):
            return None

        if not isinstance(packet_id, int) or isinstance(packet_id, bool):
            return None

        now = adafruit_ticks.ticks_ms()

        # Handle incoming ACK packets
        if data.get(self.ACK_KEY):
            if mac in self._peers:
                self._last_rx_ms[mac] = now
            self._acked_packets.add(packet_id)
            return None

        # Handle hub pairing broadcasts (sensor role)
        if self.is_message_type(data, self.PAIR_MESSAGE):
            hub_ch = data.get("ch")
            if self._auto_add_peers and self._hub_mac is None:
                # First discovery of a hub while scanning
                self.add_peer(mac, channel=self._channel if self._channel is not None else 0)
                if mac in self._peers:
                    self._hub_mac = mac
                    if isinstance(hub_ch, int) and self.MIN_CHANNEL <= hub_ch <= self.MAX_CHANNEL:
                        self._hub_channel = hub_ch
                    self._auto_add_peers = False
                    self._last_pair_packet_id = packet_id
                    self._send_ack(mac, packet_id)
                    self._expected_packet[mac] = packet_id + 1
                    self._last_rx_ms[mac] = now
                return None
            elif mac in self._peers and mac == self._hub_mac:
                # Already-paired sensor received a PAIR_MESSAGE from its hub (e.g. hub reboot or re-pair)
                if isinstance(hub_ch, int) and self.MIN_CHANNEL <= hub_ch <= self.MAX_CHANNEL:
                    self._hub_channel = hub_ch
                self._send_ack(mac, packet_id)
                self._expected_packet[mac] = packet_id + 1
                if self._sensor_paired:
                    same_burst = (
                        self._hub_pair_acked
                        and self._last_pair_packet_id == packet_id
                        and self._last_pair_ack_ms is not None
                        and adafruit_ticks.ticks_diff(now, self._last_pair_ack_ms) < self.PAIR_REACK_COOLDOWN_MS
                    )
                    channel_changed = (
                        self._hub_channel is not None
                        and self._channel is not None
                        and self._hub_channel != self._channel
                    )
                    if same_burst and not channel_changed:
                        # Suppress duplicate replies within the same confirmed pair burst
                        self._last_rx_ms[mac] = now
                    elif not self._sending_pair_success:
                        # Reply in-place so a rebooted or re-pairing hub re-registers this sensor
                        self._hub_pair_acked = False
                        self._last_pair_packet_id = packet_id
                        self._pair_reply_task = asyncio.create_task(self._send_successful_pair(mac))
                return None
            else:
                return None

        # Handle sensor pairing confirmations (hub role)
        if self.is_message_type(data, self.SUCCESSFUL_PAIR_MESSAGE):
            if self._accept_pair_responses:
                if mac not in self._paired_sensors and len(self._paired_sensors) < self._expected_pair_count:
                    if mac not in self._peers:
                        self.add_peer(mac, channel=self._channel if self._channel is not None else 0)
                    if mac in self._peers:
                        self._send_ack(mac, packet_id)
                        self._expected_packet[mac] = packet_id + 1
                        self._last_rx_ms[mac] = now
                        self._paired_sensors.append(mac)
                        print(f"[+] Successfully paired with {self.mac_to_str(mac)}")
                elif mac in self._paired_sensors:
                    # Re-ACK already-paired sensors without adding duplicates
                    self._send_ack(mac, packet_id)
                    self._expected_packet[mac] = packet_id + 1
                    self._last_rx_ms[mac] = now
            elif mac in self._peers:
                self._send_ack(mac, packet_id)
                self._expected_packet[mac] = packet_id + 1
                self._last_rx_ms[mac] = now
            return None

        # Ignore application packets from unknown peers
        if mac not in self._peers:
            return None

        # Normal traffic proves pairing is active; reset burst tracking and update liveness
        self._last_rx_ms[mac] = now
        if mac == self._hub_mac:
            self._last_pair_packet_id = None

        self._send_ack(mac, packet_id)
        expected = self._expected_packet.get(mac)
        # Allow sequence reset to 0 if the remote peer rebooted
        peer_restarted = packet_id == 0 and expected != 1

        if expected is not None and not peer_restarted:
            if packet_id < expected:
                return None

        self._expected_packet[mac] = packet_id + 1

        return data