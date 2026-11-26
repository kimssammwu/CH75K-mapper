import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from desktop_app import DesktopBridge


PROFILE = {"keys": {"bottom-center": "Ctrl+C, @120, Ctrl+V"}}


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.draft = Path(directory) / "draft.json"
        self.bridge = DesktopBridge(self.draft)
        self.addCleanup(self.bridge.close)
        self.request_id = 0

    def send(self, endpoint, data=None):
        self.request_id += 1
        request_id = self.request_id
        self.bridge.dispatch(json.dumps({"id": request_id, "endpoint": endpoint, "data": data or {}}))
        deadline = monotonic() + 2
        while monotonic() < deadline:
            replies, _ = self.bridge.poll()
            if request_id in dict(replies):
                return dict(replies)[request_id]
            sleep(0.005)
        self.fail(f"No response to {endpoint}")

    def test_preview_and_draft_round_trip(self):
        self.assertIn("Ctrl+C, @120, Ctrl+V", self.send("preview", {"profile": PROFILE})["message"])
        self.assertEqual(self.send("draft-load"), {"profile": {"keys": {}}, "revision": 0})
        self.assertEqual(self.send("draft", {"profile": PROFILE, "revision": 2}), {})
        self.assertEqual(self.send("draft", {"profile": {"keys": {}}, "revision": 1}), {})
        self.assertEqual(self.send("draft-load"), {"profile": PROFILE, "revision": 2})
        self.assertEqual(json.loads(self.draft.read_text()), PROFILE)

    def test_device_operations_require_exact_confirmation_and_current_device(self):
        devices = [("/dev/hidraw9", "CH75K")]
        with (
            patch("desktop_app.find_devices", return_value=devices),
            patch("desktop_app.read_profile", return_value="device setup") as read,
            patch("desktop_app.apply_profile", return_value="applied") as apply,
        ):
            self.assertEqual(self.send("devices"), {"devices": [{"id": devices[0][0], "label": "CH75K"}]})
            self.assertIn("error", self.send("read", {"device": devices[0][0], "confirmed": 1}))
            self.assertIn("error", self.send("apply", {"device": "/dev/hidraw0", "confirmed": True, "profile": PROFILE}))
            read.assert_not_called()
            apply.assert_not_called()
            confirmed = {"device": devices[0][0], "confirmed": True}
            self.assertEqual(self.send("read", confirmed), {"message": "device setup"})
            self.assertEqual(self.send("apply", {**confirmed, "profile": PROFILE}), {"message": "applied"})
            read.assert_called_once_with(devices[0][0])
            apply.assert_called_once_with(devices[0][0], PROFILE)

    def test_quit_acknowledges_before_closing_and_unknown_endpoint_is_denied(self):
        self.assertIn("error", self.send("unknown"))
        self.assertIn("종료", self.send("quit")["message"])
        self.assertTrue(self.bridge.stopped.wait(1))
        self.assertEqual(self.bridge.poll(), ([], True))
