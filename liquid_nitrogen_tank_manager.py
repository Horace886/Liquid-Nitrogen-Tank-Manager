from __future__ import annotations

import json
import os
import re
import csv
import calendar
import ctypes
import tkinter as tk
import uuid
import zipfile
import copy
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tkinter import font as tkfont
from tkinter import filedialog, ttk
from typing import Callable

from liquid_nitrogen_tank_excel import (
    ImportPreview,
    export_cryotube_workbook,
    export_freezer_workbook,
    export_inventory_events_workbook,
    import_freezer_workbook,
    preview_freezer_workbook,
)
from liquid_nitrogen_tank_store import (
    BoxSample,
    FreezerData as SQLiteFreezerData,
    FreezerRepository as SQLiteFreezerRepository,
    UnitRecord as SQLiteUnitRecord,
    all_unit_codes as sqlite_all_unit_codes,
    compartment_code as sqlite_compartment_code,
    parse_unit_code as sqlite_parse_unit_code,
    unit_code as sqlite_unit_code,
)


APP_TITLE = "液氮罐管理"
APP_VERSION = "1.0.0"
APP_DEVELOPER = "Horace"
APP_SUPPORT_EMAIL = "1050136527@qq.com"
DATA_FILE = Path(__file__).with_name("liquid_nitrogen_tank_storage_data.json")

COLORS = {
    "bg": "#F3F6FA",
    "panel": "#FFFFFF",
    "text": "#152338",
    "muted": "#738095",
    "line": "#E1E7EF",
    "primary": "#3478F6",
    "primary_dark": "#2365DD",
    "primary_soft": "#EAF1FF",
    "empty": "#F8FAFC",
    "occupied": "#E8F6F0",
    "occupied_text": "#13795B",
    "warning": "#FFF4DE",
    "danger": "#D94A4A",
    "sidebar": "#12243A",
    "sidebar_hover": "#1C3552",
    "sidebar_muted": "#9AAAC0",
}


_IME_ENTER_HOOK: object | None = None
_IME_ENTER_HOOK_CALLBACK: object | None = None
_IME_SWALLOW_RETURN_UP = False


def _uninstall_windows_ime_enter_guard_if_background() -> None:
    """Remove the global low-level hook while this application is inactive."""
    global _IME_ENTER_HOOK, _IME_ENTER_HOOK_CALLBACK
    if os.name != "nt" or _IME_ENTER_HOOK is None:
        return
    try:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        )
        user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
        foreground = int(user32.GetForegroundWindow() or 0)
        foreground_pid = ctypes.c_ulong(0)
        if foreground:
            user32.GetWindowThreadProcessId(
                ctypes.c_void_p(foreground),
                ctypes.byref(foreground_pid),
            )
        if foreground_pid.value == os.getpid():
            return
        user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
        user32.UnhookWindowsHookEx.restype = ctypes.c_int
        user32.UnhookWindowsHookEx(ctypes.c_void_p(_IME_ENTER_HOOK))
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    _IME_ENTER_HOOK = None
    _IME_ENTER_HOOK_CALLBACK = None


def _install_windows_ime_enter_guard() -> None:
    """Intercept Return before Microsoft Pinyin can block Tk's event loop."""
    global _IME_ENTER_HOOK, _IME_ENTER_HOOK_CALLBACK
    if os.name != "nt" or _IME_ENTER_HOOK is not None:
        return
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        class LowLevelKeyboardData(ctypes.Structure):
            _fields_ = (
                ("vkCode", ctypes.c_ulong),
                ("scanCode", ctypes.c_ulong),
                ("flags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t),
            )

        class Rect(ctypes.Structure):
            _fields_ = (
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            )

        class GuiThreadInfo(ctypes.Structure):
            _fields_ = (
                ("cbSize", ctypes.c_ulong),
                ("flags", ctypes.c_ulong),
                ("hwndActive", ctypes.c_void_p),
                ("hwndFocus", ctypes.c_void_p),
                ("hwndCapture", ctypes.c_void_p),
                ("hwndMenuOwner", ctypes.c_void_p),
                ("hwndMoveSize", ctypes.c_void_p),
                ("hwndCaret", ctypes.c_void_p),
                ("rcCaret", Rect),
            )

        low_level_keyboard_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_int,
            ctypes.c_size_t,
            ctypes.c_void_p,
        )
        user32.GetFocus.restype = ctypes.c_void_p
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        )
        user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
        user32.GetGUIThreadInfo.argtypes = (
            ctypes.c_ulong,
            ctypes.POINTER(GuiThreadInfo),
        )
        user32.GetGUIThreadInfo.restype = ctypes.c_int
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int,
            low_level_keyboard_proc_type,
            ctypes.c_void_p,
            ctypes.c_ulong,
        )
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_size_t,
            ctypes.c_void_p,
        )
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        kernel32.GetModuleHandleW.argtypes = (ctypes.c_wchar_p,)
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p

        def low_level_keyboard_proc(n_code: int, message_code: int, data_pointer: int) -> int:
            global _IME_SWALLOW_RETURN_UP
            if n_code >= 0 and data_pointer:
                data = ctypes.cast(
                    data_pointer,
                    ctypes.POINTER(LowLevelKeyboardData),
                ).contents
                if int(data.vkCode) == 0x0D and int(message_code) in {
                    0x0100, 0x0101, 0x0104, 0x0105
                }:
                    key_up = int(message_code) in {0x0101, 0x0105}
                    consume = False
                    foreground = int(user32.GetForegroundWindow() or 0)
                    foreground_pid = ctypes.c_ulong(0)
                    foreground_thread = 0
                    if foreground:
                        foreground_thread = int(user32.GetWindowThreadProcessId(
                            ctypes.c_void_p(foreground),
                            ctypes.byref(foreground_pid),
                        ))
                    if foreground_pid.value == os.getpid():
                        if key_up and _IME_SWALLOW_RETURN_UP:
                            _IME_SWALLOW_RETURN_UP = False
                            consume = True
                        elif not key_up:
                            gui_info = GuiThreadInfo()
                            gui_info.cbSize = ctypes.sizeof(GuiThreadInfo)
                            focus_handles: list[int] = []
                            if foreground_thread and user32.GetGUIThreadInfo(
                                foreground_thread,
                                ctypes.byref(gui_info),
                            ):
                                focus_handles.extend(
                                    int(handle or 0)
                                    for handle in (
                                        gui_info.hwndFocus,
                                        gui_info.hwndActive,
                                        gui_info.hwndCaret,
                                    )
                                )
                            focus_handles.extend(
                                (int(user32.GetFocus() or 0), foreground)
                            )
                            seen_handles: set[int] = set()
                            for focus_handle in focus_handles:
                                if not focus_handle or focus_handle in seen_handles:
                                    continue
                                seen_handles.add(focus_handle)
                                if _complete_ime_composition_handle(focus_handle):
                                    _IME_SWALLOW_RETURN_UP = True
                                    consume = True
                                    # Never synthesize Alt (or any other key)
                                    # here.  On Windows an injected Alt can arm
                                    # the native window-menu state; the user's
                                    # next Enter then opens the system menu
                                    # instead of submitting the form.  IME
                                    # completion plus consuming this Return is
                                    # sufficient.  Tk's widget-level handler
                                    # performs the deferred repaint when it
                                    # receives the committed text.
                                    break
                    if consume:
                        return 1
            return int(
                user32.CallNextHookEx(
                    ctypes.c_void_p(_IME_ENTER_HOOK or 0),
                    n_code,
                    message_code,
                    ctypes.c_void_p(data_pointer),
                )
            )

        callback = low_level_keyboard_proc_type(low_level_keyboard_proc)
        hook = user32.SetWindowsHookExW(
            13,  # WH_KEYBOARD_LL, before the keystroke reaches the IME.
            callback,
            kernel32.GetModuleHandleW(None),
            0,
        )
        if hook:
            _IME_ENTER_HOOK_CALLBACK = callback
            _IME_ENTER_HOOK = hook
    except (AttributeError, OSError, TypeError, ValueError):
        _IME_ENTER_HOOK = None
        _IME_ENTER_HOOK_CALLBACK = None


def _complete_ime_composition_handle(handle: int) -> bool:
    """Complete an IME composition attached to a native focus handle."""
    if os.name != "nt" or not handle:
        return False
    try:
        imm32 = ctypes.windll.imm32
        imm32.ImmGetContext.argtypes = (ctypes.c_void_p,)
        imm32.ImmGetContext.restype = ctypes.c_void_p
        imm32.ImmGetCompositionStringW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        )
        imm32.ImmGetCompositionStringW.restype = ctypes.c_long
        imm32.ImmNotifyIME.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
        )
        imm32.ImmNotifyIME.restype = ctypes.c_int
        imm32.ImmReleaseContext.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        context = imm32.ImmGetContext(ctypes.c_void_p(handle))
        if not context:
            return False
        try:
            if imm32.ImmGetCompositionStringW(context, 0x0008, None, 0) <= 0:
                return False
            completed = bool(imm32.ImmNotifyIME(context, 0x0015, 0x0001, 0))
            if completed:
                # NI_CLOSECANDIDATE.  Microsoft Pinyin may leave its native
                # candidate loop open even after CPS_COMPLETE; closing each
                # possible list releases Tk immediately instead of waiting for
                # a mouse click or Alt key.
                for candidate_index in range(4):
                    imm32.ImmNotifyIME(context, 0x0011, candidate_index, 0)
            return completed
        finally:
            imm32.ImmReleaseContext(ctypes.c_void_p(handle), context)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _ime_composition_active(widget: tk.Misc) -> bool:
    """Return whether Windows IME owns an uncommitted composition string."""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        imm32 = ctypes.windll.imm32
        user32.GetFocus.restype = ctypes.c_void_p
        imm32.ImmGetContext.argtypes = (ctypes.c_void_p,)
        imm32.ImmGetContext.restype = ctypes.c_void_p
        imm32.ImmGetCompositionStringW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        )
        imm32.ImmGetCompositionStringW.restype = ctypes.c_long
        imm32.ImmReleaseContext.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        candidates = [user32.GetFocus(), widget.winfo_id(), widget.winfo_toplevel().winfo_id()]
        seen: set[int] = set()
        for raw_handle in candidates:
            handle = int(raw_handle or 0)
            if not handle or handle in seen:
                continue
            seen.add(handle)
            context = imm32.ImmGetContext(ctypes.c_void_p(handle))
            if not context:
                continue
            try:
                # GCS_COMPSTR: bytes currently being composed but not committed.
                if imm32.ImmGetCompositionStringW(context, 0x0008, None, 0) > 0:
                    return True
            finally:
                imm32.ImmReleaseContext(ctypes.c_void_p(handle), context)
    except (AttributeError, OSError, TypeError, ValueError, tk.TclError):
        return False
    return False


def _complete_ime_composition(widget: tk.Misc) -> bool:
    """Synchronously finish the active Windows IME composition.

    Microsoft Pinyin can enter a modal candidate loop when Return is allowed
    to continue through Tk's normal binding chain.  While that loop is active,
    neither ``after`` callbacks nor paint events run until the user clicks or
    presses Alt.  Completing the composition here and consuming that Return
    prevents the modal loop instead of trying to repair the display later.
    """
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        user32.GetFocus.restype = ctypes.c_void_p
        handles = (
            user32.GetFocus(),
            widget.winfo_id(),
            widget.winfo_toplevel().winfo_id(),
        )
        seen: set[int] = set()
        for raw_handle in handles:
            handle = int(raw_handle or 0)
            if not handle or handle in seen:
                continue
            seen.add(handle)
            if _complete_ime_composition_handle(handle):
                return True
    except (AttributeError, OSError, TypeError, ValueError, tk.TclError):
        return False
    return False


def _input_widget_value(widget: tk.Misc) -> str:
    if isinstance(widget, tk.Text):
        return widget.get("1.0", "end-1c")
    getter = getattr(widget, "get", None)
    return str(getter()) if callable(getter) else ""


def _refresh_after_ime_commit(widget: tk.Misc) -> None:
    """Force Tk/Windows to paint an IME result without waiting for Alt/click.

    Some Windows IMEs deliver the committed value to Tcl but leave the Entry
    displaying a blank pre-edit surface until the window receives another
    focus or paint message.  Redraw the native window immediately, then use a
    very short focus round-trip as a fallback.  The insertion point is kept so
    continued typing feels unchanged.
    """

    def redraw() -> None:
        try:
            if not widget.winfo_exists():
                return
            widget.update_idletasks()
            widget.event_generate("<Expose>", when="tail")
            if os.name != "nt":
                return
            user32 = ctypes.windll.user32
            user32.GetFocus.restype = ctypes.c_void_p
            user32.RedrawWindow.argtypes = (
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint,
            )
            user32.RedrawWindow.restype = ctypes.c_int
            user32.UpdateWindow.argtypes = (ctypes.c_void_p,)
            user32.UpdateWindow.restype = ctypes.c_int
            handles = (
                widget.winfo_id(),
                widget.winfo_toplevel().winfo_id(),
                user32.GetFocus(),
            )
            # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN |
            # RDW_UPDATENOW | RDW_FRAME
            flags = 0x0001 | 0x0004 | 0x0080 | 0x0100 | 0x0400
            seen: set[int] = set()
            for raw_handle in handles:
                handle = int(raw_handle or 0)
                if not handle or handle in seen:
                    continue
                seen.add(handle)
                user32.RedrawWindow(ctypes.c_void_p(handle), None, None, flags)
                user32.UpdateWindow(ctypes.c_void_p(handle))
        except (AttributeError, OSError, TypeError, ValueError, tk.TclError):
            pass

    def refocus() -> None:
        try:
            if not widget.winfo_exists() or widget.focus_get() is not widget:
                redraw()
                return
            if isinstance(widget, tk.Text):
                insert_at: object = widget.index("insert")
            else:
                indexer = getattr(widget, "index", None)
                insert_at = indexer("insert") if callable(indexer) else None
            top = widget.winfo_toplevel()
            top.focus_set()
            top.update_idletasks()
            widget.focus_set()
            if insert_at is not None:
                if isinstance(widget, tk.Text):
                    widget.mark_set("insert", insert_at)
                    widget.see("insert")
                else:
                    cursor = getattr(widget, "icursor", None)
                    if callable(cursor):
                        cursor(insert_at)
            redraw()
        except tk.TclError:
            pass

    redraw()
    # Repaint once after the IME result message and, if necessary, reproduce
    # the harmless focus transition that the user's click/Alt key was causing.
    widget.after(35, redraw)
    widget.after(110, refocus)


def _bind_ime_safe_return(widget: tk.Misc, action: Callable[[], None]) -> None:
    """Keep IME candidate confirmation separate from the form Enter action.

    The first Enter while composing Chinese only commits the selected word.
    A subsequent Enter, after the widget value is already stable, invokes the
    supplied action.  This mirrors normal Windows form behaviour and avoids
    rebuilding a page before Tk has received the committed IME text.
    """

    def observe_input(event: tk.Event) -> None:
        """Infer Tk pre-edit text when the native IME API is inconclusive.

        During ordinary typing the Entry/Text value changes in its class
        binding.  During Chinese pre-edit the letters are painted by the IME,
        but ``get()`` remains unchanged.  Remembering that difference gives us
        a reliable fallback for Microsoft Pinyin versions whose candidate UI
        is not exposed through the legacy IMM query.
        """
        keysym = str(getattr(event, "keysym", ""))
        if keysym in {"Return", "KP_Enter"} or int(getattr(event, "state", 0)) & 0x0004:
            return None
        char = str(getattr(event, "char", ""))
        keycode = int(getattr(event, "keycode", 0))
        if not char and keycode != 229:
            return None
        before_key = _input_widget_value(widget)
        sequence = int(getattr(widget, "_ime_input_sequence", 0)) + 1
        widget._ime_input_sequence = sequence  # type: ignore[attr-defined]

        def compare_after_class_binding() -> None:
            try:
                if not widget.winfo_exists() or sequence != int(getattr(widget, "_ime_input_sequence", 0)):
                    return
                after_key = _input_widget_value(widget)
                widget._ime_uncommitted_input = after_key == before_key  # type: ignore[attr-defined]
                if after_key == before_key:
                    wait_for_late_commit()
            except tk.TclError:
                pass

        def wait_for_late_commit(attempt: int = 0) -> None:
            try:
                if not widget.winfo_exists() or sequence != int(getattr(widget, "_ime_input_sequence", 0)):
                    return
                if _input_widget_value(widget) != before_key:
                    widget._ime_uncommitted_input = False  # type: ignore[attr-defined]
                    _refresh_after_ime_commit(widget)
                    return
                if attempt < 80:
                    widget.after(25, lambda: wait_for_late_commit(attempt + 1))
            except tk.TclError:
                pass

        widget.after_idle(compare_after_class_binding)
        return None

    def pressed(event: tk.Event) -> str | None:
        # Ctrl+Enter is reserved for a newline in the notes controls.
        if int(getattr(event, "state", 0)) & 0x0004:
            return None
        before = _input_widget_value(widget)
        native_composition = _ime_composition_active(widget)
        composition_seen = (
            native_composition
            or bool(getattr(widget, "_ime_uncommitted_input", False))
        )
        token = int(getattr(widget, "_ime_return_token", 0)) + 1
        widget._ime_return_token = token  # type: ignore[attr-defined]

        # Finish and consume the candidate-confirming Enter synchronously.
        # If it is allowed to reach the regular Tk/IME binding chain, some
        # Microsoft Pinyin versions stop dispatching Tk events until an Alt or
        # mouse click, which is the exact blank-input symptom reported by the
        # user.  The next Enter, with no composition, still invokes ``action``.
        if native_composition and _complete_ime_composition(widget):
            widget._ime_uncommitted_input = False  # type: ignore[attr-defined]
            widget.after_idle(lambda: _refresh_after_ime_commit(widget))
            return "break"

        def wait_for_commit(
            attempt: int = 0,
            previous: str | None = None,
            stable: int = 0,
            saw_composition: bool = composition_seen,
        ) -> None:
            try:
                if not widget.winfo_exists() or token != int(getattr(widget, "_ime_return_token", 0)):
                    return
                current = _input_widget_value(widget)
                current_stable = stable + 1 if current == previous else 0
                changed = current != before
                composing = _ime_composition_active(widget)
                saw_composition = saw_composition or composing

                # The value changing around Return means that Return committed
                # an IME candidate.  It must never also submit the form.
                if not composing and changed and current_stable >= 2:
                    widget._ime_uncommitted_input = False  # type: ignore[attr-defined]
                    _refresh_after_ime_commit(widget)
                    return

                # Even if a particular IME updates its Tcl value unusually
                # late, having observed a composition is enough to reserve this
                # Return exclusively for candidate confirmation.
                if saw_composition and not composing and current_stable >= 2:
                    # Once Tk exposes committed text, the next Enter is a
                    # normal form submission.  Until then, continue treating
                    # Return as IME-owned rather than refreshing the page.
                    if current:
                        widget._ime_uncommitted_input = False  # type: ignore[attr-defined]
                    _refresh_after_ime_commit(widget)
                    return

                # With no composition and no value change this is a genuine
                # form Enter.  A short stabilization period avoids racing Tcl;
                # an initially empty field gets extra time because pre-edit
                # text is not returned by Entry.get() on some Windows IMEs.
                required_attempts = 32 if not before else 4
                if not composing and not changed and attempt >= required_attempts and current_stable >= 2:
                    action()
                    return

                # Do not force-submit on timeout.  A still-active candidate
                # window must retain ownership of this Return indefinitely.
                if attempt >= 80:
                    if saw_composition:
                        _refresh_after_ime_commit(widget)
                    return
                widget.after(
                    25,
                    lambda: wait_for_commit(
                        attempt + 1,
                        current,
                        current_stable,
                        saw_composition,
                    ),
                )
            except tk.TclError:
                pass

        # Start from KeyPress: Microsoft Pinyin may consume KeyRelease while
        # its candidate window is open.  Returning None still lets the IME
        # receive and process the Enter normally.
        widget.after(25, wait_for_commit)
        return None

    _install_windows_ime_enter_guard()

    def restore_guard(_event: tk.Event) -> None:
        _install_windows_ime_enter_guard()

    def release_guard(_event: tk.Event) -> None:
        try:
            widget.winfo_toplevel().after(
                100,
                _uninstall_windows_ime_enter_guard_if_background,
            )
        except tk.TclError:
            pass

    widget.bind("<FocusIn>", restore_guard, add="+")
    widget.bind("<FocusOut>", release_guard, add="+")
    widget.bind("<KeyPress>", observe_input, add="+")
    widget.bind("<KeyPress-Return>", pressed)
    # Keep the concrete handler reachable for deterministic UI regression
    # simulation; Tk does not dispatch generated key events to hidden test
    # toplevels on Windows.
    widget._ime_return_handler = pressed  # type: ignore[attr-defined]


def compartment_code(column: int, layer: int) -> str:
    """Return the 2-digit liquid-nitrogen box-position code: column + layer."""
    return f"{column}{layer}"


def unit_code(column: int, layer: int) -> str:
    """Return the box-position code used as the storage-unit code."""
    return compartment_code(column, layer)


def parse_unit_code(code: str) -> tuple[int, int] | None:
    if not re.fullmatch(r"[1-4][1-5]", code):
        return None
    return tuple(int(char) for char in code)  # type: ignore[return-value]


def compact_box_positions(positions: list[str] | tuple[str, ...]) -> str:
    """Compact A1,A2,A3,B1,B2 into A1–3、B1–2."""
    rows: dict[str, set[int]] = {}
    for position in positions:
        match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", str(position).upper())
        if match:
            rows.setdefault(match.group(1), set()).add(int(match.group(2)))
    parts: list[str] = []
    for row_name in sorted(rows, key=lambda name: (len(name), name)):
        numbers = sorted(rows[row_name])
        start = previous = numbers[0]
        for number in numbers[1:] + [numbers[-1] + 2]:
            if number == previous + 1:
                previous = number
                continue
            parts.append(f"{row_name}{start}" if start == previous else f"{row_name}{start}-{previous}")
            start = previous = number
    return "、".join(parts)


@dataclass
class UnitRecord:
    experiment_id: str = ""
    sample_type: str = ""
    sample_count: str = ""
    sample_date: str = ""
    stored_by: str = ""
    claimed_by: str = ""
    claimed_date: str = ""
    claimed_amount: str = ""
    notes: str = ""

    @property
    def occupied(self) -> bool:
        return bool(self.experiment_id.strip())

    @classmethod
    def from_dict(cls, value: object) -> "UnitRecord":
        if not isinstance(value, dict):
            return cls()
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: str(value.get(key, "")) for key in allowed})

    def as_dict(self) -> dict[str, str]:
        return {key: str(getattr(self, key)) for key in self.__dataclass_fields__}


@dataclass
class FreezerData:
    name: str
    records: dict[str, UnitRecord]


class FreezerRepository:
    def __init__(self, path: Path = DATA_FILE):
        self.path = path
        self.freezers: dict[str, FreezerData] = {
            "freezer-1": FreezerData("液氮罐 1", {})
        }
        self.current_freezer_id = "freezer-1"
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw_text = self.path.read_text(encoding="utf-8")
            raw = json.loads(raw_text)
            if not isinstance(raw, dict):
                return
            raw_freezers = raw.get("freezers")
            if isinstance(raw_freezers, dict) and raw_freezers:
                loaded: dict[str, FreezerData] = {}
                for freezer_id, value in raw_freezers.items():
                    if not isinstance(value, dict):
                        continue
                    units = value.get("units", {})
                    unit_items = units.items() if isinstance(units, dict) else []
                    records = {
                        code: UnitRecord.from_dict(record)
                        for code, record in unit_items
                        if parse_unit_code(code)
                    }
                    name = str(value.get("name", "")).strip() or f"液氮罐 {len(loaded) + 1}"
                    loaded[str(freezer_id)] = FreezerData(name[:50], records)
                if loaded:
                    self.freezers = loaded
                    requested = str(raw.get("current_freezer_id", ""))
                    self.current_freezer_id = requested if requested in loaded else next(iter(loaded))
                    return

            # Version 1 migration: keep all existing units in the first freezer.
            items = raw.get("units", raw)
            if isinstance(items, dict):
                migrated = {
                    code: UnitRecord.from_dict(record)
                    for code, record in items.items()
                    if parse_unit_code(code)
                }
                self.freezers = {
                    "freezer-1": FreezerData("液氮罐 1", migrated)
                }
                self.current_freezer_id = "freezer-1"
                backup_path = self.path.with_name(
                    f"{self.path.stem}_v1_backup{self.path.suffix}"
                )
                if not backup_path.exists():
                    backup_path.write_text(raw_text, encoding="utf-8")
                self.save()
        except (OSError, json.JSONDecodeError):
            # Do not prevent the UI from opening because of a damaged data file.
            self.freezers = {"freezer-1": FreezerData("液氮罐 1", {})}
            self.current_freezer_id = "freezer-1"

    @property
    def current_freezer(self) -> FreezerData:
        return self.freezers[self.current_freezer_id]

    @property
    def freezer_name(self) -> str:
        return self.current_freezer.name

    @property
    def records(self) -> dict[str, UnitRecord]:
        return self.current_freezer.records

    def list_freezers(self) -> list[tuple[str, str]]:
        return [(freezer_id, freezer.name) for freezer_id, freezer in self.freezers.items()]

    def _validate_name(self, name: str, exclude_id: str | None = None) -> str:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("液氮罐名称不能为空。")
        if len(cleaned) > 50:
            raise ValueError("液氮罐名称不能超过 50 个字符。")
        if any(
            freezer_id != exclude_id and freezer.name.casefold() == cleaned.casefold()
            for freezer_id, freezer in self.freezers.items()
        ):
            raise ValueError("已经存在同名液氮罐，请使用其他名称。")
        return cleaned

    def add_freezer(self, name: str) -> str:
        cleaned = self._validate_name(name)
        freezer_id = f"freezer-{uuid.uuid4().hex[:10]}"
        previous_id = self.current_freezer_id
        self.freezers[freezer_id] = FreezerData(cleaned, {})
        self.current_freezer_id = freezer_id
        try:
            self.save()
        except OSError:
            self.freezers.pop(freezer_id, None)
            self.current_freezer_id = previous_id
            raise
        return freezer_id

    def rename_freezer(self, freezer_id: str, name: str) -> None:
        if freezer_id not in self.freezers:
            raise ValueError("找不到要重命名的液氮罐。")
        previous_name = self.freezers[freezer_id].name
        self.freezers[freezer_id].name = self._validate_name(name, freezer_id)
        try:
            self.save()
        except OSError:
            self.freezers[freezer_id].name = previous_name
            raise

    def switch_freezer(self, freezer_id: str) -> None:
        if freezer_id not in self.freezers:
            raise ValueError("找不到所选液氮罐。")
        previous_id = self.current_freezer_id
        self.current_freezer_id = freezer_id
        try:
            self.save()
        except OSError:
            self.current_freezer_id = previous_id
            raise

    def import_freezers(
        self,
        imported: list[tuple[str, dict[str, dict[str, str]]]],
        *,
        overwrite: bool,
    ) -> dict[str, int]:
        previous_freezers = copy.deepcopy(self.freezers)
        previous_current = self.current_freezer_id
        created = 0
        updated = 0
        record_count = 0
        self.last_import_backup: Path | None = None
        if self.path.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = self.path.with_name(
                f"{self.path.stem}_导入前备份_{stamp}{self.path.suffix}"
            )
            backup_path.write_bytes(self.path.read_bytes())
            self.last_import_backup = backup_path
        try:
            for raw_name, incoming_records in imported:
                name = raw_name.strip()[:50] or f"导入液氮罐 {len(self.freezers) + 1}"
                matching_id = next(
                    (
                        freezer_id
                        for freezer_id, freezer in self.freezers.items()
                        if freezer.name.casefold() == name.casefold()
                    ),
                    None,
                )
                if matching_id is None:
                    matching_id = f"freezer-{uuid.uuid4().hex[:10]}"
                    self.freezers[matching_id] = FreezerData(name, {})
                    created += 1
                else:
                    updated += 1
                target = self.freezers[matching_id]
                if overwrite:
                    target.records = {}
                converted = {
                    code: UnitRecord.from_dict(record)
                    for code, record in incoming_records.items()
                    if parse_unit_code(code)
                }
                target.records.update(converted)
                record_count += len(converted)
            self.save()
        except (OSError, ValueError):
            self.freezers = previous_freezers
            self.current_freezer_id = previous_current
            raise
        return {
            "freezers": len(imported),
            "created": created,
            "updated": updated,
            "records": record_count,
        }

    def save(self) -> None:
        payload = {
            "version": 2,
            "updated_at": date.today().isoformat(),
            "current_freezer_id": self.current_freezer_id,
            "freezers": {
                freezer_id: {
                    "name": freezer.name,
                    "units": {
                        code: record.as_dict()
                        for code, record in sorted(freezer.records.items())
                        if record.occupied
                    },
                }
                for freezer_id, freezer in self.freezers.items()
            },
        }
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp_path, self.path)

    def get(self, code: str) -> UnitRecord:
        return self.records.get(code, UnitRecord())

    def set(self, code: str, record: UnitRecord) -> None:
        if not parse_unit_code(code):
            raise ValueError(f"无效的冻存盒编号：{code}")
        if record.occupied:
            self.records[code] = record
        else:
            self.records.pop(code, None)
        self.save()

    def clear(self, code: str) -> None:
        self.records.pop(code, None)
        self.save()

    def compartment_usage(self, column: int, layer: int) -> int:
        return int(self.get(unit_code(column, layer)).occupied)

    def layer_usage(self, layer: int) -> int:
        return sum(self.compartment_usage(column, layer) for column in range(1, 5))

    @property
    def used_count(self) -> int:
        return sum(record.occupied for record in self.records.values())

    def search(self, query: str) -> list[tuple[str, UnitRecord]]:
        needle = query.strip().lower()
        if not needle:
            return []
        results = []
        for code, record in sorted(self.records.items()):
            fields = (
                code,
                record.experiment_id,
                record.sample_type,
                record.stored_by,
                record.claimed_by,
                record.notes,
            )
            if any(needle in field.lower() for field in fields):
                results.append((code, record))
        return results


