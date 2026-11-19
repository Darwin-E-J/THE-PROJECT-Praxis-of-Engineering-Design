"""
IFTTT webhook notification client stub for air-quality push alerts.
"""


class IFTTT:
    """Wrapper for triggering IFTTT webhook notifications using an API key."""

    def __init__(self, apikey):
        """Store the IFTTT Webhooks API key."""
        self.api_key = apikey

    def trigger_push(self):
        """Send a push notification event to IFTTT (not yet implemented)."""
        pass
