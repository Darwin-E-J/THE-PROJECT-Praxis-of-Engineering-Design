"""
Edited by: Claude Opus 5.5, Gemini Flash 3.8
ESP-NOW communication management, ideal for single host to multi peer network topologies with:
    Dynamic peer management with broadcast messages
    Peer channel hopping recovery
    Packet numbering and ack system for resending dropped or out of order packets


Based upon:
Author: Aiden Cherniske
Written: 2025.10.15
Refactored: 2026.01.23

ESP-NOW communication management for ESP32-S3.

Based on Adafruit guide:
https://learn.adafruit.com/esp-now-in-circuitpython

Requirements:
- ESP32-S3 board with CircuitPython
- Adafruit CircuitPython Bundle libraries:
  - asyncio
  - adafruit_ticks

Features:
- Flexible MAC address input formats
- Asynchronous send/receive
- Automatic peer management
"""

import json
import wifi
import espnow

try:
    import asyncio
except ImportError:
    raise ImportError("This code requires the asyncio library. " "Please install it from the Adafruit CircuitPython Bundle.")

try:
    import adafruit_ticks
except ImportError:
    raise ImportError("This code requires the adafruit_ticks library. " "Please install it from the Adafruit CircuitPython Bundle.")


class ESPNowManager:
    """
    Manages ESP-NOW communication with an ESP32-S3.

    Allows adding/removing peers, sending, and receiving messages asynchronously.
    Supports multiple MAC address formats for convenience.

    Packet numbering / ack:
        - Every message is a JSON dict, sent as its own ESP-NOW packet.
        - send_message() puts the packet number as the first key, then waits
          for the peer to send back an ack with that same packet number.
        - If no ack comes back in time (data or ack was lost), the same
          packet is resent.
        - receive_messages() checks each data packet is the next expected
          one, and acks it.

    Pairing & Messaging (hub <-> sensor nodes):
        - Standard message types are defined in message_types list:
          PAIR_MESSAGE = {"type": "pair"}
          REQUEST_MESSAGE = {"type": "request"}
        - The UI hub gets the sensor MACs (from ThingSpeak), adds them as
          peers, and sends each one the special PAIR_MESSAGE: init_hub()
        - A sensor node waits for the special PAIR_MESSAGE, adds that sender
          as a peer, and saves it as hub_mac: init_sensor_node()
        - The UI hub can then request sensor readings using REQUEST_MESSAGE
    """

    # Packet numbering / ack constants
    PACKET_ID_KEY = "id"  # First key of every packet, holds the packet number
    ACK_KEY = "ack"  # Only in ack packets: {"id": n, "ack": True}
    ACK_TIMEOUT_MS = 500  # How long to wait for an ack before resending
    MAX_RETRIES = 5  # How many times to resend a packet before giving up

    # Channel hopping constants (pairing)
    MIN_CHANNEL = 1
    MAX_CHANNEL = 11  # Standard 2.4 GHz WiFi channels (1-11 in North America)
    PAIR_CHANNEL_WAIT_MS = ACK_TIMEOUT_MS * 2  # Dwell 1s (2x ack timeout) per channel while pairing

    # Magic packets
    PAIR_MESSAGE = {"type": "pair"}  # Sent by the hub so sensor nodes learn its MAC
    REQUEST_MESSAGE = {"type": "request"}  # Sent by the hub to request sensor readings

    # Known message types (list of message dicts)
    MESSAGE_TYPES = [
        PAIR_MESSAGE,
        REQUEST_MESSAGE,
    ]
    message_types = MESSAGE_TYPES  # Class-level alias

    # Inbox constants
    MAX_INBOX = 20  # Max received messages kept for read_message(), oldest are dropped first (limited RAM)

    def __init__(self):
        """Initialize ESP-NOW manager."""
        self._espnow = None
        self._packet_number = 0  # Keep track of all sent packets for ack numbering
        self._expected_packet = {}  # Next packet number expected from each peer (MAC bytes as key)
        self._acked_packets = set()  # Packet numbers acked by a peer, waiting to be picked up by send_message()
        self._inbox = []  # Received data waiting for read_message(): list of (MAC bytes, dict) tuples
        self._auto_add_peers = False  # Sensor node only: if True, listen for the special PAIR_MESSAGE to pair with hub
        self._hub_mac = None  # Sensor node only: hub MAC (bytes), set once paired
        self._receive_task = None  # Async receive_messages() task
        self._peers = {}  # Dictionary to store peers with MAC as key

    @property
    def hub_mac(self):
        """Get the paired hub MAC address (bytes), or None if not yet paired."""
        return self._hub_mac

    @staticmethod
    def mac_to_bytes(mac):
        """
        Convert MAC address from hex string to bytes.

        Accepts formats:
        - '24:6F:28:AB:CD:EF'
        - '24-6F-28-AB-CD-EF'
        - '246F28ABCDEF'
        - bytes object: b'\\x24\\x6F\\x28\\xAB\\xCD\\xEF'

        Args:
            mac: MAC address as string or bytes

        Returns:
            bytes: MAC address as bytes
        """
        if isinstance(mac, bytes):
            return mac

        # Remove separators
        mac = mac.replace(":", "").replace("-", "").replace(" ", "")

        return bytes.fromhex(mac)

    @staticmethod
    def mac_to_str(mac_bytes):
        """
        Convert MAC bytes to readable string format.

        Args:
            mac_bytes: MAC address as bytes

        Returns:
            str: MAC address as colon-separated hex string
        """
        return ":".join(f"{b:02x}" for b in mac_bytes)

    @staticmethod
    def is_message_type(packet_data: dict, message_type: dict) -> bool:
        """
        Check if received JSON data matches a specific message type template.

        Args:
            packet_data: Parsed packet payload dict
            message_type: Message template dict (e.g. PAIR_MESSAGE, REQUEST_MESSAGE)

        Returns:
            bool: True if packet_data contains all key-values of message_type
        """
        if not isinstance(packet_data, dict) or not isinstance(message_type, dict):
            return False
        # Check if the data matches the expected key-value pairs of the template
        return all(packet_data.get(k) == v for k, v in message_type.items())

    def get_message_type(self, packet_data: dict) -> dict | None:
        """
        Identify which known message template in message_types matches the packet.

        Args:
            packet_data: Parsed packet JSON dictionary

        Returns:
            dict: The matching message dict from message_types, or None if unknown
        """
        if not isinstance(packet_data, dict):
            return None
        for msg_type in self.message_types:
            if self.is_message_type(packet_data, msg_type):
                return msg_type
        return None

    def start(self):
        """Start ESP-NOW communication and background receive task."""
        if self._espnow is None:
            wifi.radio.enabled = True
            self._espnow = espnow.ESPNow()
            print("ESP-NOW started")
        if self._receive_task is None:
            self._receive_task = asyncio.create_task(self.receive_messages())

    def stop(self):
        """Stop ESP-NOW communication."""
        if self._receive_task:
            self._receive_task.cancel()
            self._receive_task = None
        if self._espnow:
            self._espnow.deinit()
            self._espnow = None
            print("ESP-NOW stopped")

    def add_peer(self, mac):
        """
        Add a peer for ESP-NOW communication.

        Args:
            mac: Peer MAC address (string or bytes)
        """
        mac_bytes = self.mac_to_bytes(mac)

        if mac_bytes in self._peers:
            print(f"Peer {self.mac_to_str(mac_bytes)} already exists")
            return

        try:
            peer = espnow.Peer(mac=mac_bytes)
            self._espnow.peers.append(peer)
            self._peers[mac_bytes] = peer
            print(f"Peer {self.mac_to_str(mac_bytes)} added")
        except Exception as e:
            print(f"Error: Failed to add peer: {e}")

    def remove_peer(self, mac):
        """
        Remove a peer from ESP-NOW communication.

        Args:
            mac: Peer MAC address (string or bytes)
        """
        mac_bytes = self.mac_to_bytes(mac)

        if mac_bytes not in self._peers:
            print(f"Peer {self.mac_to_str(mac_bytes)} not found")
            return

        try:
            peer = self._peers[mac_bytes]
            self._espnow.peers.remove(peer)
            del self._peers[mac_bytes]
            print(f"Peer {self.mac_to_str(mac_bytes)} removed")
        except Exception as e:
            print(f"Error: Failed to remove peer: {e}")

    async def init_hub(self, sensor_macs: list) -> list:
        """
        Set up this device as the UI hub and pair with every sensor node.

        Starts ESP-NOW and the background receive task, adds each sensor as a
        peer, then sends each one the special PAIR_MESSAGE. When a sensor node
        gets the pair message it learns our MAC and pairs with us
        (see init_sensor_node()).

        A unicast pair message is used instead of a broadcast because it is
        acked, confirming which sensors actually paired.

        Note: ESP-NOW only works if both devices are on the same WiFi channel.
        The hub's channel is set by the router it is connected to.

        Args:
            sensor_macs: Sensor node MAC addresses (strings or bytes, e.g. from ThingSpeak)

        Returns:
            list: MAC addresses (bytes) of the sensors that acked the pair message
        """
        self.start()

        paired = []
        for mac in sensor_macs:
            mac_bytes = self.mac_to_bytes(mac)
            self.add_peer(mac_bytes)
            if await self.send_message(mac_bytes, self.PAIR_MESSAGE):
                paired.append(mac_bytes)
            else:
                print(f"Warning: Sensor {self.mac_to_str(mac_bytes)} did not pair")

        print(f"Hub paired with {len(paired)}/{len(sensor_macs)} sensors")
        return paired

    def _set_channel(self, channel: int) -> bool:
        """
        Set the WiFi radio channel for ESP-NOW via a brief AP start/stop.

        CircuitPython's wifi.radio does not expose a direct writable channel
        property, so starting and immediately stopping an AP tunes the radio
        to the requested channel.

        Args:
            channel: 2.4 GHz WiFi channel number (typically 1-11)

        Returns:
            bool: True if the channel was set, False otherwise
        """
        try:
            wifi.radio.start_ap(" ", "", channel=channel, max_connections=0)
            wifi.radio.stop_ap()
            return True
        except Exception as e:
            print(f"Warning: Failed to set channel {channel}: {e}")
            return False

    async def init_sensor_node(self) -> bytes:
        """
        Set up this device as a sensor node and wait for the hub to pair.

        Starts ESP-NOW and the background receive task, then hops sequentially
        through WiFi channels (MIN_CHANNEL..MAX_CHANNEL), dwelling
        PAIR_CHANNEL_WAIT_MS (double ACK_TIMEOUT_MS, i.e. 1 s) on each channel
        until the hub sends the special PAIR_MESSAGE. Once received, the hub's
        MAC is added as a peer, saved in self._hub_mac, and acked, keeping the
        radio on the hub's channel.

        Returns:
            bytes: The hub's MAC address
        """
        self.start()
        self._auto_add_peers = True

        print("Waiting for hub pair message...")
        channel = self.MIN_CHANNEL
        while self._hub_mac is None:
            if self._set_channel(channel):
                print(f"Listening for pair message on channel {channel}...")
            start = adafruit_ticks.ticks_ms()
            while self._hub_mac is None and adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start) < self.PAIR_CHANNEL_WAIT_MS:
                await asyncio.sleep(0.1)  # Let receive_messages() run

            channel = self.MIN_CHANNEL if channel >= self.MAX_CHANNEL else channel + 1

        return self._hub_mac

    # -------------------------------------------------------------------------
    # Public Send / Receive API
    # -------------------------------------------------------------------------

    async def send_message(self, mac: str | bytes, message: dict) -> bool:
        """
        Send a JSON message to a peer and wait for it to be acked.

        The packet number is added as the first key (see _add_packet_id()).
        If no ack comes back within ACK_TIMEOUT_MS (data or ack was lost),
        the same packet is resent, up to MAX_RETRIES times.

        Note: receive_messages() must be running in the event loop, since it
        is what reads the incoming acks.

        Args:
            mac: Peer MAC address (string or bytes)
            message: JSON data to send (dict)

        Returns:
            bool: True if the peer acked the packet, False otherwise
        """
        mac_bytes = self.mac_to_bytes(mac)

        if mac_bytes not in self._peers:
            print(f"Error: Peer {self.mac_to_str(mac_bytes)} not found")
            return False

        # Number this packet, then advance the counter for the next one
        packet_id = self._packet_number
        packet = self._add_packet_id(message)
        self._packet_number += 1

        # Encode dict to json string, and look up the espnow.Peer object for the destination MAC
        data = json.dumps(packet).encode("utf-8")
        peer = self._peers[mac_bytes]

        retries = 0
        while True:
            try:
                self._espnow.send(data, peer)
                print(f"Message sent to {self.mac_to_str(mac_bytes)}: {packet}")
            except Exception as e:
                print(f"Error: Failed to send message: {e}")

            if await self._wait_for_ack(packet_id):
                return True

            # No ack: either our data or their ack was lost, resend the same packet
            if retries >= self.MAX_RETRIES:
                break
            retries += 1
            print(f"No ack for packet {packet_id}, resending ({retries}/{self.MAX_RETRIES})")

        print(f"Error: Packet {packet_id} was never acked by {self.mac_to_str(mac_bytes)}")
        return False

    async def read_message(self, mac: str | bytes | None = None, timeout_ms: int = 0) -> tuple | None:
        """
        Get the oldest received message, optionally only from one peer.

        Messages are put in the inbox by receive_messages(), with the packet
        number still in them.

        Example (hub asking a sensor for data):
            await manager.send_message(sensor_mac, manager.REQUEST_MESSAGE)
            reply = await manager.read_message(sensor_mac, timeout_ms=2000)
            if reply:
                mac, data = reply  # data = {"id": 0, "aqi": 42}

        Args:
            mac: Only return a message from this peer (string or bytes), or None for any peer
            timeout_ms: How long to wait for a message (0 = just check once, don't wait)

        Returns:
            tuple: (sender MAC bytes, JSON data dict), or None if nothing arrived in time
        """
        mac_bytes = self.mac_to_bytes(mac) if mac is not None else None
        start = adafruit_ticks.ticks_ms()

        while True:
            # Oldest first, skipping messages from other peers
            for i, (sender, data) in enumerate(self._inbox):
                if mac_bytes is None or sender == mac_bytes:
                    return self._inbox.pop(i)

            if adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start) >= timeout_ms:
                return None
            await asyncio.sleep(0.01)  # Let receive_messages() run

    async def receive_messages(self):
        """
        Continuously receive messages from peers.

        Acks incoming data packets, records incoming acks, and passes
        valid data to the inbox (see _handle_packet()).

        This is an async coroutine that runs in the event loop.
        init_hub() / init_sensor_node() start it automatically.
        """
        while True:
            packet = self._espnow.read()
            if packet:
                mac = packet.mac
                msg = packet.msg
                print(f"Message received from {self.mac_to_str(mac)}: {msg}")
                data = self._handle_packet(mac, msg)

                # Keep new data for read_message() (_handle_packet filters out acks and pair messages)
                if data is not None:
                    if len(self._inbox) >= self.MAX_INBOX:
                        print("Warning: Inbox full, dropping oldest message")
                        self._inbox.pop(0)
                    self._inbox.append((mac, data))
            await asyncio.sleep(0.1)

    # -------------------------------------------------------------------------
    # Internal Protocol Helpers: packet framing, acking & routing
    # -------------------------------------------------------------------------

    def _add_packet_id(self, message: dict) -> dict:
        """
        Build a new packet with the current packet number as the first key

        The original message is not modified

        Example:
            {"temp (f)": 42} -> {"id": 3, "temp (f)": 42}

        Args:
            message: JSON data to send (dict)

        Returns:
            dict: New JSON dict with the packet number first
        """
        packet = {self.PACKET_ID_KEY: self._packet_number}
        if isinstance(message, dict):
            packet.update(message)
        else:
            packet["type"] = str(message)
        return packet

    async def _wait_for_ack(self, packet_id: int) -> bool:
        """
        Wait up to ACK_TIMEOUT_MS for the ack of a packet.

        Acks are read by receive_messages(), which adds them to
        self._acked_packets. This just checks that set until the ack shows up.

        Args:
            packet_id: Packet number to wait for

        Returns:
            bool: True if the ack arrived in time, False on timeout
        """
        start = adafruit_ticks.ticks_ms()
        while adafruit_ticks.ticks_diff(adafruit_ticks.ticks_ms(), start) < self.ACK_TIMEOUT_MS:
            if packet_id in self._acked_packets:
                self._acked_packets.remove(packet_id)
                return True
            await asyncio.sleep(0.01)  # Let receive_messages() run
        return False

    def _send_ack(self, mac_bytes: bytes, packet_id: int) -> None:
        """
        Send an ack back to a peer for a packet we received.

        The ack is its own small JSON packet: {"id": n, "ack": True}
        Acks are not numbered or acked themselves (if one is lost, the peer
        just resends the data and we ack again).

        Args:
            mac_bytes: Peer MAC address (bytes)
            packet_id: Packet number from the received packet's header
        """
        if mac_bytes not in self._peers:
            print(f"Error: Can't ack, peer {self.mac_to_str(mac_bytes)} not found")
            return

        ack = {self.PACKET_ID_KEY: packet_id, self.ACK_KEY: True}
        try:
            self._espnow.send(json.dumps(ack).encode("utf-8"), self._peers[mac_bytes])
        except Exception as e:
            print(f"Error: Failed to send ack: {e}")

    def _handle_packet(self, mac: bytes, msg: bytes) -> dict | None:
        """
        Process one received packet (data, ack, or pairing).

        Ack packets: mark the packet number as acked for send_message().
        Pair packets: if waiting to pair, add sender as peer (the hub), save _hub_mac, and ack.
        Data packets: only from known peers; ack and verify packet order before accepting.

        Args:
            mac: Sender MAC address (bytes)
            msg: Raw packet contents (bytes)

        Returns:
            dict: The received JSON data if it is a new valid data packet, otherwise None
        """
        try:
            data = json.loads(msg.decode("utf-8"))
            packet_id = data[self.PACKET_ID_KEY]
        except (ValueError, KeyError, TypeError) as e:
            print(f"Error: Ignoring packet without valid JSON/packet number: {e}")
            return None

        # Ack packet: let send_message() know the peer got that packet
        if data.get(self.ACK_KEY):
            self._acked_packets.add(packet_id)
            return None

        # Special PAIR_MESSAGE handling: wait for this exact message to pair
        if self.is_message_type(data, self.PAIR_MESSAGE):
            if self._auto_add_peers and self._hub_mac is None:
                self.add_peer(mac)
                self._hub_mac = mac
                self._auto_add_peers = False  # Paired; do not accept any other device as hub
                print(f"Paired with hub {self.mac_to_str(mac)}")
                self._send_ack(mac, packet_id)
                self._expected_packet[mac] = packet_id + 1
                return None
            elif mac in self._peers:
                # Re-transmitted pair message from hub (our previous ack may have dropped)
                self._send_ack(mac, packet_id)
                return None
            else:
                print(f"Ignoring pair message from unexpected device {self.mac_to_str(mac)}")
                return None

        # Non-pair packet from an unknown device: ignore completely
        if mac not in self._peers:
            print(f"Ignoring packet from unknown device {self.mac_to_str(mac)}")
            return None

        # Always ack data from known peers, even duplicates (duplicate means our last ack was lost)
        self._send_ack(mac, packet_id)

        expected = self._expected_packet.get(mac)  # None if first packet from this peer

        # Packet 0 means the peer restarted, unless packet 0 was the last packet we
        # accepted (expected == 1), then it's a resend because our ack was lost
        peer_restarted = packet_id == 0 and expected != 1

        if expected is not None and not peer_restarted:
            if packet_id < expected:
                print(f"Duplicate packet {packet_id} from {self.mac_to_str(mac)}, ignoring")
                return None
            if packet_id > expected:
                print(f"Warning: Expected packet {expected} from {self.mac_to_str(mac)}, got {packet_id}")

        self._expected_packet[mac] = packet_id + 1
        return data
