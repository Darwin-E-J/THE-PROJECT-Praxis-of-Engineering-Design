import requests
import json

class ThingSpeak:
    # No feilds are provided so do this
    def __init__(self, rkey, wkey, cID):
        self.api_read = rkey
        self.api_write = wkey
        self.channel_id = cID


        channel_fields_data = requests.get(f"https://api.thingspeak.com/channels/{self.channel_id}/feeds.json?api_key={self.api_read}&results=2")
        channel_fields = channel_fields_data.json() # Requests has a builtin json parser!
        self.fields = channel_fields["whatever json key form the api data"]

    # Fields are provided so do this
    def __init__(self, rkey, wkey, cID, fields):
        self.api_read = rkey
        self.api_write = wkey
        self.channel_id = cID
        self.fields = fields

    # We will know the field names as you can get them dyamically with the feeds API like you do in read_field but you should either pass the fields in the __init__ function like I show above or get them dynamically from the channel id in the __init__. YOu can do multiple __init__ functions with differne t parameters to handle different initilizations!
    # def add_field(self, fieldName):
    #     self.fields.append(fieldName)

    # def remove_field(self, fieldName):
    #     try:
    #         self.fields.remove(fieldName)
    #     except ValueError:
    #         print(f"{fieldName} not found, no modifications have occured")

    def read_field(self, field_id):
        # This needs a field id. You should either use the field_id directly or have a dict mapping a string field name to id?
        pass

    def write_field(self, field, data):
        pass
