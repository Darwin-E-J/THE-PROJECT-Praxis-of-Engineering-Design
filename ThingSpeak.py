import requests
import json

class ThingSpeak:
    def __init__(self, rkey, wkey, cID):
        self.api_read = rkey
        self.api_write = wkey
        self.channel_id = cID
        self.fields = []

    def add_field(self, fieldName):
        self.fields.append(fieldName)

    def remove_field(self, fieldName):
        try:
            self.fields.remove(fieldName)
        except ValueError:
            print(f"{fieldName} not found, no modifications have occured")

    def read_field(self, results):
        thingSpeak_read = requests.get(f'https://api.thingspeak.com/channels/{self.channel_id}/feeds.json?api_key={self.api_read}&results=2')
        pass

    def write_field(self, data):
        pass