class RoundedButton(tk.Canvas):
    """A lightweight, keyboard-accessible rounded button drawn on a Canvas."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str,
        command: Callable[[], None],
        fill: str = COLORS["primary"],
        foreground: str = "white",
        hover_fill: str | None = None,
        border: str | None = None,
        canvas_bg: str | None = None,
        font: tuple[str, int] | tuple[str, int, str] = ("Microsoft YaHei UI", 9, "bold"),
        width: int | None = None,
        height: int = 38,
        radius: int = 9,
        anchor: str = "center",
        text_padx: int = 14,
    ):
        self._font = tkfont.Font(font=font)
        if width is None:
            longest_line = max(text.splitlines() or [""] , key=len)
            width = max(76, self._font.measure(longest_line) + text_padx * 2)
        if canvas_bg is None:
            try:
                candidate = str(parent.cget("background"))
                canvas_bg = candidate if candidate else COLORS["panel"]
            except tk.TclError:
                canvas_bg = COLORS["panel"]
        super().__init__(
            parent,
            width=width,
            height=height,
            background=canvas_bg,
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
            takefocus=1,
        )
        self._text = text
        self._command = command
        self._fill = fill
        self._normal_fill = fill
        self._hover_fill = hover_fill or fill
        self._foreground = foreground
        self._border = border or fill
        self._radius = radius
        self._anchor = anchor
        self._text_padx = text_padx
        self._invoke_pending = False
        self.bind("<Configure>", self._redraw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._invoke)
        self.bind("<Return>", self._invoke)
        self.bind("<space>", self._invoke)
        self._redraw()

    def _rounded_rectangle(self, width: int, height: int) -> None:
        radius = max(3, min(self._radius, width // 2, height // 2))
        points = (
            radius,
            1,
            width - radius,
            1,
            width - 1,
            1,
            width - 1,
            radius,
            width - 1,
            height - radius,
            width - 1,
            height - 1,
            width - radius,
            height - 1,
            radius,
            height - 1,
            1,
            height - 1,
            1,
            height - radius,
            1,
            radius,
            1,
            1,
        )
        self.create_polygon(
            points,
            smooth=True,
            splinesteps=24,
            fill=self._fill,
            outline=self._border,
            width=1,
        )

    def _redraw(self, _event: tk.Event | None = None) -> None:
        self.delete("all")
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        self._rounded_rectangle(width, height)
        if self._anchor == "w":
            x, text_anchor = self._text_padx, "w"
        else:
            x, text_anchor = width / 2, "center"
        self.create_text(
            x,
            height / 2,
            text=self._text,
            fill=self._foreground,
            font=self._font,
            anchor=text_anchor,
            justify="center" if text_anchor == "center" else "left",
        )

    def _on_enter(self, _event: tk.Event) -> None:
        self._fill = self._hover_fill
        self._redraw()

    def _on_leave(self, _event: tk.Event) -> None:
        self._fill = self._normal_fill
        self._redraw()

    def set_palette(
        self,
        *,
        fill: str,
        foreground: str,
        hover_fill: str,
        border: str,
    ) -> None:
        self._normal_fill = fill
        self._fill = fill
        self._foreground = foreground
        self._hover_fill = hover_fill
        self._border = border
        self._redraw()

    def set_text(self, text: str) -> None:
        self._text = text
        self._redraw()

    def _invoke(self, _event: tk.Event | None = None) -> None:
        if self._invoke_pending:
            return
        self._invoke_pending = True
        self.focus_set()
        self.after_idle(self._run_command)

    def _run_command(self) -> None:
        self._invoke_pending = False
        self._command()


class AppDialog(tk.Toplevel):
    """Modal dialog styled to match the main application."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        title: str,
        message: str,
        buttons: list[tuple[str, object, str]],
        initial_value: str | None = None,
        width: int = 450,
    ):
        super().__init__(parent)
        self.result: object = None
        self.title(title)
        self.configure(bg=COLORS["panel"])
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        header = tk.Frame(self, bg=COLORS["sidebar"], height=58)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header,
            text=title,
            bg=COLORS["sidebar"],
            fg="white",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left", padx=22, pady=16)

        body = tk.Frame(self, bg=COLORS["panel"])
        body.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(
            body,
            text=message,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            justify="left",
            anchor="w",
            wraplength=width - 48,
            font=("Microsoft YaHei UI", 10),
        ).pack(fill="x")

        self.entry: ttk.Entry | None = None
        if initial_value is not None:
            self.entry = ttk.Entry(body, font=("Microsoft YaHei UI", 10))
            self.entry.pack(fill="x", pady=(14, 2))
            self.entry.insert(0, initial_value)
            self.entry.selection_range(0, "end")
            _bind_ime_safe_return(self.entry, lambda: self._finish("__input__"))

        actions = tk.Frame(body, bg=COLORS["panel"])
        actions.pack(fill="x", pady=(20, 0))
        palette = {
            "primary": (COLORS["primary"], "white", COLORS["primary_dark"], COLORS["primary"]),
            "secondary": ("#E8EDF4", COLORS["text"], "#DCE4EE", "#E8EDF4"),
            "danger": ("#FCECEC", COLORS["danger"], "#F7DADA", "#F3D1D1"),
        }
        for label, value, kind in reversed(buttons):
            fill, foreground, hover, border = palette.get(kind, palette["secondary"])
            RoundedButton(
                actions,
                text=label,
                command=lambda selected=value: self._finish(selected),
                fill=fill,
                foreground=foreground,
                hover_fill=hover,
                border=border,
                canvas_bg=COLORS["panel"],
                width=max(92, 24 + len(label) * 15),
                height=38,
                radius=10,
            ).pack(side="right", padx=(8, 0))

        self.bind("<Escape>", lambda _event: self._cancel())
        dialog_height = (
            235
            if initial_value is not None
            else (275 if "\n" in message or len(message) > 90 else 210)
        )
        self.update_idletasks()
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        left = parent_x + max(0, (parent_width - width) // 2)
        top = parent_y + max(0, (parent_height - dialog_height) // 2)
        self.geometry(f"{width}x{dialog_height}+{left}+{top}")
        self.grab_set()
        if self.entry is not None:
            self.entry.focus_set()
        else:
            self.focus_set()

    def _finish(self, value: object) -> None:
        if value == "__input__":
            self.result = self.entry.get().strip() if self.entry is not None else ""
        else:
            self.result = value
        self._close_without_ghost()

    def _cancel(self) -> None:
        self.result = None
        self._close_without_ghost()

    def _close_without_ghost(self) -> None:
        """Hide a modal before destroying it so Windows cannot retain a stale frame."""
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.withdraw()
            self.update_idletasks()
            self.after_idle(self.destroy)
        except tk.TclError:
            pass

    def show(self) -> object:
        self.wait_window()
        return self.result


class BoxLayoutDialog(tk.Toplevel):
    """Row/column picker with a fixed multiplication sign."""

    def __init__(self, parent: tk.Misc, rows: int, columns: int):
        super().__init__(parent)
        self.result: tuple[int, int] | None = None
        self.title("设置盒子布局")
        self.configure(bg=COLORS["panel"])
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        header = tk.Frame(self, bg=COLORS["sidebar"], height=58)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="设置盒子布局", bg=COLORS["sidebar"], fg="white", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left", padx=22, pady=16)

        body = tk.Frame(self, bg=COLORS["panel"])
        body.pack(fill="both", expand=True, padx=26, pady=20)
        tk.Label(body, text="请选择盒子的行数和列数（范围 1–20）", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10), anchor="w").pack(fill="x", pady=(0, 16))

        picker = tk.Frame(body, bg=COLORS["panel"])
        picker.pack(fill="x")
        picker.columnconfigure(0, weight=1)
        picker.columnconfigure(2, weight=1)
        values = tuple(str(value) for value in range(1, 21))
        self.rows_var = tk.StringVar(value=str(rows))
        self.columns_var = tk.StringVar(value=str(columns))

        row_box = tk.Frame(picker, bg=COLORS["panel"])
        row_box.grid(row=0, column=0, sticky="ew")
        tk.Label(row_box, text="行数", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
        self.rows_picker = ttk.Combobox(row_box, textvariable=self.rows_var, values=values, state="readonly", style="Picker.TCombobox", font=("Microsoft YaHei UI", 11), justify="center")
        self.rows_picker.pack(fill="x")

        tk.Label(picker, text="×", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 18, "bold"), width=3).grid(row=0, column=1, sticky="s", padx=10, pady=(0, 2))

        column_box = tk.Frame(picker, bg=COLORS["panel"])
        column_box.grid(row=0, column=2, sticky="ew")
        tk.Label(column_box, text="列数", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
        self.columns_picker = ttk.Combobox(column_box, textvariable=self.columns_var, values=values, state="readonly", style="Picker.TCombobox", font=("Microsoft YaHei UI", 11), justify="center")
        self.columns_picker.pack(fill="x")

        self.actions = tk.Frame(body, bg=COLORS["panel"])
        self.actions.pack(fill="x", pady=(22, 0))
        RoundedButton(self.actions, text="确定", command=self._confirm, fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["panel"], width=92, height=38, radius=10).pack(side="right")
        RoundedButton(self.actions, text="取消", command=self._cancel, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=92, height=38, radius=10).pack(side="right", padx=9)

        self.bind("<Return>", lambda _event: self._confirm())
        self.bind("<Escape>", lambda _event: self._cancel())
        self.update_idletasks()
        width, height = 450, 258
        left = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        top = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{left}+{top}")
        self.grab_set()
        self.after(80, lambda: self._focus_picker_if_visible())

    def _focus_picker_if_visible(self) -> None:
        try:
            if self.rows_picker.winfo_exists() and self.rows_picker.winfo_viewable():
                self.rows_picker.focus_set()
        except tk.TclError:
            pass

    def _confirm(self) -> None:
        self.result = (int(self.rows_var.get()), int(self.columns_var.get()))
        self._close_without_ghost()

    def _cancel(self) -> None:
        self.result = None
        self._close_without_ghost()

    def _close_without_ghost(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.withdraw()
            self.update_idletasks()
            self.after_idle(self.destroy)
        except tk.TclError:
            pass

    def show(self) -> tuple[int, int] | None:
        self.wait_window()
        return self.result


class InventoryLocationsDialog(tk.Toplevel):
    """Show every physical box containing one aggregated sample."""

    def __init__(self, parent: "FreezerManagerApp", sample_name: str, locations: list[dict[str, object]]):
        super().__init__(parent)
        self.parent_app = parent
        self.sample_name = sample_name
        self.locations = locations
        self.opened_boxes = parent.inventory_location_opened_boxes
        self.title("细胞位置明细")
        self.configure(bg=COLORS["panel"])
        self.minsize(650, 360)
        self.resizable(True, True)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._close)

        header = tk.Frame(self, bg=COLORS["sidebar"], height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        title_box = tk.Frame(header, bg=COLORS["sidebar"])
        title_box.pack(side="left", padx=22, pady=11)
        tk.Label(title_box, text=f"细胞位置 · {sample_name}", bg=COLORS["sidebar"], fg="white", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        tk.Label(title_box, text=f"共 {len(locations)} 个盒子", bg=COLORS["sidebar"], fg=COLORS["sidebar_muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w")

        container = tk.Frame(self, bg=COLORS["bg"])
        container.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(container, bg=COLORS["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview, style="Flat.Vertical.TScrollbar")
        body = tk.Frame(self.canvas, bg=COLORS["bg"])
        window = self.canvas.create_window((0, 0), window=body, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        body.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width))
        # Binding on the toplevel catches wheel events from every child widget.
        self.bind("<MouseWheel>", self._on_mousewheel, add="+")
        self.bind("<Button-4>", self._on_mousewheel, add="+")
        self.bind("<Button-5>", self._on_mousewheel, add="+")

        for location in locations:
            freezer_id = str(location["freezer_id"])
            code = str(location["code"])
            opened = (freezer_id, code) in self.opened_boxes
            card_bg = COLORS["primary_soft"] if opened else COLORS["panel"]
            card = tk.Frame(
                body,
                bg=card_bg,
                highlightthickness=2 if opened else 1,
                highlightbackground=COLORS["primary"] if opened else COLORS["line"],
            )
            card.pack(fill="x", padx=18, pady=(14, 0))
            info = tk.Frame(card, bg=card_bg)
            info.pack(side="left", fill="both", expand=True, padx=18, pady=14)
            title_row = tk.Frame(info, bg=card_bg)
            title_row.pack(fill="x")
            tk.Label(title_row, text=f"{location['box_name']}  ·  {location['freezer_name']}", bg=card_bg, fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(side="left")
            if opened:
                tk.Label(
                    title_row,
                    text="已查看",
                    bg="#E7F0FF",
                    fg=COLORS["primary"],
                    font=("Microsoft YaHei UI", 8, "bold"),
                    padx=9,
                    pady=1,
                    highlightthickness=1,
                    highlightbackground="#BFD4FF",
                ).pack(side="left", padx=10)
            tk.Label(info, text=f"盒位 {code}  ·  孔位：{compact_box_positions(location['positions'])}", bg=card_bg, fg=COLORS["muted"], font=("Microsoft YaHei UI", 9), anchor="w").pack(fill="x", pady=(5, 0))
            tk.Label(info, text=f"该盒共 {len(location['positions'])} 支冻存管  ·  占用 {len(location['positions'])} 个孔位", bg=card_bg, fg=COLORS["occupied_text"], font=("Microsoft YaHei UI", 9, "bold"), anchor="w").pack(fill="x", pady=(5, 0))
            RoundedButton(card, text="再次打开" if opened else "打开盒子", command=lambda fid=freezer_id, value=code, positions=tuple(str(value) for value in location["positions"]): self._open_box(fid, value, positions), fill="#DCE8FF" if opened else COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#CFE0FF", border="#DCE8FF" if opened else COLORS["primary_soft"], canvas_bg=card_bg, width=96, height=36, radius=9).pack(side="right", padx=18, pady=16)

        footer = tk.Frame(self, bg=COLORS["panel"])
        footer.pack(fill="x")
        RoundedButton(footer, text="关闭", command=self._close, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=92, height=38, radius=10).pack(side="right", padx=18, pady=12)

        self.bind("<Escape>", lambda _event: self._close())
        self.update_idletasks()
        width = min(780, max(650, parent.winfo_width() - 260))
        height = min(620, max(400, parent.winfo_height() - 120))
        left = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        top = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{left}+{top}")
        self.grab_set()

    def _on_mousewheel(self, event: tk.Event) -> str:
        if getattr(event, "num", None) == 4:
            direction = -1
        elif getattr(event, "num", None) == 5:
            direction = 1
        else:
            delta = int(getattr(event, "delta", 0))
            if not delta:
                return "break"
            direction = -1 if delta > 0 else 1
        self.canvas.yview_scroll(direction, "units")
        return "break"

    def _open_box(self, freezer_id: str, code: str, positions: tuple[str, ...]) -> None:
        self.parent_app.inventory_location_opened_boxes.add((freezer_id, code))
        self._close()
        self.parent_app.after_idle(lambda: self.parent_app.open_inventory_location(freezer_id, code, positions, return_mode="locations"))

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.withdraw()
            self.update_idletasks()
            self.after_idle(self.destroy)
        except tk.TclError:
            pass

    def show(self) -> None:
        self.wait_window()


class BoxMoveDialog(tk.Toplevel):
    """Visually select a target tank and exact empty box positions."""

    def __init__(self, parent: "FreezerManagerApp", source_codes: list[str]):
        super().__init__(parent)
        self.parent_app = parent
        self.repository = parent.repository
        self.source_codes = sorted(source_codes)
        self.required_count = len(self.source_codes)
        self.result: tuple[str, list[str]] | None = None
        self.selected_targets: list[str] = []
        self.target_buttons: dict[str, RoundedButton] = {}
        self.freezer_items = self.repository.list_freezers()
        self.freezer_ids = [item[0] for item in self.freezer_items]
        self.freezer_names = [item[1] for item in self.freezer_items]

        self.title("可视化批量移动")
        self.configure(bg=COLORS["bg"])
        self.minsize(720, 580)
        self.resizable(True, True)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        header = tk.Frame(self, bg=COLORS["sidebar"], height=68)
        header.pack(fill="x")
        header.pack_propagate(False)
        header_text = tk.Frame(header, bg=COLORS["sidebar"])
        header_text.pack(side="left", padx=24, pady=11)
        tk.Label(header_text, text="选择移动目标", bg=COLORS["sidebar"], fg="white", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(header_text, text=f"源冻存盒：{'、'.join(self.source_codes)}", bg=COLORS["sidebar"], fg=COLORS["sidebar_muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(2, 0))

        controls = tk.Frame(self, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        controls.pack(fill="x", padx=20, pady=(18, 12))
        tk.Label(controls, text="目标液氮罐", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(side="left", padx=(18, 10), pady=13)
        self.freezer_var = tk.StringVar(value=self.repository.freezer_name)
        self.freezer_picker = ttk.Combobox(controls, textvariable=self.freezer_var, values=self.freezer_names, state="readonly", font=("Microsoft YaHei UI", 9), width=25)
        self.freezer_picker.pack(side="left", pady=12)
        self.freezer_picker.bind("<<ComboboxSelected>>", self._change_freezer)
        self.selection_label = tk.Label(controls, text="", bg=COLORS["panel"], fg=COLORS["primary"], font=("Microsoft YaHei UI", 9, "bold"))
        self.selection_label.pack(side="right", padx=18)

        grid_panel = tk.Frame(self, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        grid_panel.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        grid_head = tk.Frame(grid_panel, bg=COLORS["panel"])
        grid_head.pack(fill="x", padx=18, pady=(13, 7))
        tk.Label(grid_head, text="点击空盒位作为目标", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        tk.Label(grid_head, text="绿色可选 · 灰色已占用", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(side="right")
        self.grid = tk.Frame(grid_panel, bg=COLORS["panel"])
        self.grid.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        footer = tk.Frame(self, bg=COLORS["panel"])
        footer.pack(fill="x")
        self.confirm_button = RoundedButton(footer, text="确认移动", command=self._confirm, fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["panel"], width=106, height=40, radius=10)
        self.confirm_button.pack(side="right", padx=(8, 20), pady=12)
        RoundedButton(footer, text="取消", command=self._cancel, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=92, height=40, radius=10).pack(side="right", pady=12)

        self.bind("<Escape>", lambda _event: self._cancel())
        self.bind("<Return>", lambda _event: self._confirm())
        self._render_grid()
        self.update_idletasks()
        width = min(880, max(740, parent.winfo_width() - 220))
        height = min(700, max(600, parent.winfo_height() - 80))
        left = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        top = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{left}+{top}")
        self.grab_set()

    def _target_freezer_id(self) -> str:
        index = self.freezer_picker.current()
        if index < 0:
            index = self.freezer_names.index(self.freezer_var.get()) if self.freezer_var.get() in self.freezer_names else 0
        return self.freezer_ids[index]

    def _change_freezer(self, _event: tk.Event | None = None) -> None:
        self.selected_targets.clear()
        self._render_grid()

    def _render_grid(self) -> None:
        for child in self.grid.winfo_children():
            child.destroy()
        self.target_buttons.clear()
        target_id = self._target_freezer_id()
        columns = self.repository.freezers[target_id].storage_columns
        for column in range(columns + 1):
            self.grid.columnconfigure(column, weight=1 if column else 0, uniform="move-target")
        for row in range(6):
            self.grid.rowconfigure(row, weight=1 if row else 0)
        tk.Label(self.grid, text="层 / 列", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).grid(row=0, column=0, padx=8, pady=4)
        for column in range(1, columns + 1):
            tk.Label(self.grid, text=f"第 {column} 列", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8, "bold")).grid(row=0, column=column, pady=4)
        for layer in range(1, 6):
            tk.Label(self.grid, text=f"第 {layer} 层", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8, "bold")).grid(row=layer, column=0, padx=8)
            for column in range(1, columns + 1):
                code = compartment_code(column, layer)
                empty = self.repository.box_position_is_empty(target_id, code)
                if empty:
                    button = RoundedButton(
                        self.grid,
                        text=f"{code}\n空盒位",
                        command=lambda value=code: self._toggle_target(value),
                        fill="#F1F8F5",
                        foreground=COLORS["occupied_text"],
                        hover_fill="#D8F0E4",
                        border="#C9E9D9",
                        canvas_bg=COLORS["panel"],
                        font=("Microsoft YaHei UI", 9, "bold"),
                        width=110,
                        height=55,
                        radius=9,
                    )
                    self.target_buttons[code] = button
                    button.grid(row=layer, column=column, sticky="nsew", padx=6, pady=5)
                else:
                    tk.Label(self.grid, text=f"{code}\n已占用", bg="#EEF2F7", fg="#A0A9B7", font=("Microsoft YaHei UI", 9, "bold"), highlightthickness=1, highlightbackground=COLORS["line"], padx=8, pady=10).grid(row=layer, column=column, sticky="nsew", padx=6, pady=5)
        self._refresh_selection_status()

    def _toggle_target(self, code: str) -> None:
        if code in self.selected_targets:
            self.selected_targets.remove(code)
        elif len(self.selected_targets) < self.required_count:
            self.selected_targets.append(code)
        self._refresh_selection_status()

    def _refresh_selection_status(self) -> None:
        self.selection_label.configure(text=f"已选 {len(self.selected_targets)} / {self.required_count} 个目标盒位")
        for code, button in self.target_buttons.items():
            selected = code in self.selected_targets
            order = self.selected_targets.index(code) + 1 if selected else 0
            button.set_text(f"✓ {code}\n目标 {order}" if selected else f"{code}\n空盒位")
            button.set_palette(
                fill=COLORS["primary"] if selected else "#F1F8F5",
                foreground="white" if selected else COLORS["occupied_text"],
                hover_fill=COLORS["primary_dark"] if selected else "#D8F0E4",
                border=COLORS["primary"] if selected else "#C9E9D9",
            )

    def _confirm(self) -> None:
        if len(self.selected_targets) != self.required_count:
            self.selection_label.configure(text=f"还需选择 {self.required_count - len(self.selected_targets)} 个目标盒位", fg=COLORS["danger"])
            return
        self.result = (self._target_freezer_id(), list(self.selected_targets))
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self) -> tuple[str, list[str]] | None:
        self.wait_window()
        return self.result


class InventoryAlertDialog(tk.Toplevel):
    """Edit the low-stock thresholds for one aggregated cell inventory item."""

    def __init__(self, parent: tk.Misc, sample_name: str, low_threshold: int, warning_threshold: int):
        super().__init__(parent)
        self.result: tuple[int, int] | None = None
        self.title("库存预警设置")
        self.configure(bg=COLORS["panel"])
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        header = tk.Frame(self, bg=COLORS["sidebar"], height=62)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="库存预警设置", bg=COLORS["sidebar"], fg="white", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left", padx=22, pady=17)

        body = tk.Frame(self, bg=COLORS["panel"])
        body.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(body, text=sample_name, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(body, text="库存按已占用孔位（冻存管数）计算。", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(4, 16))

        form = tk.Frame(body, bg=COLORS["panel"])
        form.pack(fill="x")
        form.columnconfigure((0, 1), weight=1)
        self.low_var = tk.StringVar(value=str(low_threshold))
        self.warning_var = tk.StringVar(value=str(warning_threshold))
        for column, (label, variable, hint) in enumerate((
            ("最低库存", self.low_var, "≤ 此值显示红色"),
            ("提醒库存", self.warning_var, "≤ 此值显示橙色"),
        )):
            field = tk.Frame(form, bg=COLORS["panel"])
            field.grid(row=0, column=column, sticky="ew", padx=(0, 8) if column == 0 else (8, 0))
            tk.Label(field, text=label, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
            entry = ttk.Entry(field, textvariable=variable, font=("Microsoft YaHei UI", 10))
            entry.pack(fill="x", pady=(6, 4), ipady=3)
            tk.Label(field, text=hint, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w")

        self.error_label = tk.Label(body, text="", bg=COLORS["panel"], fg=COLORS["danger"], font=("Microsoft YaHei UI", 8))
        self.error_label.pack(anchor="w", pady=(12, 0))
        actions = tk.Frame(body, bg=COLORS["panel"])
        actions.pack(fill="x", pady=(8, 0))
        RoundedButton(actions, text="保存", command=self._save, fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["panel"], width=92, height=38, radius=10).pack(side="right")
        RoundedButton(actions, text="取消", command=self._cancel, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=92, height=38, radius=10).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _event: self._cancel())
        self.bind("<Return>", lambda _event: self._save())
        self.update_idletasks()
        # Leave enough client-area height for the custom Canvas buttons.  A fixed
        # 335px window clipped their lower half on Windows display scaling.
        width, height = 480, max(380, self.winfo_reqheight() + 28)
        left = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        top = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{left}+{top}")
        self.grab_set()

    def _save(self) -> None:
        try:
            low = int(self.low_var.get().strip())
            warning = int(self.warning_var.get().strip())
        except ValueError:
            self.error_label.configure(text="最低库存和提醒库存必须填写整数。")
            return
        if low < 0 or warning < 0:
            self.error_label.configure(text="预警值不能小于 0。")
            return
        if warning < low:
            self.error_label.configure(text="提醒库存不能小于最低库存。")
            return
        self.result = (low, warning)
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self) -> tuple[int, int] | None:
        self.wait_window()
        return self.result


class MultiSelectDropdown(tk.Frame):
    """Compact, non-modal checklist used by filters that accept several values.

    This control uses a floating frame inside the main window.  It creates no
    separate window and uses neither a Tk grab nor a native menu loop.  Because
    the frame is positioned with ``place``, opening it never stretches the
    filter row or the page below it.
    """

    def __init__(
        self,
        parent: tk.Misc,
        options: dict[str, str],
        selected: set[str],
        textvariable: tk.StringVar,
    ):
        super().__init__(parent, bg=COLORS["panel"], highlightthickness=0)
        self.options = dict(options)
        self.selected = selected
        self.textvariable = textvariable
        self.panel: tk.Frame | None = None
        self.variables: dict[str, tk.BooleanVar] = {
            key: tk.BooleanVar(value=key in self.selected)
            for key in self.options
        }
        self.button = ttk.Button(
            self,
            textvariable=self.textvariable,
            command=self._open,
            style="EventFilter.TButton",
        )
        self.button.pack(fill="x")
        self._update_text()

    def _update_text(self) -> None:
        if self.selected == set(self.options):
            label = "全部操作"
        elif not self.selected:
            label = "未选择操作"
        elif len(self.selected) == 1:
            key = next(iter(self.selected))
            label = self.options.get(key, key)
        else:
            label = f"已选 {len(self.selected)} 项"
        self.textvariable.set(f"{label}  ▾")

    def _open(self) -> None:
        if self.panel is not None and self.panel.winfo_exists():
            self._close()
            return
        for key, variable in self.variables.items():
            variable.set(key in self.selected)

        root = self.winfo_toplevel()
        body = tk.Frame(
            root,
            bg=COLORS["panel"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
        )
        self.panel = body
        tk.Label(
            body,
            text="可勾选多个操作类型",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w", padx=12, pady=(10, 5))
        for key, label in self.options.items():
            tk.Checkbutton(
                body,
                text=label,
                variable=self.variables[key],
                command=self._sync_selection,
                anchor="w",
                bg=COLORS["panel"],
                fg=COLORS["text"],
                activebackground=COLORS["primary_soft"],
                activeforeground=COLORS["text"],
                selectcolor=COLORS["panel"],
                font=("Microsoft YaHei UI", 9),
                padx=8,
                pady=3,
            ).pack(fill="x", padx=6)

        actions = tk.Frame(body, bg=COLORS["panel"])
        actions.pack(fill="x", padx=10, pady=(7, 10))
        ttk.Button(
            actions,
            text="全选",
            command=lambda: self._set_all(True),
            style="Secondary.TButton",
            width=6,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="清空",
            command=lambda: self._set_all(False),
            style="Secondary.TButton",
            width=6,
        ).pack(side="left", padx=6)
        ttk.Button(
            actions,
            text="完成",
            command=self._close,
            style="Primary.TButton",
            width=6,
        ).pack(side="right")

        root.update_idletasks()
        panel_width = 270
        panel_height = body.winfo_reqheight()
        x = self.winfo_rootx() - root.winfo_rootx()
        y = self.winfo_rooty() - root.winfo_rooty() + self.winfo_height() + 3
        x = max(8, min(x, root.winfo_width() - panel_width - 8))
        if y + panel_height > root.winfo_height() - 8:
            y = max(8, self.winfo_rooty() - root.winfo_rooty() - panel_height - 3)
        body.place(x=x, y=y, width=panel_width)
        body.lift()

    def _set_all(self, value: bool) -> None:
        for variable in self.variables.values():
            variable.set(value)
        self._sync_selection()

    def _sync_selection(self) -> None:
        self.selected.clear()
        self.selected.update(key for key, variable in self.variables.items() if variable.get())
        self._update_text()

    def _close(self) -> None:
        panel = self.panel
        self.panel = None
        if panel is not None:
            try:
                panel.destroy()
            except tk.TclError:
                pass

    def destroy(self) -> None:
        self._close()
        super().destroy()


class DatePickerField(tk.Frame):
    """Editable YYYY-MM-DD field with an independent floating calendar."""

    WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")

    def __init__(
        self,
        parent: tk.Misc,
        variable: tk.StringVar,
        on_return: Callable[[], object] | None = None,
    ):
        super().__init__(parent, bg=COLORS["panel"], highlightthickness=0)
        self.variable = variable
        self.panel: tk.Toplevel | None = None
        self.calendar_year = date.today().year
        self.calendar_month = date.today().month
        # Keep the requested width compact so two date fields can share a dialog
        # without Tk clipping the calendar button at the right edge.
        self.entry = ttk.Entry(self, textvariable=variable, width=12, font=("Microsoft YaHei UI", 10))
        self.entry.pack(side="left", fill="x", expand=True, ipady=3)
        if on_return is not None:
            _bind_ime_safe_return(self.entry, on_return)
        self.calendar_button = ttk.Button(
            self,
            text="日期",
            command=self.open_calendar,
            style="Secondary.TButton",
            width=5,
        )
        self.calendar_button.pack(side="left", padx=(5, 0), ipady=1)

    @staticmethod
    def _parsed_date(value: str) -> date | None:
        cleaned = value.strip()
        for date_format in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(cleaned, date_format).date()
            except ValueError:
                continue
        return None

    def open_calendar(self) -> None:
        if self.panel is not None and self.panel.winfo_exists():
            self.close_calendar()
            return
        root = self.winfo_toplevel()
        active = getattr(root, "_active_date_picker", None)
        if active is not None and active is not self:
            try:
                active.close_calendar()
            except (AttributeError, tk.TclError):
                pass
        selected = self._parsed_date(self.variable.get()) or date.today()
        self.calendar_year = selected.year
        self.calendar_month = selected.month
        panel = tk.Toplevel(
            root,
            bg=COLORS["panel"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
        )
        panel.withdraw()
        panel.overrideredirect(True)
        panel.transient(root)
        self.panel = panel
        setattr(root, "_active_date_picker", self)
        self._render_calendar()
        self._position_calendar()

    def _position_calendar(self) -> None:
        """Place the calendar in screen coordinates, outside dialog clipping."""
        panel = self.panel
        if panel is None or not panel.winfo_exists():
            return
        panel.update_idletasks()
        panel_width = 292
        panel_height = panel.winfo_reqheight()
        screen_left = self.winfo_vrootx()
        screen_top = self.winfo_vrooty()
        screen_right = screen_left + self.winfo_vrootwidth()
        screen_bottom = screen_top + self.winfo_vrootheight()
        field_left = self.winfo_rootx()
        field_top = self.winfo_rooty()
        below = field_top + self.winfo_height() + 3
        x = max(screen_left + 8, min(field_left, screen_right - panel_width - 8))
        if below + panel_height <= screen_bottom - 8:
            y = below
        else:
            y = max(screen_top + 8, field_top - panel_height - 3)
        panel.minsize(panel_width, panel_height)
        panel.geometry(f"{panel_width}x{panel_height}+{x}+{y}")
        panel.deiconify()
        try:
            panel.lift(self.winfo_toplevel())
        except tk.TclError:
            panel.lift()

    def _render_calendar(self) -> None:
        panel = self.panel
        if panel is None:
            return
        for child in panel.winfo_children():
            child.destroy()
        header = tk.Frame(panel, bg=COLORS["panel"])
        header.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Button(header, text="‹", command=lambda: self._change_month(-1), style="Secondary.TButton", width=3).pack(side="left")
        tk.Label(
            header,
            text=f"{self.calendar_year} 年 {self.calendar_month} 月",
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(header, text="›", command=lambda: self._change_month(1), style="Secondary.TButton", width=3).pack(side="right")

        grid = tk.Frame(panel, bg=COLORS["panel"])
        grid.pack(fill="x", padx=8)
        for column, weekday in enumerate(self.WEEKDAYS):
            grid.columnconfigure(column, weight=1, uniform="calendar_day")
            tk.Label(
                grid,
                text=weekday,
                bg=COLORS["panel"],
                fg=COLORS["muted"],
                font=("Microsoft YaHei UI", 8, "bold"),
            ).grid(row=0, column=column, sticky="ew", pady=(2, 4))
        selected = self._parsed_date(self.variable.get())
        today = date.today()
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(self.calendar_year, self.calendar_month)
        for row_index, week in enumerate(weeks, 1):
            for column, day_number in enumerate(week):
                if day_number == 0:
                    tk.Label(grid, text="", bg=COLORS["panel"]).grid(row=row_index, column=column, sticky="nsew", padx=1, pady=1)
                    continue
                candidate = date(self.calendar_year, self.calendar_month, day_number)
                is_selected = candidate == selected
                is_today = candidate == today
                background = COLORS["primary"] if is_selected else (COLORS["primary_soft"] if is_today else COLORS["panel"])
                foreground = "white" if is_selected else (COLORS["primary"] if is_today else COLORS["text"])
                tk.Button(
                    grid,
                    text=str(day_number),
                    command=lambda value=candidate: self._select_date(value),
                    relief="flat",
                    borderwidth=0,
                    bg=background,
                    fg=foreground,
                    activebackground=COLORS["primary_soft"],
                    activeforeground=COLORS["primary"],
                    font=("Microsoft YaHei UI", 8, "bold" if is_selected or is_today else "normal"),
                    cursor="hand2",
                ).grid(row=row_index, column=column, sticky="nsew", padx=1, pady=1, ipady=3)
        footer = tk.Frame(panel, bg=COLORS["panel"])
        footer.pack(fill="x", padx=8, pady=(6, 8))
        ttk.Button(footer, text="今天", command=lambda: self._select_date(date.today()), style="Secondary.TButton", width=7).pack(side="left")
        ttk.Button(footer, text="关闭", command=self.close_calendar, style="Secondary.TButton", width=7).pack(side="right")

    def _change_month(self, amount: int) -> None:
        month_index = self.calendar_year * 12 + self.calendar_month - 1 + amount
        self.calendar_year, month_zero_based = divmod(month_index, 12)
        self.calendar_month = month_zero_based + 1
        self._render_calendar()
        self._position_calendar()

    def _select_date(self, value: date) -> None:
        self.variable.set(value.isoformat())
        self.close_calendar()
        self.entry.focus_set()

    def close_calendar(self) -> None:
        panel = self.panel
        self.panel = None
        try:
            root = self.winfo_toplevel()
            if getattr(root, "_active_date_picker", None) is self:
                setattr(root, "_active_date_picker", None)
        except tk.TclError:
            pass
        if panel is not None:
            try:
                panel.destroy()
            except tk.TclError:
                pass

    def destroy(self) -> None:
        self.close_calendar()
        super().destroy()


class BoxSampleDialog(tk.Toplevel):
    """Compact cell editor shared by single-position and batch box entry."""

    FIELDS = (
        ("sample_name", "细胞名称 *"),
        ("sample_type", "细胞类别"),
        ("stored_date", "入库日期 *"),
        ("stored_by", "入库人 *"),
    )

    def __init__(self, parent: tk.Misc, title: str, sample: BoxSample, *, batch: bool = False, allow_clear: bool = False):
        super().__init__(parent)
        self.result: dict[str, str] | None = None
        self.title(title)
        self.configure(bg=COLORS["panel"])
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        header = tk.Frame(self, bg=COLORS["sidebar"], height=58)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text=title, bg=COLORS["sidebar"], fg="white", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left", padx=22, pady=16)

        body = tk.Frame(self, bg=COLORS["panel"])
        body.pack(fill="both", expand=True, padx=24, pady=20)
        if batch:
            tk.Label(body, text="批量入库仅限空孔位。细胞名称可使用 {position} 自动生成孔位号。", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 9), anchor="w").pack(fill="x", pady=(0, 12))
        form = tk.Frame(body, bg=COLORS["panel"])
        form.pack(fill="x")
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        self.variables: dict[str, tk.StringVar] = {}
        self.entries: dict[str, ttk.Entry] = {}
        for index, (key, label) in enumerate(self.FIELDS):
            value = getattr(sample, key)
            variable = tk.StringVar(value=value)
            self.variables[key] = variable
            row, column = divmod(index, 2)
            field = tk.Frame(form, bg=COLORS["panel"])
            field.grid(row=row, column=column, sticky="ew", padx=(0, 8) if column == 0 else (8, 0), pady=7)
            tk.Label(field, text=label, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 5))
            if key == "stored_date":
                picker = DatePickerField(field, variable, self._save)
                picker.pack(fill="x")
                entry = picker.entry
            else:
                entry = ttk.Entry(field, textvariable=variable, width=27, font=("Microsoft YaHei UI", 10))
                entry.pack(fill="x")
                _bind_ime_safe_return(entry, self._save)
            self.entries[key] = entry
            if index == 0:
                self.after(80, self._focus_sample_name)

        tk.Label(body, text="备注", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(12, 5))
        self.notes = tk.Text(body, height=4, width=58, wrap="word", relief="solid", borderwidth=1, font=("Microsoft YaHei UI", 10), fg=COLORS["text"])
        self.notes.pack(fill="x")
        self.notes.insert("1.0", sample.notes)
        _bind_ime_safe_return(self.notes, self._save)
        self.notes.bind("<Control-Return>", self._newline_in_notes)

        actions = tk.Frame(body, bg=COLORS["panel"])
        actions.pack(fill="x", pady=(18, 0))
        primary_text = "批量入库" if batch else ("保存信息" if sample.occupied else "确认入库")
        RoundedButton(actions, text=primary_text, command=self._save, fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["panel"], width=96, height=38, radius=10).pack(side="right")
        RoundedButton(actions, text="取消", command=self._cancel, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=92, height=38, radius=10).pack(side="right", padx=9)
        if allow_clear and sample.occupied:
            RoundedButton(actions, text="清除孔位", command=self._clear, fill="#FCECEC", foreground=COLORS["danger"], hover_fill="#F7DADA", border="#F3D1D1", canvas_bg=COLORS["panel"], width=92, height=38, radius=10).pack(side="left")
            RoundedButton(actions, text="出库", command=self._checkout, fill="#FFF0DE", foreground="#A85B0B", hover_fill="#FFE1BD", border="#FFE1BD", canvas_bg=COLORS["panel"], width=82, height=38, radius=10).pack(side="left", padx=9)

        self.bind("<Escape>", lambda _event: self._cancel())
        self.update_idletasks()
        width, height = 610, self.winfo_reqheight()
        x = parent.winfo_rootx() + max(20, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(20, (parent.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _newline_in_notes(self, _event: tk.Event) -> str:
        self.notes.insert("insert", "\n")
        return "break"

    def _save(self) -> None:
        values = {key: variable.get().strip() for key, variable in self.variables.items()}
        values["notes"] = self.notes.get("1.0", "end-1c").strip()
        missing_key = next(
            (key for key in ("sample_name", "stored_by", "stored_date") if not values[key]),
            None,
        )
        if missing_key is not None:
            self.bell()
            try:
                self.grab_release()
            except tk.TclError:
                pass
            AppDialog(
                self,
                title="信息未填写完整",
                message={
                    "sample_name": "请填写细胞名称后再保存。",
                    "stored_by": "请填写入库人后再保存。",
                    "stored_date": "请填写入库日期后再保存。",
                }[missing_key],
                buttons=[("知道了", True, "danger")],
                width=410,
            ).show()
            try:
                if self.winfo_exists():
                    self.grab_set()
                    self.after_idle(lambda key=missing_key: self._focus_required_field(key))
            except tk.TclError:
                pass
            return
        self.result = values
        self._close_without_ghost()

    def _focus_sample_name(self) -> None:
        self._focus_required_field("sample_name")

    def _focus_required_field(self, key: str) -> None:
        try:
            entry = self.entries[key]
            if self.winfo_exists() and entry.winfo_exists() and entry.winfo_viewable():
                entry.focus_set()
        except (KeyError, tk.TclError):
            pass

    def _cancel(self) -> None:
        self.result = None
        self._close_without_ghost()

    def _clear(self) -> None:
        self.result = {"__action__": "clear"}
        self._close_without_ghost()

    def _checkout(self) -> None:
        self.result = {"__action__": "checkout"}
        self._close_without_ghost()

    def _close_without_ghost(self) -> None:
        """Remove the modal from the compositor before the caller updates its page."""
        try:
            self.grab_release()
        except tk.TclError:
            pass
        try:
            self.withdraw()
            self.update_idletasks()
            self.after_idle(self.destroy)
        except tk.TclError:
            pass

    def show(self) -> dict[str, str] | None:
        self.grab_set()
        self.wait_window()
        return self.result


class BatchOutboundDialog(tk.Toplevel):
    """Collect the common operator and business date for one outbound batch."""

    def __init__(self, parent: tk.Misc, count: int, location: str):
        super().__init__(parent)
        self.result: tuple[str, str] | None = None
        dialog_title = "出库登记" if count == 1 else "批量出库登记"
        self.title(dialog_title)
        self.configure(bg=COLORS["panel"])
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        header = tk.Frame(self, bg=COLORS["sidebar"], height=60)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text=dialog_title, bg=COLORS["sidebar"], fg="white", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left", padx=22, pady=17)

        body = tk.Frame(self, bg=COLORS["panel"])
        body.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(body, text=f"将从 {location} 出库 {count} 支冻存管", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 16))

        form = tk.Frame(body, bg=COLORS["panel"])
        form.pack(fill="x")
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        self.operator_var = tk.StringVar()
        self.date_var = tk.StringVar(value=date.today().isoformat())
        self.entries: list[ttk.Entry] = []
        for column, (label, variable) in enumerate((("出库人 *", self.operator_var), ("出库日期 *", self.date_var))):
            cell = tk.Frame(form, bg=COLORS["panel"])
            cell.grid(row=0, column=column, sticky="ew", padx=(0, 8) if column == 0 else (8, 0))
            tk.Label(cell, text=label, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
            if column == 1:
                picker = DatePickerField(cell, variable, self._save)
                picker.pack(fill="x")
                entry = picker.entry
            else:
                entry = ttk.Entry(cell, textvariable=variable, font=("Microsoft YaHei UI", 10))
                entry.pack(fill="x", ipady=3)
                _bind_ime_safe_return(entry, self._save)
            self.entries.append(entry)

        self.error_label = tk.Label(body, text="", bg=COLORS["panel"], fg=COLORS["danger"], font=("Microsoft YaHei UI", 8))
        self.error_label.pack(anchor="w", pady=(12, 0))
        actions = tk.Frame(body, bg=COLORS["panel"])
        actions.pack(fill="x", pady=(12, 0))
        RoundedButton(actions, text="确认出库", command=self._save, fill="#D9822B", hover_fill="#C56F1E", canvas_bg=COLORS["panel"], width=100, height=38, radius=10).pack(side="right")
        RoundedButton(actions, text="取消", command=self._cancel, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=88, height=38, radius=10).pack(side="right", padx=9)
        self.bind("<Escape>", lambda _event: self._cancel())
        self.update_idletasks()
        width, height = 510, max(300, self.winfo_reqheight() + 20)
        x = parent.winfo_rootx() + max(20, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(20, (parent.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.after(80, lambda: self.entries[0].focus_set())

    def _save(self) -> None:
        operator = self.operator_var.get().strip()
        operation_date = self.date_var.get().strip()
        if not operator:
            self.error_label.configure(text="请填写出库人。")
            self.entries[0].focus_set()
            return
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{8}", operation_date):
            self.error_label.configure(text="出库日期请填写为 YYYY-MM-DD 或 YYYYMMDD。")
            self.entries[1].focus_set()
            return
        if not FreezerManagerApp._valid_date(operation_date):
            self.error_label.configure(text="出库日期不是有效日期。")
            self.entries[1].focus_set()
            return
        normalized_date = datetime.strptime(operation_date, "%Y%m%d").date().isoformat() if len(operation_date) == 8 else operation_date
        self.result = (operator, normalized_date)
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self) -> tuple[str, str] | None:
        self.grab_set()
        self.wait_window()
        return self.result


class InventoryEventExportDialog(tk.Toplevel):
    """Collect the inclusive date range for the formal inbound/outbound ledger."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.result: tuple[str, str] | None = None
        self.title("导出出入库登记")
        self.configure(bg=COLORS["panel"])
        self.resizable(False, False)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        header = tk.Frame(self, bg=COLORS["sidebar"], height=60)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header,
            text="导出出入库登记",
            bg=COLORS["sidebar"],
            fg="white",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left", padx=22, pady=17)

        body = tk.Frame(self, bg=COLORS["panel"])
        body.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(
            body,
            text="仅导出普通入库和出库记录（包含起止日期）。",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=(0, 15))

        form = tk.Frame(body, bg=COLORS["panel"])
        form.pack(fill="x")
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        today = date.today().isoformat()
        self.date_from_var = tk.StringVar(value=today)
        self.date_to_var = tk.StringVar(value=today)
        self.entries: list[ttk.Entry] = []
        for column, (label, variable) in enumerate((
            ("初始日期 *", self.date_from_var),
            ("截至日期 *", self.date_to_var),
        )):
            cell = tk.Frame(form, bg=COLORS["panel"])
            cell.grid(row=0, column=column, sticky="ew", padx=(0, 8) if column == 0 else (8, 0))
            tk.Label(
                cell,
                text=label,
                bg=COLORS["panel"],
                fg=COLORS["text"],
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(anchor="w", pady=(0, 6))
            picker = DatePickerField(cell, variable, self._save)
            picker.pack(fill="x")
            entry = picker.entry
            tk.Label(
                cell,
                text="YYYY-MM-DD",
                bg=COLORS["panel"],
                fg=COLORS["muted"],
                font=("Microsoft YaHei UI", 7),
            ).pack(anchor="w", pady=(3, 0))
            self.entries.append(entry)

        self.error_label = tk.Label(
            body,
            text="",
            bg=COLORS["panel"],
            fg=COLORS["danger"],
            font=("Microsoft YaHei UI", 8),
        )
        self.error_label.pack(anchor="w", pady=(12, 0))
        actions = tk.Frame(body, bg=COLORS["panel"])
        actions.pack(fill="x", pady=(12, 0))
        RoundedButton(
            actions,
            text="导出",
            command=self._save,
            fill=COLORS["primary"],
            hover_fill=COLORS["primary_dark"],
            canvas_bg=COLORS["panel"],
            width=92,
            height=38,
            radius=10,
        ).pack(side="right")
        RoundedButton(
            actions,
            text="取消",
            command=self._cancel,
            fill="#E8EDF4",
            foreground=COLORS["text"],
            hover_fill="#DCE4EE",
            border="#E8EDF4",
            canvas_bg=COLORS["panel"],
            width=88,
            height=38,
            radius=10,
        ).pack(side="right", padx=9)

        self.bind("<Escape>", lambda _event: self._cancel())
        self.update_idletasks()
        width, height = 530, max(310, self.winfo_reqheight() + 14)
        x = parent.winfo_rootx() + max(20, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(20, (parent.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.after(80, lambda: self.entries[0].focus_set())

    @staticmethod
    def _normalize_date(value: str) -> str | None:
        for date_format in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(value, date_format).date().isoformat()
            except ValueError:
                continue
        return None

    def _save(self) -> None:
        raw_from = self.date_from_var.get().strip()
        raw_to = self.date_to_var.get().strip()
        if not raw_from:
            self.error_label.configure(text="请选择或输入初始日期。")
            self.entries[0].focus_set()
            return
        date_from = self._normalize_date(raw_from)
        if date_from is None:
            self.error_label.configure(text="初始日期不是有效日期，请使用 YYYY-MM-DD。")
            self.entries[0].focus_set()
            return
        date_to = self._normalize_date(raw_to)
        if date_to is None:
            self.error_label.configure(text="截至日期不是有效日期，请使用 YYYY-MM-DD。")
            self.entries[1].focus_set()
            return
        if date_from > date_to:
            self.error_label.configure(text="初始日期不能晚于截至日期。")
            self.entries[0].focus_set()
            return
        self.result = (date_from, date_to)
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self) -> tuple[str, str] | None:
        self.grab_set()
        self.wait_window()
        return self.result


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent: tk.Misc, *, horizontal: bool = False, **kwargs: object):
        super().__init__(parent, **kwargs)
        self.horizontal = horizontal
        self.viewport = ttk.Frame(self, style="Page.TFrame")
        self.viewport.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            self.viewport, background=COLORS["bg"], highlightthickness=0, borderwidth=0
        )
        self.scrollbar = ttk.Scrollbar(self.viewport, orient="vertical", command=self.canvas.yview, style="Flat.Vertical.TScrollbar")
        self.horizontal_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.body = ttk.Frame(self.canvas, style="Page.TFrame")
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set, xscrollcommand=self.horizontal_scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar_visible = False
        self._horizontal_scrollbar_visible = False
        self.body.bind("<Configure>", self._sync_scroll_region)
        self.canvas.bind("<Configure>", self._sync_width)
        toplevel = self.winfo_toplevel()
        register = getattr(toplevel, "_register_scrollable_frame", None)
        if callable(register):
            register(self)

    def _sync_scroll_region(self, _event: tk.Event) -> None:
        self._sync_window_width()
        bbox = self.canvas.bbox("all")
        self.canvas.configure(scrollregion=bbox)
        self.after_idle(self._update_scroll_state)

    def _sync_width(self, event: tk.Event) -> None:
        requested = self.body.winfo_reqwidth() if self.horizontal else 0
        self.canvas.itemconfigure(self._window, width=max(event.width, requested))
        self.after_idle(self._update_scroll_state)

    def _sync_window_width(self) -> None:
        canvas_width = max(1, self.canvas.winfo_width())
        requested = self.body.winfo_reqwidth() if self.horizontal else 0
        self.canvas.itemconfigure(self._window, width=max(canvas_width, requested))

    def _content_overflows(self) -> bool:
        bbox = self.canvas.bbox("all")
        if bbox is None:
            return False
        content_height = max(0, bbox[3] - bbox[1])
        viewport_height = max(0, self.canvas.winfo_height())
        return content_height > viewport_height + 2

    def _content_overflows_horizontally(self) -> bool:
        if not self.horizontal:
            return False
        bbox = self.canvas.bbox("all")
        if bbox is None:
            return False
        content_width = max(0, bbox[2] - bbox[0])
        viewport_width = max(0, self.canvas.winfo_width())
        return content_width > viewport_width + 2

    def _update_scroll_state(self) -> None:
        if not self.winfo_exists():
            return
        overflow = self._content_overflows()
        if overflow and not self._scrollbar_visible:
            self.scrollbar.pack(side="right", fill="y")
            self._scrollbar_visible = True
        elif not overflow:
            if self._scrollbar_visible:
                self.scrollbar.pack_forget()
                self._scrollbar_visible = False
            self.canvas.yview_moveto(0.0)
        horizontal_overflow = self._content_overflows_horizontally()
        if horizontal_overflow and not self._horizontal_scrollbar_visible:
            self.horizontal_scrollbar.pack(side="bottom", fill="x", before=self.viewport)
            self._horizontal_scrollbar_visible = True
        elif not horizontal_overflow:
            if self._horizontal_scrollbar_visible:
                self.horizontal_scrollbar.pack_forget()
                self._horizontal_scrollbar_visible = False
            self.canvas.xview_moveto(0.0)

    def _contains_widget(self, widget: tk.Misc | None) -> bool:
        """Return whether the wheel event originated inside this page."""
        current = widget
        while current is not None:
            if current is self:
                return True
            try:
                current = current.master
            except (AttributeError, tk.TclError):
                return False
        return False

    def _event_is_over_scroll_area(self, event: tk.Event) -> bool:
        """Use the pointer position because Windows may send wheel events to focus."""
        try:
            pointer_x = int(event.x_root)
            pointer_y = int(event.y_root)
            # Include the vertical scrollbar as part of the active wheel area.
            left = self.viewport.winfo_rootx()
            top = self.viewport.winfo_rooty()
            if (
                left <= pointer_x < left + self.viewport.winfo_width()
                and top <= pointer_y < top + self.viewport.winfo_height()
            ):
                return True
        except (AttributeError, TypeError, ValueError, tk.TclError):
            pass
        return self._contains_widget(getattr(event, "widget", None))

    def _on_mousewheel(self, event: tk.Event) -> str | None:
        if not self.winfo_ismapped() or not self._event_is_over_scroll_area(event):
            return None
        state = int(getattr(event, "state", 0))
        event_number = getattr(event, "num", None)
        if event_number == 4:
            delta = 120
        elif event_number == 5:
            delta = -120
        else:
            delta = int(getattr(event, "delta", 0))
        if state & 0x0001 and self._content_overflows_horizontally():
            if delta:
                self.canvas.xview_scroll(-1 if delta > 0 else 1, "units")
            return "break"
        if not self._content_overflows():
            self.canvas.yview_moveto(0.0)
            return "break"
        if delta == 0:
            return None
        self.canvas.yview_scroll(-1 if delta > 0 else 1, "units")
        return "break"

    def destroy(self) -> None:
        toplevel = self.winfo_toplevel()
        unregister = getattr(toplevel, "_unregister_scrollable_frame", None)
        if callable(unregister):
            unregister(self)
        super().destroy()


# Public aliases keep the existing UI and third-party imports compatible while
# persistence is handled by the SQLite repository.
FreezerRepository = SQLiteFreezerRepository
FreezerData = SQLiteFreezerData
UnitRecord = SQLiteUnitRecord
compartment_code = sqlite_compartment_code
unit_code = sqlite_unit_code
parse_unit_code = sqlite_parse_unit_code
all_unit_codes = sqlite_all_unit_codes


class FreezerManagerApp(tk.Tk):
    def __init__(self, repository: FreezerRepository | None = None):
        super().__init__()
        self._scrollable_frames: list[ScrollableFrame] = []
        self.repository = repository or FreezerRepository()
        self.current_layer = 1
        self.current_compartment: tuple[int, int] | None = None
        self.search_var = tk.StringVar()
        self.filter_sample_type_var = tk.StringVar()
        self.filter_stored_by_var = tk.StringVar()
        self.filter_date_from_var = tk.StringVar()
        self.filter_date_to_var = tk.StringVar()
        self.filter_status_var = tk.StringVar(value="有细胞")
        self.result_selection: dict[str, tk.BooleanVar] = {}
        self.current_filter_results: list[tuple[str, UnitRecord]] = []
        self.pending_import_path: Path | None = None
        self.pending_import_preview: ImportPreview | None = None
        self.overview_batch_mode = False
        self.selected_compartments: set[str] = set()
        self.compartment_batch_mode = False
        self.selected_compartment_units: set[str] = set()
        self.current_box_code: str | None = None
        self.current_box_freezer_id: str | None = None
        self.box_batch_mode = False
        self.box_quick_move_mode = False
        self.selected_box_positions: set[str] = set()
        self.box_drag_source: str | None = None
        self.box_drag_target: str | None = None
        self.box_move_undo: tuple[str, str, str, str, BoxSample] | None = None
        self.box_move_hint_job: str | None = None
        self.box_auto_fit_job: str | None = None
        self.box_zoom_size_cache: dict[tuple[int, int, int], tuple[int, int]] = {}
        self.box_page_visible = False
        self.box_zoom_level = 0
        self.box_grid_widget: tk.Frame | None = None
        self.box_grid_canvas: tk.Canvas | None = None
        self.box_grid_canvas_window: int | None = None
        self.box_legend_widget: tk.Frame | None = None
        self.box_horizontal_scrollbar: ttk.Scrollbar | None = None
        self.box_vertical_scrollbar: ttk.Scrollbar | None = None
        self.box_column_labels: list[tk.Label] = []
        self.box_row_labels: list[tk.Label] = []
        self.inventory_query_var = tk.StringVar()
        self.inventory_status_var = tk.StringVar(value="全部")
        self.event_query_var = tk.StringVar()
        self.event_action_var = tk.StringVar(value="全部操作")
        self.event_action_filters = set(self._event_action_labels())
        self.event_freezer_var = tk.StringVar(value="全部液氮罐")
        self.event_date_from_var = tk.StringVar()
        self.event_date_to_var = tk.StringVar()
        self.empty_required_var = tk.StringVar(value="1")
        self.empty_scope_var = tk.StringVar(value="当前液氮罐")
        self.empty_arrangement_var = tk.StringVar(value="连续优先")
        self.pending_empty_recommendation: tuple[str, str, list[str]] | None = None
        self.empty_opened_boxes: set[tuple[str, str]] = set()
        self.empty_search_signature: tuple[int, str, str] | None = None
        self.box_empty_highlights: set[str] = set()
        self.inventory_location_sample_name = ""
        self.inventory_location_opened_boxes: set[tuple[str, str]] = set()
        self.pending_search_highlight: tuple[str, str, set[str]] | None = None
        self.box_search_highlights: set[str] = set()
        self.search_opened_boxes: set[tuple[str, str]] = set()
        self.search_session_query = ""
        self.box_search_return_mode = ""
        self._page_generation = 0
        self._page_commit_job: str | None = None
        self._visible_content: ttk.Frame | None = None
        self.page_title_var = tk.StringVar(value="冻存盒总览")
        self.title(APP_TITLE)
        window_width = min(1240, self.winfo_screenwidth() - 80)
        window_height = min(780, self.winfo_screenheight() - 100)
        left = max(0, (self.winfo_screenwidth() - window_width) // 2)
        top = max(0, (self.winfo_screenheight() - window_height) // 2 - 12)
        self.geometry(f"{window_width}x{window_height}+{left}+{top}")
        self.minsize(1000, 620)
        self.configure(background=COLORS["bg"])
        self._configure_styles()
        self._build_shell()
        self.bind("<MouseWheel>", self._dispatch_page_mousewheel, add="+")
        self.bind("<Button-4>", self._dispatch_page_mousewheel, add="+")
        self.bind("<Button-5>", self._dispatch_page_mousewheel, add="+")
        self.bind_all("<Shift-MouseWheel>", self._on_box_grid_horizontal_scroll, add="+")
        self.bind("<ButtonRelease-1>", self._finish_box_drag_outside_cell, add="+")
        self.show_overview()

    def _register_scrollable_frame(self, frame: ScrollableFrame) -> None:
        """Track page scrollers without adding a binding for every page."""
        active: list[ScrollableFrame] = []
        for item in self._scrollable_frames:
            try:
                if item.winfo_exists() and item is not frame:
                    active.append(item)
            except tk.TclError:
                continue
        active.append(frame)
        self._scrollable_frames = active

    def _unregister_scrollable_frame(self, frame: ScrollableFrame) -> None:
        self._scrollable_frames = [item for item in self._scrollable_frames if item is not frame]

    def _dispatch_page_mousewheel(self, event: tk.Event) -> str | None:
        """Route the wheel to the visible scroll page under the pointer."""
        if self._on_box_grid_vertical_scroll(event) == "break":
            return "break"
        active: list[ScrollableFrame] = []
        handled = False
        for frame in self._scrollable_frames:
            try:
                if not frame.winfo_exists():
                    continue
                active.append(frame)
                if not handled and frame._on_mousewheel(event) == "break":
                    handled = True
            except tk.TclError:
                continue
        self._scrollable_frames = active
        return "break" if handled else None

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Page.TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Heading.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Text.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Microsoft YaHei UI", 9))
        style.configure("PageMuted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=("Microsoft YaHei UI", 10))
        style.configure("CardValue.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Microsoft YaHei UI", 20, "bold"))
        style.configure(
            "Event.Treeview",
            background=COLORS["panel"],
            fieldbackground=COLORS["panel"],
            foreground=COLORS["text"],
            bordercolor=COLORS["line"],
            lightcolor=COLORS["line"],
            darkcolor=COLORS["line"],
            rowheight=38,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Event.Treeview.Heading",
            background="#F6F8FB",
            foreground=COLORS["muted"],
            bordercolor=COLORS["line"],
            lightcolor=COLORS["line"],
            darkcolor=COLORS["line"],
            relief="flat",
            padding=(8, 9),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "Event.Treeview",
            background=[("selected", COLORS["primary_soft"])],
            foreground=[("selected", COLORS["text"])],
        )
        style.map("Event.Treeview.Heading", background=[("active", "#EDF2F8")])
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), foreground="white", background=COLORS["primary"], padding=(20, 11), borderwidth=0)
        style.map("Primary.TButton", background=[("active", COLORS["primary_dark"])])
        style.configure("Secondary.TButton", font=("Microsoft YaHei UI", 10), foreground=COLORS["text"], background="#EDF2F8", padding=(14, 9), borderwidth=0)
        style.map("Secondary.TButton", background=[("active", "#DEE7F1")])
        style.configure("Danger.TButton", font=("Microsoft YaHei UI", 10), foreground=COLORS["danger"], background="#FCEEEE", padding=(14, 9), borderwidth=0)
        style.configure("TEntry", padding=9, fieldbackground="white", bordercolor=COLORS["line"], lightcolor=COLORS["line"], darkcolor=COLORS["line"])
        style.configure(
            "TCombobox",
            padding=(10, 8),
            fieldbackground=COLORS["panel"],
            background=COLORS["panel"],
            foreground=COLORS["text"],
            bordercolor=COLORS["line"],
            lightcolor=COLORS["line"],
            darkcolor=COLORS["line"],
            arrowcolor=COLORS["primary"],
            relief="flat",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", COLORS["panel"]), ("focus", COLORS["primary_soft"])],
            background=[("readonly", COLORS["panel"]), ("active", COLORS["primary_soft"])],
            bordercolor=[("focus", COLORS["primary"]), ("readonly", COLORS["line"])],
            arrowcolor=[("active", COLORS["primary_dark"]), ("readonly", COLORS["primary"])],
            foreground=[("readonly", COLORS["text"])],
        )
        style.configure(
            "EventFilter.TButton",
            padding=(10, 8),
            background=COLORS["panel"],
            foreground=COLORS["text"],
            bordercolor=COLORS["line"],
            lightcolor=COLORS["line"],
            darkcolor=COLORS["line"],
            relief="flat",
            anchor="w",
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "EventFilter.TButton",
            background=[("active", COLORS["primary_soft"]), ("pressed", COLORS["primary_soft"])],
            bordercolor=[("focus", COLORS["primary"])],
        )
        style.configure(
            "Picker.TCombobox",
            padding=(12, 9),
            fieldbackground=COLORS["panel"],
            background=COLORS["primary_soft"],
            foreground=COLORS["text"],
            bordercolor=COLORS["line"],
            lightcolor=COLORS["line"],
            darkcolor=COLORS["line"],
            arrowcolor=COLORS["primary"],
            relief="flat",
        )
        style.map(
            "Picker.TCombobox",
            fieldbackground=[("readonly", COLORS["panel"]), ("focus", COLORS["primary_soft"])],
            background=[("readonly", COLORS["primary_soft"]), ("active", "#DCE8FF")],
            bordercolor=[("focus", COLORS["primary"]), ("readonly", COLORS["line"])],
            arrowcolor=[("active", COLORS["primary_dark"]), ("readonly", COLORS["primary"])],
            foreground=[("readonly", COLORS["text"])],
        )
        style.configure(
            "Freezer.TCombobox",
            padding=(10, 8),
            fieldbackground="#162D47",
            background="#1C3552",
            foreground="white",
            bordercolor="#31506F",
            lightcolor="#31506F",
            darkcolor="#31506F",
            arrowcolor="#83ACFF",
            relief="flat",
        )
        style.map(
            "Freezer.TCombobox",
            fieldbackground=[("readonly", "#162D47"), ("focus", "#1C3552")],
            background=[("readonly", "#1C3552"), ("active", "#284664")],
            foreground=[("readonly", "white")],
            bordercolor=[("focus", COLORS["primary"]), ("readonly", "#31506F")],
            arrowcolor=[("active", "white"), ("readonly", "#83ACFF")],
        )
        style.layout(
            "Flat.Vertical.TScrollbar",
            [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})],
        )
        style.configure(
            "Flat.Vertical.TScrollbar",
            gripcount=0,
            background="#B8C4D2",
            troughcolor=COLORS["bg"],
            bordercolor=COLORS["bg"],
            lightcolor="#B8C4D2",
            darkcolor="#B8C4D2",
            arrowcolor=COLORS["bg"],
            width=9,
            relief="flat",
        )
        style.map(
            "Flat.Vertical.TScrollbar",
            background=[("active", "#8FA1B5"), ("pressed", COLORS["primary"]), ("!active", "#B8C4D2")],
        )
        style.layout(
            "Flat.Horizontal.TScrollbar",
            [("Horizontal.Scrollbar.trough", {"sticky": "ew", "children": [("Horizontal.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})],
        )
        style.configure(
            "Flat.Horizontal.TScrollbar",
            gripcount=0,
            background="#B8C4D2",
            troughcolor=COLORS["bg"],
            bordercolor=COLORS["bg"],
            lightcolor="#B8C4D2",
            darkcolor="#B8C4D2",
            arrowcolor=COLORS["bg"],
            width=9,
            relief="flat",
        )
        style.map(
            "Flat.Horizontal.TScrollbar",
            background=[("active", "#8FA1B5"), ("pressed", COLORS["primary"]), ("!active", "#B8C4D2")],
        )
        self.option_add("*TCombobox*Listbox.background", COLORS["panel"])
        self.option_add("*TCombobox*Listbox.foreground", COLORS["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", COLORS["primary"])
        self.option_add("*TCombobox*Listbox.selectForeground", "white")
        self.option_add("*TCombobox*Listbox.font", "{Microsoft YaHei UI} 10")

    @staticmethod
    def _focus_if_exists(widget: tk.Misc) -> None:
        try:
            if widget.winfo_exists() and widget.winfo_viewable():
                widget.focus_set()
        except tk.TclError:
            pass

    def _build_shell(self) -> None:
        shell = tk.Frame(self, bg=COLORS["bg"])
        shell.pack(fill="both", expand=True)

        sidebar = tk.Frame(shell, bg=COLORS["sidebar"], width=224)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Frame(sidebar, bg=COLORS["sidebar"])
        brand.pack(fill="x", padx=22, pady=(10, 7))
        icon = tk.Label(brand, text="LN₂", width=4, height=2, bg=COLORS["primary"], fg="white", font=("Microsoft YaHei UI", 11, "bold"))
        icon.pack(side="left", padx=(0, 11))
        brand_text = tk.Frame(brand, bg=COLORS["sidebar"])
        brand_text.pack(side="left")
        brand_title = tk.Label(brand_text, text="液氮罐管理", fg="white", bg=COLORS["sidebar"], font=("Microsoft YaHei UI", 14, "bold"), cursor="hand2")
        brand_title.pack(anchor="w")
        self.brand_version_label = tk.Label(
            brand_text,
            text=f"LOCAL STORAGE · v{APP_VERSION}",
            fg=COLORS["sidebar_muted"],
            bg=COLORS["sidebar"],
            font=("Segoe UI", 7, "bold"),
            cursor="hand2",
        )
        self.brand_version_label.pack(anchor="w")
        for brand_widget in (brand, icon, brand_text, brand_title, self.brand_version_label):
            brand_widget.bind("<Button-1>", self.show_about_dialog)
            brand_widget.configure(cursor="hand2")
        self.brand_version_label.bind(
            "<Enter>",
            lambda _event: self.brand_version_label.configure(fg="white"),
        )
        self.brand_version_label.bind(
            "<Leave>",
            lambda _event: self.brand_version_label.configure(fg=COLORS["sidebar_muted"]),
        )

        freezer_panel = tk.Frame(sidebar, bg="#0E1E31", highlightthickness=1, highlightbackground="#294766")
        freezer_panel.pack(fill="x", padx=15, pady=(0, 7))
        tk.Label(freezer_panel, text="当前液氮罐", fg=COLORS["sidebar_muted"], bg="#0E1E31", font=("Microsoft YaHei UI", 8), anchor="w").pack(fill="x", padx=12, pady=(8, 4))
        self.freezer_selector_var = tk.StringVar()
        self.freezer_selector = ttk.Combobox(
            freezer_panel,
            textvariable=self.freezer_selector_var,
            state="readonly",
            style="Freezer.TCombobox",
            font=("Microsoft YaHei UI", 9),
        )
        self.freezer_selector.pack(fill="x", padx=10)
        self.freezer_selector.bind("<<ComboboxSelected>>", self._on_freezer_selected)
        freezer_actions = tk.Frame(freezer_panel, bg="#0E1E31")
        freezer_actions.pack(fill="x", padx=10, pady=(6, 7))
        RoundedButton(
            freezer_actions,
            text="＋ 新增",
            command=self.add_freezer_dialog,
            fill="#1C3552",
            hover_fill="#284664",
            border="#294766",
            canvas_bg="#0E1E31",
            width=82,
            height=29,
            radius=8,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(side="left")
        RoundedButton(
            freezer_actions,
            text="重命名",
            command=self.rename_freezer_dialog,
            fill="#1C3552",
            hover_fill="#284664",
            border="#294766",
            canvas_bg="#0E1E31",
            width=82,
            height=29,
            radius=8,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right")
        RoundedButton(
            freezer_panel,
            text="归档管理",
            command=self.show_archive_page,
            fill="#1C3552",
            hover_fill="#284664",
            border="#294766",
            canvas_bg="#0E1E31",
            width=174,
            height=29,
            radius=8,
            font=("Microsoft YaHei UI", 8),
        ).pack(fill="x", padx=10, pady=(0, 7))

        workbench_label = tk.Label(sidebar, text="工作台", fg=COLORS["sidebar_muted"], bg=COLORS["sidebar"], font=("Microsoft YaHei UI", 8), anchor="w")
        workbench_label.pack(fill="x", padx=25, pady=(0, 3))
        self.nav_buttons = {
            "overview": self._sidebar_button(sidebar, "▦   冻存盒总览", self.show_overview, active=True),
            "search": self._sidebar_button(sidebar, "⌕   查找细胞", self.show_search_page),
            "empty": self._sidebar_button(sidebar, "□   查找空位", self.show_empty_search_page),
            "inventory": self._sidebar_button(sidebar, "▤   细胞库存", self.show_inventory_page),
            "events": self._sidebar_button(sidebar, "↕   出入库登记", self.show_inventory_events_page),
            "import": self._sidebar_button(sidebar, "⇧   从 Excel 导入", self.import_from_excel),
            "export": self._sidebar_button(sidebar, "⇩   导出 Excel", self.export_to_excel),
            "backup": self._sidebar_button(sidebar, "◷   备份与恢复", self.show_backup_page),
        }

        summary = tk.Frame(sidebar, bg="#0E1E31", height=96, highlightthickness=1, highlightbackground="#203A58")
        # Insert the bottom card before the workbench/nav items in the pack
        # order so it reserves its full height instead of receiving only the
        # clipped remainder on shorter screens.
        summary.pack(side="bottom", fill="x", padx=16, pady=(10, 12), before=workbench_label)
        summary.pack_propagate(False)
        tk.Label(summary, text="冻存管容量", bg="#0E1E31", fg=COLORS["sidebar_muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=14, pady=(10, 2))
        self.sidebar_usage_label = tk.Label(summary, text="", bg="#0E1E31", fg="white", font=("Microsoft YaHei UI", 15, "bold"))
        self.sidebar_usage_label.pack(anchor="w", padx=14)
        self.sidebar_rate_label = tk.Label(summary, text="", bg="#0E1E31", fg=COLORS["sidebar_muted"], font=("Microsoft YaHei UI", 8))
        self.sidebar_rate_label.pack(anchor="w", padx=14, pady=(0, 9))

        main = tk.Frame(shell, bg=COLORS["bg"])
        main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(main, bg=COLORS["panel"], height=72, highlightthickness=1, highlightbackground=COLORS["line"])
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, textvariable=self.page_title_var, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 14, "bold")).pack(side="left", padx=28)

        self.content_host = tk.Frame(main, bg=COLORS["bg"])
        self.content_host.pack(fill="both", expand=True)
        self.content = ttk.Frame(self.content_host, style="Page.TFrame")
        self.content.place(x=0, y=0, relwidth=1, relheight=1)
        self._visible_content = self.content
        self._refresh_freezer_selector()
        self._update_sidebar_summary()

    def _sidebar_button(self, parent: tk.Misc, text: str, command: Callable[[], None], active: bool = False) -> RoundedButton:
        return_button = RoundedButton(
            parent,
            text=text,
            command=lambda target=command: self._run_sidebar_command(target),
            anchor="w",
            fill=COLORS["sidebar_hover"] if active else COLORS["sidebar"],
            foreground="white" if active else "#C6D0DE",
            hover_fill=COLORS["sidebar_hover"],
            border=COLORS["sidebar_hover"] if active else COLORS["sidebar"],
            canvas_bg=COLORS["sidebar"],
            font=("Microsoft YaHei UI", 10, "bold" if active else "normal"),
            height=36,
            radius=11,
            text_padx=17,
        )
        return_button.pack(fill="x", padx=12, pady=0)
        return return_button

    def _run_sidebar_command(self, command: Callable[[], None]) -> None:
        """Leave transient box modes before following a workbench link."""
        self._exit_box_interaction_modes()
        command()

    def _run_box_back_command(self, command: Callable[[], None]) -> None:
        """A box Back action always leaves its transient interaction modes."""
        self._exit_box_interaction_modes()
        command()

    def _exit_box_interaction_modes(self) -> None:
        self.box_batch_mode = False
        self.box_quick_move_mode = False
        self.selected_box_positions.clear()
        self.box_empty_highlights.clear()
        self.pending_empty_recommendation = None
        self._clear_box_drag_state()
        if self.box_auto_fit_job is not None:
            try:
                self.after_cancel(self.box_auto_fit_job)
            except tk.TclError:
                pass
            self.box_auto_fit_job = None

    def _set_active_nav(self, active_key: str | None) -> None:
        if not hasattr(self, "nav_buttons"):
            return
        for key, button in self.nav_buttons.items():
            active = key == active_key
            button.set_palette(
                fill=COLORS["sidebar_hover"] if active else COLORS["sidebar"],
                foreground="white" if active else "#C6D0DE",
                hover_fill=COLORS["sidebar_hover"],
                border=COLORS["sidebar_hover"] if active else COLORS["sidebar"],
            )

    def _notify(self, title: str, message: str, *, danger: bool = False) -> None:
        AppDialog(
            self,
            title=title,
            message=message,
            buttons=[("知道了", True, "danger" if danger else "primary")],
        ).show()

    def show_about_dialog(self, _event: tk.Event | None = None) -> None:
        AppDialog(
            self,
            title="关于液氮罐管理系统",
            message=(
                "液氮罐管理系统\n\n"
                f"版本：v{APP_VERSION}\n"
                f"开发者：{APP_DEVELOPER}\n"
                f"技术支持：{APP_DEVELOPER}\n"
                f"联系邮箱：{APP_SUPPORT_EMAIL}\n\n"
                f"© 2026 {APP_DEVELOPER}"
            ),
            buttons=[("关闭", True, "primary")],
            width=480,
        ).show()

    def _ask_text(self, title: str, message: str, initial: str) -> str | None:
        result = AppDialog(
            self,
            title=title,
            message=message,
            initial_value=initial,
            buttons=[("取消", None, "secondary"), ("确定", "__input__", "primary")],
        ).show()
        return str(result) if result is not None else None

    def _ask_confirm(self, title: str, message: str, *, danger: bool = False) -> bool:
        result = AppDialog(
            self,
            title=title,
            message=message,
            buttons=[
                ("取消", False, "secondary"),
                ("确认", True, "danger" if danger else "primary"),
            ],
        ).show()
        return result is True

    def _choose_import_mode(self, freezer_count: int, record_count: int) -> str | None:
        result = AppDialog(
            self,
            title="选择导入方式",
            message=(
                f"Excel 中识别到 {freezer_count} 个液氮罐、{record_count} 条占用记录。\n\n"
                "合并导入：更新同编号记录，保留 Excel 中未出现的现有记录。\n"
                "覆盖同名：先清空同名液氮罐，再按 Excel 内容恢复。"
            ),
            buttons=[
                ("取消", None, "secondary"),
                ("覆盖同名", "overwrite", "danger"),
                ("合并导入", "merge", "primary"),
            ],
            width=560,
        ).show()
        return str(result) if result is not None else None

    def _update_sidebar_summary(self) -> None:
        used = self.repository.tube_used_count
        capacity = self.repository.tube_capacity
        self.sidebar_usage_label.configure(text=f"{used} / {capacity}")
        self.sidebar_rate_label.configure(text=f"当前使用率 {used / capacity * 100:.1f}%")

    def _refresh_freezer_selector(self) -> None:
        self._freezer_ids = [freezer_id for freezer_id, _name in self.repository.list_freezers()]
        names = [name for _freezer_id, name in self.repository.list_freezers()]
        self.freezer_selector.configure(values=names)
        if self.repository.current_freezer_id in self._freezer_ids:
            self.freezer_selector.current(self._freezer_ids.index(self.repository.current_freezer_id))

    def _on_freezer_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.freezer_selector.current()
        if selection < 0 or selection >= len(self._freezer_ids):
            return
        try:
            self.repository.switch_freezer(self._freezer_ids[selection])
        except (OSError, ValueError) as exc:
            self._notify("切换失败", str(exc), danger=True)
            self._refresh_freezer_selector()
            return
        self.current_layer = 1
        self.search_var.set("")
        self.overview_batch_mode = False
        self.selected_compartments.clear()
        self.compartment_batch_mode = False
        self.selected_compartment_units.clear()
        self.show_overview()

    def add_freezer_dialog(self) -> None:
        suggested = f"液氮罐 {len(self.repository.freezers) + 1}"
        name = self._ask_text("新增液氮罐", "请输入新液氮罐的名称：", suggested)
        if name is None:
            return
        try:
            self.repository.add_freezer(name)
        except (OSError, ValueError) as exc:
            self._notify("无法新增液氮罐", str(exc), danger=True)
            return
        self.current_layer = 1
        self._refresh_freezer_selector()
        self.show_overview()

    def rename_freezer_dialog(self) -> None:
        name = self._ask_text(
            "重命名液氮罐", "请输入新的液氮罐名称：", self.repository.freezer_name
        )
        if name is None:
            return
        try:
            self.repository.rename_freezer(self.repository.current_freezer_id, name)
        except (OSError, ValueError) as exc:
            self._notify("无法重命名", str(exc), danger=True)
            return
        self._refresh_freezer_selector()
        self.show_overview()

    def show_archive_page(self) -> None:
        self._set_active_nav(None)
        self.page_title_var.set("液氮罐归档管理")
        self.clear_content()
        page = ttk.Frame(self.content, style="Page.TFrame", padding=(28, 24))
        page.pack(fill="both", expand=True)
        heading = ttk.Frame(page, style="Page.TFrame")
        heading.pack(fill="x", pady=(0, 16))
        title_box = ttk.Frame(heading, style="Page.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text="液氮罐归档管理", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="归档不会删除任何盒位或细胞数据，可随时恢复。", style="PageMuted.TLabel").pack(anchor="w", pady=(3, 0))

        active_panel = self._panel(page)
        active_panel.pack(fill="x", pady=(0, 14))
        tk.Label(active_panel, text="当前液氮罐", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=20, pady=(17, 8))
        current = self.repository.current_freezer
        row = tk.Frame(active_panel, bg=COLORS["panel"])
        row.pack(fill="x", padx=20, pady=(0, 18))
        tk.Label(row, text=current.name, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        tk.Label(row, text=f"  ·  {self.repository.used_count} 个有细胞冻存盒", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(side="left")
        RoundedButton(row, text="归档当前液氮罐", command=self.archive_current_freezer, fill="#FCECEC", foreground=COLORS["danger"], hover_fill="#F7DADA", border="#F3D1D1", canvas_bg=COLORS["panel"], width=148, height=36, radius=9).pack(side="right")

        archived_panel = self._panel(page)
        archived_panel.pack(fill="both", expand=True)
        tk.Label(archived_panel, text="已归档液氮罐", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=20, pady=(17, 8))
        archived = self.repository.list_archived_freezers()
        if not archived:
            tk.Label(archived_panel, text="暂无已归档液氮罐", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 10), pady=24).pack()
        for freezer_id, name, used in archived:
            item = tk.Frame(archived_panel, bg=COLORS["panel"])
            item.pack(fill="x", padx=20, pady=8)
            tk.Label(item, text=name, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
            tk.Label(item, text=f"  ·  {used} 条记录", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(side="left")
            RoundedButton(item, text="恢复", command=lambda value=freezer_id: self.restore_archived_freezer(value), fill=COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#DCE8FF", border=COLORS["primary_soft"], canvas_bg=COLORS["panel"], width=82, height=34, radius=9).pack(side="right")

    def archive_current_freezer(self) -> None:
        freezer = self.repository.current_freezer
        if not self._ask_confirm("确认归档", f"归档“{freezer.name}”吗？\n其中 {self.repository.used_count} 个已使用细胞冻存盒会完整保留。", danger=True):
            return
        try:
            self.repository.archive_freezer(self.repository.current_freezer_id)
        except (OSError, ValueError) as exc:
            self._notify("无法归档", str(exc), danger=True)
            return
        self._refresh_freezer_selector()
        self._update_sidebar_summary()
        self.show_archive_page()

    def restore_archived_freezer(self, freezer_id: str) -> None:
        try:
            self.repository.restore_freezer(freezer_id)
        except (OSError, ValueError) as exc:
            self._notify("无法恢复", str(exc), danger=True)
            return
        self._refresh_freezer_selector()
        self.show_archive_page()

    def show_backup_page(self) -> None:
        self._set_active_nav("backup")
        self.page_title_var.set("备份与恢复")
        self.clear_content()
        page = ttk.Frame(self.content, style="Page.TFrame", padding=(28, 24))
        page.pack(fill="both", expand=True)
        heading = ttk.Frame(page, style="Page.TFrame")
        heading.pack(fill="x", pady=(0, 16))
        title_box = ttk.Frame(heading, style="Page.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text="备份与恢复", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="SQLite 数据库备份包含所有液氮罐、归档状态和细胞记录。", style="PageMuted.TLabel").pack(anchor="w", pady=(3, 0))
        RoundedButton(heading, text="＋ 创建备份", command=self.create_manual_backup, fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["bg"], width=120, height=40, radius=10).pack(side="right")
        RoundedButton(heading, text="清理旧备份", command=self.prune_old_backups, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["bg"], width=112, height=40, radius=10).pack(side="right", padx=(0, 8))

        panel = self._panel(page)
        panel.pack(fill="both", expand=True)
        tk.Label(panel, text="可用备份", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=20, pady=(17, 8))
        backups = self.repository.list_backups()
        if not backups:
            tk.Label(panel, text="暂无备份。Excel 导入和数据库恢复前也会自动创建备份。", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 10), pady=30).pack()
        for path, modified, size in backups[:30]:
            item = tk.Frame(panel, bg=COLORS["panel"])
            item.pack(fill="x", padx=20, pady=7)
            tk.Label(item, text=path.stem, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
            tk.Label(item, text=f"  {modified:%Y-%m-%d %H:%M:%S}  ·  {size / 1024:.1f} KB", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left")
            RoundedButton(item, text="恢复此备份", command=lambda value=path: self.restore_selected_backup(value), fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=112, height=34, radius=9).pack(side="right")
            RoundedButton(item, text="删除", command=lambda value=path: self.delete_selected_backup(value), fill="#FCECEC", foreground=COLORS["danger"], hover_fill="#F7DADA", border="#F3D1D1", canvas_bg=COLORS["panel"], width=72, height=34, radius=9).pack(side="right", padx=(0, 8))

    def create_manual_backup(self) -> None:
        try:
            path = self.repository.create_backup("手动备份")
        except OSError as exc:
            self._notify("备份失败", str(exc), danger=True)
            return
        self._notify("备份完成", f"已创建：{path.name}")
        self.show_backup_page()

    def prune_old_backups(self) -> None:
        value = self._ask_text("清理旧备份", "请输入希望保留的最近备份数量（1–200）：", "20")
        if value is None:
            return
        try:
            keep = int(value.strip())
        except ValueError:
            self._notify("数量无效", "请输入 1 到 200 之间的整数。", danger=True)
            return
        backups = self.repository.list_backups()
        remove_count = max(0, len(backups) - keep)
        if remove_count == 0:
            self._notify("无需清理", f"当前共有 {len(backups)} 份备份，不超过保留数量。")
            return
        if not self._ask_confirm("确认清理", f"将保留最近 {keep} 份备份，并永久删除更早的 {remove_count} 份备份。", danger=True):
            return
        try:
            removed = self.repository.prune_backups(keep)
        except (OSError, ValueError) as exc:
            self._notify("清理失败", str(exc), danger=True)
            return
        self._notify("清理完成", f"已删除 {removed} 份旧备份。")
        self.show_backup_page()

    def delete_selected_backup(self, path: Path) -> None:
        if not self._ask_confirm(
            "确认删除备份",
            f"确定永久删除备份“{path.stem}”吗？\n删除后无法通过本程序恢复。",
            danger=True,
        ):
            return
        try:
            self.repository.delete_backup(path)
        except (OSError, ValueError) as exc:
            self._notify("删除失败", str(exc), danger=True)
            self.show_backup_page()
            return
        self._notify("备份已删除", f"已删除：{path.name}")
        self.show_backup_page()

    def restore_selected_backup(self, path: Path) -> None:
        if not self._ask_confirm("确认恢复", f"确定恢复备份“{path.stem}”吗？\n当前数据库会先自动创建安全备份。", danger=True):
            return
        try:
            safety = self.repository.restore_backup(path)
        except (OSError, ValueError) as exc:
            self._notify("恢复失败", str(exc), danger=True)
            return
        self._refresh_freezer_selector()
        self._update_sidebar_summary()
        self._notify("恢复完成", f"数据已恢复。\n恢复前安全备份：{safety.name}")
        self.show_overview()

    def focus_search(self) -> None:
        self.show_search_page()

    def show_empty_search_page(self, *, run_search: bool = False) -> None:
        self._set_active_nav("empty")
        self.box_search_return_mode = ""
        self.page_title_var.set("查找空位")
        self.clear_content()
        scroll = ScrollableFrame(self.content)
        scroll.pack(fill="both", expand=True)
        page = scroll.body
        page.columnconfigure(0, weight=1)

        heading = ttk.Frame(page, style="Page.TFrame")
        heading.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 16))
        ttk.Label(heading, text="查找冻存管空位", style="Title.TLabel").pack(anchor="w")
        ttk.Label(heading, text="输入所需管数，系统优先推荐能在同一冻存盒内连续放置的位置", style="PageMuted.TLabel").pack(anchor="w", pady=(3, 0))

        panel = self._panel(page)
        panel.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 16))
        form = tk.Frame(panel, bg=COLORS["panel"])
        form.pack(fill="x", padx=20, pady=16)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(2, weight=1)

        fields = (
            ("所需冻存管数", self.empty_required_var, None),
            ("查询范围", self.empty_scope_var, ("当前液氮罐", "全部液氮罐")),
            ("排列方式", self.empty_arrangement_var, ("连续优先", "任意空位")),
        )
        for column, (label, variable, choices) in enumerate(fields):
            cell = tk.Frame(form, bg=COLORS["panel"])
            cell.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 12, 0))
            tk.Label(cell, text=label, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
            if choices is None:
                widget = ttk.Entry(cell, textvariable=variable, font=("Microsoft YaHei UI", 10))
                _bind_ime_safe_return(widget, self.run_empty_search)
            else:
                widget = ttk.Combobox(cell, textvariable=variable, values=choices, state="readonly", font=("Microsoft YaHei UI", 10))
            widget.pack(fill="x", ipady=3)

        actions = tk.Frame(form, bg=COLORS["panel"])
        actions.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        RoundedButton(actions, text="开始查找", command=self.run_empty_search, fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["panel"], width=108, height=38, radius=10).pack(side="right")
        tk.Label(actions, text="结果按：同盒容纳全部 → 连续空位 → 优先填充已使用冻存盒", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left")

        if not run_search:
            hint = self._panel(page)
            hint.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 24))
            tk.Label(hint, text="例如输入 5，系统会优先寻找同一冻存盒中连续的5个空孔位。", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 10), pady=28).pack()
            return

        try:
            required = int(self.empty_required_var.get().strip())
            if required < 1:
                raise ValueError
        except ValueError:
            self._notify("管数不正确", "所需冻存管数必须是大于0的整数。", danger=True)
            return
        signature = (required, self.empty_scope_var.get(), self.empty_arrangement_var.get())
        if signature != self.empty_search_signature:
            self.empty_opened_boxes.clear()
            self.empty_search_signature = signature
        results = self.repository.find_empty_positions(
            required,
            all_freezers=self.empty_scope_var.get() == "全部液氮罐",
            continuous=self.empty_arrangement_var.get() == "连续优先",
        )
        self._render_empty_search_results(page, results, required)

    def run_empty_search(self) -> None:
        self.show_empty_search_page(run_search=True)

    def _render_empty_search_results(self, page: ttk.Frame, results: list[dict[str, object]], required: int) -> None:
        fitting = [item for item in results if item["fits"]]
        summary = self._panel(page)
        summary.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 14))
        tk.Label(summary, text=f"找到 {len(fitting)} 个可一次容纳 {required} 管的冻存盒", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", padx=18, pady=14)
        if fitting:
            tk.Label(summary, text="最推荐结果已排在最前", bg=COLORS["panel"], fg=COLORS["occupied_text"], font=("Microsoft YaHei UI", 8, "bold")).pack(side="right", padx=18)

        list_panel = self._panel(page)
        list_panel.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 24))
        if not fitting:
            tk.Label(list_panel, text="没有单个冻存盒可以容纳所需管数。可以减少管数，或改为查询全部液氮罐。", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 10), pady=32).pack()
            return
        for index, item in enumerate(fitting[:30]):
            freezer_id = str(item["freezer_id"])
            code = str(item["code"])
            opened = (freezer_id, code) in self.empty_opened_boxes
            card_bg = COLORS["primary_soft"] if opened else COLORS["panel"]
            card = tk.Frame(
                list_panel,
                bg=card_bg,
                highlightthickness=2 if opened else 0,
                highlightbackground=COLORS["primary"],
            )
            card.pack(fill="x", padx=18, pady=(13 if index == 0 else 0, 13))
            rank = tk.Label(card, text="推荐" if index == 0 else str(index + 1), width=6, bg=COLORS["occupied"] if index == 0 else "#F4F7FB", fg=COLORS["occupied_text"] if index == 0 else COLORS["muted"], font=("Microsoft YaHei UI", 8, "bold"), padx=6, pady=8)
            rank.pack(side="left", padx=(0, 14))
            detail = tk.Frame(card, bg=card_bg)
            detail.pack(side="left", fill="x", expand=True)
            title_row = tk.Frame(detail, bg=card_bg)
            title_row.pack(fill="x")
            tk.Label(title_row, text=f"{item['freezer_name']} · 细胞冻存盒 {code}", bg=card_bg, fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
            if opened:
                tk.Label(
                    title_row,
                    text="已查看",
                    bg="#E7F0FF",
                    fg=COLORS["primary"],
                    font=("Microsoft YaHei UI", 8, "bold"),
                    padx=9,
                    pady=1,
                    highlightthickness=1,
                    highlightbackground="#BFD4FF",
                ).pack(side="left", padx=10)
            positions = list(item["recommended_positions"])
            tk.Label(detail, text=f"推荐孔位：{compact_box_positions(positions)}", bg=card_bg, fg=COLORS["primary"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(4, 0))
            tk.Label(detail, text=f"布局 {item['rows']}×{item['columns']} · 空余 {item['empty_count']} 管位 · 已使用 {item['occupied_count']} 管", bg=card_bg, fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(3, 0))
            RoundedButton(card, text="再次打开" if opened else "使用推荐孔位", command=lambda fid=freezer_id, value=code, pos=positions: self.open_recommended_empty_positions(fid, value, pos), fill="#DCE8FF" if opened else COLORS["primary"], foreground=COLORS["primary"] if opened else "white", hover_fill="#CFE0FF" if opened else COLORS["primary_dark"], canvas_bg=card_bg, width=120, height=36, radius=9, font=("Microsoft YaHei UI", 8, "bold")).pack(side="right", padx=(16, 0))
            if index < len(fitting[:30]) - 1:
                tk.Frame(list_panel, height=1, bg=COLORS["line"]).pack(fill="x", padx=18)

    def open_recommended_empty_positions(self, freezer_id: str, code: str, positions: list[str]) -> None:
        try:
            self.repository.switch_freezer(freezer_id)
        except (OSError, ValueError) as exc:
            self._notify("无法打开推荐位置", str(exc), danger=True)
            return
        self.pending_empty_recommendation = (freezer_id, code, list(positions))
        self.empty_opened_boxes.add((freezer_id, code))
        self.box_search_return_mode = "empty"
        self._refresh_freezer_selector()
        self._update_sidebar_summary()
        self.show_box(code)

    def show_inventory_page(self) -> None:
        self._set_active_nav("inventory")
        self.page_title_var.set("细胞库存 · 全部液氮罐")
        self.clear_content()
        scroll = ScrollableFrame(self.content)
        scroll.pack(fill="both", expand=True)
        page = scroll.body
        page.columnconfigure(0, weight=1)

        heading = ttk.Frame(page, style="Page.TFrame")
        heading.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 16))
        title_box = ttk.Frame(heading, style="Page.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text="细胞库存目录", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="快速发现低库存细胞；预警库存按已占用孔位（冻存管数）计算", style="PageMuted.TLabel").pack(anchor="w")

        search_panel = self._panel(page)
        search_panel.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 16))
        search_row = tk.Frame(search_panel, bg=COLORS["panel"])
        search_row.pack(fill="x", padx=18, pady=14)
        entry = tk.Entry(search_row, textvariable=self.inventory_query_var, relief="flat", bg="#F4F7FB", fg=COLORS["text"], insertbackground=COLORS["text"], highlightthickness=1, highlightbackground=COLORS["line"], highlightcolor=COLORS["primary"], font=("Microsoft YaHei UI", 10))
        entry.pack(side="left", fill="x", expand=True, ipady=9)
        _bind_ime_safe_return(entry, self.show_inventory_page)
        RoundedButton(search_row, text="筛选库存", command=self.show_inventory_page, fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["panel"], width=105, height=40, radius=10).pack(side="left", padx=(10, 0))
        RoundedButton(search_row, text="清除", command=self.clear_inventory_query, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=78, height=40, radius=10).pack(side="left", padx=(8, 0))

        all_items = self.repository.inventory_summary(self.inventory_query_var.get())
        for item in all_items:
            low, warning, configured = self.repository.get_inventory_alert(str(item["sample_name"]))
            tubes = len(item["positions"])
            status_key, status_text, status_color, status_bg = self._inventory_alert_status(tubes, low, warning)
            item.update({
                "alert_low": low,
                "alert_warning": warning,
                "alert_configured": configured,
                "alert_status": status_key,
                "alert_text": status_text,
                "alert_color": status_color,
                "alert_bg": status_bg,
            })

        alert_counts = {
            key: sum(1 for item in all_items if item["alert_status"] == key)
            for key in ("low", "warning", "normal")
        }
        stats = self._panel(page)
        stats.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 16))
        alert_cards = (
            ("全部", "全部细胞", len(all_items), COLORS["primary"], COLORS["primary_soft"], "#DCE8FF"),
            ("库存不足", "库存不足", alert_counts["low"], "#C93C3C", "#FDECEC", "#F8DADA"),
            ("即将不足", "即将不足", alert_counts["warning"], "#B56A00", "#FFF3DA", "#FFE5AD"),
            ("库存正常", "库存正常", alert_counts["normal"], COLORS["occupied_text"], COLORS["occupied"], "#D4EEE3"),
        )
        for status_filter, label, value, foreground, soft_color, soft_hover in alert_cards:
            active = self.inventory_status_var.get() == status_filter
            RoundedButton(
                stats,
                text=f"{value} 种\n{label}",
                command=lambda value=status_filter: self.set_inventory_status_filter(value),
                fill=foreground if active else soft_color,
                foreground="white" if active else foreground,
                hover_fill=foreground if active else soft_hover,
                border=foreground if active else soft_color,
                canvas_bg=COLORS["panel"],
                font=("Microsoft YaHei UI", 10, "bold"),
                width=148,
                height=62,
                radius=11,
            ).pack(side="left", padx=(14, 0), pady=14)
        default_low, default_warning = self.repository.default_inventory_alert
        default_settings = tk.Frame(stats, bg=COLORS["panel"])
        default_settings.pack(side="right", padx=16, pady=10)
        tk.Label(
            default_settings,
            text=f"默认：不足≤{default_low}管 / 提醒≤{default_warning}管",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="e", pady=(0, 5))
        RoundedButton(
            default_settings,
            text="设置默认阈值",
            command=self.configure_default_inventory_alert,
            fill="#E8EDF4",
            foreground=COLORS["text"],
            hover_fill="#DCE4EE",
            border="#E8EDF4",
            canvas_bg=COLORS["panel"],
            width=112,
            height=32,
            radius=8,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(anchor="e")

        selected_status = self.inventory_status_var.get()
        status_key_by_filter = {"库存不足": "low", "即将不足": "warning", "库存正常": "normal"}
        selected_key = status_key_by_filter.get(selected_status)
        items = [item for item in all_items if selected_key is None or item["alert_status"] == selected_key]
        status_rank = {"low": 0, "warning": 1, "normal": 2}
        items.sort(key=lambda item: (status_rank[str(item["alert_status"])], len(item["positions"]), str(item["sample_name"]).casefold()))

        list_panel = self._panel(page)
        list_panel.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 24))
        list_heading = tk.Frame(list_panel, bg=COLORS["panel"])
        list_heading.pack(fill="x", padx=16, pady=(12, 10))
        tk.Label(list_heading, text=f"{selected_status} · {len(items)} 种细胞", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        tk.Label(list_heading, text="列表已按预警级别和剩余管数排序", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(side="right")
        inventory_grid = tk.Frame(list_panel, bg=COLORS["line"])
        inventory_grid.pack(fill="x")
        inventory_columns = (
            ("细胞名称", 5, 170),
            ("剩余管数", 2, 78),
            ("预警阈值", 4, 175),
            ("库存状态", 2, 88),
            ("操作", 4, 178),
        )
        for column, (_text, weight, minimum) in enumerate(inventory_columns):
            inventory_grid.columnconfigure(column, weight=weight, minsize=minimum, uniform="inventory")
        for column, (text_value, _weight, _minimum) in enumerate(inventory_columns):
            tk.Label(inventory_grid, text=text_value, anchor="w" if column < 2 else "center", bg="#F6F8FB", fg=COLORS["muted"], font=("Microsoft YaHei UI", 8, "bold"), padx=12, pady=11).grid(row=0, column=column, sticky="nsew", padx=(0, 1) if column < len(inventory_columns) - 1 else 0, pady=(0, 1))
        if not items:
            empty_text = "当前筛选条件下没有细胞库存。" if all_items else "暂无匹配的细胞库存。请打开一个细胞冻存盒并录入孔位数据。"
            tk.Label(inventory_grid, text=empty_text, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 10), pady=30).grid(row=1, column=0, columnspan=len(inventory_columns), sticky="ew")
            return
        for row_index, item in enumerate(items, start=1):
            positions = list(item["positions"])
            threshold_text = f"不足≤{item['alert_low']} / 提醒≤{item['alert_warning']}"
            if not item["alert_configured"]:
                threshold_text += "（默认）"
            values = (
                (str(item["sample_name"]), COLORS["text"], "bold"),
                (f"{len(positions)} 管", str(item["alert_color"]), "bold"),
                (threshold_text, COLORS["muted"], "normal"),
                (str(item["alert_text"]), str(item["alert_color"]), "bold"),
            )
            for column, (text_value, color, font_weight) in enumerate(values):
                font_size = 8 if column == 2 else 9
                tk.Label(inventory_grid, text=text_value, anchor="w" if column == 0 else "center", bg=COLORS["panel"], fg=color, font=("Microsoft YaHei UI", font_size, font_weight), padx=12, pady=13).grid(row=row_index, column=column, sticky="nsew", padx=(0, 1), pady=(0, 1))
            action_cell = tk.Frame(inventory_grid, bg=COLORS["panel"])
            action_cell.grid(row=row_index, column=4, sticky="nsew", pady=(0, 1))
            RoundedButton(action_cell, text="设置预警", command=lambda name=str(item["sample_name"]): self.configure_inventory_alert(name), fill="#FFF3DA", foreground="#9A5B00", hover_fill="#FFE5AD", border="#FFF3DA", canvas_bg=COLORS["panel"], width=78, height=32, radius=8).pack(side="left", padx=(8, 4), pady=8)
            RoundedButton(action_cell, text="查看位置", command=lambda name=str(item["sample_name"]): self.show_inventory_locations(name), fill=COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#DCE8FF", border=COLORS["primary_soft"], canvas_bg=COLORS["panel"], width=78, height=32, radius=8).pack(side="left", padx=(4, 8), pady=8)

    @staticmethod
    def _inventory_alert_status(tubes: int, low: int, warning: int) -> tuple[str, str, str, str]:
        if tubes <= low:
            return "low", "库存不足", "#C93C3C", "#FDECEC"
        if tubes <= warning:
            return "warning", "即将不足", "#B56A00", "#FFF3DA"
        return "normal", "库存正常", COLORS["occupied_text"], COLORS["occupied"]

    def set_inventory_status_filter(self, status: str) -> None:
        self.inventory_status_var.set(status)
        self.show_inventory_page()

    def configure_inventory_alert(self, sample_name: str) -> None:
        low, warning, _configured = self.repository.get_inventory_alert(sample_name)
        result = InventoryAlertDialog(self, sample_name, low, warning).show()
        if result is None:
            return
        try:
            self.repository.set_inventory_alert(sample_name, *result)
        except (OSError, ValueError) as exc:
            self._notify("预警设置失败", str(exc), danger=True)
            return
        self.show_inventory_page()

    def configure_default_inventory_alert(self) -> None:
        low, warning = self.repository.default_inventory_alert
        result = InventoryAlertDialog(self, "所有未单独设置的细胞", low, warning).show()
        if result is None:
            return
        try:
            self.repository.set_default_inventory_alert(*result)
        except (OSError, ValueError) as exc:
            self._notify("默认预警设置失败", str(exc), danger=True)
            return
        self.show_inventory_page()

    def clear_inventory_query(self) -> None:
        self.inventory_query_var.set("")
        self.show_inventory_page()

    @staticmethod
    def _event_action_labels() -> dict[str, str]:
        return {
            "IN": "入库",
            "OUT": "出库",
            "CLEAR": "清除",
            "IN_MOVE": "入库（快捷移动）",
            "IN_MOVE_UNDO": "入库（撤销移动）",
            "ADJUST": "信息更正",
            "HISTORY": "历史迁入",
        }

    def show_inventory_events_page(self) -> None:
        self._set_active_nav("events")
        self.page_title_var.set("出入库登记")
        self.clear_content()
        page = ttk.Frame(self.content, style="Page.TFrame", padding=(28, 24))
        page.pack(fill="both", expand=True)

        ttk.Label(page, text="出入库登记", style="Title.TLabel").pack(anchor="w")
        ttk.Label(page, text="集中记录入库、出库、清除和快捷移动等实际库存操作。", style="PageMuted.TLabel").pack(anchor="w", pady=(4, 16))

        action_totals = self.repository.inventory_event_action_counts()
        action_counts = {
            "IN": sum(action_totals.get(action, 0) for action in {"IN", "IN_MOVE", "IN_MOVE_UNDO"}),
            "OUT": action_totals.get("OUT", 0),
            "CLEAR": action_totals.get("CLEAR", 0),
        }
        all_event_count = sum(action_totals.values())
        cards = tk.Frame(page, bg=COLORS["bg"])
        cards.pack(fill="x", pady=(0, 14))
        for index, (label, value, color) in enumerate((
            ("全部登记", all_event_count, COLORS["primary"]),
            ("入库", action_counts["IN"], COLORS["occupied_text"]),
            ("出库", action_counts["OUT"], "#B86613"),
            ("清除", action_counts["CLEAR"], COLORS["danger"]),
        )):
            cards.columnconfigure(index, weight=1)
            card = self._panel(cards)
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))
            tk.Label(card, text=label, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=15, pady=(12, 2))
            tk.Label(card, text=str(value), bg=COLORS["panel"], fg=color, font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w", padx=15, pady=(0, 12))

        filter_panel = self._panel(page)
        filter_panel.pack(fill="x", pady=(0, 14))
        form = tk.Frame(filter_panel, bg=COLORS["panel"])
        form.pack(fill="x", padx=18, pady=16)
        # Date fields contain an entry plus a calendar action, so equal-width
        # columns make them visibly cramped.  Reserve more room for both date
        # ranges while keeping the compact filters usable at the 1000px
        # minimum application width.
        for column, (weight, minimum) in enumerate((
            (9, 140),
            (9, 140),
            (11, 165),
            (13, 195),
            (13, 195),
        )):
            form.columnconfigure(column, weight=weight, minsize=minimum)
        action_labels = self._event_action_labels()
        freezer_options = ("全部液氮罐", *(name for _freezer_id, name in self.repository.list_freezers(include_archived=True)))
        fields: tuple[tuple[str, tk.StringVar, str, tuple[str, ...] | None], ...] = (
            ("关键词", self.event_query_var, "细胞、人员或位置", None),
            ("操作类型", self.event_action_var, "", ()),
            ("液氮罐", self.event_freezer_var, "", freezer_options),
            ("日期从", self.event_date_from_var, "YYYY-MM-DD", None),
            ("日期至", self.event_date_to_var, "YYYY-MM-DD", None),
        )
        for column, (label, variable, hint, options) in enumerate(fields):
            cell = tk.Frame(form, bg=COLORS["panel"])
            cell.grid(row=0, column=column, sticky="new", padx=5)
            tk.Label(cell, text=label, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 8, "bold")).pack(anchor="w", pady=(0, 5))
            if column == 1:
                MultiSelectDropdown(
                    cell,
                    action_labels,
                    self.event_action_filters,
                    self.event_action_var,
                ).pack(fill="x")
            elif column in {3, 4}:
                picker = DatePickerField(
                    cell,
                    variable,
                    self.show_inventory_events_page,
                )
                picker.entry.pack_configure(ipady=0)
                picker.calendar_button.configure(width=4)
                picker.calendar_button.pack_configure(ipady=0)
                picker.pack(fill="x")
            elif options is None:
                entry = ttk.Entry(cell, textvariable=variable, font=("Microsoft YaHei UI", 9))
                entry.pack(fill="x")
                _bind_ime_safe_return(entry, self.show_inventory_events_page)
            else:
                ttk.Combobox(cell, textvariable=variable, values=options, state="readonly", font=("Microsoft YaHei UI", 9)).pack(fill="x")
            if hint:
                tk.Label(cell, text=hint, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 7)).pack(anchor="w", pady=(3, 0))
        actions = tk.Frame(filter_panel, bg=COLORS["panel"])
        actions.pack(fill="x", padx=18, pady=(0, 14))
        RoundedButton(actions, text="应用筛选", command=self.show_inventory_events_page, fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["panel"], width=100, height=36, radius=9).pack(side="left")
        RoundedButton(actions, text="重置", command=self.reset_inventory_event_filters, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=82, height=36, radius=9).pack(side="left", padx=8)
        RoundedButton(actions, text="导出出入库", command=self.export_inventory_events_to_excel, fill=COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#DCE8FF", border=COLORS["primary_soft"], canvas_bg=COLORS["panel"], width=108, height=36, radius=9).pack(side="right")

        selected_freezer_id = next((freezer_id for freezer_id, name in self.repository.list_freezers(include_archived=True) if name == self.event_freezer_var.get()), "")
        event_filter = {
            "query": self.event_query_var.get(),
            "actions": self.event_action_filters,
            "freezer_id": selected_freezer_id,
            "date_from": self.event_date_from_var.get(),
            "date_to": self.event_date_to_var.get(),
        }
        event_count = self.repository.count_inventory_events(**event_filter)
        events = self.repository.list_inventory_events(
            **event_filter,
            limit=300,
        )

        list_panel = self._panel(page)
        list_panel.pack(fill="both", expand=True, pady=(0, 4))
        list_header = tk.Frame(list_panel, bg=COLORS["panel"])
        list_header.pack(fill="x", padx=16, pady=(12, 9))
        tk.Label(list_header, text=f"登记记录 · {event_count} 条", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        if event_count > 300:
            tk.Label(list_header, text="当前显示最近 300 条，请使用筛选缩小范围", bg=COLORS["panel"], fg="#B56A00", font=("Microsoft YaHei UI", 8)).pack(side="right")

        table_host = tk.Frame(list_panel, bg=COLORS["panel"])
        table_host.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        table_host.rowconfigure(0, weight=1)
        table_host.columnconfigure(0, weight=1)
        column_specs = (
            ("action", "操作", 104, "center"),
            ("sample", "细胞名称", 170, "w"),
            ("location", "位置", 260, "w"),
            ("operator", "人员", 90, "center"),
            ("business_date", "业务日期", 100, "center"),
            ("created_at", "系统时间", 145, "center"),
        )
        tree = ttk.Treeview(
            table_host,
            columns=tuple(spec[0] for spec in column_specs),
            show="headings",
            selectmode="browse",
            style="Event.Treeview",
            height=10,
        )
        for column_id, label, width, anchor in column_specs:
            tree.heading(column_id, text=label, anchor=anchor)
            tree.column(column_id, width=width, minwidth=max(70, width // 2), anchor=anchor, stretch=column_id in {"sample", "location"})
        vertical = ttk.Scrollbar(table_host, orient="vertical", command=tree.yview, style="Flat.Vertical.TScrollbar")
        horizontal = ttk.Scrollbar(table_host, orient="horizontal", command=tree.xview, style="Flat.Horizontal.TScrollbar")
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        tree.tag_configure("in", foreground=COLORS["occupied_text"])
        tree.tag_configure("out", foreground="#B86613")
        tree.tag_configure("clear", foreground=COLORS["danger"])
        tree.tag_configure("history", foreground=COLORS["muted"])
        for event in events:
            source = f"{event.freezer_name} / {event.unit_code}/{event.position}"
            location = source
            if event.target_position:
                location += f" → {event.target_unit_code or event.unit_code}/{event.target_position}"
            values = (
                action_labels.get(event.action, event.action),
                event.sample_name or "—",
                location,
                (event.operator or "—") if event.action in {"IN", "OUT"} else "",
                event.operation_date or "—",
                event.created_at.replace("T", " "),
            )
            if event.action in {"IN", "IN_MOVE", "IN_MOVE_UNDO"}:
                tag = "in"
            elif event.action == "OUT":
                tag = "out"
            elif event.action == "CLEAR":
                tag = "clear"
            else:
                tag = "history"
            tree.insert("", "end", values=values, tags=(tag,))
        if not events:
            tree.insert("", "end", values=("", "没有符合当前条件的出入库记录。", "", "", "", ""), tags=("history",))

    def reset_inventory_event_filters(self) -> None:
        self.event_query_var.set("")
        self.event_action_filters.clear()
        self.event_action_filters.update(self._event_action_labels())
        self.event_action_var.set("全部操作")
        self.event_freezer_var.set("全部液氮罐")
        self.event_date_from_var.set("")
        self.event_date_to_var.set("")
        self.show_inventory_events_page()

    def export_inventory_events_to_excel(self) -> None:
        date_range = InventoryEventExportDialog(self).show()
        if date_range is None:
            return
        date_from, date_to = date_range
        events = self.repository.list_inventory_events(
            actions={"IN", "OUT"},
            date_from=date_from,
            date_to=date_to,
        )
        if not events:
            self._notify(
                "没有可导出的出入库记录",
                f"{date_from} 至 {date_to} 没有普通入库或出库记录。",
                danger=True,
            )
            return
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="导出出入库登记",
            initialdir=str(Path(__file__).parent),
            initialfile=f"出入库登记_{date_from.replace('-', '')}-{date_to.replace('-', '')}.xlsx",
            defaultextension=".xlsx",
            filetypes=(("Excel 工作簿", "*.xlsx"),),
        )
        if not selected:
            return
        try:
            export_inventory_events_workbook(Path(selected), events)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            self._notify("导出失败", f"无法生成出入库登记 Excel：\n{exc}", danger=True)
            return
        self._notify(
            "出入库登记导出完成",
            f"已导出 {date_from} 至 {date_to} 的入库、出库记录 {len(events)} 条。\n\n文件：{Path(selected).name}",
        )

    def show_inventory_locations(self, sample_name: str) -> None:
        if sample_name.casefold() != self.inventory_location_sample_name.casefold():
            self.inventory_location_opened_boxes.clear()
        self.inventory_location_sample_name = sample_name
        locations = self.repository.inventory_locations(sample_name)
        if not locations:
            self._notify("未找到存放位置", "该细胞目前没有可用的盒内位置记录。", danger=True)
            return
        InventoryLocationsDialog(self, sample_name, locations).show()

    def return_to_inventory_locations(self) -> None:
        """Restore the inventory page, then reopen the current cell's locations."""
        sample_name = self.inventory_location_sample_name
        if not sample_name:
            self.show_inventory_page()
            return
        self.show_inventory_page()
        self.after_idle(lambda value=sample_name: self.show_inventory_locations(value))

    def open_inventory_location(
        self,
        freezer_id: str,
        code: str,
        positions: tuple[str, ...] | list[str] = (),
        *,
        return_mode: str = "",
    ) -> None:
        try:
            self.repository.switch_freezer(freezer_id)
        except (OSError, ValueError) as exc:
            self._notify("无法打开位置", str(exc), danger=True)
            return
        self._refresh_freezer_selector()
        self._update_sidebar_summary()
        self.box_search_return_mode = return_mode
        if return_mode == "basic":
            self.search_opened_boxes.add((freezer_id, code))
        self.pending_search_highlight = (
            freezer_id,
            code,
            {str(position).upper() for position in positions},
        ) if positions else None
        self.show_box(code)

    def open_legacy_search_result(self, freezer_id: str, code: str) -> None:
        """Open a legacy box-level result after switching to its owning tank."""
        try:
            self.repository.switch_freezer(freezer_id)
        except (OSError, ValueError) as exc:
            self._notify("无法打开位置", str(exc), danger=True)
            return
        self._refresh_freezer_selector()
        self._update_sidebar_summary()
        self.box_search_return_mode = "basic"
        self.search_opened_boxes.add((freezer_id, code))
        self.show_editor(code)

    def show_search_page(self) -> None:
        self._set_active_nav("search")
        self.page_title_var.set("查找细胞 · 全部液氮罐")
        self.clear_content()
        page = ttk.Frame(self.content, style="Page.TFrame", padding=(28, 26))
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="查找细胞", style="Title.TLabel").pack(anchor="w")
        ttk.Label(page, text="搜索范围：全部未归档液氮罐", style="PageMuted.TLabel").pack(anchor="w", pady=(4, 18))
        panel = self._panel(page)
        panel.pack(fill="x")
        inside = tk.Frame(panel, bg=COLORS["panel"])
        inside.pack(fill="x", padx=24, pady=24)
        tk.Label(inside, text="输入盒位、孔位、细胞名称、类别、入库人或备注", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 9))
        search_row = tk.Frame(inside, bg=COLORS["panel"])
        search_row.pack(fill="x")
        self.search_entry = tk.Entry(search_row, textvariable=self.search_var, relief="flat", bg="#F4F7FB", fg=COLORS["text"], insertbackground=COLORS["text"], highlightthickness=1, highlightbackground=COLORS["line"], highlightcolor=COLORS["primary"], font=("Microsoft YaHei UI", 11))
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=10)
        _bind_ime_safe_return(self.search_entry, self.show_search_results)
        RoundedButton(search_row, text="开始搜索", command=self.show_search_results, fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["panel"], width=112, height=43, radius=11).pack(side="left", padx=(12, 0))
        RoundedButton(search_row, text="高级搜索", command=self.show_advanced_search_page, fill=COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#DCE8FF", border=COLORS["primary_soft"], canvas_bg=COLORS["panel"], width=112, height=43, radius=11).pack(side="left", padx=(9, 0))
        tk.Label(inside, text="例如：11、11/A1、HEK293T、细胞类别或入库人姓名", bg=COLORS["panel"], fg="#98A4B5", font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(8, 0))
        self.after(80, lambda widget=self.search_entry: self._focus_if_exists(widget))

    def show_advanced_search_page(self, *, run_search: bool = False) -> None:
        self._set_active_nav("search")
        self.page_title_var.set(f"高级查找 · {self.repository.freezer_name}")
        self.clear_content()
        scroll = ScrollableFrame(self.content)
        scroll.pack(fill="both", expand=True)
        page = ttk.Frame(scroll.body, style="Page.TFrame", padding=(28, 24))
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="高级查找与批量操作", style="Title.TLabel").pack(anchor="w")
        ttk.Label(page, text=f"当前范围：{self.repository.freezer_name}。可筛选有细胞或空闲冻存盒。", style="PageMuted.TLabel").pack(anchor="w", pady=(4, 16))

        panel = self._panel(page)
        panel.pack(fill="x", pady=(0, 14))
        form = tk.Frame(panel, bg=COLORS["panel"])
        form.pack(fill="x", padx=20, pady=18)
        for column in range(3):
            form.columnconfigure(column, weight=1)
        fields = (
            ("关键词", self.search_var, "盒位、孔位、名称、类别、人员或备注"),
            ("细胞类别", self.filter_sample_type_var, "如：肿瘤细胞"),
            ("入库人", self.filter_stored_by_var, "姓名"),
            ("入库日期从", self.filter_date_from_var, "YYYY-MM-DD"),
            ("入库日期至", self.filter_date_to_var, "YYYY-MM-DD"),
        )
        for index, (label, variable, hint) in enumerate(fields):
            row, column = divmod(index, 3)
            cell = tk.Frame(form, bg=COLORS["panel"])
            cell.grid(row=row * 2, column=column, sticky="ew", padx=(0 if column == 0 else 12, 0), pady=(0, 10))
            tk.Label(cell, text=label, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 5))
            entry = ttk.Entry(cell, textvariable=variable, font=("Microsoft YaHei UI", 9))
            entry.pack(fill="x")
            _bind_ime_safe_return(entry, self.run_advanced_search)
        status_cell = tk.Frame(form, bg=COLORS["panel"])
        status_cell.grid(row=2, column=2, sticky="ew", padx=(12, 0), pady=(0, 10))
        tk.Label(status_cell, text="冻存盒状态", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 5))
        ttk.Combobox(status_cell, textvariable=self.filter_status_var, values=("有细胞", "空冻存盒"), state="readonly", font=("Microsoft YaHei UI", 9)).pack(fill="x")
        action_row = tk.Frame(form, bg=COLORS["panel"])
        action_row.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        RoundedButton(action_row, text="应用筛选", command=self.run_advanced_search, fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["panel"], width=108, height=38, radius=10).pack(side="left")
        RoundedButton(action_row, text="重置条件", command=self.reset_search_filters, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=102, height=38, radius=10).pack(side="left", padx=9)

        if not run_search:
            self.current_filter_results = []
            self.advanced_box_results = {}
            self.result_selection = {}
            waiting_panel = self._panel(page)
            waiting_panel.pack(fill="x")
            tk.Label(
                waiting_panel,
                text="请设置检索条件，然后点击“应用筛选”或按 Enter 查看结果。",
                bg=COLORS["panel"],
                fg=COLORS["muted"],
                font=("Microsoft YaHei UI", 10),
                pady=30,
            ).pack()
            return

        occupied_search = self.filter_status_var.get() != "空冻存盒"
        advanced_results = self.repository.advanced_box_search(
            self.search_var.get(),
            sample_type=self.filter_sample_type_var.get(),
            stored_by=self.filter_stored_by_var.get(),
            date_from=self.filter_date_from_var.get(),
            date_to=self.filter_date_to_var.get(),
            occupied=occupied_search,
        )
        self.current_filter_results = []
        self.advanced_box_results = {str(item["code"]): item for item in advanced_results}
        self.result_selection = {code: tk.BooleanVar(value=False) for code in self.advanced_box_results}
        result_panel = self._panel(page)
        result_panel.pack(fill="both", expand=True)
        toolbar = tk.Frame(result_panel, bg=COLORS["panel"])
        toolbar.pack(fill="x", padx=18, pady=(14, 8))
        tk.Label(toolbar, text=f"筛选结果：{len(advanced_results)} 个冻存盒", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        RoundedButton(toolbar, text="全选 / 取消", command=self.toggle_all_results, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=104, height=34, radius=9).pack(side="right")
        RoundedButton(toolbar, text="导出选中", command=self.batch_export_selected, fill=COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#DCE8FF", border=COLORS["primary_soft"], canvas_bg=COLORS["panel"], width=96, height=34, radius=9).pack(side="right", padx=7)
        RoundedButton(toolbar, text="批量移动", command=self.batch_move_selected, fill=COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#DCE8FF", border=COLORS["primary_soft"], canvas_bg=COLORS["panel"], width=96, height=34, radius=9).pack(side="right")
        RoundedButton(toolbar, text="批量清空", command=self.batch_clear_selected, fill="#FCECEC", foreground=COLORS["danger"], hover_fill="#F7DADA", border="#F3D1D1", canvas_bg=COLORS["panel"], width=96, height=34, radius=9).pack(side="right", padx=7)
        if not advanced_results:
            empty_text = "没有符合条件的冻存盒或细胞。"
            if not occupied_search and any(value.strip() for value in (self.filter_sample_type_var.get(), self.filter_stored_by_var.get(), self.filter_date_from_var.get(), self.filter_date_to_var.get())):
                empty_text = "空冻存盒没有细胞类别、入库人或入库日期信息，请清除这些条件后重试。"
            tk.Label(result_panel, text=empty_text, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 10), pady=26).pack()
        for result in advanced_results:
            self._render_advanced_box_result(result_panel, result, occupied_search)

    def _render_box_search_result(self, parent: tk.Misc, freezer_id: str, freezer_name: str, code: str, position: str, sample: BoxSample) -> None:
        item = tk.Frame(parent, bg=COLORS["panel"])
        item.pack(fill="x", padx=18, pady=7)
        tk.Label(item, text=f"{code}/{position}", width=12, anchor="w", bg=COLORS["panel"], fg=COLORS["occupied_text"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        detail = tk.Frame(item, bg=COLORS["panel"])
        detail.pack(side="left", fill="x", expand=True)
        tk.Label(detail, text=sample.sample_name, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        subtitle = " · ".join(value for value in (sample.sample_type, sample.stored_by, sample.stored_date) if value)
        tk.Label(detail, text=f"{freezer_name} · 细胞冻存盒 {code}" + (f" · {subtitle}" if subtitle else ""), bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        RoundedButton(item, text="打开盒子", command=lambda: self.open_inventory_location(freezer_id, code, (position,), return_mode="basic"), fill=COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#DCE8FF", border=COLORS["primary_soft"], canvas_bg=COLORS["panel"], width=92, height=30, radius=8, font=("Microsoft YaHei UI", 8)).pack(side="right")

    def _render_advanced_box_result(self, parent: tk.Misc, result: dict[str, object], occupied: bool) -> None:
        code = str(result["code"])
        positions = [str(value) for value in result["positions"]]
        samples: list[BoxSample] = list(result["samples"])
        card = tk.Frame(parent, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        card.pack(fill="x", padx=18, pady=7)
        card.columnconfigure(1, weight=1)
        tk.Checkbutton(card, variable=self.result_selection[code], bg=COLORS["panel"], activebackground=COLORS["panel"], highlightthickness=0).grid(row=0, column=0, sticky="w", padx=(12, 4), pady=16)
        detail = tk.Frame(card, bg=COLORS["panel"])
        detail.grid(row=0, column=1, sticky="ew", padx=(4, 16), pady=12)
        tk.Label(detail, text=f"细胞冻存盒 {code}", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        if occupied:
            names: list[str] = []
            for sample in samples:
                if sample.sample_name not in names:
                    names.append(sample.sample_name)
            preview = "、".join(names[:5]) + (f" 等 {len(names)} 种" if len(names) > 5 else "")
            tk.Label(detail, text=f"匹配孔位 {compact_box_positions(positions)}  ·  {len(positions)} 支冻存管", bg=COLORS["panel"], fg=COLORS["occupied_text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(4, 0))
            tk.Label(detail, text=f"细胞：{preview}", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(3, 0))
        else:
            layout = self.repository.get_box_layout(code)
            tk.Label(detail, text=f"空冻存盒  ·  {layout.rows}×{layout.columns}  ·  可用 {layout.rows * layout.columns} 个孔位", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(4, 0))
        RoundedButton(
            card,
            text="打开盒子",
            command=lambda values=tuple(positions): self.open_inventory_location(self.repository.current_freezer_id, code, values, return_mode="advanced"),
            fill=COLORS["primary_soft"],
            foreground=COLORS["primary"],
            hover_fill="#DCE8FF",
            border=COLORS["primary_soft"],
            canvas_bg=COLORS["panel"],
            width=92,
            height=34,
            radius=9,
            font=("Microsoft YaHei UI", 8),
        ).grid(row=0, column=2, sticky="e", padx=(0, 16), pady=16)

    @staticmethod
    def _group_box_search_results(results: list[tuple[str, str, str, str, BoxSample]]) -> list[dict[str, object]]:
        grouped: dict[tuple[str, str], dict[str, object]] = {}
        for freezer_id, freezer_name, code, position, sample in results:
            item = grouped.setdefault(
                (freezer_id, code),
                {"freezer_id": freezer_id, "freezer_name": freezer_name, "code": code, "positions": [], "samples": []},
            )
            item["positions"].append(position)
            item["samples"].append(sample)
        return list(grouped.values())

    def _render_box_search_group(self, parent: tk.Misc, group: dict[str, object]) -> None:
        freezer_id = str(group["freezer_id"])
        freezer_name = str(group["freezer_name"])
        code = str(group["code"])
        positions = list(group["positions"])
        samples = list(group["samples"])
        names: list[str] = []
        for sample in samples:
            if sample.sample_name not in names:
                names.append(sample.sample_name)
        name_preview = "、".join(names[:4]) + (f" 等 {len(names)} 种" if len(names) > 4 else "")
        opened = (freezer_id, code) in self.search_opened_boxes
        card_bg = COLORS["primary_soft"] if opened else COLORS["panel"]
        card = tk.Frame(
            parent,
            bg=card_bg,
            highlightthickness=2 if opened else 1,
            highlightbackground=COLORS["primary"] if opened else COLORS["line"],
        )
        card.pack(fill="x", padx=18, pady=7)
        detail = tk.Frame(card, bg=card_bg)
        detail.pack(side="left", fill="x", expand=True, padx=16, pady=12)
        title_row = tk.Frame(detail, bg=card_bg)
        title_row.pack(fill="x")
        tk.Label(title_row, text=f"细胞冻存盒 {code}", bg=card_bg, fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        if opened:
            tk.Label(
                title_row,
                text="已查看",
                bg="#E7F0FF",
                fg=COLORS["primary"],
                font=("Microsoft YaHei UI", 8, "bold"),
                padx=9,
                pady=1,
                highlightthickness=1,
                highlightbackground="#BFD4FF",
            ).pack(side="left", padx=10)
        tk.Label(detail, text=f"匹配孔位 {compact_box_positions(positions)}  ·  {len(positions)} 支冻存管", bg=card_bg, fg=COLORS["occupied_text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(4, 0))
        tk.Label(detail, text=f"{freezer_name}  ·  细胞：{name_preview}", bg=card_bg, fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(3, 0))
        RoundedButton(card, text="再次打开" if opened else "打开盒子", command=lambda values=tuple(str(position) for position in positions): self.open_inventory_location(freezer_id, code, values, return_mode="basic"), fill="#DCE8FF" if opened else COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#CFE0FF", border="#DCE8FF" if opened else COLORS["primary_soft"], canvas_bg=card_bg, width=92, height=34, radius=9, font=("Microsoft YaHei UI", 8)).pack(side="right", padx=16, pady=16)

    def run_advanced_search(self) -> None:
        self.show_advanced_search_page(run_search=True)

    def reset_search_filters(self) -> None:
        for variable in (self.search_var, self.filter_sample_type_var, self.filter_stored_by_var, self.filter_date_from_var, self.filter_date_to_var):
            variable.set("")
        self.filter_status_var.set("有细胞")
        self.show_advanced_search_page()

    def toggle_all_results(self) -> None:
        new_value = not all(variable.get() for variable in self.result_selection.values())
        for variable in self.result_selection.values():
            variable.set(new_value)

    def _selected_result_codes(self, *, occupied_only: bool = False) -> list[str]:
        selected = [code for code, variable in self.result_selection.items() if variable.get()]
        if occupied_only:
            selected = [code for code in selected if self.repository.box_has_samples(code)]
        if not selected:
            self._notify("尚未选择", "请先勾选至少一个符合条件的冻存盒。", danger=True)
        return selected

    def batch_clear_selected(self) -> None:
        codes = self._selected_result_codes(occupied_only=True)
        if not codes:
            return
        tube_count = sum(len(self.repository.get_box_samples(code)) for code in codes)
        if not self._ask_confirm("确认批量清空冻存盒", f"确定清空选中的 {len(codes)} 个冻存盒吗？\n盒内共 {tube_count} 支冻存管。执行前会自动创建备份。", danger=True):
            return
        if not self._ask_confirm("再次确认清除", "第二次确认：这些冻存管将从当前库存中清除，操作会写入出入库登记。\n\n确定继续吗？", danger=True):
            return
        try:
            self.repository.create_backup("高级检索批量清空前")
            removed_boxes, removed_tubes = self.repository.clear_boxes(codes)
        except (OSError, ValueError) as exc:
            self._notify("批量清空失败", str(exc), danger=True)
            return
        self._update_sidebar_summary()
        self._notify("批量清空完成", f"已清空 {removed_boxes} 个冻存盒，共移除 {removed_tubes} 支冻存管。")
        self.show_advanced_search_page(run_search=True)

    def batch_move_selected(self) -> None:
        codes = self._selected_result_codes(occupied_only=True)
        if not codes:
            return
        result = BoxMoveDialog(self, codes).show()
        if result is None:
            return
        target_id, target_codes = result
        try:
            self.repository.create_backup("高级检索冻存盒批量移动前")
            mappings = self.repository.move_boxes(codes, target_id, target_codes)
        except (OSError, ValueError) as exc:
            self._notify("移动失败", str(exc), danger=True)
            return
        preview = "、".join(f"{source}→{target}" for source, target in mappings[:6])
        if len(mappings) > 6:
            preview += "……"
        self._update_sidebar_summary()
        self._notify("批量移动完成", f"已移动 {len(mappings)} 个冻存盒。\n{preview}")
        self.show_advanced_search_page(run_search=True)

    def batch_export_selected(self) -> None:
        codes = self._selected_result_codes()
        if not codes:
            return
        selected = filedialog.asksaveasfilename(parent=self, title="导出选中记录", initialdir=str(Path(__file__).parent), initialfile=f"{self.repository.freezer_name}_筛选结果.xlsx", defaultextension=".xlsx", filetypes=(("Excel 工作簿", "*.xlsx"),))
        if not selected:
            return
        samples = {
            (code, position): sample
            for code in codes
            for position, sample in self.repository.get_box_samples(code).items()
        }
        selected_capacity = sum(
            self.repository.get_box_layout(code).rows * self.repository.get_box_layout(code).columns
            for code in codes
        )
        try:
            export_cryotube_workbook(Path(selected), [(f"{self.repository.freezer_name}_筛选结果", samples, selected_capacity)])
        except (OSError, ValueError) as exc:
            self._notify("导出失败", str(exc), danger=True)
            return
        self._notify("导出完成", f"已导出 {len(codes)} 个冻存盒、{len(samples)} 支冻存管：{Path(selected).name}")

    def clear_content(self, *, preserve_box_modes: bool = False) -> None:
        """Start building the next page behind the currently visible page.

        Page builders keep using ``self.content`` as before, but now they write
        into an off-screen layer.  The old page remains on top until the new
        layer has completed more than one stable geometry pass.
        """
        if not preserve_box_modes:
            self._exit_box_interaction_modes()
            self.box_page_visible = False
        if self._page_commit_job is not None:
            try:
                self.after_cancel(self._page_commit_job)
            except tk.TclError:
                pass
            self._page_commit_job = None

        visible = self._visible_content
        if self.content is not visible and self.content.winfo_exists():
            self.content.destroy()

        self._page_generation += 1
        generation = self._page_generation
        staging = ttk.Frame(self.content_host, style="Page.TFrame")
        staging.place(x=0, y=0, relwidth=1, relheight=1)
        if visible is not None and visible.winfo_exists():
            visible.tkraise()
        self.content = staging
        self._page_commit_job = self.after(
            16,
            lambda: self._settle_content_page(
                staging, generation, None, 0, 0
            ),
        )

    def _content_layout_signature(self, root: tk.Misc) -> tuple[object, ...]:
        """Return enough geometry state to detect an unfinished Tk layout."""
        signature: list[object] = []
        pending = [root]
        while pending:
            widget = pending.pop()
            try:
                signature.extend(
                    (
                        widget.winfo_class(),
                        widget.winfo_reqwidth(),
                        widget.winfo_reqheight(),
                        widget.winfo_width(),
                        widget.winfo_height(),
                    )
                )
                if isinstance(widget, tk.Canvas):
                    signature.extend((widget.bbox("all"), widget.cget("scrollregion")))
                pending.extend(widget.winfo_children())
            except tk.TclError:
                continue
        return tuple(signature)

    def _settle_content_page(
        self,
        staging: ttk.Frame,
        generation: int,
        previous_signature: tuple[object, ...] | None,
        stable_passes: int,
        attempts: int,
    ) -> None:
        """Atomically reveal a page after its geometry has stopped changing."""
        self._page_commit_job = None
        if (
            generation != self._page_generation
            or staging is not self.content
            or not staging.winfo_exists()
        ):
            return
        try:
            self.update_idletasks()
            signature = self._content_layout_signature(staging)
        except tk.TclError:
            return

        stable_passes = stable_passes + 1 if signature == previous_signature else 0
        attempts += 1
        if stable_passes >= 2 or attempts >= 8:
            old_page = self._visible_content
            staging.tkraise()
            self._visible_content = staging
            if old_page is not None and old_page is not staging and old_page.winfo_exists():
                self.after_idle(old_page.destroy)
            return

        self._page_commit_job = self.after(
            16,
            lambda: self._settle_content_page(
                staging, generation, signature, stable_passes, attempts
            ),
        )

    @staticmethod
    def _panel(parent: tk.Misc, **grid_options: object) -> tk.Frame:
        panel = tk.Frame(parent, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        if grid_options:
            panel.grid(**grid_options)
        return panel

    def show_overview(self) -> None:
        self.box_search_return_mode = ""
        self.box_empty_highlights.clear()
        self.box_search_highlights.clear()
        self._set_active_nav("overview")
        self.page_title_var.set(f"冻存盒总览 · {self.repository.freezer_name}")
        self._update_sidebar_summary()
        self.clear_content()
        page = ttk.Frame(self.content, style="Page.TFrame", padding=(26, 20))
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=1)
        grid_row = 3 if self.overview_batch_mode else 2
        page.rowconfigure(grid_row, weight=1)

        title_row = ttk.Frame(page, style="Page.TFrame")
        title_row.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        title_box = ttk.Frame(title_row, style="Page.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text=self.repository.freezer_name, style="Title.TLabel").pack(anchor="w")
        storage_columns = self.repository.storage_columns
        box_capacity = self.repository.storage_capacity
        tube_used = self.repository.tube_used_count
        tube_capacity = self.repository.tube_capacity
        subtitle = "选择冻存盒位进行批量处理" if self.overview_batch_mode else f"{storage_columns}列 × 5层；点击盒位查看详情或打开盒内孔位"
        ttk.Label(title_box, text=subtitle, style="PageMuted.TLabel").pack(anchor="w", pady=(3, 0))
        RoundedButton(
            title_row,
            text="退出批量" if self.overview_batch_mode else "批量处理",
            command=self.toggle_overview_batch_mode,
            fill="#E8EDF4" if self.overview_batch_mode else COLORS["primary_soft"],
            foreground=COLORS["text"] if self.overview_batch_mode else COLORS["primary"],
            hover_fill="#DCE4EE" if self.overview_batch_mode else "#DCE8FF",
            border="#E8EDF4" if self.overview_batch_mode else COLORS["primary_soft"],
            canvas_bg=COLORS["bg"],
            width=116,
            height=42,
            radius=11,
        ).pack(side="right", padx=(0, 9), pady=3)
        if storage_columns < 5 and not self.overview_batch_mode:
            RoundedButton(
                title_row,
                text="＋ 添加一列",
                command=self.add_storage_column,
                fill=COLORS["primary_soft"],
                foreground=COLORS["primary"],
                hover_fill="#DCE8FF",
                border=COLORS["primary_soft"],
                canvas_bg=COLORS["bg"],
                width=116,
                height=42,
                radius=11,
            ).pack(side="right", padx=(0, 9), pady=3)
        elif storage_columns == 5 and not self.overview_batch_mode:
            RoundedButton(
                title_row,
                text="去除一列",
                command=self.remove_storage_column,
                fill="#FCECEC",
                foreground=COLORS["danger"],
                hover_fill="#F7DADA",
                border="#F3D1D1",
                canvas_bg=COLORS["bg"],
                width=116,
                height=42,
                radius=11,
            ).pack(side="right", padx=(0, 9), pady=3)

        row_offset = 1 if self.overview_batch_mode else 0
        if self.overview_batch_mode:
            batch_panel = self._panel(page, row=1, column=0, sticky="ew", pady=(0, 14))
            selected_boxes = self._overview_selected_box_codes(occupied_only=True)
            self.overview_batch_status_label = tk.Label(batch_panel, text=f"已选 {len(self.selected_compartments)} 个盒位 · {len(selected_boxes)} 个有细胞冻存盒", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"))
            self.overview_batch_status_label.pack(side="left", padx=18, pady=12)
            RoundedButton(batch_panel, text="清空选择", command=self.clear_overview_batch_selection, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=90, height=34, radius=9).pack(side="right", padx=(0, 14), pady=8)
            RoundedButton(batch_panel, text="导出选中", command=self.overview_batch_export, fill=COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#DCE8FF", border=COLORS["primary_soft"], canvas_bg=COLORS["panel"], width=92, height=34, radius=9).pack(side="right", padx=6, pady=8)
            RoundedButton(batch_panel, text="批量移动", command=self.overview_batch_move, fill=COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#DCE8FF", border=COLORS["primary_soft"], canvas_bg=COLORS["panel"], width=92, height=34, radius=9).pack(side="right", pady=8)
            RoundedButton(batch_panel, text="批量清空", command=self.overview_batch_clear, fill="#FCECEC", foreground=COLORS["danger"], hover_fill="#F7DADA", border="#F3D1D1", canvas_bg=COLORS["panel"], width=92, height=34, radius=9).pack(side="right", padx=6, pady=8)

        stats = ttk.Frame(page, style="Page.TFrame")
        stats.grid(row=1 + row_offset, column=0, sticky="ew", pady=(0, 14))
        for index in range(4):
            stats.columnconfigure(index, weight=1)
        used_boxes = self.repository.used_count
        cards = [
            ("已用冻存管", str(tube_used), f"管 · {used_boxes}个盒", COLORS["primary"]),
            ("剩余管位", str(tube_capacity - tube_used), "个冻存管位", COLORS["occupied_text"]),
            ("冻存盒位", f"{used_boxes}/{box_capacity}", f"{storage_columns}列×5层", "#805AD5"),
            ("总体使用率", f"{tube_used / tube_capacity * 100:.1f}%", f"总容量 {tube_capacity} 管", "#E29431"),
        ]
        for index, (label, value, suffix, accent) in enumerate(cards):
            card = self._panel(stats, row=0, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0 if index == 3 else 6))
            inside = tk.Frame(card, bg=COLORS["panel"])
            inside.pack(fill="both", expand=True, padx=17, pady=12)
            label_row = tk.Frame(inside, bg=COLORS["panel"])
            label_row.pack(fill="x")
            tk.Frame(label_row, width=7, height=7, bg=accent).pack(side="left", padx=(0, 7), pady=4)
            tk.Label(label_row, text=label, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(side="left")
            value_row = tk.Frame(inside, bg=COLORS["panel"])
            value_row.pack(fill="x", pady=(3, 0))
            tk.Label(value_row, text=value, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 19, "bold")).pack(side="left")
            tk.Label(value_row, text=suffix, bg=COLORS["panel"], fg="#A0A9B7", font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(7, 0), pady=(7, 0))

        grid_panel = self._panel(page, row=2 + row_offset, column=0, sticky="nsew")
        grid_panel.rowconfigure(1, weight=1)
        grid_panel.columnconfigure(0, weight=1)
        panel_head = tk.Frame(grid_panel, bg=COLORS["panel"])
        panel_head.grid(row=0, column=0, sticky="ew", padx=18, pady=(13, 7))
        tk.Label(panel_head, text="液氮罐冻存盒位分布", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        legend = tk.Frame(panel_head, bg=COLORS["panel"])
        legend.pack(side="right")
        tk.Frame(legend, width=8, height=8, bg=COLORS["occupied_text"]).pack(side="left", padx=(0, 6), pady=5)
        tk.Label(legend, text="已有细胞", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(0, 14))
        tk.Frame(legend, width=8, height=8, bg="#D9E0E9").pack(side="left", padx=(0, 6), pady=5)
        tk.Label(legend, text="全部空闲", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left")

        grid = tk.Frame(grid_panel, bg=COLORS["panel"])
        grid.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.overview_compartment_buttons: dict[str, RoundedButton] = {}
        for column in range(storage_columns + 1):
            grid.columnconfigure(column, weight=1 if column else 0, uniform="storage")
        for row in range(6):
            grid.rowconfigure(row, weight=1 if row else 0)
        tk.Label(grid, text="层  /  列", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).grid(row=0, column=0, padx=8, pady=4)
        for column in range(1, storage_columns + 1):
            tk.Label(grid, text=f"第 {column} 列", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8, "bold")).grid(row=0, column=column, pady=4)
        for layer in range(1, 6):
            tk.Label(grid, text=f"第 {layer} 层", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8, "bold")).grid(row=layer, column=0, padx=8)
            for column in range(1, storage_columns + 1):
                self._compartment_button(grid, column, layer).grid(row=layer, column=column, sticky="nsew", padx=7, pady=6)

    def add_storage_column(self) -> None:
        if not self._ask_confirm(
            "添加第5列",
            "确定为当前液氮罐添加第5列吗？\n添加后将新增 51–55 五个冻存盒位，总容量变为25。",
        ):
            return
        try:
            self.repository.add_storage_column()
        except (OSError, ValueError) as exc:
            self._notify("添加列失败", str(exc), danger=True)
            return
        self._update_sidebar_summary()
        self.show_overview()

    def remove_storage_column(self) -> None:
        occupied = self.repository.fifth_column_occupied_codes()
        if occupied:
            self._notify(
                "无法去除第5列",
                f"第5列的盒位 {'、'.join(occupied)} 中仍有细胞。\n请先将这些冻存管移动到其他列或清空后再操作。",
                danger=True,
            )
            return
        if not self._ask_confirm(
            "去除第5列",
            "确定去除当前液氮罐的第5列吗？\n51–55 均为空，去除后总容量恢复为20个冻存盒位。",
            danger=True,
        ):
            return
        try:
            self.repository.remove_storage_column()
        except (OSError, ValueError) as exc:
            self._notify("去除列失败", str(exc), danger=True)
            return
        self.selected_compartments.difference_update({"51", "52", "53", "54", "55"})
        self._update_sidebar_summary()
        self.show_overview()

    def _compartment_button(self, parent: tk.Misc, column: int, layer: int) -> RoundedButton:
        used = self.repository.compartment_usage(column, layer)
        code = compartment_code(column, layer)
        occupied = used > 0
        selected = self.overview_batch_mode and code in self.selected_compartments
        button = RoundedButton(
            parent,
            text=(f"✓  {code}\n●  已有细胞" if selected else (f"{code}\n●  已有细胞" if occupied else f"{code}\n○  空冻存盒")),
            command=(lambda value=code: self.toggle_compartment_selection(value)) if self.overview_batch_mode else (lambda: self.show_compartment(column, layer)),
            fill=COLORS["primary"] if selected else (COLORS["occupied"] if occupied else COLORS["empty"]),
            foreground="white" if selected else (COLORS["occupied_text"] if occupied else COLORS["text"]),
            hover_fill=COLORS["primary_dark"] if selected else ("#D8F0E4" if occupied else COLORS["primary_soft"]),
            border=COLORS["primary"] if selected else ("#BFE4D2" if occupied else COLORS["line"]),
            canvas_bg=COLORS["panel"],
            font=("Microsoft YaHei UI", 9, "bold"),
            width=118 if self.repository.storage_columns == 5 else 148,
            height=64,
            radius=10,
        )
        if self.overview_batch_mode:
            self.overview_compartment_buttons[code] = button
        return button

    def toggle_overview_batch_mode(self) -> None:
        self.overview_batch_mode = not self.overview_batch_mode
        if not self.overview_batch_mode:
            self.selected_compartments.clear()
        self.show_overview()

    def toggle_compartment_selection(self, code: str) -> None:
        if code in self.selected_compartments:
            self.selected_compartments.remove(code)
        else:
            self.selected_compartments.add(code)
        self._refresh_overview_compartment_button(code)
        self._refresh_overview_batch_status()

    def clear_overview_batch_selection(self) -> None:
        self.selected_compartments.clear()
        for code in tuple(getattr(self, "overview_compartment_buttons", {})):
            self._refresh_overview_compartment_button(code)
        self._refresh_overview_batch_status()

    def _refresh_overview_batch_status(self) -> None:
        label = getattr(self, "overview_batch_status_label", None)
        if label is None or not label.winfo_exists():
            return
        selected_boxes = self._overview_selected_box_codes(occupied_only=True)
        label.configure(text=f"已选 {len(self.selected_compartments)} 个盒位 · {len(selected_boxes)} 个有细胞冻存盒")

    def _refresh_overview_compartment_button(self, code: str) -> None:
        button = getattr(self, "overview_compartment_buttons", {}).get(code)
        if button is None or not button.winfo_exists():
            return
        column, layer = (int(char) for char in code)
        used = self.repository.compartment_usage(column, layer)
        occupied = used > 0
        selected = code in self.selected_compartments
        button.set_text(
            f"✓  {code}\n●  已有细胞"
            if selected
            else (f"{code}\n●  已有细胞" if occupied else f"{code}\n○  空冻存盒")
        )
        button.set_palette(
            fill=COLORS["primary"] if selected else (COLORS["occupied"] if occupied else COLORS["empty"]),
            foreground="white" if selected else (COLORS["occupied_text"] if occupied else COLORS["text"]),
            hover_fill=COLORS["primary_dark"] if selected else ("#D8F0E4" if occupied else COLORS["primary_soft"]),
            border=COLORS["primary"] if selected else ("#BFE4D2" if occupied else COLORS["line"]),
        )

    def _overview_selected_unit_codes(self, *, occupied_only: bool) -> list[str]:
        codes: list[str] = []
        for compartment in sorted(self.selected_compartments):
            if not occupied_only or self.repository.get(compartment).occupied:
                codes.append(compartment)
        return codes

    def _overview_selected_box_codes(self, *, occupied_only: bool) -> list[str]:
        return [
            code for code in sorted(self.selected_compartments)
            if not occupied_only or self.repository.box_has_samples(code)
        ]

    def overview_batch_clear(self) -> None:
        codes = self._overview_selected_box_codes(occupied_only=True)
        if not codes:
            self._notify("没有可清空的冻存盒", "所选冻存盒内没有已录入的细胞。", danger=True)
            return
        tube_count = sum(len(self.repository.get_box_samples(code)) for code in codes)
        if not self._ask_confirm(
            "确认批量清空冻存盒",
            f"确定清空选中的 {len(codes)} 个冻存盒吗？\n"
            f"盒内共 {tube_count} 支冻存管，清空后孔位将全部变为空位。\n"
            "执行前会自动创建备份。",
            danger=True,
        ):
            return
        try:
            self.repository.create_backup("冻存盒批量清空前")
            removed_boxes, removed_tubes = self.repository.clear_boxes(codes)
        except (OSError, ValueError) as exc:
            self._notify("批量清空失败", str(exc), danger=True)
            return
        self.selected_compartments.clear()
        self._update_sidebar_summary()
        self._notify("批量清空完成", f"已清空 {removed_boxes} 个冻存盒，共移除 {removed_tubes} 支冻存管。")
        self.show_overview()

    def overview_batch_move(self) -> None:
        codes = self._overview_selected_box_codes(occupied_only=True)
        if not codes:
            self._notify("没有可移动冻存盒", "请选择至少一个盒内已有细胞的冻存盒。", danger=True)
            return
        result = BoxMoveDialog(self, codes).show()
        if result is None:
            return
        target_id, target_codes = result
        try:
            self.repository.create_backup("冻存盒批量移动前")
            mappings = self.repository.move_boxes(codes, target_id, target_codes)
        except (OSError, ValueError) as exc:
            self._notify("移动失败", str(exc), danger=True)
            return
        self.selected_compartments.clear()
        self.overview_batch_mode = False
        self._update_sidebar_summary()
        summary = "、".join(f"{source}→{target}" for source, target in mappings[:6])
        target_name = self.repository.freezers[target_id].name
        self._notify("批量移动完成", f"已将 {len(mappings)} 个细胞冻存盒移动到“{target_name}”。\n{summary}{'……' if len(mappings) > 6 else ''}")
        self.show_overview()

    def overview_batch_export(self) -> None:
        codes = self._overview_selected_box_codes(occupied_only=True)
        if not codes:
            self._notify("没有可导出记录", "所选盒位中没有已录入的冻存管。", danger=True)
            return
        selected = filedialog.asksaveasfilename(parent=self, title="导出总览选中记录", initialdir=str(Path(__file__).parent), initialfile=f"{self.repository.freezer_name}_总览选中记录.xlsx", defaultextension=".xlsx", filetypes=(("Excel 工作簿", "*.xlsx"),))
        if not selected:
            return
        try:
            samples = {
                (code, position): sample
                for code in codes
                for position, sample in self.repository.get_box_samples(code).items()
            }
            selected_capacity = sum(
                self.repository.get_box_layout(code).rows * self.repository.get_box_layout(code).columns
                for code in codes
            )
            export_cryotube_workbook(
                Path(selected),
                [(f"{self.repository.freezer_name}_选中记录", samples, selected_capacity)],
            )
        except (OSError, ValueError) as exc:
            self._notify("导出失败", str(exc), danger=True)
            return
        self._notify("导出完成", f"已导出 {len(samples)} 支冻存管：{Path(selected).name}")

    def change_layer(self, layer: int) -> None:
        self.current_layer = layer
        self.show_overview()

    def show_compartment(self, column: int, layer: int) -> None:
        self.box_search_return_mode = ""
        self.box_empty_highlights.clear()
        self.box_search_highlights.clear()
        self._set_active_nav("overview")
        self.page_title_var.set(f"冻存盒详情 · {self.repository.freezer_name}")
        self.current_layer = layer
        next_compartment = (column, layer)
        self.compartment_batch_mode = False
        self.selected_compartment_units.clear()
        self.current_compartment = next_compartment
        self.clear_content()
        page = ttk.Frame(self.content, style="Page.TFrame", padding=(28, 18))
        page.pack(fill="both", expand=True)
        code = compartment_code(column, layer)

        nav = ttk.Frame(page, style="Page.TFrame")
        nav.pack(fill="x", pady=(0, 16))
        RoundedButton(
            nav,
            text="←  返回总览",
            command=self.show_overview,
            fill="#E8EDF4",
            foreground=COLORS["text"],
            hover_fill="#DCE4EE",
            border="#E8EDF4",
            canvas_bg=COLORS["bg"],
            width=112,
            height=38,
            radius=10,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        title_box = ttk.Frame(nav, style="Page.TFrame")
        title_box.pack(side="left", padx=18)
        ttk.Label(title_box, text=f"冻存盒位 {code}", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text=f"第{column}列 · 第{layer}层", style="PageMuted.TLabel").pack(anchor="w")

        info = self._panel(page)
        info.pack(fill="x", pady=(0, 16))
        box_layout = self.repository.get_box_layout(code)
        box_sample_count = len(self.repository.get_box_samples(code))
        box_capacity = box_layout.rows * box_layout.columns
        summary_text = tk.Frame(info, bg=COLORS["panel"])
        summary_text.pack(side="left", padx=20, pady=13)
        tk.Label(summary_text, text=f"冻存盒位 {code}", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        tk.Label(summary_text, text=f"第 {column} 列 · 第 {layer} 层", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(2, 0))
        metrics = tk.Frame(info, bg=COLORS["panel"])
        metrics.pack(side="right", padx=18, pady=10)
        for label, value, color in (
            ("盒位类型", "细胞冻存盒", COLORS["occupied_text"]),
            ("盒内布局", f"{box_layout.rows}×{box_layout.columns}", COLORS["primary"]),
            ("已用孔位", f"{box_sample_count}/{box_capacity}", "#805AD5"),
        ):
            metric = tk.Frame(metrics, bg="#F7F9FC", padx=13, pady=6)
            metric.pack(side="left", padx=4)
            tk.Label(metric, text=label, bg="#F7F9FC", fg=COLORS["muted"], font=("Microsoft YaHei UI", 7)).pack()
            tk.Label(metric, text=value, bg="#F7F9FC", fg=color, font=("Microsoft YaHei UI", 10, "bold")).pack()

        unit_area = ttk.Frame(page, style="Page.TFrame")
        unit_area.pack(fill="x")
        unit_area.columnconfigure(0, weight=1)
        self.compartment_unit_widgets: dict[str, tuple[tk.Frame, RoundedButton]] = {}
        for depth in range(1, 2):
            ucode = unit_code(column, layer)
            record = self.repository.get(ucode)
            card = tk.Frame(unit_area, bg=COLORS["panel"], highlightthickness=1, highlightbackground="#BFE4D2" if box_sample_count else COLORS["line"])
            card.grid(row=0, column=0, sticky="nsew")
            tk.Frame(card, height=6, bg=COLORS["occupied_text"] if box_sample_count else COLORS["line"]).pack(fill="x")
            inside = tk.Frame(card, bg=COLORS["panel"])
            inside.pack(fill="x", padx=20, pady=18)
            details = tk.Frame(inside, bg=COLORS["panel"])
            details.pack(side="left", fill="both", expand=True)
            heading_row = tk.Frame(details, bg=COLORS["panel"])
            heading_row.pack(fill="x")
            tk.Label(heading_row, text=f"细胞冻存盒 {ucode}", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
            status_text = f"已用 {box_sample_count} 孔" if box_sample_count else "盒内暂无细胞"
            tk.Label(heading_row, text=status_text, bg=COLORS["occupied"] if box_sample_count else "#EEF2F7", fg=COLORS["occupied_text"] if box_sample_count else COLORS["muted"], font=("Microsoft YaHei UI", 8, "bold"), padx=10, pady=4).pack(side="left", padx=12)
            tk.Label(details, text=f"位置：第 {column} 列 / 第 {layer} 层    ·    盒内：{box_sample_count} 个已用孔位", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(5, 12))
            actions = tk.Frame(inside, bg=COLORS["panel"], width=190)
            actions.pack(side="right", fill="y", padx=(24, 0))
            actions.pack_propagate(False)
            action_button = RoundedButton(
                actions,
                text="打开细胞冻存盒",
                command=lambda value=ucode: self.show_box(value),
                fill=COLORS["primary"],
                foreground="white",
                hover_fill=COLORS["primary_dark"],
                border=COLORS["primary"],
                canvas_bg=COLORS["panel"],
                height=40,
                radius=10,
            )
            action_button.pack(side="bottom", fill="x")

        hint = self._panel(page)
        hint.pack(fill="x", pady=(14, 0))
        tk.Label(hint, text="编号规则", bg=COLORS["panel"], fg=COLORS["primary"], font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=(18, 10), pady=12)
        tk.Label(hint, text="41 = 第4列 / 第1层 / 对应一个冻存盒位", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(side="left", pady=12)

    def toggle_compartment_batch_mode(self, column: int, layer: int) -> None:
        self.compartment_batch_mode = not self.compartment_batch_mode
        if not self.compartment_batch_mode:
            self.selected_compartment_units.clear()
        self.show_compartment(column, layer)

    def toggle_compartment_unit_selection(self, code: str) -> None:
        if code in self.selected_compartment_units:
            self.selected_compartment_units.remove(code)
        else:
            self.selected_compartment_units.add(code)
        self._refresh_compartment_unit_selection(code)
        self._refresh_compartment_batch_status()

    def clear_compartment_batch_selection(self) -> None:
        self.selected_compartment_units.clear()
        for code in tuple(getattr(self, "compartment_unit_widgets", {})):
            self._refresh_compartment_unit_selection(code)
        self._refresh_compartment_batch_status()

    def _selected_compartment_unit_codes(self, *, occupied_only: bool) -> list[str]:
        return [
            code
            for code in sorted(self.selected_compartment_units)
            if not occupied_only or self.repository.get(code).occupied
        ]

    def _refresh_compartment_batch_status(self) -> None:
        label = getattr(self, "compartment_batch_status_label", None)
        if label is None or not label.winfo_exists():
            return
        occupied = self._selected_compartment_unit_codes(occupied_only=True)
        label.configure(text=f"已选 {len(self.selected_compartment_units)} 个冻存盒 · {len(occupied)} 个有细胞冻存盒")

    def _refresh_compartment_unit_selection(self, code: str) -> None:
        widgets = getattr(self, "compartment_unit_widgets", {}).get(code)
        if widgets is None:
            return
        card, button = widgets
        if not card.winfo_exists() or not button.winfo_exists():
            return
        selected = code in self.selected_compartment_units
        record = self.repository.get(code)
        card.configure(
            highlightthickness=2 if selected else 1,
            highlightbackground=COLORS["primary"] if selected else ("#BFE4D2" if record.occupied else COLORS["line"]),
        )
        button.set_text("取消选择" if selected else "选择冻存盒")
        button.set_palette(
            fill=COLORS["primary"] if selected else COLORS["primary_soft"],
            foreground="white" if selected else COLORS["primary"],
            hover_fill=COLORS["primary_dark"] if selected else "#DCE8FF",
            border=COLORS["primary"] if selected else COLORS["primary_soft"],
        )

    def compartment_batch_clear(self) -> None:
        codes = self._selected_compartment_unit_codes(occupied_only=True)
        if not codes:
            self._notify("没有可处理记录", "请选择至少一个有细胞的冻存盒。", danger=True)
            return
        if not self._ask_confirm("确认批量清空", f"确定清空选中的 {len(codes)} 条记录吗？\n此操作不可撤销。", danger=True):
            return
        if not self._ask_confirm("再次确认清除", "第二次确认：确定继续清除这些记录吗？", danger=True):
            return
        try:
            removed = self.repository.batch_clear(codes)
        except OSError as exc:
            self._notify("批量清空失败", str(exc), danger=True)
            return
        compartment = self.current_compartment
        self.selected_compartment_units.clear()
        self.compartment_batch_mode = False
        self._update_sidebar_summary()
        self._notify("批量清空完成", f"已清空 {removed} 条记录。")
        if compartment:
            self.show_compartment(*compartment)

    def compartment_batch_move(self) -> None:
        codes = self._selected_compartment_unit_codes(occupied_only=True)
        if not codes:
            self._notify("没有可处理记录", "请选择至少一个有细胞的冻存盒。", danger=True)
            return
        target_name = self._ask_text("批量移动", "请输入目标液氮罐的完整名称：", self.repository.freezer_name)
        if target_name is None:
            return
        target_id = next((freezer_id for freezer_id, name in self.repository.list_freezers() if name.casefold() == target_name.strip().casefold()), None)
        if target_id is None:
            self._notify("目标液氮罐不存在", "请输入侧栏中显示的完整液氮罐名称。", danger=True)
            return
        start_code = self._ask_text("目标起始盒位", "程序会从该位置开始依次寻找空位。", "11")
        if start_code is None or not self._ask_confirm("确认移动", f"将 {len(codes)} 条记录移动到“{target_name}”，并从 {start_code} 开始寻找空位吗？"):
            return
        try:
            self.repository.create_backup("冻存盒批量移动前")
            mappings = self.repository.batch_move(codes, target_id, start_code.strip())
        except (OSError, ValueError) as exc:
            self._notify("移动失败", str(exc), danger=True)
            return
        compartment = self.current_compartment
        self.selected_compartment_units.clear()
        self.compartment_batch_mode = False
        self._update_sidebar_summary()
        summary = "、".join(f"{source}→{target}" for source, target in mappings[:6])
        self._notify("批量移动完成", f"已移动 {len(mappings)} 条记录。\n{summary}{'……' if len(mappings) > 6 else ''}")
        if compartment:
            self.show_compartment(*compartment)

    def compartment_batch_export(self) -> None:
        codes = self._selected_compartment_unit_codes(occupied_only=True)
        if not codes:
            self._notify("没有可导出记录", "请选择至少一个有细胞的冻存盒。", danger=True)
            return
        selected = filedialog.asksaveasfilename(parent=self, title="导出冻存盒位选中记录", initialdir=str(Path(__file__).parent), initialfile=f"{self.repository.freezer_name}_{codes[0]}_选中记录.xlsx", defaultextension=".xlsx", filetypes=(("Excel 工作簿", "*.xlsx"),))
        if not selected:
            return
        try:
            export_freezer_workbook(Path(selected), [(f"{self.repository.freezer_name}_{codes[0]}", {code: self.repository.get(code) for code in codes})])
        except (OSError, ValueError) as exc:
            self._notify("导出失败", str(exc), danger=True)
            return
        self._notify("导出完成", f"已导出 {len(codes)} 条记录：{Path(selected).name}")

    def show_box(self, code: str) -> None:
        if not self.repository.is_storage_code(code):
            return
        current_freezer_id = self.repository.current_freezer_id
        changed_box = (
            self.current_box_code != code
            or self.current_box_freezer_id != current_freezer_id
        )
        opening_box_page = changed_box or not self.box_page_visible
        self._set_active_nav("overview")
        self.page_title_var.set(f"细胞冻存盒 · {self.repository.freezer_name} / {code}")
        pending_highlight = self.pending_search_highlight
        if (
            pending_highlight
            and pending_highlight[0] == self.repository.current_freezer_id
            and pending_highlight[1] == code
        ):
            self.box_search_highlights = {
                position
                for position in pending_highlight[2]
                if self.repository.get_box_sample(code, position).occupied
            }
            self.pending_search_highlight = None
        elif changed_box:
            self.box_search_highlights.clear()
        recommendation = self.pending_empty_recommendation
        use_recommendation = bool(
            recommendation
            and recommendation[0] == self.repository.current_freezer_id
            and recommendation[1] == code
        )
        if use_recommendation:
            still_empty = [
                position for position in recommendation[2]
                if not self.repository.get_box_sample(code, position).occupied
            ]
            self.box_batch_mode = True
            self.box_quick_move_mode = False
            self.selected_box_positions = set(still_empty)
            self.box_empty_highlights = set(still_empty)
            self.pending_empty_recommendation = None
        elif changed_box:
            self.box_batch_mode = False
            self.box_quick_move_mode = False
            self.selected_box_positions.clear()
            self.box_empty_highlights.clear()
            self._clear_box_drag_state()
        self.current_box_code = code
        self.current_box_freezer_id = current_freezer_id
        self.clear_content(preserve_box_modes=True)
        self.box_page_visible = True

        page = ttk.Frame(self.content, style="Page.TFrame")
        page.pack(fill="both", expand=True)
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)
        layout = self.repository.get_box_layout(code)
        samples = self.repository.get_box_samples(code)

        nav = ttk.Frame(page, style="Page.TFrame")
        nav.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 14))
        parsed = parse_unit_code(code)
        if self.box_search_return_mode == "basic":
            back_text = "←  返回搜索结果"
            back_command = self.show_search_results
        elif self.box_search_return_mode == "advanced":
            back_text = "←  返回高级搜索"
            back_command = lambda: self.show_advanced_search_page(run_search=True)
        elif self.box_search_return_mode == "empty":
            back_text = "←  返回查找空位"
            back_command = lambda: self.show_empty_search_page(run_search=True)
        elif self.box_search_return_mode == "locations":
            back_text = "←  返回细胞位置"
            back_command = self.return_to_inventory_locations
        else:
            back_text = f"←  返回冻存盒位 {code}"
            back_command = lambda: self.show_compartment(*parsed)
        RoundedButton(nav, text=back_text, command=lambda target=back_command: self._run_box_back_command(target), fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["bg"], width=160, height=38, radius=10).pack(side="left")
        title_box = ttk.Frame(nav, style="Page.TFrame")
        title_box.pack(side="left", padx=18)
        ttk.Label(title_box, text=f"细胞冻存盒 {code}", style="Title.TLabel").pack(anchor="w")
        self.box_title_summary_label = ttk.Label(title_box, text=f"盒位 {code} · {layout.rows}×{layout.columns} · 已使用 {len(samples)}/{layout.rows * layout.columns}", style="PageMuted.TLabel")
        self.box_title_summary_label.pack(anchor="w")
        if self.box_search_highlights:
            tk.Label(
                title_box,
                text=f"查找结果：已高亮 {len(self.box_search_highlights)} 个匹配孔位",
                bg=COLORS["bg"],
                fg="#B76E00",
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(anchor="w", pady=(3, 0))
        if self.box_empty_highlights:
            tk.Label(
                title_box,
                text=f"推荐空位：已高亮并选中 {len(self.box_empty_highlights)} 个孔位",
                bg=COLORS["bg"],
                fg=COLORS["primary"],
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(anchor="w", pady=(3, 0))
        RoundedButton(nav, text="退出批量" if self.box_batch_mode else "批量选择", command=lambda: self.toggle_box_batch_mode(code), fill="#E8EDF4" if self.box_batch_mode else COLORS["primary_soft"], foreground=COLORS["text"] if self.box_batch_mode else COLORS["primary"], hover_fill="#DCE4EE" if self.box_batch_mode else "#DCE8FF", border="#E8EDF4" if self.box_batch_mode else COLORS["primary_soft"], canvas_bg=COLORS["bg"], width=105, height=38, radius=10).pack(side="right")
        RoundedButton(nav, text="设置布局", command=lambda: self.configure_box_dialog(code), fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["bg"], width=100, height=38, radius=10).pack(side="right", padx=9)
        if not self.box_batch_mode:
            RoundedButton(
                nav,
                text="关闭快捷移动" if self.box_quick_move_mode else "快捷移动",
                command=lambda: self.toggle_box_quick_move_mode(code),
                fill=COLORS["primary"] if self.box_quick_move_mode else COLORS["primary_soft"],
                foreground="white" if self.box_quick_move_mode else COLORS["primary"],
                hover_fill=COLORS["primary_dark"] if self.box_quick_move_mode else "#DCE8FF",
                border=COLORS["primary"] if self.box_quick_move_mode else COLORS["primary_soft"],
                canvas_bg=COLORS["bg"],
                width=118,
                height=38,
                radius=10,
            ).pack(side="right")

        toolbar = self._panel(page)
        toolbar.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 14))
        self.box_batch_status_label = tk.Label(toolbar, text=self._box_status_text(code), bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"))
        self.box_batch_status_label.pack(side="left", padx=18, pady=13)
        if self.box_batch_mode:
            RoundedButton(toolbar, text="全选", command=lambda: self.select_all_box_positions(code), fill=COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#DCE8FF", border=COLORS["primary_soft"], canvas_bg=COLORS["panel"], width=72, height=34, radius=9).pack(side="right", padx=(5, 14), pady=8)
            RoundedButton(toolbar, text="清空选择", command=lambda: self.clear_box_selection(code), fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=88, height=34, radius=9).pack(side="right", padx=5, pady=8)
            RoundedButton(toolbar, text="清除", command=lambda: self.batch_clear_box_positions(code), fill="#FCECEC", foreground=COLORS["danger"], hover_fill="#F7DADA", border="#F3D1D1", canvas_bg=COLORS["panel"], width=78, height=34, radius=9).pack(side="right", padx=5, pady=8)
            RoundedButton(toolbar, text="批量出库", command=lambda: self.batch_checkout_box_positions(code), fill="#FFF0DE", foreground="#A85B0B", hover_fill="#FFE1BD", border="#FFE1BD", canvas_bg=COLORS["panel"], width=88, height=34, radius=9).pack(side="right", padx=5, pady=8)
            RoundedButton(toolbar, text="批量入库", command=lambda: self.batch_edit_box_positions(code), fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["panel"], width=88, height=34, radius=9).pack(side="right", padx=5, pady=8)
        else:
            zoom_controls = tk.Frame(toolbar, bg=COLORS["panel"])
            zoom_controls.pack(side="right", padx=(6, 14), pady=7)
            RoundedButton(zoom_controls, text="− 缩小", command=lambda: self._change_box_grid_zoom(-1), fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=70, height=32, radius=8, font=("Microsoft YaHei UI", 8)).pack(side="left")
            self.box_zoom_value_label = tk.Label(zoom_controls, text=f"{self._box_zoom_percent()}%", width=6, bg=COLORS["panel"], fg=COLORS["primary"], font=("Microsoft YaHei UI", 9, "bold"))
            self.box_zoom_value_label.pack(side="left", padx=3)
            RoundedButton(zoom_controls, text="＋ 放大", command=lambda: self._change_box_grid_zoom(1), fill=COLORS["primary_soft"], foreground=COLORS["primary"], hover_fill="#DCE8FF", border=COLORS["primary_soft"], canvas_bg=COLORS["panel"], width=70, height=32, radius=8, font=("Microsoft YaHei UI", 8, "bold")).pack(side="left")
            RoundedButton(zoom_controls, text="适应窗口", command=lambda: self.fit_box_grid_to_view(code), fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=78, height=32, radius=8, font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(7, 0))

        box_panel = self._panel(page)
        box_panel.grid(row=2, column=0, sticky="nsew", padx=28, pady=(0, 18))
        legend = tk.Frame(box_panel, bg=COLORS["panel"], height=42)
        self.box_legend_widget = legend
        legend.pack(side="bottom", fill="x", padx=22, pady=(2, 8))
        legend.pack_propagate(False)
        for label, color in (("空孔", "#EEF2F7"), ("已有细胞", COLORS["occupied"]), ("查找命中", "#FFD166"), ("可移动目标", "#DCE8FF"), ("已选择", COLORS["primary"])):
            tk.Label(legend, text=f"  {label}  ", bg=color, fg="white" if color == COLORS["primary"] else COLORS["text"], font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(0, 8), pady=8)
        self.box_move_undo_button = None
        if self.box_quick_move_mode:
            self.box_move_undo_button = RoundedButton(
                legend,
                text="撤销上次移动" if self._box_undo_available(code) else "暂无可撤销移动",
                command=self.undo_box_quick_move,
                fill=COLORS["primary_soft"] if self._box_undo_available(code) else "#EEF2F7",
                foreground=COLORS["primary"] if self._box_undo_available(code) else COLORS["muted"],
                hover_fill="#DCE8FF" if self._box_undo_available(code) else "#EEF2F7",
                border=COLORS["primary_soft"] if self._box_undo_available(code) else "#EEF2F7",
                canvas_bg=COLORS["panel"],
                width=112,
                height=30,
                radius=8,
                font=("Microsoft YaHei UI", 8, "bold"),
            )
            self.box_move_undo_button.pack(side="right", pady=5)

        grid_host = tk.Frame(box_panel, bg=COLORS["panel"])
        grid_host.pack(side="top", fill="both", expand=True, padx=18, pady=(12, 4))
        grid_host.rowconfigure(0, weight=1)
        grid_host.columnconfigure(0, weight=1)
        grid_canvas = tk.Canvas(grid_host, bg=COLORS["panel"], highlightthickness=0, borderwidth=0)
        grid_canvas.grid(row=0, column=0, sticky="nsew")
        horizontal_scrollbar = ttk.Scrollbar(grid_host, orient="horizontal", command=grid_canvas.xview, style="Flat.Horizontal.TScrollbar")
        vertical_scrollbar = ttk.Scrollbar(grid_host, orient="vertical", command=grid_canvas.yview, style="Flat.Vertical.TScrollbar")
        grid_canvas.configure(xscrollcommand=horizontal_scrollbar.set, yscrollcommand=vertical_scrollbar.set)
        grid = tk.Frame(grid_canvas, bg=COLORS["panel"])
        grid_window = grid_canvas.create_window((0, 0), window=grid, anchor="nw")
        self.box_grid_widget = grid
        self.box_grid_canvas = grid_canvas
        self.box_grid_canvas_window = grid_window
        self.box_horizontal_scrollbar = horizontal_scrollbar
        self.box_vertical_scrollbar = vertical_scrollbar
        grid.bind("<Configure>", self._sync_box_grid_scroll_region)
        grid_canvas.bind("<Configure>", self._sync_box_grid_viewport)
        self.box_position_buttons: dict[str, tk.Button] = {}
        self.box_column_labels = []
        self.box_row_labels = []
        metrics = self._box_zoom_metrics(layout.columns)
        tk.Label(grid, text="", bg=COLORS["panel"], width=3).grid(row=0, column=0)
        for column in range(1, layout.columns + 1):
            label = tk.Label(grid, text=str(column), bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", metrics["label_font"], "bold"), width=metrics["width"])
            label.grid(row=0, column=column, pady=(0, 5))
            self.box_column_labels.append(label)
        for row in range(1, layout.rows + 1):
            row_label = self.repository.box_position(row, 1)[:-1]
            label = tk.Label(grid, text=row_label, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", metrics["label_font"], "bold"), width=3)
            label.grid(row=row, column=0, padx=(0, 5))
            self.box_row_labels.append(label)
            for column in range(1, layout.columns + 1):
                position = self.repository.box_position(row, column)
                button = tk.Button(
                    grid,
                    text=self._box_button_text(position, samples.get(position)),
                    command=lambda value=position: self.toggle_box_position(code, value) if self.box_batch_mode else self.edit_box_position(code, value),
                    relief="flat",
                    borderwidth=0,
                    cursor="hand2",
                    width=metrics["width"],
                    height=metrics["height"],
                    wraplength=metrics["wrap"],
                    font=("Microsoft YaHei UI", metrics["font"], "bold"),
                )
                button.grid(row=row, column=column, padx=3, pady=3, sticky="nsew")
                self.box_position_buttons[position] = button
                button._box_position = position  # type: ignore[attr-defined]
                button.bind("<ButtonPress-1>", lambda event, value=position: self._start_box_drag(event, code, value), add="+")
                button.bind("<B1-Motion>", lambda event: self._update_box_drag(event, code), add="+")
                button.bind("<ButtonRelease-1>", lambda event: self._finish_box_drag(event, code), add="+")
                self._style_box_position(code, position)

        if opening_box_page:
            self._schedule_box_grid_auto_fit(code)
        else:
            self.after_idle(self._update_box_horizontal_scrollbar)

    def toggle_box_quick_move_mode(self, code: str) -> None:
        if self.box_batch_mode:
            return
        self.box_quick_move_mode = not self.box_quick_move_mode
        self._clear_box_drag_state(code)
        self.show_box(code)

    def _clear_box_drag_state(self, code: str | None = None) -> None:
        source = self.box_drag_source
        target = self.box_drag_target
        self.box_drag_source = None
        self.box_drag_target = None
        if code is None or self.current_box_code != code:
            return
        for position in {source, target} - {None}:
            self._style_box_position(code, str(position))

    def _box_position_at_pointer(self, event: tk.Event) -> str | None:
        try:
            pointer_x = int(event.x_root)
            pointer_y = int(event.y_root)
        except (AttributeError, TypeError, ValueError):
            return None
        # Checking the cell rectangles first is more reliable while Tk has an
        # implicit mouse grab on the source button during a drag.
        for position, button in getattr(self, "box_position_buttons", {}).items():
            try:
                left = button.winfo_rootx()
                top = button.winfo_rooty()
                if left <= pointer_x < left + button.winfo_width() and top <= pointer_y < top + button.winfo_height():
                    return position
            except tk.TclError:
                continue
        try:
            widget: tk.Misc | None = self.winfo_containing(pointer_x, pointer_y)
        except tk.TclError:
            return None
        while widget is not None:
            position = getattr(widget, "_box_position", None)
            if isinstance(position, str):
                return position
            if widget is self.box_grid_widget:
                break
            try:
                widget = widget.master
            except (AttributeError, tk.TclError):
                break
        return None

    def _start_box_drag(self, _event: tk.Event, code: str, position: str) -> str | None:
        if self.box_batch_mode or not self.box_quick_move_mode or self.current_box_code != code:
            return None
        if not self.repository.get_box_sample(code, position).occupied:
            return None
        self._clear_box_drag_state(code)
        self.box_drag_source = position
        self.box_drag_target = position
        self._style_box_position(code, position)
        status = getattr(self, "box_batch_status_label", None)
        if status is not None and status.winfo_exists():
            status.configure(text=f"正在移动 {position} · 拖到同盒空孔位后松开鼠标")
        # Keep the normal Button class binding active. If the mouse is released
        # on this same cell, Tk will still treat it as a regular click and open
        # the editor; a release over another cell is handled as a move below.
        return None

    def _update_box_drag(self, event: tk.Event, code: str) -> str | None:
        if self.box_drag_source is None or self.current_box_code != code:
            return None
        target = self._box_position_at_pointer(event)
        if target == self.box_drag_target:
            return "break"
        previous = self.box_drag_target
        self.box_drag_target = target
        for position in {previous, target} - {None, self.box_drag_source}:
            self._style_box_position(code, str(position))
        self._style_box_position(code, self.box_drag_source)
        return "break"

    def _finish_box_drag(self, event: tk.Event, code: str) -> str | None:
        if self.box_drag_source is None or self.current_box_code != code:
            return None
        target = self._box_position_at_pointer(event)
        source = self.box_drag_source
        self.box_drag_target = target
        if target is None or target == source:
            self._clear_box_drag_state(code)
            self._restore_box_move_status(code)
            return None if target == source else "break"
        if self.repository.get_box_sample(code, target).occupied:
            self._clear_box_drag_state(code)
            status = getattr(self, "box_batch_status_label", None)
            if status is not None and status.winfo_exists():
                status.configure(text=f"{target} 已有细胞，未执行移动 · 请选择空孔位")
                self._schedule_box_move_hint_reset(code)
            return "break"

        try:
            moved_sample = self.repository.move_box_sample(code, source, target)
        except (OSError, ValueError) as exc:
            self._clear_box_drag_state(code)
            self._notify("移动失败", str(exc), danger=True)
            return "break"

        self.box_drag_source = None
        self.box_drag_target = None
        # Search highlighting belongs to the matched cell, not its old hole.
        if source in self.box_search_highlights:
            self.box_search_highlights.discard(source)
            self.box_search_highlights.add(target)
        self._set_box_move_undo(code, source, target, moved_sample)
        self._refresh_box_page_state(code, (source, target))
        self._update_sidebar_summary()
        status = getattr(self, "box_batch_status_label", None)
        if status is not None and status.winfo_exists():
            status.configure(text=f"已将“{moved_sample.sample_name}”从 {source} 移至 {target} · 可点击“撤销上次移动”恢复")
        return "break"

    def _finish_box_drag_outside_cell(self, event: tk.Event) -> str | None:
        code = self.current_box_code
        if self.box_drag_source is None or code is None:
            return None
        return self._finish_box_drag(event, code)

    def _schedule_box_move_hint_reset(self, code: str) -> None:
        if self.box_move_hint_job is not None:
            try:
                self.after_cancel(self.box_move_hint_job)
            except tk.TclError:
                pass
        self.box_move_hint_job = self.after(2200, lambda: self._restore_box_move_status(code))

    def _restore_box_move_status(self, code: str) -> None:
        self.box_move_hint_job = None
        if self.current_box_code != code:
            return
        status = getattr(self, "box_batch_status_label", None)
        if status is not None and status.winfo_exists():
            status.configure(text=self._box_status_text(code))

    def _set_box_move_undo(self, code: str, source: str, target: str, sample: BoxSample) -> None:
        self.box_move_undo = (self.repository.current_freezer_id, code, source, target, sample)
        self._sync_box_move_undo_button(code)

    def _box_undo_available(self, code: str) -> bool:
        undo = self.box_move_undo
        if undo is None:
            return False
        freezer_id, undo_code, source, target, sample = undo
        return bool(
            freezer_id == self.repository.current_freezer_id
            and undo_code == code
            and not self.repository.get_box_sample(code, source).occupied
            and self.repository.get_box_sample(code, target) == sample
        )

    def _clear_box_move_undo(self) -> None:
        self.box_move_undo = None
        self._sync_box_move_undo_button(self.current_box_code)

    def _sync_box_move_undo_button(self, code: str | None) -> None:
        button = getattr(self, "box_move_undo_button", None)
        if button is None or not button.winfo_exists():
            return
        available = bool(code and self._box_undo_available(code))
        button.set_text("撤销上次移动" if available else "暂无可撤销移动")
        button.set_palette(
            fill=COLORS["primary_soft"] if available else "#EEF2F7",
            foreground=COLORS["primary"] if available else COLORS["muted"],
            hover_fill="#DCE8FF" if available else "#EEF2F7",
            border=COLORS["primary_soft"] if available else "#EEF2F7",
        )

    def undo_box_quick_move(self) -> None:
        undo = self.box_move_undo
        if undo is None:
            return
        freezer_id, code, source, target, sample = undo
        if freezer_id != self.repository.current_freezer_id or self.current_box_code != code:
            self._clear_box_move_undo()
            return
        if self.repository.get_box_sample(code, source).occupied or self.repository.get_box_sample(code, target) != sample:
            self._clear_box_move_undo()
            self._notify("无法撤销", "孔位内容已经发生变化，为避免覆盖数据，本次撤销已取消。", danger=True)
            return
        try:
            self.repository.move_box_sample(code, target, source, undo=True)
        except (OSError, ValueError) as exc:
            self._clear_box_move_undo()
            self._notify("撤销失败", str(exc), danger=True)
            return
        # Move a search hit back with the cell so all other highlighted holes
        # keep exactly the same visual state.
        if target in self.box_search_highlights:
            self.box_search_highlights.discard(target)
            self.box_search_highlights.add(source)
        self._clear_box_move_undo()
        self._refresh_box_page_state(code, (source, target))
        self._update_sidebar_summary()
        status = getattr(self, "box_batch_status_label", None)
        if status is not None and status.winfo_exists():
            status.configure(text=f"已撤销：细胞已从 {target} 移回 {source}")
            self._schedule_box_move_hint_reset(code)

    @staticmethod
    def _base_box_cell_width(columns: int) -> int:
        return 7 if columns <= 10 else (5 if columns <= 14 else 3)

    def _box_zoom_metrics(self, columns: int) -> dict[str, int]:
        base_width = self._base_box_cell_width(columns)
        level = self.box_zoom_level
        width = max(3, base_width + level * 2)
        height = max(1, 2 + level // 2)
        font_size = max(6, 7 + level)
        return {
            "width": width,
            "height": height,
            "font": font_size,
            "label_font": max(7, 8 + level),
            "wrap": max(28, width * max(7, font_size - 1)),
            "name_length": max(5, width + level * 3),
        }

    def _box_zoom_percent(self) -> int:
        return 100 + self.box_zoom_level * 20

    def _box_button_text(self, position: str, sample: BoxSample | None) -> str:
        if sample and sample.occupied:
            name = sample.sample_name.strip()
            layout = self.repository.get_box_layout(self.current_box_code) if self.current_box_code else None
            limit = self._box_zoom_metrics(layout.columns)["name_length"] if layout else 7
            return f"{position}\n{name[:limit]}{'…' if len(name) > limit else ''}"
        return position

    def _sync_box_grid_scroll_region(self, _event: tk.Event | None = None) -> None:
        canvas = self.box_grid_canvas
        grid = self.box_grid_widget
        if canvas is None or grid is None or not canvas.winfo_exists() or not grid.winfo_exists():
            return
        canvas.configure(scrollregion=canvas.bbox("all"))
        self.after_idle(self._update_box_horizontal_scrollbar)

    def _sync_box_grid_viewport(self, event: tk.Event) -> None:
        canvas = self.box_grid_canvas
        grid = self.box_grid_widget
        window = self.box_grid_canvas_window
        if canvas is None or grid is None or window is None:
            return
        canvas.itemconfigure(window, width=max(event.width, grid.winfo_reqwidth()))
        self.after_idle(self._update_box_horizontal_scrollbar)

    def _update_box_horizontal_scrollbar(self) -> None:
        canvas = self.box_grid_canvas
        grid = self.box_grid_widget
        horizontal = self.box_horizontal_scrollbar
        vertical = self.box_vertical_scrollbar
        window = self.box_grid_canvas_window
        if canvas is None or grid is None or horizontal is None or vertical is None or window is None:
            return
        if not canvas.winfo_exists() or not grid.winfo_exists() or not horizontal.winfo_exists() or not vertical.winfo_exists():
            return
        content_width = grid.winfo_reqwidth()
        content_height = grid.winfo_reqheight()
        viewport_width = canvas.winfo_width()
        viewport_height = canvas.winfo_height()
        canvas.itemconfigure(window, width=max(viewport_width, content_width))
        canvas.configure(scrollregion=canvas.bbox("all"))
        if content_width > viewport_width + 2:
            horizontal.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        else:
            horizontal.grid_remove()
            canvas.xview_moveto(0.0)
        if content_height > viewport_height + 2:
            vertical.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        else:
            vertical.grid_remove()
            canvas.yview_moveto(0.0)

    def _on_box_grid_vertical_scroll(self, event: tk.Event) -> str | None:
        canvas = self.box_grid_canvas
        grid = self.box_grid_widget
        if canvas is None or grid is None or not canvas.winfo_exists() or not grid.winfo_exists():
            return None
        try:
            state = int(getattr(event, "state", 0))
        except (TypeError, ValueError):
            state = 0
        if state & 0x0001:  # Shift is handled by the horizontal binding.
            return None
        current = getattr(event, "widget", None)
        inside_grid = False
        while current is not None:
            if current is grid or current is canvas:
                inside_grid = True
                break
            try:
                current = current.master
            except (AttributeError, tk.TclError):
                break
        if not inside_grid or grid.winfo_reqheight() <= canvas.winfo_height() + 2:
            return None
        if getattr(event, "num", None) == 4:
            direction = -1
        elif getattr(event, "num", None) == 5:
            direction = 1
        else:
            try:
                delta = int(getattr(event, "delta", 0))
            except (TypeError, ValueError):
                delta = 0
            if not delta:
                return None
            direction = -1 if delta > 0 else 1
        canvas.yview_scroll(direction, "units")
        return "break"

    def _on_box_grid_horizontal_scroll(self, event: tk.Event) -> str | None:
        canvas = self.box_grid_canvas
        grid = self.box_grid_widget
        if canvas is None or grid is None or not canvas.winfo_exists() or not grid.winfo_exists():
            return None
        current = getattr(event, "widget", None)
        inside_grid = False
        while current is not None:
            if current is grid or current is canvas:
                inside_grid = True
                break
            try:
                current = current.master
            except (AttributeError, tk.TclError):
                break
        if not inside_grid or grid.winfo_reqwidth() <= canvas.winfo_width() + 2:
            return None
        try:
            delta = int(getattr(event, "delta", 0))
        except (TypeError, ValueError):
            delta = 0
        if delta:
            canvas.xview_scroll(-1 if delta > 0 else 1, "units")
        return "break"

    def _change_box_grid_zoom(self, direction: int) -> None:
        if self.current_box_code is None:
            return
        if self.box_auto_fit_job is not None:
            try:
                self.after_cancel(self.box_auto_fit_job)
            except tk.TclError:
                pass
            self.box_auto_fit_job = None
        new_level = max(-1, min(5, self.box_zoom_level + (1 if direction > 0 else -1)))
        if new_level == self.box_zoom_level:
            return
        self.box_zoom_level = new_level
        self._apply_box_grid_zoom(self.current_box_code)

    def _schedule_box_grid_auto_fit(self, code: str) -> None:
        if self.box_auto_fit_job is not None:
            try:
                self.after_cancel(self.box_auto_fit_job)
            except tk.TclError:
                pass
        # Run before the staged page is committed, so measurement is never
        # presented as a visible sequence of intermediate zoom levels.
        self.box_auto_fit_job = self.after_idle(lambda: self.fit_box_grid_to_view(code))

    def fit_box_grid_to_view(self, code: str, attempt: int = 0) -> None:
        """Choose the largest zoom that fits the current grid viewport."""
        self.box_auto_fit_job = None
        if not self.box_page_visible or self.current_box_code != code:
            return
        canvas = self.box_grid_canvas
        grid = self.box_grid_widget
        if canvas is None or grid is None or not canvas.winfo_exists() or not grid.winfo_exists():
            return
        self.update_idletasks()
        available_width = canvas.winfo_width()
        available_height = canvas.winfo_height()
        if (available_width < 120 or available_height < 120) and attempt < 8:
            self.box_auto_fit_job = self.after(40, lambda: self.fit_box_grid_to_view(code, attempt + 1))
            return

        layout = self.repository.get_box_layout(code)
        cache_keys = [(layout.rows, layout.columns, level) for level in range(-1, 6)]
        if any(key not in self.box_zoom_size_cache for key in cache_keys):
            for level, cache_key in zip(range(-1, 6), cache_keys):
                self.box_zoom_level = level
                self._apply_box_grid_zoom(code)
                grid.update_idletasks()
                self.box_zoom_size_cache[cache_key] = (
                    grid.winfo_reqwidth(),
                    grid.winfo_reqheight(),
                )

        # A small vertical overflow is preferable to leaving a large unused
        # area. This typically keeps a full-screen 9x9 box at 160% while still
        # rejecting the next level when it would require substantial scrolling.
        height_tolerance = max(18, min(72, available_height // 10))
        chosen_level = -1
        for level in range(5, -2, -1):
            required_width, required_height = self.box_zoom_size_cache[
                (layout.rows, layout.columns, level)
            ]
            if (
                required_width <= available_width - 4
                and required_height <= available_height + height_tolerance
            ):
                chosen_level = level
                break
        self.box_zoom_level = chosen_level
        self._apply_box_grid_zoom(code)
        canvas.xview_moveto(0.0)
        canvas.yview_moveto(0.0)
        self.after_idle(self._update_box_horizontal_scrollbar)

    def _apply_box_grid_zoom(self, code: str) -> None:
        layout = self.repository.get_box_layout(code)
        metrics = self._box_zoom_metrics(layout.columns)
        for label in self.box_column_labels:
            if label.winfo_exists():
                label.configure(width=metrics["width"], font=("Microsoft YaHei UI", metrics["label_font"], "bold"))
        for label in self.box_row_labels:
            if label.winfo_exists():
                label.configure(font=("Microsoft YaHei UI", metrics["label_font"], "bold"))
        for position, button in self.box_position_buttons.items():
            if button.winfo_exists():
                sample = self.repository.get_box_sample(code, position)
                button.configure(
                    width=metrics["width"],
                    height=metrics["height"],
                    wraplength=metrics["wrap"],
                    font=("Microsoft YaHei UI", metrics["font"], "bold"),
                    text=self._box_button_text(position, sample),
                )
        value_label = getattr(self, "box_zoom_value_label", None)
        if value_label is not None and value_label.winfo_exists():
            value_label.configure(text=f"{self._box_zoom_percent()}%")
        grid = self.box_grid_widget
        if grid is not None and grid.winfo_exists():
            grid.update_idletasks()
        self._sync_box_grid_scroll_region()

    def _box_status_text(self, code: str) -> str:
        if self.box_batch_mode:
            occupied = sum(self.repository.get_box_sample(code, position).occupied for position in self.selected_box_positions)
            return f"已选 {len(self.selected_box_positions)} 个孔位 · 其中 {occupied} 个已有细胞"
        layout = self.repository.get_box_layout(code)
        used = len(self.repository.get_box_samples(code))
        status = f"盒内库存：{used} 个已用孔位 · {layout.rows * layout.columns - used} 个空孔"
        if self.box_quick_move_mode:
            status += " · 快捷移动已开启：直接拖动已有细胞到空孔"
        return status

    def _style_box_position(self, code: str, position: str) -> None:
        button = getattr(self, "box_position_buttons", {}).get(position)
        if button is None or not button.winfo_exists():
            return
        selected = self.box_batch_mode and position in self.selected_box_positions
        sample = self.repository.get_box_sample(code, position)
        if self.box_drag_source == position:
            bg, fg, active = "#8CB8FF", "white", COLORS["primary"]
        elif self.box_drag_source is not None and self.box_drag_target == position:
            if sample.occupied:
                bg, fg, active = "#F8CACA", COLORS["danger"], "#F3B7B7"
            else:
                bg, fg, active = "#B8D2FF", COLORS["primary_dark"], "#A8C6FA"
        elif selected:
            bg, fg, active = COLORS["primary"], "white", COLORS["primary_dark"]
        elif position in self.box_search_highlights and sample.occupied:
            bg, fg, active = "#FFD166", "#513500", "#F4BD3D"
        elif sample.occupied:
            bg, fg, active = COLORS["occupied"], COLORS["occupied_text"], "#D7EFE5"
        else:
            bg = "#EAF1FF" if self.box_quick_move_mode else "#EEF2F7"
            fg, active = COLORS["muted"], "#D6E4FC" if self.box_quick_move_mode else "#DEE7F1"
        cursor = "fleur" if self.box_quick_move_mode and sample.occupied else "hand2"
        button.configure(bg=bg, fg=fg, activebackground=active, activeforeground=fg, cursor=cursor, text=self._box_button_text(position, sample))

    def _refresh_box_page_state(self, code: str, positions: list[str] | tuple[str, ...] = ()) -> None:
        """Refresh changed box cells and counters without rebuilding the 9×9 page."""
        if self.current_box_code != code:
            return
        for position in positions:
            self._style_box_position(code, position)
        layout = self.repository.get_box_layout(code)
        used = len(self.repository.get_box_samples(code))
        title_label = getattr(self, "box_title_summary_label", None)
        if title_label is not None and title_label.winfo_exists():
            title_label.configure(text=f"盒位 {code} · {layout.rows}×{layout.columns} · 已使用 {used}/{layout.rows * layout.columns}")
        status_label = getattr(self, "box_batch_status_label", None)
        if status_label is not None and status_label.winfo_exists():
            status_label.configure(text=self._box_status_text(code))

    def toggle_box_batch_mode(self, code: str) -> None:
        self.box_batch_mode = not self.box_batch_mode
        self.box_quick_move_mode = False
        self._clear_box_drag_state(code)
        if not self.box_batch_mode:
            self.selected_box_positions.clear()
        self.show_box(code)

    def toggle_box_position(self, code: str, position: str) -> None:
        if position in self.selected_box_positions:
            self.selected_box_positions.remove(position)
        else:
            self.selected_box_positions.add(position)
        self._style_box_position(code, position)
        if self.box_batch_status_label.winfo_exists():
            self.box_batch_status_label.configure(text=self._box_status_text(code))

    def select_all_box_positions(self, code: str) -> None:
        layout = self.repository.get_box_layout(code)
        self.selected_box_positions = {self.repository.box_position(row, column) for row in range(1, layout.rows + 1) for column in range(1, layout.columns + 1)}
        for position in self.selected_box_positions:
            self._style_box_position(code, position)
        self.box_batch_status_label.configure(text=self._box_status_text(code))

    def clear_box_selection(self, code: str) -> None:
        previous = tuple(self.selected_box_positions)
        self.selected_box_positions.clear()
        for position in previous:
            self._style_box_position(code, position)
        self.box_batch_status_label.configure(text=self._box_status_text(code))

    def configure_box_dialog(self, code: str) -> None:
        layout = self.repository.get_box_layout(code)
        selected_layout = BoxLayoutDialog(self, layout.rows, layout.columns).show()
        if selected_layout is None:
            return
        rows, columns = selected_layout
        try:
            self.repository.configure_box(code, rows, columns, f"细胞冻存盒 {code}")
        except (OSError, ValueError) as exc:
            self._notify("布局保存失败", str(exc), danger=True)
            return
        self._update_sidebar_summary()
        self.box_page_visible = False
        self.show_box(code)

    def edit_box_position(self, code: str, position: str) -> None:
        sample = self.repository.get_box_sample(code, position)
        was_occupied = sample.occupied
        dialog_sample = sample if was_occupied else BoxSample(stored_date=date.today().isoformat())
        title = f"编辑细胞 · {code}/{position}" if was_occupied else f"入库细胞 · {code}/{position}"
        result = BoxSampleDialog(self, title, dialog_sample, allow_clear=was_occupied).show()
        if result is None:
            return
        if result.get("__action__") == "checkout":
            checkout = BatchOutboundDialog(
                self,
                1,
                f"{self.repository.freezer_name} / 冻存盒 {code}/{position}",
            ).show()
            if checkout is None:
                return
            operator, operation_date = checkout
            if not self._ask_confirm(
                "确认出库",
                f"确定将 {code}/{position} 中的细胞“{sample.sample_name}”出库吗？\n"
                f"出库人：{operator}\n出库日期：{operation_date}",
                danger=True,
            ):
                return
            try:
                self.repository.batch_checkout_box_samples(
                    code, [position], operator, operation_date
                )
            except (OSError, ValueError) as exc:
                self._notify("出库失败", str(exc), danger=True)
                return
            self._refresh_box_page_state(code, [position])
            self._update_sidebar_summary()
            self._notify("出库完成", f"{code}/{position} 已出库，并写入出入库登记。")
            return
        if result.get("__action__") == "clear":
            if not self._ask_confirm(
                "确认清空孔位",
                f"第一次确认：确定要清空 {code}/{position} 中的细胞“{sample.sample_name}”吗？",
                danger=True,
            ):
                return
            if not self._ask_confirm(
                "再次确认清空",
                f"第二次确认：清空后该孔位会变为空闲，但操作会保留在出入库登记中。\n\n确定继续吗？",
                danger=True,
            ):
                return
            try:
                self.repository.set_box_sample(code, position, BoxSample())
            except (OSError, ValueError) as exc:
                self._notify("清空失败", str(exc), danger=True)
                return
            self._refresh_box_page_state(code, [position])
            self._update_sidebar_summary()
            return
        if result["stored_date"] and not self._valid_date(result["stored_date"]):
            self._notify("日期格式不正确", "入库日期请填写为 YYYY-MM-DD 或 YYYYMMDD。", danger=True)
            return
        try:
            self.repository.set_box_sample(code, position, BoxSample(**result))
        except (OSError, ValueError) as exc:
            self._notify("保存失败", str(exc), danger=True)
            return
        self._refresh_box_page_state(code, [position])
        self._update_sidebar_summary()
        if not was_occupied:
            self._notify("入库完成", f"细胞已存入 {code}/{position}，并写入出入库登记。")

    def batch_edit_box_positions(self, code: str) -> None:
        positions = sorted(self.selected_box_positions)
        if not positions:
            self._notify("尚未选择孔位", "请先选择需要批量入库的孔位。", danger=True)
            return
        occupied = [
            position
            for position in positions
            if self.repository.get_box_sample(code, position).occupied
        ]
        if occupied:
            self._notify(
                "所选孔位已有细胞",
                f"以下孔位已有细胞，不能批量入库：\n{compact_box_positions(occupied)}\n\n"
                "请先出库或清除这些孔位，再重新进行批量入库。",
                danger=True,
            )
            return
        result = BoxSampleDialog(self, f"批量入库 · {len(positions)} 个孔位", BoxSample(stored_date=date.today().isoformat()), batch=True).show()
        if result is None:
            return
        if result["stored_date"] and not self._valid_date(result["stored_date"]):
            self._notify("日期格式不正确", "入库日期请填写为 YYYY-MM-DD 或 YYYYMMDD。", danger=True)
            return
        try:
            count = self.repository.batch_set_box_samples(code, positions, BoxSample(**result))
        except (OSError, ValueError) as exc:
            self._notify("批量入库失败", str(exc), danger=True)
            return
        changed_positions = tuple(positions)
        self.selected_box_positions.clear()
        self._refresh_box_page_state(code, changed_positions)
        self._update_sidebar_summary()
        self._notify("批量入库完成", f"已入库 {count} 支冻存管，并写入出入库登记。")

    def batch_checkout_box_positions(self, code: str) -> None:
        positions = sorted(self.selected_box_positions, key=self.repository.parse_box_position)
        if not positions:
            self._notify("尚未选择孔位", "请先选择需要批量出库的孔位。", danger=True)
            return
        empty = [position for position in positions if not self.repository.get_box_sample(code, position).occupied]
        if empty:
            self._notify(
                "所选孔位包含空孔",
                f"以下孔位没有细胞，不能出库：\n{compact_box_positions(empty)}\n\n请取消这些孔位后重新操作。",
                danger=True,
            )
            return
        result = BatchOutboundDialog(
            self,
            len(positions),
            f"{self.repository.freezer_name} / 冻存盒 {code}",
        ).show()
        if result is None:
            return
        operator, operation_date = result
        names = sorted({self.repository.get_box_sample(code, position).sample_name for position in positions})
        preview = "、".join(names[:5]) + (f" 等 {len(names)} 种" if len(names) > 5 else "")
        if not self._ask_confirm(
            "确认批量出库",
            f"将出库 {len(positions)} 支冻存管。\n细胞：{preview}\n孔位：{compact_box_positions(positions)}\n出库人：{operator}\n出库日期：{operation_date}",
            danger=True,
        ):
            return
        try:
            count = self.repository.batch_checkout_box_samples(code, positions, operator, operation_date)
        except (OSError, ValueError) as exc:
            self._notify("批量出库失败", str(exc), danger=True)
            return
        changed_positions = tuple(positions)
        self.selected_box_positions.clear()
        self._refresh_box_page_state(code, changed_positions)
        self._update_sidebar_summary()
        self._notify("批量出库完成", f"已出库 {count} 支冻存管，并写入出入库登记。")

    def batch_clear_box_positions(self, code: str) -> None:
        occupied = [position for position in self.selected_box_positions if self.repository.get_box_sample(code, position).occupied]
        if not occupied:
            self._notify("没有可清空细胞", "所选孔位中没有已有细胞。", danger=True)
            return
        if not self._ask_confirm("确认清除", f"第一次确认：确定清除选中的 {len(occupied)} 个细胞记录吗？", danger=True):
            return
        if not self._ask_confirm("再次确认清除", "第二次确认：清除不计入正常出库，但会保留一条清除登记。\n\n确定继续吗？", danger=True):
            return
        try:
            count = self.repository.batch_clear_box_samples(code, occupied)
        except OSError as exc:
            self._notify("批量清空失败", str(exc), danger=True)
            return
        changed_positions = tuple(occupied)
        self.selected_box_positions.clear()
        self._refresh_box_page_state(code, changed_positions)
        self._update_sidebar_summary()
        self._notify("清除完成", f"已清除 {count} 个盒内细胞记录，并写入出入库登记。")

    @staticmethod
    def _unit_detail_line(parent: tk.Misc, label: str, value: str) -> None:
        tk.Label(parent, text=label, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(5, 0))
        tk.Label(parent, text=value, bg=COLORS["panel"], fg=COLORS["text"], wraplength=145, justify="left", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")

    def show_editor(self, code: str) -> None:
        self._set_active_nav("overview")
        parsed = parse_unit_code(code)
        if parsed is None:
            return
        self.page_title_var.set(f"编辑冻存盒位 · {self.repository.freezer_name}")
        column, layer = parsed
        record = self.repository.get(code)
        self.clear_content()
        scroll = ScrollableFrame(self.content)
        scroll.pack(fill="both", expand=True)
        page = scroll.body
        page.columnconfigure(0, weight=1)

        nav = ttk.Frame(page, style="Page.TFrame")
        nav.grid(row=0, column=0, sticky="ew", padx=28, pady=(22, 16))
        if self.box_search_return_mode == "basic":
            back_text = "←  返回搜索结果"
            back_command = self.show_search_results
        else:
            back_text = f"←  返回冻存盒位 {code}"
            back_command = lambda: self.show_compartment(column, layer)
        RoundedButton(
            nav,
            text=back_text,
            command=back_command,
            fill="#E8EDF4",
            foreground=COLORS["text"],
            hover_fill="#DCE4EE",
            border="#E8EDF4",
            canvas_bg=COLORS["bg"],
            width=150,
            height=38,
            radius=10,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        title_box = ttk.Frame(nav, style="Page.TFrame")
        title_box.pack(side="left", padx=18)
        ttk.Label(title_box, text=f"编辑冻存盒位 {code}", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text=f"第{column}列 · 第{layer}层", style="PageMuted.TLabel").pack(anchor="w")

        panel = self._panel(page)
        panel.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 26))
        form = tk.Frame(panel, bg=COLORS["panel"])
        form.pack(fill="x", padx=24, pady=22)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)

        self.form_vars: dict[str, tk.StringVar] = {
            key: tk.StringVar(value=str(getattr(record, key)))
            for key in UnitRecord.__dataclass_fields__
            if key != "notes"
        }
        fields = [
            ("sample_name", "细胞名称 *", "请输入细胞名称", "entry"),
            ("stored_date", "入库日期 *", "YYYY-MM-DD 或 YYYYMMDD", "entry"),
            ("experiment_id", "实验编号", "如 BEBT-EF-0001，可留空", "entry"),
            ("sample_type", "细胞类别", "请选择或输入细胞类别", "combo"),
            ("sample_count", "细胞数量", "如 20", "entry"),
            ("sample_date", "采样日期", "YYYY-MM-DD 或 YYYYMMDD，可留空", "entry"),
            ("stored_by", "入库人", "请输入入库人姓名，可留空", "entry"),
            ("claimed_by", "领用人", "未领用可留空", "entry"),
            ("claimed_date", "领用日期", "YYYY-MM-DD，未领用可留空", "entry"),
            ("claimed_amount", "领用量", "如 2 管、500 μL", "entry"),
        ]
        for index, (key, label, hint, kind) in enumerate(fields):
            row, column_index = divmod(index, 2)
            field = tk.Frame(form, bg=COLORS["panel"])
            field.grid(row=row, column=column_index, sticky="ew", padx=(0 if column_index == 0 else 12, 12 if column_index == 0 else 0), pady=9)
            tk.Label(field, text=label, bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
            if kind == "combo":
                widget = ttk.Combobox(field, textvariable=self.form_vars[key], values=("肿瘤细胞", "原代细胞", "干细胞", "免疫细胞", "细胞系", "其他"), font=("Microsoft YaHei UI", 10))
            else:
                widget = ttk.Entry(field, textvariable=self.form_vars[key], font=("Microsoft YaHei UI", 10))
            widget.pack(fill="x")
            _bind_ime_safe_return(widget, lambda value=code: self.save_editor(value))
            tk.Label(field, text=hint, bg=COLORS["panel"], fg="#98A2B3", font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(4, 0))

        notes_field = tk.Frame(form, bg=COLORS["panel"])
        notes_field.grid(row=5, column=0, columnspan=2, sticky="ew", pady=9)
        tk.Label(notes_field, text="备注", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", pady=(0, 6))
        self.notes_text = tk.Text(notes_field, height=4, wrap="word", relief="solid", borderwidth=1, highlightthickness=0, font=("Microsoft YaHei UI", 10), fg=COLORS["text"])
        self.notes_text.pack(fill="x")
        self.notes_text.insert("1.0", record.notes)
        _bind_ime_safe_return(self.notes_text, lambda value=code: self.save_editor(value))
        self.notes_text.bind("<Control-Return>", self._newline_in_editor_notes)

        actions = tk.Frame(form, bg=COLORS["panel"])
        actions.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        RoundedButton(
            actions,
            text="保存记录",
            command=lambda: self.save_editor(code),
            fill=COLORS["primary"],
            hover_fill=COLORS["primary_dark"],
            canvas_bg=COLORS["panel"],
            width=112,
            height=40,
            radius=11,
        ).pack(side="right")
        RoundedButton(
            actions,
            text="取消",
            command=lambda: self.show_compartment(column, layer),
            fill="#E8EDF4",
            foreground=COLORS["text"],
            hover_fill="#DCE4EE",
            border="#E8EDF4",
            canvas_bg=COLORS["panel"],
            width=90,
            height=40,
            radius=11,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right", padx=10)
        if record.occupied:
            RoundedButton(
                actions,
                text="清空此冻存盒",
                command=lambda: self.clear_unit(code),
                fill="#FCECEC",
                foreground=COLORS["danger"],
                hover_fill="#F7DADA",
                border="#F3D1D1",
                canvas_bg=COLORS["panel"],
                width=112,
                height=40,
                radius=11,
            ).pack(side="left")

    def _newline_in_editor_notes(self, _event: tk.Event) -> str:
        self.notes_text.insert("insert", "\n")
        return "break"

    @staticmethod
    def _valid_date(value: str) -> bool:
        if not value:
            return True
        from datetime import datetime

        for format_string in ("%Y-%m-%d", "%Y%m%d"):
            try:
                datetime.strptime(value, format_string)
                return True
            except ValueError:
                continue
        return False

    def save_editor(self, code: str) -> None:
        values = {key: variable.get().strip() for key, variable in self.form_vars.items()}
        values["notes"] = self.notes_text.get("1.0", "end-1c").strip()
        missing_labels = [
            label
            for key, label in (("sample_name", "细胞名称"), ("stored_date", "入库日期"))
            if not values[key]
        ]
        if missing_labels:
            self._notify("信息未填写完整", "请填写：" + "、".join(missing_labels), danger=True)
            return
        if not self._valid_date(values["stored_date"]):
            self._notify("日期格式不正确", "入库日期请填写为 YYYY-MM-DD 或 YYYYMMDD。", danger=True)
            return
        if values["sample_date"] and not self._valid_date(values["sample_date"]):
            self._notify("日期格式不正确", "采样日期请填写为 YYYY-MM-DD 或 YYYYMMDD。", danger=True)
            return
        if values["claimed_date"] and not self._valid_date(values["claimed_date"]):
            self._notify("日期格式不正确", "领用日期请填写为 YYYY-MM-DD 或 YYYYMMDD。", danger=True)
            return
        if values["sample_count"] and not values["sample_count"].isdigit():
            self._notify("细胞数量不正确", "细胞数量应填写非负整数。", danger=True)
            return
        try:
            self.repository.set(code, UnitRecord(**values))
        except OSError as exc:
            self._notify("保存失败", f"无法写入本地数据文件：\n{exc}", danger=True)
            return
        self._update_sidebar_summary()
        self._notify("保存成功", f"冻存盒 {code} 已保存。")
        parsed = parse_unit_code(code)
        if parsed:
            self.show_compartment(*parsed)

    def clear_unit(self, code: str) -> None:
        record = self.repository.get(code)
        if not self._ask_confirm(
            "确认清空",
            f"确定清空冻存盒 {code} 中的记录“{record.sample_name or record.experiment_id}”吗？\n此操作不可撤销。",
            danger=True,
        ):
            return
        try:
            self.repository.clear(code)
        except OSError as exc:
            self._notify("清空失败", f"无法写入本地数据文件：\n{exc}", danger=True)
            return
        self._update_sidebar_summary()
        parsed = parse_unit_code(code)
        if parsed:
            self.show_compartment(*parsed)

    def export_to_excel(self) -> None:
        default_name = f"液氮罐总台账_{date.today().strftime('%Y%m%d')}.xlsx"
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="导出液氮罐台账",
            initialdir=str(Path(__file__).parent),
            initialfile=default_name,
            defaultextension=".xlsx",
            filetypes=(("Excel 工作簿", "*.xlsx"),),
        )
        if not selected:
            return
        try:
            freezer_exports = []
            exported_tube_count = 0
            for freezer_id, freezer in self.repository.freezers.items():
                if freezer.archived:
                    continue
                samples = {
                    (code, position): sample
                    for (stored_id, code, position), sample in self.repository.box_samples.items()
                    if stored_id == freezer_id and sample.occupied
                }
                exported_tube_count += len(samples)
                capacity = sum(
                    self.repository.get_box_layout(code, freezer_id).rows
                    * self.repository.get_box_layout(code, freezer_id).columns
                    for code in sqlite_all_unit_codes(freezer.storage_columns)
                )
                freezer_exports.append((freezer.name, samples, capacity))
            export_cryotube_workbook(
                Path(selected),
                freezer_exports,
            )
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            self._notify("导出失败", f"无法生成 Excel 文件：\n{exc}", danger=True)
            return
        self._notify(
            "导出完成",
            "Excel 台账已生成。\n\n"
            f"文件：{Path(selected).name}\n"
            f"已导出 {exported_tube_count} 支冻存管，覆盖 {len(self.repository.list_freezers())} 个未归档液氮罐，并包含管位汇总统计和使用说明。",
        )

    def import_from_excel(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="从 Excel 导入液氮罐台账",
            initialdir=str(Path(__file__).parent),
            filetypes=(("Excel 工作簿", "*.xlsx *.xlsm"),),
        )
        if not selected:
            return
        self._set_active_nav("import")
        try:
            preview = preview_freezer_workbook(Path(selected))
        except ValueError as exc:
            self._notify("无法导入", str(exc), danger=True)
            return
        self.pending_import_path = Path(selected)
        self.pending_import_preview = preview
        self.show_import_preview()

    def show_import_preview(self) -> None:
        self._set_active_nav("import")
        preview = self.pending_import_preview
        if preview is None or self.pending_import_path is None:
            self.import_from_excel()
            return
        self.page_title_var.set("Excel 导入预览")
        self.clear_content()
        scroll = ScrollableFrame(self.content)
        scroll.pack(fill="both", expand=True)
        page = ttk.Frame(scroll.body, style="Page.TFrame", padding=(28, 24))
        page.pack(fill="both", expand=True)
        heading = ttk.Frame(page, style="Page.TFrame")
        heading.pack(fill="x", pady=(0, 16))
        title_box = ttk.Frame(heading, style="Page.TFrame")
        title_box.pack(side="left")
        ttk.Label(title_box, text="Excel 导入预览", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text=self.pending_import_path.name, style="PageMuted.TLabel").pack(anchor="w", pady=(3, 0))
        RoundedButton(heading, text="重新选择", command=self.import_from_excel, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["bg"], width=100, height=38, radius=10).pack(side="right")

        cards = tk.Frame(page, bg=COLORS["bg"])
        cards.pack(fill="x", pady=(0, 14))
        values = (("识别液氮罐", len(preview.freezers), COLORS["primary"]), ("有效记录", preview.record_count, COLORS["occupied_text"]), ("错误", preview.error_count, COLORS["danger"]), ("提醒", preview.warning_count, "#C77B16"))
        for index, (label, value, color) in enumerate(values):
            cards.columnconfigure(index, weight=1)
            card = self._panel(cards)
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))
            tk.Label(card, text=label, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16, pady=(13, 2))
            tk.Label(card, text=str(value), bg=COLORS["panel"], fg=color, font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", padx=16, pady=(0, 13))

        summary_panel = self._panel(page)
        summary_panel.pack(fill="x", pady=(0, 14))
        tk.Label(summary_panel, text="将要处理的液氮罐", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=18, pady=(14, 6))
        for name, records in preview.freezers:
            tk.Label(summary_panel, text=f"• {name}  ·  {len(records)} 条占用记录", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=20, pady=3)
        tk.Frame(summary_panel, height=8, bg=COLORS["panel"]).pack()

        issue_panel = self._panel(page)
        issue_panel.pack(fill="both", expand=True)
        issue_header = tk.Frame(issue_panel, bg=COLORS["panel"])
        issue_header.pack(fill="x", padx=18, pady=(14, 8))
        tk.Label(issue_header, text="校验结果", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        if preview.issues:
            RoundedButton(issue_header, text="导出问题清单", command=self.export_import_issues, fill="#E8EDF4", foreground=COLORS["text"], hover_fill="#DCE4EE", border="#E8EDF4", canvas_bg=COLORS["panel"], width=118, height=34, radius=9).pack(side="right")
        if not preview.issues:
            tk.Label(issue_panel, text="✓ 未发现格式或数据问题，可以继续导入。", bg=COLORS["panel"], fg=COLORS["occupied_text"], font=("Microsoft YaHei UI", 10, "bold"), pady=22).pack()
        for issue in preview.issues[:100]:
            row = tk.Frame(issue_panel, bg=COLORS["panel"])
            row.pack(fill="x", padx=18, pady=5)
            color = COLORS["danger"] if issue.severity == "错误" else "#C77B16"
            tk.Label(row, text=issue.severity, width=5, bg=COLORS["panel"], fg=color, font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
            tk.Label(row, text=f"{issue.sheet} · 第 {issue.row} 行 · {issue.field}", width=30, anchor="w", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 9)).pack(side="left")
            tk.Label(row, text=issue.message, anchor="w", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(side="left", fill="x", expand=True)
        actions = tk.Frame(page, bg=COLORS["bg"])
        actions.pack(fill="x", pady=(14, 0))
        if preview.can_import:
            RoundedButton(actions, text="继续选择导入方式", command=self.commit_pending_import, fill=COLORS["primary"], hover_fill=COLORS["primary_dark"], canvas_bg=COLORS["bg"], width=168, height=42, radius=11).pack(side="right")
        else:
            tk.Label(actions, text="存在错误，已暂停导入。请按问题清单修改 Excel 后重新选择。", bg=COLORS["bg"], fg=COLORS["danger"], font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")

    def export_import_issues(self) -> None:
        preview = self.pending_import_preview
        if preview is None or not preview.issues:
            return
        selected = filedialog.asksaveasfilename(parent=self, title="导出导入问题清单", initialdir=str(Path(__file__).parent), initialfile="Excel导入问题清单.csv", defaultextension=".csv", filetypes=(("CSV 文件", "*.csv"),))
        if not selected:
            return
        try:
            with Path(selected).open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(("级别", "工作表", "行号", "字段", "原值", "说明"))
                for issue in preview.issues:
                    writer.writerow((issue.severity, issue.sheet, issue.row, issue.field, issue.value, issue.message))
        except OSError as exc:
            self._notify("导出失败", str(exc), danger=True)
            return
        self._notify("导出完成", f"问题清单已保存：{Path(selected).name}")

    def commit_pending_import(self) -> None:
        preview = self.pending_import_preview
        if preview is None or not preview.can_import:
            return
        imported = preview.freezers

        record_count = sum(len(records) for _name, records in imported)
        mode = self._choose_import_mode(len(imported), record_count)
        if mode is None:
            return
        try:
            stats = self.repository.import_freezers(
                imported,
                overwrite=mode == "overwrite",
            )
        except (OSError, ValueError) as exc:
            self._notify("导入失败", f"无法写入本地数据：\n{exc}", danger=True)
            return

        self._refresh_freezer_selector()
        self._update_sidebar_summary()
        backup_name = (
            self.repository.last_import_backup.name
            if self.repository.last_import_backup is not None
            else "未生成（原数据文件不存在）"
        )
        self._notify(
            "导入完成",
            f"已处理 {stats['freezers']} 个液氮罐、{stats['records']} 条占用记录。\n"
            f"新增液氮罐：{stats['created']} 个；更新同名液氮罐：{stats['updated']} 个。\n\n"
            f"导入前备份：{backup_name}",
        )
        self.show_overview()

    def show_search_results(self) -> None:
        self._set_active_nav("search")
        query = self.search_var.get().strip()
        if not query:
            self.show_search_page()
            return
        if query.casefold() != self.search_session_query:
            self.search_opened_boxes.clear()
            self.search_session_query = query.casefold()
        self.box_search_return_mode = ""
        self.page_title_var.set("细胞搜索 · 全部液氮罐")
        self.clear_content()
        scroll = ScrollableFrame(self.content)
        scroll.pack(fill="both", expand=True)
        page = ttk.Frame(scroll.body, style="Page.TFrame", padding=(28, 22))
        page.pack(fill="both", expand=True)
        heading = ttk.Frame(page, style="Page.TFrame")
        heading.pack(fill="x", pady=(0, 16))
        RoundedButton(
            heading,
            text="←  返回搜索",
            command=self.show_search_page,
            fill="#E8EDF4",
            foreground=COLORS["text"],
            hover_fill="#DCE4EE",
            border="#E8EDF4",
            canvas_bg=COLORS["bg"],
            width=118,
            height=38,
            radius=10,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        ttk.Label(heading, text=f"搜索结果：{query}", style="Title.TLabel").pack(side="left", padx=18)

        results = self.repository.search_all_freezer_records(query)
        box_results = self.repository.search_box_samples(query, all_freezers=True)
        box_groups = self._group_box_search_results(box_results)
        if not results and not box_results:
            empty = self._panel(page)
            empty.pack(fill="x")
            tk.Label(empty, text="没有找到匹配的细胞或冻存盒孔位", bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 11), pady=32).pack()
            return

        list_panel = self._panel(page)
        list_panel.pack(fill="both", expand=True)
        for index, (freezer_id, freezer_name, code, record) in enumerate(results):
            row = tk.Frame(list_panel, bg=COLORS["panel"])
            row.pack(fill="x", padx=18, pady=(12 if index == 0 else 0, 12))
            tk.Label(row, text=code, width=8, anchor="w", bg=COLORS["panel"], fg=COLORS["primary"], font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
            detail = tk.Frame(row, bg=COLORS["panel"])
            detail.pack(side="left", fill="x", expand=True)
            tk.Label(detail, text=record.sample_name or record.experiment_id or "空位", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
            details = " · ".join(value for value in (record.experiment_id, record.sample_type, record.stored_by, record.stored_date) if value)
            subtitle = freezer_name + (f" · {details}" if details else "")
            tk.Label(detail, text=subtitle, bg=COLORS["panel"], fg=COLORS["muted"], font=("Microsoft YaHei UI", 9)).pack(anchor="w")
            RoundedButton(
                row,
                text="查看 / 编辑",
                command=lambda owner=freezer_id, value=code: self.open_legacy_search_result(owner, value),
                fill=COLORS["primary_soft"],
                foreground=COLORS["primary"],
                hover_fill="#DCE8FF",
                border=COLORS["primary_soft"],
                canvas_bg=COLORS["panel"],
                width=108,
                height=36,
                radius=9,
            ).pack(side="right")
            if index < len(results) - 1 or box_groups:
                tk.Frame(list_panel, height=1, bg=COLORS["line"]).pack(fill="x", padx=18)
        if box_groups:
            tk.Label(list_panel, text=f"匹配细胞冻存盒 {len(box_groups)} 个 · 共 {len(box_results)} 个孔位", bg=COLORS["panel"], fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=18, pady=(12, 4))
            for group in box_groups:
                self._render_box_search_group(list_panel, group)


def main() -> None:
    app = FreezerManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
