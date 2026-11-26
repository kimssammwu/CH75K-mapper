"""Run the CH75K desktop editor with a direct WebView message bridge."""

from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout, suppress
import faulthandler
from io import BytesIO
import json
import math
from pathlib import Path
from queue import Empty, Full, Queue, SimpleQueue
from secrets import token_urlsafe
import shutil
import struct
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Event, Thread
import wave

from ch75k import Device, apply_editor_profile, candidates, capture_profile, preview_profile, state_directory
from device_profile import compare_editor_profiles, compare_profile, decode_profile
from hid_common import HidrawAccessError, HidrawError
from protocol import ProtocolError
from settings import BUTTONS


PAGE = Path(__file__).with_name("mapper.html")
MAX_MESSAGE_BYTES = 65_536
SMOKE_SUCCESS = "CH75K_PORTLESS_SMOKE_OK"


def _draft_json(profile: object) -> str:
    if not isinstance(profile, dict) or set(profile) != {"keys"}:
        raise ValueError("올바른 프로필 초안이 필요합니다.")
    keys = profile["keys"]
    if not isinstance(keys, dict) or keys.keys() - BUTTONS.keys():
        raise ValueError("프로필 초안에 지원하지 않는 키가 있습니다.")
    encoded = json.dumps(profile, allow_nan=False, separators=(",", ":"))
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError("프로필 초안이 너무 큽니다.")
    return encoded


def _load_draft(path: Path) -> dict:
    try:
        with path.open("rb") as file:
            content = file.read(MAX_MESSAGE_BYTES + 1)
        if len(content) <= MAX_MESSAGE_BYTES:
            profile = json.loads(content)
            _draft_json(profile)
            return profile
    except (OSError, ValueError, RecursionError):
        pass
    return {"keys": {}}


