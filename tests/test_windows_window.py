import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from windows_window import run_window


PAGE = Path(__file__).resolve().parents[1] / "mapper.html"


class NativeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def fire(self, *args):
        for handler in self.handlers:
            handler(*args)


class WindowsWindowTests(unittest.TestCase):
    def setUp(self):
        self.core = Mock()
        self.view = SimpleNamespace(
            CoreWebView2=self.core,
            CoreWebView2InitializationCompleted=NativeEvent(),
            NavigationCompleted=NativeEvent(),
            NavigationStarting=NativeEvent(),
            WebMessageReceived=NativeEvent(),
        )
        self.form = SimpleNamespace(
            webview=self.view, FormClosed=NativeEvent(), Shown=NativeEvent(),
            browser=SimpleNamespace(on_script_notify=Mock(), on_navigation_completed=Mock()),
        )
        self.view.WebMessageReceived += self.form.browser.on_script_notify
        self.view.NavigationCompleted += self.form.browser.on_navigation_completed
        self.form.Close = lambda: self.form.FormClosed.fire(None, None)
        self.timer = Mock(Tick=NativeEvent())
        self.before_show = NativeEvent()
        self.webview = SimpleNamespace(
            create_window=Mock(return_value=SimpleNamespace(
                native=self.form, events=SimpleNamespace(before_show=self.before_show),
            )),
            start=Mock(),
            settings={},
        )
        self.renderer = SimpleNamespace(renderer="edgechromium")
        self.enterContext(patch.dict("sys.modules", {
            "webview": self.webview,
            "webview.guilib": SimpleNamespace(initialize=lambda gui: self.renderer),
            "System.Windows.Forms": SimpleNamespace(Timer=lambda: self.timer),
        }))
        self.enterContext(patch("windows_window._require_edge_runtime"))

    def test_local_window_dispatches_requests_and_drains_replies(self):
        dispatch = Mock()
        poll = Mock(side_effect=[([(1, {"message": "연결"})], False), ([], True)])

        def run_gui(**options):
            self.assertEqual(options, {"gui": "edgechromium", "http_server": False, "private_mode": True})
            self.before_show.fire()
            self.assertFalse(self.view.AllowExternalDrop)
            for url in ("about:blank", "file:///somewhere.html", "https://example.com", "about:blank#other"):
                args = SimpleNamespace(Uri=url)
                self.view.NavigationStarting.fire(None, args)
                self.assertEqual(args.Cancel, url != "about:blank")

            options = self.webview.create_window.call_args.kwargs
            self.assertNotIn("url", options)
            self.assertNotIn("js_api", options)
            self.assertLess(options["html"].index("bridge.postMessage(raw)"),
                            options["html"].index("const nativeBridge"))
            self.view.NavigationCompleted.fire(None, SimpleNamespace(IsSuccess=True))
            self.form.browser.on_navigation_completed.assert_not_called()
            untrusted = SimpleNamespace(Source="https://example.com", TryGetWebMessageAsString=Mock())
            self.view.WebMessageReceived.fire(None, untrusted)
            self.view.WebMessageReceived.fire(None, SimpleNamespace(
                Source="about:blank", TryGetWebMessageAsString=Mock(side_effect=ValueError),
            ))
            untrusted.TryGetWebMessageAsString.assert_not_called()
            dispatch.assert_not_called()
            self.timer.Tick.fire(None, None)
            poll.assert_not_called()
            self.view.WebMessageReceived.fire(None, SimpleNamespace(
                Source="about:blank", TryGetWebMessageAsString=lambda: '{"id":1,"endpoint":"devices"}',
            ))
            self.form.browser.on_script_notify.assert_not_called()
            self.timer.Tick.fire(None, None)
            sent, = self.core.PostWebMessageAsJson.call_args.args
            self.assertEqual(json.loads(sent), [[1, {"message": "연결"}]])
            self.timer.Tick.fire(None, None)

        self.webview.start.side_effect = run_gui
        run_window(PAGE, dispatch, poll)
        dispatch.assert_called_once_with('{"id":1,"endpoint":"devices"}')
        self.timer.Dispose.assert_called_once_with()

    def test_legacy_renderer_does_not_open_a_window(self):
        self.renderer.renderer = "mshtml"
        with self.assertRaisesRegex(RuntimeError, "WebView2"):
            run_window(PAGE, Mock(), Mock())
        self.webview.create_window.assert_not_called()

    def test_initialization_failure_stops_the_window(self):
        def run_gui(**_options):
            self.before_show.fire()
            self.view.CoreWebView2InitializationCompleted.fire(None, SimpleNamespace(
                IsSuccess=False, InitializationException="test startup error",
            ))
            self.timer.Tick.fire(None, None)

        self.webview.start.side_effect = run_gui
        with self.assertRaisesRegex(RuntimeError, "test startup error"):
            run_window(PAGE, Mock(), Mock())
        self.timer.Dispose.assert_called_once_with()

    def test_missing_first_request_reports_the_stalled_phase(self):
        def run_gui(**_options):
            self.before_show.fire()
            self.timer.Tick.fire(None, None)

        self.webview.start.side_effect = run_gui
        with patch("windows_window.monotonic", side_effect=[0, 30]):
            with self.assertRaisesRegex(RuntimeError, "첫 요청"):
                run_window(PAGE, Mock(), Mock())
        self.timer.Dispose.assert_called_once_with()

    def test_native_event_setup_failure_surfaces_when_the_form_shows(self):
        self.view.WebMessageReceived.handlers.clear()
        self.form.Close = Mock()

        def run_gui(**_options):
            self.before_show.fire()
            self.form.Shown.fire(self.form, None)

        self.webview.start.side_effect = run_gui
        with self.assertRaisesRegex(RuntimeError, "창을 준비"):
            run_window(PAGE, Mock(), Mock())
        self.form.Close.assert_called_once_with()
