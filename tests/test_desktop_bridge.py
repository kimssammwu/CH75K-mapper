import json
from pathlib import Path
from queue import Queue
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

from ch75k import DeviceSnapshot
from desktop_app import DesktopBridge
from hid_common import HidrawAccessError, HidrawError
from settings import MACRO_END
from test_settings_link import BASE, factory_image


PROFILE = {"keys": {"bottom-center": "Ctrl+C, @120, Ctrl+V"}}


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.draft = Path(directory) / "draft.json"
        self.bridge = DesktopBridge(self.draft)
        self.addCleanup(self.bridge.close)
        self.sound = self.enterContext(patch("desktop_app._play_connection_sound"))
        self.request_id = 0
        self.progress = []

    def send(self, endpoint, data=None):
        self.request_id += 1
        request_id = self.request_id
        self.bridge.dispatch(json.dumps({"id": request_id, "endpoint": endpoint, "data": data or {}}))
        deadline = monotonic() + 2
        while monotonic() < deadline:
            replies, _ = self.bridge.poll()
            for reply_id, reply in replies:
                if reply_id == request_id:
                    if "progress" not in reply:
                        return reply
                    self.progress.append(reply["progress"])
            sleep(0.005)
        self.fail(f"No response to {endpoint}")

    def test_preview_and_draft_round_trip(self):
        self.assertIn("Ctrl+C, @120, Ctrl+V", self.send("preview", {"profile": PROFILE})["message"])
        self.assertEqual(self.send("draft-load"), {"profile": {"keys": {}}, "revision": 0})
        self.assertEqual(self.send("draft", {"profile": PROFILE, "revision": 2}), {})
        self.assertEqual(self.send("draft", {"profile": {"keys": {}}, "revision": 1}), {})
        self.assertEqual(self.send("draft-load"), {"profile": PROFILE, "revision": 2})
        self.assertEqual(json.loads(self.draft.read_text()), PROFILE)

    def attachment(self, path="/dev/hidraw8", instance=1):
        return SimpleNamespace(path=Path(path), name="CH75K", vid=0x30FA, pid=0x2350, instance=instance)

    def confirmed(self, device, profile=PROFILE):
        entry = self.send("devices")["devices"][0]
        self.assertEqual(entry["label"], "CH75K (30fa:2350)")
        return {"device": str(device.path), "identity": entry["identity"], "confirmed": True, "profile": profile}

    def test_connection_sound_plays_only_for_new_attachments(self):
        devices = [self.attachment()]
        played = Queue()
        self.sound.side_effect = lambda: played.put(True)
        with patch("desktop_app.candidates", side_effect=[[], devices, devices, [], devices, devices]):
            self.send("devices")
            self.sound.assert_not_called()
            first = self.send("devices")
            self.assertTrue(played.get(timeout=1))
            self.assertEqual(self.send("devices"), first)
            self.send("devices")
            self.sound.assert_called_once()
            self.assertNotEqual(self.send("devices"), first)
            self.assertTrue(played.get(timeout=1))
            self.send("devices")
            self.assertEqual(self.sound.call_count, 2)

    def test_apply_requires_exact_confirmation_and_a_current_attachment(self):
        device = self.attachment()
        replacement = self.attachment(instance=2)
        saved = {"message": "applied", "profile": PROFILE, "unsupported": {}}
        with (
            patch("desktop_app.candidates", return_value=[device]) as candidates,
            patch("desktop_app.capture_profile") as capture,
            patch("desktop_app.apply_editor_profile", return_value=saved) as apply,
        ):
            confirmed = self.confirmed(device)
            self.assertIn("error", self.send("apply", {**confirmed, "confirmed": 1}))
            self.assertIn("error", self.send("apply", {**confirmed, "identity": "old"}))
            self.assertIn("error", self.send("apply", {**confirmed, "device": "/dev/hidraw0"}))
            apply.assert_not_called()
            self.assertEqual(self.send("apply", confirmed), saved)
            apply.assert_called_once_with(device, PROFILE)
            capture.assert_not_called()
            candidates.return_value = [replacement]
            self.assertIn("새로고침", self.send("apply", confirmed)["error"])
            apply.assert_called_once()
            fresh = self.confirmed(replacement)
            self.assertNotEqual(fresh["identity"], confirmed["identity"])
            self.assertEqual(self.send("apply", fresh), saved)
            apply.assert_called_with(replacement, PROFILE)

    def test_quit_acknowledges_before_closing_and_unknown_endpoint_is_denied(self):
        self.assertIn("error", self.send("unknown"))
        self.assertIn("종료", self.send("quit")["message"])
        self.assertTrue(self.bridge.stopped.wait(1))
        self.assertEqual(self.bridge.poll(), ([], True))

    def test_comparing_a_device_reads_actual_mappings_without_writing(self):
        device = self.attachment()
        snapshot = DeviceSnapshot(device, BASE, bytes(factory_image()[BASE:MACRO_END]))
        with (
            patch("desktop_app.candidates", return_value=[device]),
            patch("desktop_app.capture_profile", return_value=snapshot) as capture,
            patch("desktop_app.apply_editor_profile") as apply,
        ):
            compared = self.send("compare", self.confirmed(device, {"keys": {"top-left": "Esc"}}))
            self.assertEqual(compared["profile"]["keys"]["top-left"], "Enter")
            self.assertEqual(compared["changes"], {"keys": {"top-left": "Esc"}})
            capture.assert_called_once_with(device)
            apply.assert_not_called()

    def test_refresh_restores_input_mode_on_the_confirmed_attachment_without_writing(self):
        device = self.attachment()
        snapshot = DeviceSnapshot(device, BASE, bytes(factory_image()[BASE:MACRO_END]))
        with (
            patch("desktop_app.candidates", return_value=[device]),
            patch("desktop_app.capture_profile", return_value=snapshot) as capture,
            patch("desktop_app.apply_editor_profile") as apply,
        ):
            confirmed = self.confirmed(device)
            reply = self.send("reload", confirmed)
            self.assertEqual(reply["device"]["id"], str(device.path))
            self.assertEqual(reply["device"]["identity"], confirmed["identity"])
            self.assertEqual(reply["profile"]["keys"]["top-left"], "Enter")
            capture.assert_called_once_with(device, resume=True)
            apply.assert_not_called()

    def test_refresh_rejects_a_replacement_at_the_same_path(self):
        device = self.attachment()
        replacement = self.attachment(instance=2)
        with (
            patch("desktop_app.candidates", side_effect=[[device], [replacement], [replacement]]),
            patch("desktop_app.capture_profile") as capture,
        ):
            confirmed = self.confirmed(device)
            self.assertIn("연결이 바뀌었습니다", self.send("reload", confirmed)["error"])
            capture.assert_not_called()
            self.assertNotEqual(self.confirmed(replacement)["identity"], confirmed["identity"])

    def test_profile_changes_only_contains_different_explicit_mappings(self):
        baseline = {"keys": {"top-left": "Enter", "bottom-center": {
            "type": "macro", "sequence": "Ctrl+C", "repeat": "count", "count": 1,
        }}}
        with patch("desktop_app.candidates") as scan:
            self.assertEqual(self.send("profile-changes", {"baseline": baseline, "profile": {
                "keys": {"top-left": "Esc", "bottom-center": "ctrl+c"},
            }}), {"changes": {"keys": {"top-left": "Esc"}}})
            self.assertEqual(self.send("profile-changes", {"baseline": baseline, "profile": {
                "keys": {"bottom-center": "Ctrl+C"},
            }}), {"changes": {"keys": {}}})
            self.assertIn("error", self.send("profile-changes", {
                "baseline": baseline, "profile": {"keys": {"top-left": "Ctrl+"}},
            }))
            scan.assert_not_called()

    @unittest.skipIf(sys.platform == "win32", "The Linux hidraw permission helper uses POSIX APIs.")
    def test_linux_permission_retries_read_or_apply_once_with_original_device(self):
        device = self.attachment()
        snapshot = DeviceSnapshot(device, BASE, bytes(factory_image()[BASE:MACRO_END]))
        for endpoint, operation, arguments, result in (
            ("compare", "capture_profile", (), snapshot),
            ("apply", "apply_editor_profile", (PROFILE,), {"message": "done"}),
        ):
            with (
                self.subTest(endpoint=endpoint),
                patch("desktop_app.sys.platform", "linux"),
                patch("desktop_app.candidates", return_value=[device]),
                patch(f"desktop_app.{operation}", side_effect=[HidrawAccessError(device), result]) as run,
                patch("linux_permissions.request_hid_access") as authorize,
            ):
                self.progress.clear()
                self.assertNotIn("error", self.send(endpoint, self.confirmed(device)))
                self.assertEqual(run.call_args_list, [call(device, *arguments), call(device, *arguments)])
                authorize.assert_called_once_with(device)
                self.assertEqual(len(self.progress), 1)
                self.assertIn("시스템 인증", self.progress[0])

    @unittest.skipIf(sys.platform == "win32", "The Linux hidraw permission helper uses POSIX APIs.")
    def test_permission_cancellation_and_workspace_errors_do_not_retry(self):
        device = self.attachment()
        with (
            patch("desktop_app.sys.platform", "linux"),
            patch("desktop_app.candidates", return_value=[device]),
            patch("desktop_app.apply_editor_profile", side_effect=HidrawAccessError(device)) as apply,
            patch("linux_permissions.request_hid_access", side_effect=HidrawError("시스템 인증을 취소했습니다.")) as authorize,
        ):
            arguments = self.confirmed(device)
            self.assertIn("인증을 취소", self.send("apply", arguments)["error"])
            apply.assert_called_once_with(device, PROFILE)
            authorize.assert_called_once_with(device)
            authorize.reset_mock()
            apply.reset_mock(side_effect=True)
            apply.side_effect = PermissionError("백업 파일 권한 오류")
            self.assertIn("백업 파일 권한 오류", self.send("apply", arguments)["error"])
            apply.assert_called_once_with(device, PROFILE)
            authorize.assert_not_called()

    @unittest.skipIf(sys.platform == "win32", "The Linux hidraw permission helper uses POSIX APIs.")
    def test_linux_open_error_after_authorization_does_not_prompt_again(self):
        device = self.attachment()
        with (
            patch("desktop_app.sys.platform", "linux"),
            patch("desktop_app.candidates", return_value=[device]),
            patch("desktop_app.capture_profile", side_effect=HidrawAccessError(device)) as capture,
            patch("linux_permissions.request_hid_access") as authorize,
        ):
            self.assertIn("접근 권한", self.send("compare", self.confirmed(device))["error"])
            self.assertEqual(capture.call_args_list, [call(device), call(device)])
            authorize.assert_called_once_with(device)

    @unittest.skipIf(sys.platform == "win32", "The Linux hidraw permission helper uses POSIX APIs.")
    def test_elevated_rule_matches_the_manual_install_rule(self):
        from linux_permissions import _ROOT_PROGRAM, _RULE

        rules = Path(__file__).resolve().parents[1] / "70-ch75k-native.rules"
        self.assertEqual(_RULE, rules.read_bytes())
        compile(_ROOT_PROGRAM, "CH75K privileged routine", "exec")
