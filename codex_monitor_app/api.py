import json
import ssl
import urllib.request

import certifi

from .config import USAGE_API_URL
from .models import UsageResponse


class UsageApiClient:
    def __init__(self, usage_api_url: str = USAGE_API_URL):
        self.usage_api_url = usage_api_url

    def fetch_usage(self, jwt: str) -> UsageResponse:
        request = urllib.request.Request(self.usage_api_url)
        request.add_header("Authorization", f"Bearer {jwt}")
        request.add_header(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )

        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, context=context, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
