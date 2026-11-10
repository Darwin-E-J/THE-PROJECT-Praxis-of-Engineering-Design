import requests
import json

class ThingSpeak:
    

    # No feilds are provided so do this
    def __init__(self, rkey, wkey, cID):
        self.api_read = rkey
        self.api_write = wkey
        self.channel_id = cID

        self.BASE_URL = "https://api.thingspeak.com"

        channel_fields_data = requests.get(f"{self.BASE_URL}/channels/{self.channel_id}/feeds.json?api_key={self.api_read}&results=2")
        channel_fields = channel_fields_data.json() # Requests has a builtin json parser!

        self.fields = {}

        for key in channel_fields["channel"]:
            if key.startswith("field"):
                self.fields[channel_fields["channel"][key]] = key[-1]

    # Managment
    def update_fields(self):
        channel_fields_data = requests.get(f"{self.BASE_URL}/channels/{self.channel_id}/feeds.json?api_key={self.api_read}&results=2")
        channel_fields = channel_fields_data.json()

        for key in channel_fields["channel"]:
            if key.startswith("field") and (key not in self.fields):
                self.fields[channel_fields["channel"][key]] = key[-1]

    # ------------------------
    # FUNCTIONS TO READ DATA
    # ------------------------

    def _read_request(self, read_type, params):

        """
        This function essentially builds the url for any read request. The ThingSpeak URL can be
        broken down into sections that this function uses to construct a URL based on what data
        you want to read.

        Args:
            read_type(str): ThingSpeak has three ways to read a channel (Feed, Field, and Status)
                            which have unique endpoints
            params(dict):   These are the parameters appended at the end of the link. We build the
                            link using requests.get() since it follows the standard rules for URL 
                            query strings. Examples of this would be {'results':2} to read the
                            last two results.
        
        Returns:
            A json file with the requested information

        Raises:
            HTTPError:  if the read_type section is an invalid endpoint, or a parameter does
                        not exist.
        """

        # The api key is added to the parameters automatically
        params["api_key"] = self.api_read

        # Building the majority of the URL
        url = f"{self.BASE_URL}/channels/{self.channel_id}/{read_type}"

        # Apparently requests has context managers, huge find
        with requests.get(url, params=params) as response:
            response.raise_for_status()
            return response.json()
    
    def read_channel_feed(self, results=None, minutes=None,
                          start=None, end=None):

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

        if not isinstance(field_ids, (list, tuple)):
            raise TypeError("field_ids must be a list or tuple")

        if not field_ids:
            raise ValueError("At least one field ID must be provided")

        for field_id in field_ids:
            self._validate_field_id(field_id)

        # Retrieve the channel feed
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

        # Keep only selected measurements
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

    def write_field(self, field, data):
        pass
