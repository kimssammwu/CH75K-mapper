"""Host the bundled mapper in Windows WebView2 without a local server."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic


RUNTIME_MESSAGE = "Microsoft Edge WebView2 Evergreen 런타임을 설치한 뒤 CH75K 매퍼를 다시 실행하세요."
RUNTIME_REGISTRY_KEY = r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
LOCAL_HOST = "ch75k.invalid"
DOCUMENT_NAME = "mapper.html"
DOCUMENT_URL = f"https://{LOCAL_HOST}/{DOCUMENT_NAME}"

# Define the mapper's bridge before its script executes. WebView2 delivers
# these messages in order without a second injected JavaScript API.
BRIDGE_SCRIPT = """
<script>
(() => {
  const bridge = window.chrome.webview;
  window.webkit ??= {};
  window.webkit.messageHandlers ??= {};
  window.webkit.messageHandlers.ch75k = {postMessage: raw => bridge.postMessage(raw)};
  bridge.addEventListener("message", event => {
    for (const [id, response] of event.data) window.ch75kReply?.(id, response);
  });
})();
</script>
"""


def _require_edge_runtime():
    # Evergreen registers in the machine's 32-bit view or the user's registry.
    import winreg

    for hive, flags in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                        (winreg.HKEY_CURRENT_USER, 0)):
        try:
            with winreg.OpenKey(hive, RUNTIME_REGISTRY_KEY, 0, winreg.KEY_READ | flags) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
            if tuple(map(int, version.split("."))) > (0, 0, 0, 0):
                return
        except (OSError, AttributeError, ValueError):
            continue
    raise RuntimeError(RUNTIME_MESSAGE)


def run_window(page: Path, dispatch, poll) -> None:
    """Run the local WinForms window until the user or the bridge closes it."""
    content = page.resolve(strict=True).read_text(encoding="utf-8")
    if "</head>" not in content:
        raise ValueError("CH75K 화면의 헤더를 찾지 못했습니다.")
    content = content.replace("</head>", BRIDGE_SCRIPT + "</head>", 1)
    with TemporaryDirectory(prefix="ch75k-webview-page-") as directory:
        documents = Path(directory).resolve()
        (documents / DOCUMENT_NAME).write_text(content, encoding="utf-8")
        _run_webview(documents, dispatch, poll)


def _run_webview(documents: Path, dispatch, poll):
    _require_edge_runtime()
    try:
        import webview
        from webview.guilib import initialize
    except ImportError as exc:
        raise RuntimeError("Windows용 requirements-windows.txt의 pywebview가 필요합니다.") from exc

    # Setting gui alone does not stop this pywebview version falling back to MSHTML.
    if initialize("edgechromium").renderer != "edgechromium":
        raise RuntimeError(RUNTIME_MESSAGE)
    from Microsoft.Web.WebView2.Core import CoreWebView2HostResourceAccessKind
    from System.Windows.Forms import Timer

    webview.settings.update({
        "ALLOW_DOWNLOADS": True,  # The bundled page exports a local profile Blob.
        "ALLOW_FILE_URLS": False,
        "OPEN_EXTERNAL_LINKS_IN_BROWSER": False,
    })
    window = webview.create_window("CH75K 매퍼",
                                   width=1280, height=900, min_size=(700, 600))
    failure = None

    def prepare(form):
        nonlocal failure
        view = form.webview
        deadline = monotonic() + 25
        received_request = False
        mapper_started = False
        navigation_id = None
        timer = Timer()
        timer.Interval = 35

        def keep_local(_sender, args):
            nonlocal navigation_id
            args.Cancel = str(args.Uri) != DOCUMENT_URL
            if not args.Cancel:
                navigation_id = args.NavigationId

        def initialized(_sender, args):
            nonlocal failure
            if not args.IsSuccess:
                failure = RuntimeError(f"{RUNTIME_MESSAGE}\n{args.InitializationException}")

        def receive_message(_sender, args):
            nonlocal received_request
            if str(args.Source) != DOCUMENT_URL:
                return
            try:
                payload = args.TryGetWebMessageAsString()
            except Exception:
                # WebView2 raises if a page sends an object instead of a string.
                return
            if isinstance(payload, str):
                dispatch(payload)
                received_request = True

        def navigation_finished(_sender, args):
            nonlocal failure
            if args.NavigationId == navigation_id and not args.IsSuccess and not received_request:
                failure = RuntimeError(f"Windows WebView2가 CH75K 화면을 열지 못했습니다. {args.WebErrorStatus}")

        def drain(_sender, _args):
            nonlocal failure, mapper_started
            try:
                if failure is None:
                    core = view.CoreWebView2
                    if core is not None and not mapper_started:
                        # Register the local origin before starting the mapper, whether
                        # the control was initialized before or after before_show.
                        core.SetVirtualHostNameToFolderMapping(
                            LOCAL_HOST, str(documents), CoreWebView2HostResourceAccessKind.Deny,
                        )
                        core.Navigate(DOCUMENT_URL)
                        mapper_started = True
                    if not received_request:
                        if monotonic() >= deadline:
                            if core is None:
                                raise RuntimeError(RUNTIME_MESSAGE)
                            raise RuntimeError("Windows WebView2에서 CH75K 화면의 첫 요청을 받지 못했습니다.")
                        return
                    replies, stopped = poll()
                    if replies:
                        core.PostWebMessageAsJson(json.dumps(replies, allow_nan=False))
                    if not stopped:
                        return
            except Exception as exc:
                failure = RuntimeError(f"Windows WebView2에서 매퍼를 실행하지 못했습니다. {exc}")
            timer.Stop()
            form.Close()

        def stop_timer(_sender, _args):
            timer.Stop()
            timer.Dispose()

        view.AllowExternalDrop = False
        # pywebview 6.2.1 installs these when it creates its native control.
        # Use that control and its dialogs without injecting its own RPC layer.
        view.WebMessageReceived -= form.browser.on_script_notify
        view.NavigationCompleted -= form.browser.on_navigation_completed
        view.WebMessageReceived += receive_message
        view.NavigationStarting += keep_local
        view.NavigationCompleted += navigation_finished
        view.CoreWebView2InitializationCompleted += initialized
        form.FormClosed += stop_timer
        timer.Tick += drain
        timer.Start()

    def before_show():
        nonlocal failure
        form = window.native
        try:
            prepare(form)
        except Exception as exc:
            # pywebview logs and swallows before_show errors. Close after the
            # form is shown so Python gets the actual error and can report it.
            failure = RuntimeError(f"Windows WebView2 창을 준비하지 못했습니다. {exc}")
            form.Shown += lambda sender, _args: sender.Close()

    window.events.before_show += before_show
    webview.start(gui="edgechromium", http_server=False, private_mode=True)
    if failure is not None:
        raise failure
