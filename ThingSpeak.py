"""
ThingSpeak REST API client compatible with both CPython and CircuitPython.

Provides validated helpers for reading channel feeds/fields and writing field
updates or status messages to a ThingSpeak channel.
"""

try:
    import requests
except ImportError:
    # Fall back to CircuitPython's adafruit_requests over the Wi-Fi radio socket pool
    import ssl
    import wifi
    import socketpool
    import adafruit_requests
    requests = None

import json


class ThingSpeak:
    """Client for reading from and writing to a ThingSpeak IoT channel."""

    def __init__(self, rkey, wkey, cID):
        """
        Initialize the ThingSpeak client and load channel field definitions.

        Args:
            rkey: ThingSpeak channel Read API key (may be None for public channels).
            wkey: ThingSpeak channel Write API key.
            cID: Numeric or string ThingSpeak channel ID.
        """
        self.api_read = rkey
        self.api_write = wkey
        self.channel_id = cID

        self.BASE_URL = "https://api.thingspeak.com"
        self._session = (
            requests
            if requests is not None
            else adafruit_requests.Session(socketpool.SocketPool(wifi.radio), ssl.create_default_context())
        )

        # Fetch channel metadata to map configured field names to field numbers
        with self._session.get(f"{self.BASE_URL}/channels/{self.channel_id}/feeds.json?api_key={self.api_read}&results=2") as channel_fields_data:
            channel_fields = channel_fields_data.json()  # Requests has a built-in JSON parser

        self.fields = {}

        for key in channel_fields["channel"]:
            if key.startswith("field"):
                self.fields[channel_fields["channel"][key]] = key[-1]

    # ------------------------
    # CHANNEL MANAGEMENT
    # ------------------------

    def update_fields(self):
        """Refresh the cached ``self.fields`` mapping from ThingSpeak channel metadata."""
        with self._session.get(f"{self.BASE_URL}/channels/{self.channel_id}/feeds.json?api_key={self.api_read}&results=2") as channel_fields_data:
            channel_fields = channel_fields_data.json()

        for key in channel_fields["channel"]:
            if key.startswith("field") and (key not in self.fields):
                self.fields[channel_fields["channel"][key]] = key[-1]

    def _validate_field_id(self, field_id):
        """Ensure ``field_id`` is an integer between 1 and 8 inclusive."""
        if not isinstance(field_id, int) or isinstance(field_id, bool) or not (1 <= field_id <= 8):
            raise ValueError("field_id must be an integer between 1 and 8")

    # ------------------------
    # FUNCTIONS TO READ DATA
    # ------------------------

    def _read_request(self, read_type, params):
        """
        Build and execute a GET request against a ThingSpeak channel read endpoint.

        Args:
            read_type (str): Endpoint suffix such as ``'feeds.json'`` or ``'fields/1.json'``.
            params (dict): Query parameters appended to the URL (e.g. ``{'results': 2}``).

        Returns:
            dict: Parsed JSON payload returned by ThingSpeak.

        Raises:
            HTTPError: If the endpoint or query parameters produce an HTTP error response.
        """
        # Automatically include the Read API key when configured
        if self.api_read:
            params["api_key"] = self.api_read

        query = "&".join(f"{k}={str(v).replace(' ', '%20')}" for k, v in params.items() if v is not None)

        # Assemble the full channel read URL
        url = f"{self.BASE_URL}/channels/{self.channel_id}/{read_type}"
        if query:
            url = f"{url}?{query}"

        with self._session.get(url) as response:
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response.json()

    def read_channel_feed(self, results=None, minutes=None,
                          start=None, end=None):
        """
        Read entries across all fields of the ThingSpeak channel feed.

        Args:
            results: Optional positive integer count of most recent entries to retrieve.
            minutes: Optional positive integer window in minutes prior to now.
            start: Optional start timestamp string (requires ``end``).
            end: Optional end timestamp string (requires ``start``).

        Returns:
            dict: ThingSpeak JSON payload containing ``'channel'`` and ``'feeds'``.
        """
        params = {}

        if results is not None:
            if not isinstance(results, int) or isinstance(results, bool) or results < 1:
                raise ValueError("results must be a positive integer")
            params["results"] = results

        if minutes is not None:
            if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < 1:
                raise ValueError("minutes must be a positive integer")
            params["minutes"] = minutes

        if start is not None or end is not None:

            if start is None or end is None:
                raise ValueError("Both start and end must be provided")

            params["start"] = start
            params["end"] = end

        return self._read_request("feeds.json", params)

    def read_channel_field(self, field_id, results=None,
                           minutes=None, start=None, end=None):
        """
        Read historical values for a single ThingSpeak channel field (1-8).

        Args:
            field_id: Integer field number between 1 and 8.
            results: Optional positive integer count of most recent entries.
            minutes: Optional positive integer window in minutes.
            start: Optional start timestamp string (requires ``end``).
            end: Optional end timestamp string (requires ``start``).

        Returns:
            dict: ThingSpeak JSON payload containing metadata and field entries.
        """
        self._validate_field_id(field_id)

        params = {}

        if results is not None:
            if not isinstance(results, int) or isinstance(results, bool) or results < 1:
                raise ValueError("results must be a positive integer")
            params["results"] = results

        if minutes is not None:
            if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < 1:
                raise ValueError("minutes must be a positive integer")
            params["minutes"] = minutes

        if start is not None or end is not None:

            if start is None or end is None:
                raise ValueError("Both start and end must be provided")

            params["start"] = start
            params["end"] = end

        endpoint = f"fields/{field_id}.json"

        return self._read_request(endpoint, params)

    def read_selected_fields(self, field_ids, results=None,
                             minutes=None, start=None, end=None):
        """
        Read the channel feed and filter both metadata and entries to ``field_ids``.

        Args:
            field_ids: Non-empty list or tuple of integer field IDs (1-8).
            results: Optional positive integer count of most recent entries.
            minutes: Optional positive integer window in minutes.
            start: Optional start timestamp string (requires ``end``).
            end: Optional end timestamp string (requires ``start``).

        Returns:
            dict: Filtered ``{'channel': ..., 'feeds': ...}`` dictionary.
        """
        if not isinstance(field_ids, (list, tuple)):
            raise TypeError("field_ids must be a list or tuple")

        if not field_ids:
            raise ValueError("At least one field ID must be provided")

        for field_id in field_ids:
            self._validate_field_id(field_id)

        # Retrieve the full channel feed before filtering locally
        data = self.read_channel_feed(
            results=results,
            minutes=minutes,
            start=start,
            end=end
        )

        selected_keys = {f"field{i}" for i in field_ids}

        # Keep only selected field metadata
        channel = data["channel"].copy()

        for i in range(1, 9):
            key = f"field{i}"

            if key not in selected_keys:
                channel.pop(key, None)

        # Keep only non-field metadata plus selected field measurements
        feeds = []

        for entry in data["feeds"]:

            filtered_entry = {
                key: value
                for key, value in entry.items()
                if key not in {f"field{i}" for i in range(1, 9)}
                or key in selected_keys
            }

            feeds.append(filtered_entry)

        return {
            "channel": channel,
            "feeds": feeds
        }

    # ------------------------
    # FUNCTIONS TO WRITE DATA
    # ------------------------

    def _write_request(self, params):
        """
        POST form parameters to ThingSpeak's ``/update`` endpoint.

        Returns:
            int: The ThingSpeak entry ID (0 if the write was rejected/rate-limited).
        """
        url = f"{self.BASE_URL}/update"

        with self._session.post(url, data=params) as response:
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()

            result = response.text.strip()

            if not result.isdigit():
                raise ValueError(f"Unexpected ThingSpeak response: {result}")

            return int(result)

    def write_fields(self, data):
        """
        Write one or more validated field values to the ThingSpeak channel.

        Args:
            data: Dict mapping field keys present in ``self.fields`` to int/float/str values.

        Returns:
            int: Created ThingSpeak entry ID (0 on failure).
        """
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary")

        if not data:
            raise ValueError("At least one field is required")

        params = {
            "api_key": self.api_write
        }

        for field, value in data.items():

            if field not in self.fields:
                raise ValueError(f"Invalid field: {field}")

            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise TypeError(f"Invalid value for {field}")

            params[field] = value

        return self._write_request(params)

    def write_status(self, status):
        """
        Post a channel status string (up to 255 characters) to ThingSpeak.

        Returns:
            int: Created ThingSpeak entry ID (0 on failure).
        """
        if not isinstance(status, str):
            raise TypeError("Status must be a string")

        if len(status) > 255:
            raise ValueError("Status cannot exceed 255 characters")

        params = {
            "api_key": self.api_write,
            "status": status
        }

        return self._write_request(params)

    def write_MAC_ADDRESS(self, addresses):
        """
        Write MAC address data to ThingSpeak ``field1``.

        Args:
            addresses: Iterable of MAC address values to write.

        Returns:
            int: Created ThingSpeak entry ID (0 on failure).
        """
        if not addresses:
            raise ValueError("Please enter MAC address")

        if isinstance(addresses, (list, tuple)):
            raise TypeError("Invalid format, pass MAC addresses as a list or tuple")

        params = {
            "api_key": self.api_write
        }

        for mac_ad in addresses:
            params["field1"] = mac_ad

        return self._write_request(params=params)
