"""Display the CH75K mapper in the system WebKit view on macOS."""

from __future__ import annotations

import ctypes as C
from itertools import count
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import current_thread, main_thread


_ID = C.c_void_p
_BOOL = C.c_int8 if os.uname().machine == "x86_64" else C.c_bool
_INTEGER = C.c_long
_UNSIGNED = C.c_ulong
_CLASS_IDS = count()
_MESSAGE_NAME = "ch75k"
_APP_ICON = Path(__file__).resolve().with_name("assets") / "app-icon.png"


class _Point(C.Structure):
    _fields_ = (("x", C.c_double), ("y", C.c_double))


class _Size(C.Structure):
    _fields_ = (("width", C.c_double), ("height", C.c_double))


class _Rect(C.Structure):
    _fields_ = (("origin", _Point), ("size", _Size))


class _Block(C.Structure):
    # Header of a compiler-generated Objective-C block (Apple Blocks ABI).
    _fields_ = (("isa", _ID), ("flags", C.c_int), ("reserved", C.c_int), ("invoke", _ID))


def _complete(block, value_type, value):
    invoke = C.cast(block, C.POINTER(_Block)).contents.invoke
    C.CFUNCTYPE(None, _ID, value_type)(invoke)(block, value)


class _Cocoa:
    def __init__(self):
        self.appkit = C.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        self.webkit = C.CDLL("/System/Library/Frameworks/WebKit.framework/WebKit")
        self.objc = C.CDLL("/usr/lib/libobjc.A.dylib")
        signatures = (
            ("objc_getClass", _ID, (C.c_char_p,)),
            ("sel_registerName", _ID, (C.c_char_p,)),
            ("objc_allocateClassPair", _ID, (_ID, C.c_char_p, C.c_size_t)),
            ("objc_registerClassPair", None, (_ID,)),
            ("class_addMethod", C.c_bool, (_ID, _ID, _ID, C.c_char_p)),
        )
        for name, result, parameters in signatures:
            function = getattr(self.objc, name)
            function.restype = result
            function.argtypes = parameters
            setattr(self, name, function)
        self.dispatch = C.cast(self.objc.objc_msgSend, _ID).value
        self.callbacks = []

    def selector(self, name):
        return self.sel_registerName(name.encode("ascii"))

    def cls(self, name):
        result = self.objc_getClass(name.encode("ascii"))
        if not result:
            raise RuntimeError(f"macOS에서 {name} 클래스를 찾지 못했습니다.")
        return result

    def send(self, receiver, name, result=None, *arguments):
        # A fully typed prototype for every call also handles arm64's different
        # calling convention for variadic arguments.
        prototype = C.CFUNCTYPE(result, _ID, _ID, *(kind for kind, _ in arguments))
        return prototype(self.dispatch)(receiver, self.selector(name), *(value for _, value in arguments))

    def text(self, value):
        return self.send(self.cls("NSString"), "stringWithUTF8String:", _ID, (C.c_char_p, value.encode("utf-8")))

    def string(self, value):
        data = self.send(value, "UTF8String", C.c_char_p)
        return data.decode("utf-8", errors="replace") if data else ""

    def create(self, name):
        return self.send(self.send(self.cls(name), "alloc", _ID), "init", _ID)

    def delegate(self, methods, report_error):
        name = f"CH75KWindowDelegate{next(_CLASS_IDS)}".encode("ascii")
        cls = self.objc_allocateClassPair(self.cls("NSObject"), name, 0)
        if not cls:
            raise RuntimeError("macOS 창의 이벤트 처리기를 만들지 못했습니다.")

        def bridge(handler, result, arguments):
            @C.CFUNCTYPE(result, _ID, _ID, *arguments)
            def method(_receiver, _selector, *actual):
                try:
                    return handler(*actual)
                except Exception as exc:
                    report_error(exc)
                    return 0 if result else None
            return method

        for name, signature, callback, result, arguments in methods:
            method = bridge(callback, result, arguments)
            if not self.class_addMethod(cls, self.selector(name), C.cast(method, _ID), signature):
                raise RuntimeError(f"macOS 이벤트 처리기를 등록하지 못했습니다. {name}")
            self.callbacks.append(method)
        self.objc_registerClassPair(cls)
        return self.send(self.send(cls, "alloc", _ID), "init", _ID)


