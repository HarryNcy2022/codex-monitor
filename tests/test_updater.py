import io
import json
import unittest
import urllib.error

import codex_monitor_app.updater as updater


class FakeResponse:
    def __init__(self, payload=None, url="https://example.com"):
        self.payload = payload
        self.url = url

    def __enter__(self):
        if self.payload is None:
            return self
        return io.StringIO(json.dumps(self.payload))

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False

    def geturl(self):
        return self.url


class UpdaterTests(unittest.TestCase):
    def test_fetch_latest_release_uses_api_payload(self):
        calls = []
        payload = {
            "tag_name": "v1.1.7",
            "html_url": "https://github.com/koodev24/codex-monitor/releases/tag/v1.1.7",
            "assets": [
                {
                    "name": "CodexMonitor-macOS.zip",
                    "browser_download_url": "https://example.com/download.zip",
                }
            ],
        }

        def fake_urlopen(request, context=None, timeout=0):
            calls.append(request.full_url)
            return FakeResponse(payload=payload)

        original_urlopen = updater.urllib.request.urlopen
        updater.urllib.request.urlopen = fake_urlopen
        try:
            release = updater.fetch_latest_release()
        finally:
            updater.urllib.request.urlopen = original_urlopen

        self.assertEqual(calls, [updater.RELEASES_API_URL])
        self.assertEqual(release.version, "1.1.7")
        self.assertEqual(release.asset_url, "https://example.com/download.zip")

    def test_fetch_latest_release_falls_back_when_api_rate_limited(self):
        calls = []

        def fake_urlopen(request, context=None, timeout=0):
            calls.append(request.full_url)
            if request.full_url == updater.RELEASES_API_URL:
                raise urllib.error.HTTPError(
                    request.full_url,
                    403,
                    "rate limit exceeded",
                    hdrs=None,
                    fp=None,
                )
            return FakeResponse(
                url="https://github.com/koodev24/codex-monitor/releases/tag/v1.1.7"
            )

        original_urlopen = updater.urllib.request.urlopen
        updater.urllib.request.urlopen = fake_urlopen
        try:
            release = updater.fetch_latest_release()
        finally:
            updater.urllib.request.urlopen = original_urlopen

        self.assertEqual(calls, [updater.RELEASES_API_URL, updater.RELEASES_PAGE_URL])
        self.assertEqual(release.tag_name, "v1.1.7")
        self.assertEqual(release.version, "1.1.7")
        self.assertEqual(
            release.asset_url,
            "https://github.com/koodev24/codex-monitor/releases/download/v1.1.7/CodexMonitor-macOS.zip",
        )


if __name__ == "__main__":
    unittest.main()
