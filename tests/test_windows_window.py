import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from windows_window import run_window


PAGE = Path(__file__).resolve().parents[1] / "mapper.html"


class NativeEvent:
    def __iadd__(self, handler):
        self.handler = handler
        return self

    def fire(self, *args):
        self.handler(*args)


class WindowsWindowTests(unittest.TestCase):
    def setUp(self):
        self.core = Mock()
        self.view = SimpleNamespace(
            CoreWebView2=self.core,
            CoreWebView2InitializationCompleted=NativeEvent(),
            NavigationStarting=NativeEvent(),
        )
        self.form = SimpleNamespace(webview=self.view, FormClosed=NativeEvent())
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
            self.assertLess(options["html"].index("window.pywebview.api.ch75k_post"),
                            options["html"].index("const nativeBridge"))
            options["js_api"].ch75k_post('{"id":1,"endpoint":"devices"}')
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