class _Window:
    def __init__(self, page, dispatch, poll):
        self.page = page
        self.url = ""
        self.dispatch = dispatch
        self.poll = poll
        self.cocoa = _Cocoa()
        self.send = self.cocoa.send
        self.app = self.window = self.view = None
        self.closed = False
        self.error = None
        self.downloads = {}

    def _stop(self):
        self.send(self.app, "stop:", None, (_ID, None))
        # NSApplication.stop: takes effect after an event has been dispatched.
        event = self.send(
            self.cocoa.cls("NSEvent"),
            "otherEventWithType:location:modifierFlags:timestamp:windowNumber:context:subtype:data1:data2:",
            _ID, (_UNSIGNED, 15), (_Point, _Point(0, 0)), (_UNSIGNED, 0),
            (C.c_double, 0), (_INTEGER, 0), (_ID, None), (C.c_short, 0),
            (_INTEGER, 0), (_INTEGER, 0),
        )
        self.send(self.app, "postEvent:atStart:", None, (_ID, event), (_BOOL, True))

    def _close(self, _notification):
        self.closed = True
        self._stop()

    def _close_window(self, _sender):
        self.send(self.window, "close")

    def _should_terminate(self, _application):
        self._close_window(None)
        return 0  # NSTerminateCancel; Python first waits for outstanding USB work.

    def _poll(self, _timer):
        replies, stopped = self.poll()
        for request_id, response in replies:
            arguments = json.dumps((request_id, response), ensure_ascii=True)
            script = f"window.ch75kReply(...{arguments})"
            self.send(self.view, "evaluateJavaScript:completionHandler:", None,
                      (_ID, self.cocoa.text(script)), (_ID, None))
        if stopped:
            self._close_window(None)

    def _report_error(self, error):
        self.error = error
        self._stop()

    def _request_address(self, request):
        url = self.send(request, "URL", _ID)
        return self.cocoa.string(self.send(url, "absoluteString", _ID))

    def _is_page_frame(self, frame):
        return (frame and self.send(frame, "isMainFrame", _BOOL)
                and self._request_address(self.send(frame, "request", _ID)) == self.url)

    def _script_message(self, _controller, message):
        if self.send(message, "webView", _ID) != self.view:
            return
        if self.cocoa.string(self.send(message, "name", _ID)) != _MESSAGE_NAME:
            return
        if not self._is_page_frame(self.send(message, "frameInfo", _ID)):
            return
        body = self.send(message, "body", _ID)
        if self.send(body, "isKindOfClass:", _BOOL, (_ID, self.cocoa.cls("NSString"))):
            self.dispatch(self.cocoa.string(body))

    def _navigation_policy(self, view, action, decision):
        policy = 0  # WKNavigationActionPolicyCancel
        try:
            if view != self.view:
                return
            address = self._request_address(self.send(action, "request", _ID))
            frame = self.send(action, "targetFrame", _ID)
            if address == self.url and frame and self.send(frame, "isMainFrame", _BOOL):
                policy = 1  # WKNavigationActionPolicyAllow
            elif (address.startswith(("blob:null/", "blob:file:/"))
                    and self._is_page_frame(self.send(action, "sourceFrame", _ID))
                    and self.send(action, "respondsToSelector:", _BOOL,
                                  (_ID, self.cocoa.selector("shouldPerformDownload")))
                    and self.send(action, "shouldPerformDownload", _BOOL)):
                policy = 2  # WKNavigationActionPolicyDownload
        finally:
            _complete(decision, _INTEGER, policy)

    def _load_error(self, _view, _navigation, error):
        if self.send(error, "code", _INTEGER) != -999:  # NSURLErrorCancelled
            detail = self.cocoa.string(self.send(error, "localizedDescription", _ID))
            self._report_error(RuntimeError(f"CH75K 화면을 불러오지 못했습니다. {detail}"))

    def _web_process_terminated(self, _view):
        self._report_error(RuntimeError("CH75K 화면을 표시하는 WebKit 프로세스가 종료됐습니다."))

    def _open_panel(self, view, _parameters, frame, decision):
        selection = None
        try:
            if view == self.view and self._is_page_frame(frame):
                panel = self.send(self.cocoa.cls("NSOpenPanel"), "openPanel", _ID)
                types = self.send(self.cocoa.cls("NSArray"), "arrayWithObject:", _ID, (_ID, self.cocoa.text("json")))
                self.send(panel, "setAllowedFileTypes:", None, (_ID, types))
                if self.send(panel, "runModal", _INTEGER) == 1:  # NSModalResponseOK
                    selection = self.send(panel, "URLs", _ID)
        finally:
            _complete(decision, _ID, selection)

    def _start_download(self, _view, _navigation, download):
        self.send(download, "setDelegate:", None, (_ID, self.delegate))

    def _save_panel(self, download, _response, suggested_name, decision):
        destination_url = None
        try:
            panel = self.send(self.cocoa.cls("NSSavePanel"), "savePanel", _ID)
            self.send(panel, "setNameFieldStringValue:", None, (_ID, suggested_name))
            if self.send(panel, "runModal", _INTEGER) == 1:
                url = self.send(panel, "URL", _ID)
                destination = Path(self.cocoa.string(self.send(url, "path", _ID)))
                # WebKit requires a nonexistent path. Stage beside the selected file
                # so a confirmed overwrite works without removing it in advance.
                work = TemporaryDirectory(prefix=".ch75k-download-", dir=destination.parent)
                temporary_file = Path(work.name) / "profile.json"
                self.downloads[download] = (work, temporary_file, destination)
                destination_url = self.send(self.cocoa.cls("NSURL"), "fileURLWithPath:", _ID,
                                            (_ID, self.cocoa.text(str(temporary_file))))
        except OSError as exc:
            self._alert(f"프로필을 저장하지 못했습니다. {exc}")
        finally:
            _complete(decision, _ID, destination_url)

    def _finish_download(self, download):
        work, temporary_file, destination = self.downloads.pop(download)
        try:
            os.replace(temporary_file, destination)
        except OSError as exc:
            self._alert(f"프로필을 저장하지 못했습니다. {exc}")
        finally:
            work.cleanup()

    def _download_error(self, download, error, _resume_data):
        files = self.downloads.pop(download, None)
        if files:
            files[0].cleanup()
            detail = self.cocoa.string(self.send(error, "localizedDescription", _ID))
            self._alert(f"프로필을 저장하지 못했습니다. {detail}")

    def _alert(self, message):
        alert = self.cocoa.create("NSAlert")
        try:
            self.send(alert, "setMessageText:", None, (_ID, self.cocoa.text("CH75K Studio")))
            self.send(alert, "setInformativeText:", None, (_ID, self.cocoa.text(message)))
            self.send(alert, "runModal", _INTEGER)
        finally:
            self.send(alert, "release")

    def _install_menus(self):
        bar = self.cocoa.create("NSMenu")
        menus = (
            ("CH75K Studio", (("CH75K Studio 종료", "closeMapper:", "q", self.delegate),)),
            ("편집", (
                ("실행 취소", "undo:", "z", None), ("잘라내기", "cut:", "x", None),
                ("복사", "copy:", "c", None), ("붙여넣기", "paste:", "v", None),
                ("전체 선택", "selectAll:", "a", None),
            )),
        )
        for title, items in menus:
            parent = self.cocoa.create("NSMenuItem")
            menu = self.cocoa.create("NSMenu")
            self.send(menu, "setTitle:", None, (_ID, self.cocoa.text(title)))
            self.send(bar, "addItem:", None, (_ID, parent))
            self.send(parent, "setSubmenu:", None, (_ID, menu))
            for label, action, shortcut, target in items:
                item = self.send(menu, "addItemWithTitle:action:keyEquivalent:", _ID,
                                 (_ID, self.cocoa.text(label)), (_ID, self.cocoa.selector(action)),
                                 (_ID, self.cocoa.text(shortcut)))
                if target:
                    self.send(item, "setTarget:", None, (_ID, target))
            self.send(menu, "release")
            self.send(parent, "release")
        self.send(self.app, "setMainMenu:", None, (_ID, bar))
        self.send(bar, "release")

    def _install_application_icon(self):
        if not _APP_ICON.is_file():
            return
        image = self.send(
            self.send(self.cocoa.cls("NSImage"), "alloc", _ID),
            "initWithContentsOfFile:", _ID, (_ID, self.cocoa.text(str(_APP_ICON))),
        )
        if image:
            try:
                self.send(self.app, "setApplicationIconImage:", None, (_ID, image))
            finally:
                self.send(image, "release")

    def run(self):
        pool = self.cocoa.create("NSAutoreleasePool")
        delegate = view = window = timer = controller = None
        try:
            self.app = self.send(self.cocoa.cls("NSApplication"), "sharedApplication", _ID)
            self._install_application_icon()
            self.send(self.app, "setActivationPolicy:", _BOOL, (_INTEGER, 0))
            delegate = self.delegate = self.cocoa.delegate((
                ("closeMapper:", b"v@:@", self._close_window, None, (_ID,)),
                ("applicationShouldTerminate:", b"Q@:@", self._should_terminate, _UNSIGNED, (_ID,)),
                ("windowWillClose:", b"v@:@", self._close, None, (_ID,)),
                ("pollMapperReplies:", b"v@:@", self._poll, None, (_ID,)),
                ("userContentController:didReceiveScriptMessage:", b"v@:@@",
                 self._script_message, None, (_ID, _ID)),
                ("webView:decidePolicyForNavigationAction:decisionHandler:", b"v@:@@@?",
                 self._navigation_policy, None, (_ID, _ID, _ID)),
                ("webView:didFailProvisionalNavigation:withError:", b"v@:@@@",
                 self._load_error, None, (_ID, _ID, _ID)),
                ("webView:didFailNavigation:withError:", b"v@:@@@", self._load_error, None, (_ID, _ID, _ID)),
                ("webViewWebContentProcessDidTerminate:", b"v@:@",
                 self._web_process_terminated, None, (_ID,)),
                ("webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:", b"v@:@@@@?",
                 self._open_panel, None, (_ID, _ID, _ID, _ID)),
                ("webView:navigationAction:didBecomeDownload:", b"v@:@@@",
                 self._start_download, None, (_ID, _ID, _ID)),
                ("download:decideDestinationUsingResponse:suggestedFilename:completionHandler:", b"v@:@@@@?",
                 self._save_panel, None, (_ID, _ID, _ID, _ID)),
                ("downloadDidFinish:", b"v@:@", self._finish_download, None, (_ID,)),
                ("download:didFailWithError:resumeData:", b"v@:@@@",
                 self._download_error, None, (_ID, _ID, _ID)),
            ), self._report_error)
            self.send(self.app, "setDelegate:", None, (_ID, delegate))
            self._install_menus()
            self.send(self.app, "finishLaunching")

            frame = _Rect(_Point(0, 0), _Size(1200, 800))
            # Titled, closable, miniaturizable and resizable.
            window = self.window = self.send(
                self.send(self.cocoa.cls("NSWindow"), "alloc", _ID),
                "initWithContentRect:styleMask:backing:defer:", _ID,
                (_Rect, frame), (_UNSIGNED, 15), (_UNSIGNED, 2), (_BOOL, False),
            )
            if not window:
                raise RuntimeError("CH75K 창을 만들지 못했습니다.")
            self.send(window, "setReleasedWhenClosed:", None, (_BOOL, False))
            self.send(window, "setTitle:", None, (_ID, self.cocoa.text("CH75K Studio")))
            self.send(window, "setDelegate:", None, (_ID, delegate))
            self.send(window, "setContentMinSize:", None, (_Size, _Size(560, 490)))
            self.send(window, "center")
            configuration = self.cocoa.create("WKWebViewConfiguration")
            try:
                store = self.send(self.cocoa.cls("WKWebsiteDataStore"), "nonPersistentDataStore", _ID)
                self.send(configuration, "setWebsiteDataStore:", None, (_ID, store))
                controller = self.send(configuration, "userContentController", _ID)
                self.send(controller, "retain", _ID)
                self.send(controller, "addScriptMessageHandler:name:", None,
                          (_ID, delegate), (_ID, self.cocoa.text(_MESSAGE_NAME)))
                view = self.view = self.send(
                    self.send(self.cocoa.cls("WKWebView"), "alloc", _ID),
                    "initWithFrame:configuration:", _ID, (_Rect, frame), (_ID, configuration),
                )
            finally:
                self.send(configuration, "release")
            if not view:
                raise RuntimeError("CH75K 화면을 초기화하지 못했습니다.")
            self.send(view, "setNavigationDelegate:", None, (_ID, delegate))
            self.send(view, "setUIDelegate:", None, (_ID, delegate))
            self.send(window, "setContentView:", None, (_ID, view))
            url = self.send(self.cocoa.cls("NSURL"), "fileURLWithPath:", _ID,
                            (_ID, self.cocoa.text(str(self.page))))
            self.url = self.cocoa.string(self.send(url, "absoluteString", _ID))
            self.send(view, "loadFileURL:allowingReadAccessToURL:", _ID, (_ID, url), (_ID, url))
            self.send(window, "makeKeyAndOrderFront:", None, (_ID, None))
            self.send(self.app, "activateIgnoringOtherApps:", None, (_BOOL, True))
            timer = self.send(
                self.cocoa.cls("NSTimer"), "scheduledTimerWithTimeInterval:target:selector:userInfo:repeats:", _ID,
                (C.c_double, 0.05), (_ID, delegate), (_ID, self.cocoa.selector("pollMapperReplies:")),
                (_ID, None), (_BOOL, True),
            )
            self.send(self.app, "run")
            if self.error:
                self._alert(str(self.error))
                raise self.error
        finally:
            self.send(timer, "invalidate")
            if controller:
                self.send(controller, "removeScriptMessageHandlerForName:", None,
                          (_ID, self.cocoa.text(_MESSAGE_NAME)))
                self.send(controller, "release")
            self.send(view, "setNavigationDelegate:", None, (_ID, None))
            self.send(view, "setUIDelegate:", None, (_ID, None))
            self.send(view, "stopLoading")
            self.send(window, "setDelegate:", None, (_ID, None))
            if not self.closed:
                self.send(window, "close")
            self.send(self.app, "setDelegate:", None, (_ID, None))
            self.send(self.app, "setMainMenu:", None, (_ID, None))
            for work, _, _ in self.downloads.values():
                work.cleanup()
            for object_ in (view, window, delegate):
                self.send(object_, "release")
            self.send(pool, "drain")


def run_window(page: Path, dispatch, poll) -> None:
    """Run the file-backed WebKit window until the user or bridge closes it."""
    if sys.platform != "darwin" or current_thread() is not main_thread():
        raise RuntimeError("macOS의 주 스레드에서 CH75K 창을 실행해야 합니다.")
    page = page.resolve(strict=True)
    if not page.is_file():
        raise ValueError("CH75K Studio 화면 파일을 찾지 못했습니다.")
    _Window(page, dispatch, poll).run()
