"""Display the bundled CH75K mapper and connect it to the host through WebKit."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import c_char_p, c_int, c_uint, c_ulong, c_void_p, CFUNCTYPE
import json
import os
from pathlib import Path
import sys
from typing import Callable

from linux_desktop import APP_ID, APP_ICON


NAVIGATION_ACTION = 0
NEW_WINDOW_ACTION = 1
SAVE_FILE = 1
RESPONSE_ACCEPT = -3
APPLICATION_NON_UNIQUE = 1 << 5
BRIDGE_CHANNEL = b"ch75k"
LOCAL_BLOBS = (b"blob:null/", b"blob:file:///")


def _library(*names: str) -> ctypes.CDLL:
    for name in names:
        try:
            return ctypes.CDLL(name)
        except OSError as error:
            last_error = error
    raise RuntimeError("GTK 3와 WebKitGTK (4.1 또는 4.0) 런타임이 필요합니다.") from last_error


def _signatures(library: ctypes.CDLL, declarations: tuple) -> None:
    for name, restype, *arguments in declarations:
        function = getattr(library, name)
        function.argtypes = arguments
        function.restype = restype


@contextmanager
def _system_child_environment():
    # WebKit and the native file chooser launch system helpers that must use
    # the system's shared libraries, including inside a PyInstaller build.
    key = "LD_LIBRARY_PATH"
    saved = os.environ.get(key)
    if getattr(sys, "frozen", False):
        original = os.environ.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = saved


def run_window(page: Path, dispatch: Callable[[str], None],
               poll: Callable[[], tuple[list[tuple[int, dict]], bool]]) -> None:
    """Run the GTK event loop on the main thread and load only the bundled page."""
    page = page.resolve(strict=True)
    if not page.is_file():
        raise ValueError("CH75K 화면 파일을 열 수 없습니다.")
    with _system_child_environment():
        _run_window(page.as_uri().encode("ascii"), dispatch, poll)


def _run_window(url: bytes, dispatch, poll) -> None:
    app_id = APP_ID.encode("ascii")
    glib = _library("libglib-2.0.so.0")
    _signatures(glib, (
        ("g_set_prgname", None, c_char_p),
        ("g_set_application_name", None, c_char_p),
        ("g_timeout_add", c_uint, c_uint, c_void_p, c_void_p),
        ("g_source_remove", c_int, c_uint),
        ("g_free", None, c_void_p),
    ))
    # GDK 3 uses the GLib program name as the Wayland xdg_toplevel app_id.
    glib.g_set_prgname(app_id)
    glib.g_set_application_name(b"CH75K Studio")
    gtk = _library("libgtk-3.so.0")
    gio = _library("libgio-2.0.so.0")
    gobject = _library("libgobject-2.0.so.0")
    webkit = _library("libwebkit2gtk-4.1.so.0", "libwebkit2gtk-4.0.so.37")

    _signatures(gtk, (
        ("gtk_init_check", c_int, c_void_p, c_void_p),
        ("gtk_application_new", c_void_p, c_char_p, c_uint),
        ("gtk_application_window_new", c_void_p, c_void_p),
        ("gtk_window_new", c_void_p, c_int),
        ("gtk_window_set_title", None, c_void_p, c_char_p),
        ("gtk_window_set_icon_from_file", c_int, c_void_p, c_char_p, c_void_p),
        ("gtk_window_set_default_size", None, c_void_p, c_int, c_int),
        ("gtk_window_present", None, c_void_p),
        ("gtk_container_add", None, c_void_p, c_void_p),
        ("gtk_widget_show_all", None, c_void_p),
        ("gtk_widget_destroy", None, c_void_p),
        ("gtk_widget_grab_focus", None, c_void_p),
        ("gtk_main", None),
        ("gtk_main_level", c_uint),
        ("gtk_main_quit", None),
        ("gtk_file_chooser_native_new", c_void_p, c_char_p, c_void_p, c_int, c_char_p, c_char_p),
        ("gtk_file_chooser_set_current_name", None, c_void_p, c_char_p),
        ("gtk_file_chooser_set_do_overwrite_confirmation", None, c_void_p, c_int),
        ("gtk_file_chooser_get_filename", c_void_p, c_void_p),
        ("gtk_native_dialog_run", c_int, c_void_p),
        ("gtk_native_dialog_hide", None, c_void_p),
    ))
    _signatures(gio, (
        ("g_application_register", c_int, c_void_p, c_void_p, c_void_p),
    ))
    _signatures(gobject, (
        ("g_signal_connect_data", c_ulong, c_void_p, c_char_p, c_void_p, c_void_p, c_void_p, c_uint),
        ("g_object_ref", c_void_p, c_void_p),
        ("g_object_unref", None, c_void_p),
    ))
    _signatures(webkit, (
        ("webkit_web_context_new_ephemeral", c_void_p),
        ("webkit_web_view_new_with_context", c_void_p, c_void_p),
        ("webkit_web_view_get_user_content_manager", c_void_p, c_void_p),
        ("webkit_web_view_load_uri", None, c_void_p, c_char_p),
        ("webkit_web_view_run_javascript", None, c_void_p, c_char_p, c_void_p, c_void_p, c_void_p),
        ("webkit_user_content_manager_register_script_message_handler", c_int, c_void_p, c_char_p),
        ("webkit_user_content_manager_unregister_script_message_handler", None, c_void_p, c_char_p),
        ("webkit_javascript_result_get_js_value", c_void_p, c_void_p),
        ("jsc_value_is_string", c_int, c_void_p),
        ("jsc_value_to_string", c_void_p, c_void_p),
        ("webkit_navigation_policy_decision_get_navigation_action", c_void_p, c_void_p),
        ("webkit_navigation_action_get_request", c_void_p, c_void_p),
        ("webkit_uri_request_get_uri", c_char_p, c_void_p),
        ("webkit_policy_decision_ignore", None, c_void_p),
        ("webkit_policy_decision_download", None, c_void_p),
        ("webkit_download_get_request", c_void_p, c_void_p),
        ("webkit_download_set_destination", None, c_void_p, c_char_p),
        ("webkit_download_set_allow_overwrite", None, c_void_p, c_int),
        ("webkit_download_cancel", None, c_void_p),
    ))

    if not gtk.gtk_init_check(None, None):
        raise RuntimeError("CH75K 창을 열 수 있는 그래픽 세션을 찾지 못했습니다.")

    # An application window also exposes its GTK application ID to GNOME over
    # Wayland. Keep instances independent and retain a plain-window fallback.
    application = gtk.gtk_application_new(app_id, APPLICATION_NON_UNIQUE)
    if application and not gio.g_application_register(application, None, None):
        gobject.g_object_unref(application)
        application = None
    window = gtk.gtk_application_window_new(application) if application else gtk.gtk_window_new(0)
    context = webkit.webkit_web_context_new_ephemeral()
    view = webkit.webkit_web_view_new_with_context(context)
    manager = gobject.g_object_ref(webkit.webkit_web_view_get_user_content_manager(view))
    active_dialog = None
    window_open = True
    timer = 0
    error = None

    @CFUNCTYPE(None, c_void_p, c_void_p)
    def on_destroy(_window, _data):
        nonlocal window_open
        window_open = False
        if active_dialog:
            gtk.gtk_native_dialog_hide(active_dialog)
        if gtk.gtk_main_level():
            gtk.gtk_main_quit()

    @CFUNCTYPE(c_int, c_void_p)
    def on_timeout(_data):
        nonlocal timer, error
        try:
            replies, closed = poll()
            if replies:
                script = "\n".join(
                    f"window.ch75kReply({int(request_id)},{json.dumps(response, allow_nan=False)});"
                    for request_id, response in replies
                )
                webkit.webkit_web_view_run_javascript(view, script.encode("ascii"), None, None, None)
            if not closed:
                return 1
        except Exception as exc:
            error = exc
        timer = 0
        gtk.gtk_widget_destroy(window)
        return 0

    @CFUNCTYPE(None, c_void_p, c_void_p, c_void_p)
    def on_message(_manager, result, _data):
        nonlocal error
        value = webkit.webkit_javascript_result_get_js_value(result)
        if not value or not webkit.jsc_value_is_string(value):
            return
        raw = webkit.jsc_value_to_string(value)
        if raw:
            try:
                dispatch(ctypes.string_at(raw).decode("utf-8"))
            except Exception as exc:
                error = exc
                gtk.gtk_widget_destroy(window)
            finally:
                glib.g_free(raw)

    @CFUNCTYPE(None, c_void_p, c_int, c_void_p)
    def on_process_terminated(_view, _reason, _data):
        nonlocal error
        error = RuntimeError("WebKitGTK의 CH75K 화면 프로세스가 종료됐습니다.")
        gtk.gtk_widget_destroy(window)

    @CFUNCTYPE(c_int, c_void_p, c_void_p, c_int, c_void_p)
    def on_policy(_view, decision, decision_type, _data):
        if decision_type == NEW_WINDOW_ACTION:
            webkit.webkit_policy_decision_ignore(decision)
            return 1
        if decision_type != NAVIGATION_ACTION:
            return 0
        action = webkit.webkit_navigation_policy_decision_get_navigation_action(decision)
        request = webkit.webkit_navigation_action_get_request(action)
        uri = webkit.webkit_uri_request_get_uri(request) or b""
        if uri == url:
            return 0
        if uri.startswith(LOCAL_BLOBS):
            webkit.webkit_policy_decision_download(decision)
        else:
            webkit.webkit_policy_decision_ignore(decision)
        return 1

    @CFUNCTYPE(c_int, c_void_p, c_char_p, c_void_p)
    def on_destination(download, _filename, _data):
        nonlocal active_dialog
        if not window_open or active_dialog:
            webkit.webkit_download_cancel(download)
            return 1
        dialog = gtk.gtk_file_chooser_native_new(
            "프로필 저장".encode(), window, SAVE_FILE, "저장".encode(), "취소".encode())
        active_dialog = dialog
        destination = None
        try:
            gtk.gtk_file_chooser_set_current_name(dialog, b"CH75K-profile.json")
            gtk.gtk_file_chooser_set_do_overwrite_confirmation(dialog, 1)
            if gtk.gtk_native_dialog_run(dialog) == RESPONSE_ACCEPT:
                filename = gtk.gtk_file_chooser_get_filename(dialog)
                if filename:
                    try:
                        destination = Path(os.fsdecode(ctypes.string_at(filename))).as_uri().encode("ascii")
                    finally:
                        glib.g_free(filename)
        finally:
            active_dialog = None
            gobject.g_object_unref(dialog)
            if destination:
                webkit.webkit_download_set_allow_overwrite(download, 1)
                webkit.webkit_download_set_destination(download, destination)
            else:
                webkit.webkit_download_cancel(download)
        return 1

    @CFUNCTYPE(None, c_void_p, c_void_p, c_void_p)
    def on_download(_context, download, _data):
        request = webkit.webkit_download_get_request(download)
        uri = webkit.webkit_uri_request_get_uri(request) or b""
        if uri.startswith(LOCAL_BLOBS):
            gobject.g_signal_connect_data(download, b"decide-destination", on_destination, None, None, 0)
        else:
            webkit.webkit_download_cancel(download)

    gobject.g_signal_connect_data(window, b"destroy", on_destroy, None, None, 0)
    gobject.g_signal_connect_data(view, b"decide-policy", on_policy, None, None, 0)
    gobject.g_signal_connect_data(view, b"web-process-terminated", on_process_terminated, None, None, 0)
    gobject.g_signal_connect_data(context, b"download-started", on_download, None, None, 0)
    gobject.g_signal_connect_data(manager, b"script-message-received::ch75k", on_message, None, None, 0)
    gtk.gtk_window_set_title(window, b"CH75K Studio")
    if APP_ICON.is_file():
        gtk.gtk_window_set_icon_from_file(window, os.fsencode(APP_ICON), None)
    gtk.gtk_window_set_default_size(window, 1240, 820)
    gtk.gtk_container_add(window, view)
    try:
        if not webkit.webkit_user_content_manager_register_script_message_handler(manager, BRIDGE_CHANNEL):
            raise RuntimeError("CH75K 화면 연결을 준비할 수 없습니다.")
        webkit.webkit_web_view_load_uri(view, url)
        gtk.gtk_widget_show_all(window)
        gtk.gtk_widget_grab_focus(view)
        gtk.gtk_window_present(window)
        timer = glib.g_timeout_add(50, on_timeout, None)
        gtk.gtk_main()
    finally:
        if timer:
            glib.g_source_remove(timer)
        webkit.webkit_user_content_manager_unregister_script_message_handler(manager, BRIDGE_CHANNEL)
        if window_open:
            gtk.gtk_widget_destroy(window)
        gobject.g_object_unref(manager)
        gobject.g_object_unref(context)
        if application:
            gobject.g_object_unref(application)
    if error:
        raise error
