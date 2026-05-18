"""Cross-platform push-to-talk hotkey + paste.

Windows : `keyboard` library with per-key suppress, so CapsLock can be the
          hotkey without toggling caps state while held.
macOS/Linux : `pynput`. Does NOT suppress events, so the configured key
          should have no side effect when pressed alone — use the right
          Cmd / right Option key, or an F13+ key. (CapsLock on macOS would
          still toggle caps state.)
"""
import sys

# Map config key names to pynput Key attribute names.
_PYNPUT_KEYS = {
    "caps lock": "caps_lock", "capslock": "caps_lock",
    "right ctrl": "ctrl_r", "ctrl_r": "ctrl_r",
    "right shift": "shift_r", "shift_r": "shift_r",
    "right alt": "alt_r", "alt_r": "alt_r", "right option": "alt_r",
    "right cmd": "cmd_r", "cmd_r": "cmd_r", "right command": "cmd_r",
    "f13": "f13", "f14": "f14", "f15": "f15", "f16": "f16",
    "f17": "f17", "f18": "f18", "f19": "f19",
}


def send_paste() -> None:
    """Simulate the OS paste shortcut (Ctrl+V on Windows/Linux, Cmd+V on Mac)."""
    if sys.platform == "win32":
        import keyboard
        keyboard.send("ctrl+v")
    else:
        from pynput.keyboard import Controller, Key
        controller = Controller()
        modifier = Key.cmd if sys.platform == "darwin" else Key.ctrl
        controller.press(modifier)
        controller.press("v")
        controller.release("v")
        controller.release(modifier)


class HotkeyListener:
    """Fires on_press / on_release each time the configured key transitions."""

    def __init__(self, key_name: str, on_press, on_release):
        self._key_name = key_name
        self._on_press = on_press
        self._on_release = on_release

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class _WindowsHotkey(HotkeyListener):
    def start(self) -> None:
        import keyboard
        keyboard.on_press_key(self._key_name, self._press, suppress=True)
        keyboard.on_release_key(self._key_name, self._release, suppress=True)

    def _press(self, _event):
        self._on_press()

    def _release(self, _event):
        self._on_release()

    def stop(self) -> None:
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass


class _PynputHotkey(HotkeyListener):
    """macOS / Linux. Needs Accessibility permission on macOS."""

    def __init__(self, key_name: str, on_press, on_release):
        super().__init__(key_name, on_press, on_release)
        self._pressed = False
        self._listener = None
        self._target = self._resolve(key_name)

    def _resolve(self, name: str):
        from pynput.keyboard import Key, KeyCode
        raw = name.strip()
        attr = _PYNPUT_KEYS.get(raw.lower())
        if attr and hasattr(Key, attr):
            return getattr(Key, attr)
        # 捕获得到的 pynput 名（caps_lock / ctrl_r / f8 …）直接解析
        if hasattr(Key, raw.lower()):
            return getattr(Key, raw.lower())
        if len(raw) == 1:
            return KeyCode.from_char(raw)
        # default to right option if unrecognized
        return Key.alt_r

    def _matches(self, key) -> bool:
        return key == self._target

    def _handle_press(self, key):
        if self._matches(key) and not self._pressed:
            self._pressed = True
            self._on_press()

    def _handle_release(self, key):
        if self._matches(key) and self._pressed:
            self._pressed = False
            self._on_release()

    def start(self) -> None:
        from pynput import keyboard as pk
        self._listener = pk.Listener(
            on_press=self._handle_press, on_release=self._handle_release
        )
        self._listener.start()

    def stop(self) -> None:
        try:
            if self._listener:
                self._listener.stop()
        except Exception:
            pass


def make_hotkey(key_name: str, on_press, on_release) -> HotkeyListener:
    """Build the platform-appropriate hotkey listener."""
    if sys.platform == "win32":
        return _WindowsHotkey(key_name, on_press, on_release)
    return _PynputHotkey(key_name, on_press, on_release)


def capture_key():
    """阻塞直到用户按下一个键，返回键名（可直接传给 make_hotkey）。失败返回 None。

    用于「自定义录音热键」——调用前应先停掉当前热键监听，避免互相干扰。
    """
    try:
        if sys.platform == "win32":
            import keyboard
            while True:
                ev = keyboard.read_event(suppress=False)
                if ev.event_type == "down" and ev.name:
                    return ev.name
        else:
            from pynput import keyboard as pk
            from pynput.keyboard import Key, KeyCode
            holder: dict = {}

            def _on(key):
                if isinstance(key, Key):
                    holder["name"] = key.name
                elif isinstance(key, KeyCode) and key.char:
                    holder["name"] = key.char
                return False  # 抓到第一个就停

            with pk.Listener(on_press=_on) as lis:
                lis.join()
            return holder.get("name")
    except Exception:
        return None