def _save_draft(path: Path, content: str):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=".ch75k-", delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _play_connection_sound():
    """Play a short, low-volume chime through the system audio player."""
    sample_rate = 24_000
    frames = bytearray()
    for index, frequency in enumerate((560, 760)):
        if index:
            frames.extend(b"\0\0" * (sample_rate // 100))
        length = sample_rate * 9 // 100
        for position in range(length):
            envelope = math.sin(math.pi * position / length) ** 2
            amplitude = math.sin(2 * math.pi * frequency * position / sample_rate)
            frames.extend(struct.pack("<h", int(1800 * envelope * amplitude)))
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        wav.writeframes(frames)
    sound = buffer.getvalue()
    if sys.platform == "win32":
        import winsound

        with suppress(RuntimeError):
            winsound.PlaySound(sound, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
        return

    commands = (("/usr/bin/afplay",),) if sys.platform == "darwin" else (
        ("pw-play",), ("paplay",), ("aplay", "-q"),
    )
    players = [(executable, *options) for name, *options in commands
               if (executable := shutil.which(name))]
    if not players:
        return
    try:
        with NamedTemporaryFile(suffix=".wav") as audio:
            audio.write(sound)
            audio.flush()
            for player in players:
                try:
                    result = subprocess.run([*player, audio.name], stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    continue
                if result.returncode == 0:
                    return
    except OSError:
        # Audio is optional; connection detection remains available.
        pass


class DesktopBridge:
    """Move WebView commands to a worker and replies back to the UI thread."""

    def __init__(self, draft: Path, *, smoke: bool = False):
        self.draft = draft
        self.revision = 0
        self.smoke = smoke
        self.smoke_passed = False
        self.smoke_error = ""
        self.previewed = False
        self.devices: dict[str, tuple[Device, str]] = {}
        self.commands = Queue(maxsize=128)
        self.replies = SimpleQueue()
        self.stopped = Event()
        self.window_closed = Event()
        self.thread = Thread(target=self._run, name="CH75K commands")
        self.thread.start()

    def dispatch(self, raw: str):
        if self.stopped.is_set() or not isinstance(raw, str) or len(raw) > MAX_MESSAGE_BYTES:
            return
        try:
            request = json.loads(raw)
        except (ValueError, RecursionError):
            return
        if not isinstance(request, dict):
            return
        request_id = request.get("id")
        if type(request_id) is not int or not 1 <= request_id <= 2 ** 53 - 1:
            return
        endpoint, data = request.get("endpoint"), request.get("data", {})
        if not isinstance(endpoint, str) or not isinstance(data, dict):
            self.replies.put((request_id, {"error": "올바른 CH75K 요청이 필요합니다."}))
            return
        try:
            self.commands.put_nowait((request_id, endpoint, data))
        except Full:
            self.replies.put((request_id, {"error": "요청이 많습니다. 다시 시도하세요."}))

    def poll(self) -> tuple[list[tuple[int, dict]], bool]:
        replies = []
        while True:
            try:
                replies.append(self.replies.get_nowait())
            except Empty:
                return replies, self.stopped.is_set() and not replies

    def close(self):
        self.window_closed.set()
        self.commands.put(None)
        self.thread.join()

    def _run(self):
        while (request := self.commands.get()) is not None:
            request_id, endpoint, data = request
            try:
                if self.stopped.is_set():
                    raise ValueError("매퍼가 종료 중입니다.")
                reply = self._handle(endpoint, data, request_id=request_id)
            except (OSError, ValueError, HidrawError, ProtocolError, RecursionError) as exc:
                reply = {"error": str(exc)}
            except Exception as exc:
                reply = {"error": f"CH75K 요청을 처리하지 못했습니다. {exc}"}
            self.replies.put((request_id, reply))
            if endpoint == "quit" or self.smoke_error:
                self.stopped.set()

    def _refresh_devices(self):
        devices = {}
        new_attachment = False
        for device in candidates():
            path = str(device.path)
            previous = self.devices.get(path)
            if previous is not None and device == previous[0]:
                devices[path] = (device, previous[1])
            else:
                devices[path] = (device, token_urlsafe(18))
                new_attachment = True
        self.devices = devices
        if new_attachment:
            Thread(target=_play_connection_sound, name="CH75K connection sound", daemon=True).start()

    def _device_inventory(self) -> dict:
        return {"devices": [
            {"id": path, "label": f"{device.name} ({device.vid:04x}:{device.pid:04x})", "identity": identity}
            for path, (device, identity) in self.devices.items()
        ]}

    def _resume_device(self, device: Device, identity: str, *, request_id: int) -> dict:
        if self.window_closed.is_set():
            raise ValueError("창이 닫혀 장치 작업을 취소했습니다.")
        self.replies.put((request_id, {
            "progress": "저장된 설정을 읽고 CH75K를 키 입력 모드로 되돌리는 중입니다.",
        }))
        snapshot = self._with_device_access(capture_profile, device, request_id=request_id, resume=True)
        inventory = self._device_inventory()
        return {
            **inventory,
            "device": next(entry for entry in inventory["devices"] if entry["identity"] == identity),
            **decode_profile(snapshot.base, snapshot.image),
            "message": "저장된 설정을 다시 읽고 키 입력 모드 복귀 명령을 보냈습니다.",
        }

    def _handle(self, endpoint: str, data: dict, *, request_id: int) -> dict:
        if endpoint == "devices":
            if not self.smoke:
                self._refresh_devices()
            return self._device_inventory()
        if endpoint == "draft-load":
            return {"profile": _load_draft(self.draft), "revision": self.revision}
        if endpoint == "draft":
            revision = data.get("revision")
            if type(revision) is not int or not 1 <= revision <= 2 ** 53 - 1:
                raise ValueError("올바른 프로필 초안 버전이 필요합니다.")
            if revision > self.revision:
                _save_draft(self.draft, _draft_json(data.get("profile")))
                self.revision = revision
            return {}
        if endpoint == "preview":
            result = preview_profile(data.get("profile"))
            self.previewed = True
            return {"message": result}
        if endpoint == "profile-changes":
            draft = json.loads(_draft_json(data.get("profile")))
            baseline = json.loads(_draft_json(data.get("baseline")))
            return {"changes": compare_editor_profiles(baseline, draft)["changes"]}
        if endpoint == "quit":
            return {"message": "매퍼를 종료했습니다."}
        if self.smoke and endpoint == "smoke-result":
            self.smoke_passed = data.get("ok") is True and self.previewed
            if not self.smoke_passed:
                self.smoke_error = str(data.get("error") or "매크로 미리 보기 요청을 받지 못했습니다.")
            return {}
        if not self.smoke and endpoint in ("compare", "apply", "reload"):
            path, identity = data.get("device"), data.get("identity")
            if (not isinstance(path, str) or not isinstance(identity, str)
                    or data.get("confirmed") is not True):
                raise ValueError("새 장치 확인 창에서 사용할 CH75K를 확인해 주세요.")
            self._refresh_devices()
            attachment = self.devices.get(path)
            if attachment is None or identity != attachment[1]:
                raise ValueError("CH75K 연결이 바뀌었습니다. 장치를 새로고침해 주세요.")
            device = attachment[0]
            if endpoint == "reload":
                return self._resume_device(device, identity, request_id=request_id)
            draft = json.loads(_draft_json(data.get("profile")))
            if endpoint == "apply":
                return self._with_device_access(apply_editor_profile, device, draft, request_id=request_id)
            snapshot = self._with_device_access(capture_profile, device, request_id=request_id)
            return compare_profile(snapshot.base, snapshot.image, draft)
        raise ValueError("요청한 작업을 찾지 못했습니다.")

    def _with_device_access(self, operation, device, *arguments, request_id, **options):
        try:
            return operation(device, *arguments, **options)
        except HidrawAccessError as error:
            if not sys.platform.startswith("linux") or self.window_closed.is_set():
                raise
            from linux_permissions import request_hid_access

            self.replies.put((request_id, {
                "progress": "CH75K USB 접근 권한이 필요합니다. 시스템 인증 창에서 승인해 주세요.",
            }))
            request_hid_access(error.device)
            if self.window_closed.is_set():
                raise RuntimeError("창이 닫혀 장치 작업을 취소했습니다.")
            # Reopen the same identity snapshot, even if a hidraw name has been reused.
            return operation(error.device, *arguments, **options)


def _run_window(page: Path, bridge: DesktopBridge):
    try:
        if sys.platform == "darwin":
            from macos_window import run_window
        elif sys.platform == "win32":
            from windows_window import run_window
        else:
            from linux_window import run_window
        run_window(page, bridge.dispatch, bridge.poll)
    finally:
        bridge.close()


SMOKE_SCRIPT = """
<script>
(async () => {
  const report = (data) => window.webkit.messageHandlers.ch75k.postMessage(JSON.stringify({
    id: 900000000, endpoint: 'smoke-result', data
  }));
  const waitFor = async (when) => {
    for (let count = 0; count < 100; count++) {
      if (when()) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error('Timed out waiting for native WebView UI');
  };
  try {
    await waitFor(() => document.querySelector('#iso-bottom-center [data-control]') ||
      document.querySelector('#iso-bottom-center')?.getAttribute('aria-pressed') === 'false');
    await waitFor(() => !document.querySelector('#quit-button').hidden);
    await waitFor(() => document.querySelector('#device-status').textContent === '연결 대기');
    document.querySelector('#iso-bottom-center').dispatchEvent(new MouseEvent('click', {bubbles:true}));
    if (document.querySelector('#position-name').textContent !== '복사') throw new Error('3D selection failed');
    document.querySelector('[data-action-type=macro]').click();
    const input = document.querySelector('#macro-input');
    input.value = 'Ctrl+C, @120, Ctrl+V';
    input.dispatchEvent(new Event('input', {bubbles:true}));
    await waitFor(() => document.querySelector('#preview-content').textContent.includes('120'));
    await new Promise(resolve => setTimeout(resolve, 750));
    report({ok:true});
    document.querySelector('#quit-button').click();
  } catch (error) {
    report({ok:false, error:String(error)});
  }
})();
</script>
"""


def _smoke_test():
    with TemporaryDirectory(prefix="ch75k-webview-smoke-") as directory:
        folder = Path(directory)
        page = folder / PAGE.name
        content = PAGE.read_text(encoding="utf-8")
        if "</body>" not in content:
            raise RuntimeError("CH75K 테스트 화면을 만들 수 없습니다.")
        page.write_text(content.replace("</body>", SMOKE_SCRIPT + "</body>", 1), encoding="utf-8")
        draft = folder / "draft.json"
        bridge = DesktopBridge(draft, smoke=True)
        _run_window(page, bridge)
        profile = _load_draft(draft)
        if not bridge.smoke_passed or "bottom-center" not in profile["keys"]:
            raise RuntimeError(bridge.smoke_error or "3D 매크로 편집과 초안 저장을 확인하지 못했습니다.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CH75K Studio")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.smoke_result is not None and not args.smoke_test:
        parser.error("--smoke-result requires --smoke-test")
    if sys.platform == "win32" and args.smoke_result is not None:
        # A windowed executable has no standard streams for reporting test failures.
        log_path = args.smoke_result.with_suffix(".log")
        with log_path.open("w", encoding="utf-8", buffering=1) as log, redirect_stderr(log), redirect_stdout(log):
            faulthandler.dump_traceback_later(60, file=log)
            try:
                return _launch(args)
            finally:
                faulthandler.cancel_dump_traceback_later()
    return _launch(args)


def _launch(args: argparse.Namespace) -> int:
    try:
        if not PAGE.is_file():
            raise FileNotFoundError(PAGE)
        if args.smoke_test:
            _smoke_test()
            if args.smoke_result is not None:
                args.smoke_result.write_text(SMOKE_SUCCESS, encoding="utf-8")
            elif sys.stdout is not None:
                print(SMOKE_SUCCESS, flush=True)
        else:
            if sys.platform.startswith("linux"):
                from linux_desktop import register_desktop_icon

                register_desktop_icon()
            _run_window(PAGE, DesktopBridge(state_directory() / "draft.json"))
    except Exception as exc:
        if args.smoke_result is None and not isinstance(exc, (OSError, RuntimeError, ValueError)):
            raise
        message = f"CH75K Studio를 열지 못했습니다. {exc}"
        if args.smoke_result is not None:
            with suppress(OSError):
                args.smoke_result.write_text(message, encoding="utf-8")
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        elif sys.platform == "win32" and not args.smoke_test:
            from ctypes import windll

            windll.user32.MessageBoxW(None, message, "CH75K Studio", 0x10)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
