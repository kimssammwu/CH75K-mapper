"""Run the CH75K desktop editor with a direct WebView message bridge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from queue import Empty, Full, Queue, SimpleQueue
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Event, Thread

from ch75k import apply_profile, find_devices, preview_profile, read_profile, state_directory
from hid_common import HidrawError
from protocol import ProtocolError
from settings import BUTTONS


PAGE = Path(__file__).with_name("mapper.html")
MAX_MESSAGE_BYTES = 65_536


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


class DesktopBridge:
    """Move WebView commands to a worker and replies back to the UI thread."""

    def __init__(self, draft: Path, *, smoke: bool = False):
        self.draft = draft
        self.revision = 0
        self.smoke = smoke
        self.smoke_passed = False
        self.smoke_error = ""
        self.previewed = False
        self.commands = Queue(maxsize=128)
        self.replies = SimpleQueue()
        self.stopped = Event()
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
        self.commands.put(None)
        self.thread.join()

    def _run(self):
        while (request := self.commands.get()) is not None:
            request_id, endpoint, data = request
            try:
                if self.stopped.is_set():
                    raise ValueError("매퍼가 종료 중입니다.")
                reply = self._handle(endpoint, data)
            except (OSError, ValueError, HidrawError, ProtocolError, RecursionError) as exc:
                reply = {"error": str(exc)}
            except Exception as exc:
                reply = {"error": f"CH75K 요청을 처리하지 못했습니다. {exc}"}
            self.replies.put((request_id, reply))
            if endpoint == "quit" or self.smoke_error:
                self.stopped.set()

    def _handle(self, endpoint: str, data: dict) -> dict:
        if endpoint == "devices":
            devices = [] if self.smoke else find_devices()
            return {"devices": [{"id": path, "label": label} for path, label in devices]}
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
        if endpoint == "quit":
            return {"message": "매퍼를 종료했습니다."}
        if self.smoke and endpoint == "smoke-result":
            self.smoke_passed = data.get("ok") is True and self.previewed
            if not self.smoke_passed:
                self.smoke_error = str(data.get("error") or "매크로 미리 보기 요청을 받지 못했습니다.")
            return {}
        if not self.smoke and endpoint in ("read", "apply"):
            device = data.get("device")
            if not isinstance(device, str) or not device or data.get("confirmed") is not True:
                raise ValueError("장치를 선택하고 CH75K 모델을 확인해 주세요.")
            if device not in {path for path, _ in find_devices()}:
                raise ValueError("선택한 장치를 다시 연결해 주세요.")
            result = read_profile(device) if endpoint == "read" else apply_profile(device, data.get("profile"))
            return {"message": result}
        raise ValueError("요청한 작업을 찾지 못했습니다.")


def _run_window(page: Path, bridge: DesktopBridge):
    try:
        if sys.platform == "darwin":
            from macos_window import run_window
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
    print("CH75K_PORTLESS_SMOKE_OK", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CH75K 데스크톱 매퍼")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if not PAGE.is_file():
            raise FileNotFoundError(PAGE)
        if args.smoke_test:
            _smoke_test()
        else:
            _run_window(PAGE, DesktopBridge(state_directory() / "draft.json"))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"CH75K 매퍼를 열지 못했습니다. {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
