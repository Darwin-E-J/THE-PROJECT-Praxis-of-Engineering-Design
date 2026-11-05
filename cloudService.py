class ThingSpeak:
    fields = []

    def __init__(self, rkey, wkey, cID):
        self.api_read = rkey
        self.api_write = wkey
        self.channel_id = cID

    def add_field(self, fieldName):
        self.fields.append(fieldName)

    def remove_field(self, fieldName):
        try:
            self.fields.remove(fieldName)
        except ValueError:
            print(f"{fieldName} not found, no modifications have occured")

    def read_field(self, results):
        pass

    def write_field(self, data):
        pass

class IFTTT:
    def __init__(self, apikey):
        self.api_key = apikey
    def trigger_push(self):
        pass
