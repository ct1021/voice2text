"""voice2text - hold a hotkey to dictate; transcribe + AI-clean + paste.

A floating ball on the desktop shows state (gray/red/orange/purple).
Click it to toggle the history panel. Right-click for the menu.
All settings live in config.toml (auto-created from config.example.toml).
"""
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
LOG_PATH = str(APP_DIR / "voice2text.log")
if sys.stdout is None:
    _log_fp = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_fp
    sys.stderr = _log_fp

# Hide subprocess console windows on Windows (claude-agent-sdk spawns claude.cmd).
if sys.platform == "win32":
    _orig_popen_init = subprocess.Popen.__init__

    def _silent_popen_init(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0)
        kwargs["creationflags"] = flags | subprocess.CREATE_NO_WINDOW
        if kwargs.get("startupinfo") is None:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            kwargs["startupinfo"] = si
        _orig_popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _silent_popen_init

import numpy as np
import pyperclip
import sounddevice as sd

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QColor, QAction
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QLabel, QPushButton, QTextEdit, QMenu, QFrame,
)

from config import load_config
from stt import make_engine
from ai import make_cleaner, NoOpCleaner
from hotkey import make_hotkey, send_paste

# ===== Config =====
CONFIG = load_config()
HOTKEY = CONFIG["hotkey"]["key"]
SAMPLE_RATE = CONFIG["audio"]["sample_rate"]
MIN_DURATION_SEC = 0.3
MAX_HISTORY = 100

HISTORY_FILE = APP_DIR / "history.jsonl"
UI_CONFIG_FILE = APP_DIR / "ui_config.json"

# ===== Glossary =====
DEFAULT_GLOSSARY = (
    "Claude Code, Claude, ChatGPT, Codex, Cursor, GitHub, Git, "
    "Python, JavaScript, TypeScript, Node.js, Docker, WSL, PowerShell, "
    "API, SDK, MCP, LLM, faster-whisper, SenseVoice, Whisper"
)


def _load_glossary() -> str:
    gf = APP_DIR / "glossary.txt"
    if gf.exists():
        try:
            words = []
            for line in gf.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    words.append(line)
            if words:
                return ", ".join(words)
        except Exception:
            pass
    return DEFAULT_GLOSSARY


GLOSSARY = _load_glossary()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def beep(ok: bool = True) -> None:
    """Feedback sound. Windows: winsound; macOS: afplay; Linux: silent."""
    try:
        if sys.platform == "win32":
            import winsound
            winsound.MessageBeep(
                winsound.MB_OK if ok else winsound.MB_ICONEXCLAMATION
            )
        elif sys.platform == "darwin":
            sound = ("/System/Library/Sounds/Glass.aiff" if ok
                     else "/System/Library/Sounds/Basso.aiff")
            subprocess.Popen(["afplay", sound])
    except Exception:
        pass


# ===== UI config (ball position, intro flag) =====
def load_ui_config() -> dict:
    d = {"ball_x": -1, "ball_y": -1, "intro_shown": False}
    if UI_CONFIG_FILE.exists():
        try:
            d.update(json.loads(UI_CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return d


def save_ui_config(d: dict) -> None:
    try:
        UI_CONFIG_FILE.write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# ===== History =====
def load_history() -> list:
    items = []
    if HISTORY_FILE.exists():
        try:
            with HISTORY_FILE.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        items.append(json.loads(line))
        except Exception as e:
            log(f"history load failed: {e}")
    return items[-MAX_HISTORY:]


def append_history(item: dict) -> None:
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception as e:
        log(f"history append failed: {e}")


# ===== Signal bus =====
class Bus(QObject):
    state = pyqtSignal(str)            # idle | loading | rec | proc
    history_added = pyqtSignal(dict)
    error = pyqtSignal(str)


bus = Bus()

COLORS = {
    "idle":    QColor(140, 140, 140),
    "loading": QColor(100, 130, 200),
    "rec":     QColor(231, 76, 60),
    "proc":    QColor(243, 156, 18),
    "error":   QColor(155, 89, 182),
}


# ===== Backend =====
class Backend:
    def __init__(self):
        self.audio_q: queue.Queue = queue.Queue()
        self.recording = False
        self.processing_lock = threading.Lock()
        self.engine = None
        self.cleaner = None
        self.stream = None
        self.hotkey = None
        self.ready = False

    def start_async(self):
        threading.Thread(target=self._init, daemon=True).start()

    def _resolve_device(self):
        name = CONFIG["audio"]["device"].strip()
        if not name:
            return None
        try:
            for i, dev in enumerate(sd.query_devices()):
                if (dev["max_input_channels"] > 0
                        and name.lower() in dev["name"].lower()):
                    log(f"mic device: {dev['name']}")
                    return i
        except Exception as e:
            log(f"device query failed: {e}")
        log(f"mic '{name}' not found, using default")
        return None

    def _init(self):
        try:
            log(f"loading STT backend: {CONFIG['stt']['backend']}...")
            self.engine = make_engine(CONFIG["stt"])
            log(f"STT ready: {self.engine.name}")
        except Exception as e:
            log(f"STT init failed: {e}")
            bus.error.emit(f"STT 初始化失败: {e}")
            return
        try:
            self.cleaner = make_cleaner(CONFIG["ai"])
            log(f"AI ready: {self.cleaner.name}")
        except Exception as e:
            log(f"AI init failed: {e}")
            bus.error.emit(f"AI 后端初始化失败，已降级为不清洗: {e}")
            self.cleaner = NoOpCleaner()
        try:
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1,
                callback=self._audio_cb, device=self._resolve_device(),
            )
            self.stream.start()
        except Exception as e:
            log(f"mic failed: {e}")
            bus.error.emit(f"麦克风打开失败: {e}")
            return
        try:
            self.hotkey = make_hotkey(HOTKEY, self._on_press, self._on_release)
            self.hotkey.start()
        except Exception as e:
            log(f"hotkey failed: {e}")
            bus.error.emit(f"热键 '{HOTKEY}' 注册失败: {e}")
            return
        self.ready = True
        bus.state.emit("idle")
        log(f"backend ready - hold {HOTKEY} to dictate")

    def _system_prompt(self) -> str:
        template = CONFIG["ai"]["prompts"].get("default", "")
        return f"{template}\n\n专有名词参考词典：\n{GLOSSARY}"

    def _audio_cb(self, indata, frames, time_info, status):
        if self.recording:
            self.audio_q.put(indata.copy())

    def _on_press(self):
        if self.recording:
            return
        if not self.ready:
            return
        if self.processing_lock.locked():
            log("REC ignored - busy")
            beep(ok=False)
            return
        self.recording = True
        while not self.audio_q.empty():
            try:
                self.audio_q.get_nowait()
            except queue.Empty:
                break
        log("REC start")
        bus.state.emit("rec")

    def _on_release(self):
        if not self.recording:
            return
        self.recording = False
        log("REC stop")
        threading.Thread(target=self._process, daemon=True).start()

    def _process(self):
        if not self.processing_lock.acquire(blocking=False):
            return
        try:
            bus.state.emit("proc")
            chunks = []
            while not self.audio_q.empty():
                try:
                    chunks.append(self.audio_q.get_nowait())
                except queue.Empty:
                    break
            if not chunks:
                log("skip empty audio")
                return
            audio = np.concatenate(chunks, axis=0).flatten().astype(np.float32)
            duration = len(audio) / SAMPLE_RATE
            if duration < MIN_DURATION_SEC:
                log(f"skip short {duration:.2f}s")
                return
            log(f"STT transcribing {duration:.1f}s...")
            t0 = time.time()
            try:
                raw = self.engine.transcribe(audio, GLOSSARY)
            except Exception as e:
                log(f"STT failed: {e}")
                bus.error.emit(f"转写失败: {e}")
                return
            stt_sec = time.time() - t0
            log(f"STT {stt_sec:.2f}s -> {raw}")
            if not raw:
                log("skip empty transcription")
                return
            t1 = time.time()
            ai_failed = False
            try:
                cleaned = self.cleaner.clean(raw, self._system_prompt())
            except Exception as e:
                log(f"AI clean failed: {e}")
                bus.error.emit(f"AI 清洗失败，已用原始转写: {e}")
                cleaned = raw
                ai_failed = True
            ai_sec = time.time() - t1
            if not cleaned:
                cleaned = raw
            pyperclip.copy(cleaned)
            time.sleep(0.08)
            send_paste()
            beep(ok=True)
            item = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": round(duration, 1),
                "stt_sec": round(stt_sec, 2),
                "ai_sec": round(ai_sec, 2),
                "raw": raw,
                "cleaned": cleaned,
                "ai_failed": ai_failed,
            }
            append_history(item)
            bus.history_added.emit(item)
            log("done pasted")
        except Exception as e:
            log(f"process error: {e}")
            bus.error.emit(f"处理出错: {e}")
        finally:
            self.processing_lock.release()
            bus.state.emit("idle")

    def stop(self):
        try:
            if self.hotkey:
                self.hotkey.stop()
        except Exception:
            pass
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass


# ===== Intro bubble (first-run guidance) =====
class IntroBubble(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(300, 110)
        frame = QFrame(self)
        frame.setGeometry(0, 0, 300, 110)
        frame.setStyleSheet("""
            QFrame {
                background-color: rgba(40, 40, 48, 248);
                border-radius: 10px;
                border: 1px solid rgba(255, 255, 255, 45);
            }
            QLabel { color: #eee; font-size: 12px; }
        """)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 12, 14, 12)
        label = QLabel(
            f"👋 我是 Voice2Text\n\n"
            f"按住 {HOTKEY} 说话，松开自动转写并粘贴。\n"
            f"单击我打开历史面板，右键我看菜单。"
        )
        label.setWordWrap(True)
        lay.addWidget(label)

    def show_near(self, ball: QWidget):
        x = ball.x() - self.width() - 12
        if x < 0:
            x = ball.x() + ball.width() + 12
        self.move(x, max(0, ball.y() - 30))
        self.show()
        QTimer.singleShot(9000, self.close)


# ===== Floating ball =====
class FloatingBall(QWidget):
    def __init__(self, panel: "HistoryPanel", backend: Backend):
        super().__init__()
        self.panel = panel
        self.backend = backend
        self.size_px = CONFIG["ui"]["ball_size"]
        self.state = "loading"
        self.drag_offset = None
        self._moved = False
        self._pulse = 0
        self._intro = None

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if CONFIG["ui"]["always_on_top"]:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(self.size_px, self.size_px)

        self.ui_cfg = load_ui_config()
        if self.ui_cfg["ball_x"] >= 0:
            self.move(self.ui_cfg["ball_x"], self.ui_cfg["ball_y"])
        else:
            scr = QApplication.primaryScreen().availableGeometry()
            self.move(scr.right() - 80, scr.bottom() - 200)

        bus.state.connect(self._on_state)
        bus.error.connect(self._on_error)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(80)
        self._err_timer = QTimer(self)
        self._err_timer.setSingleShot(True)
        self._err_timer.timeout.connect(lambda: self._set_state("idle"))

    def show_intro_if_needed(self):
        if not self.ui_cfg.get("intro_shown"):
            self._intro = IntroBubble()
            self._intro.show_near(self)
            self.ui_cfg["intro_shown"] = True
            save_ui_config(self.ui_cfg)

    def _tick(self):
        if self.state in ("rec", "proc", "loading", "error"):
            self._pulse = (self._pulse + 1) % 20
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = COLORS.get(self.state, COLORS["idle"])
        halo = QColor(color)
        if self.state in ("rec", "proc", "loading", "error"):
            halo.setAlpha(60 + int(60 * abs(self._pulse - 10) / 10))
        else:
            halo.setAlpha(60)
        p.setBrush(halo)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, self.size_px, self.size_px)
        p.setBrush(color)
        p.drawEllipse(8, 8, self.size_px - 16, self.size_px - 16)
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(18, 18, self.size_px - 36, self.size_px - 36)

    def _set_state(self, state: str):
        self.state = state
        tips = {
            "idle":    "Voice2Text · 空闲（按住 {} 说话）".format(HOTKEY),
            "loading": "Voice2Text · 加载中...",
            "rec":     "Voice2Text · 录音中",
            "proc":    "Voice2Text · 处理中",
            "error":   "Voice2Text · 出错（详见历史面板）",
        }
        self.setToolTip(tips.get(state, "Voice2Text"))
        self.update()

    def _on_state(self, state: str):
        if self.state == "error" and self._err_timer.isActive():
            return  # keep error visible until its timer fires
        self._set_state(state)

    def _on_error(self, _msg: str):
        self._set_state("error")
        self._err_timer.start(5000)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = e.globalPosition().toPoint() - self.pos()
            self._moved = False
        elif e.button() == Qt.MouseButton.RightButton:
            self._show_menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and self.drag_offset:
            self.move(e.globalPosition().toPoint() - self.drag_offset)
            self._moved = True

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if not self._moved:
                self._toggle_panel()
            else:
                self.ui_cfg["ball_x"] = self.x()
                self.ui_cfg["ball_y"] = self.y()
                save_ui_config(self.ui_cfg)
            self.drag_offset = None
            self._moved = False

    def _toggle_panel(self):
        if self.panel.isVisible():
            self.panel.hide()
            return
        scr = QApplication.primaryScreen().availableGeometry()
        x = self.x() - self.panel.width() - 10
        if x < scr.left() + 4:
            x = self.x() + self.size_px + 10
        y = self.y() + self.size_px // 2 - self.panel.height() // 2
        y = max(scr.top() + 4, min(y, scr.bottom() - 4 - self.panel.height()))
        self.panel.move(x, y)
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def _show_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: rgba(35, 35, 40, 245);
                color: #ddd; border: 1px solid rgba(255,255,255,40);
                border-radius: 6px; padding: 4px;
            }
            QMenu::item { padding: 6px 18px; border-radius: 4px; }
            QMenu::item:selected { background: rgba(255,255,255,30); }
            QMenu::separator { height: 1px; background: rgba(255,255,255,25);
                               margin: 4px 8px; }
        """)
        a_hist = QAction("显示历史", self)
        a_hist.triggered.connect(self._toggle_panel)
        menu.addAction(a_hist)
        menu.addSeparator()
        import os
        a_log = QAction("打开日志", self)
        a_log.triggered.connect(lambda: os.startfile(LOG_PATH))
        menu.addAction(a_log)
        a_cfg = QAction("打开配置 config.toml", self)
        a_cfg.triggered.connect(
            lambda: os.startfile(str(APP_DIR / "config.toml"))
        )
        menu.addAction(a_cfg)
        a_dir = QAction("打开文件夹", self)
        a_dir.triggered.connect(lambda: os.startfile(str(APP_DIR)))
        menu.addAction(a_dir)
        menu.addSeparator()
        a_quit = QAction("退出", self)
        a_quit.triggered.connect(QApplication.quit)
        menu.addAction(a_quit)
        menu.exec(pos)


# ===== History panel =====
class HistoryPanel(QWidget):
    WIDTH = 440
    HEIGHT = 500

    def __init__(self, backend: Backend):
        super().__init__()
        self.backend = backend
        self.history = load_history()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(self.WIDTH, self.HEIGHT)
        self._build_ui()
        bus.history_added.connect(self._on_added)
        bus.state.connect(self._on_state)
        bus.error.connect(self._on_error)
        self.hide()

    def _build_ui(self):
        frame = QFrame(self)
        frame.setGeometry(0, 0, self.WIDTH, self.HEIGHT)
        frame.setStyleSheet("""
            QFrame {
                background-color: rgba(28, 28, 32, 245);
                border-radius: 14px;
                border: 1px solid rgba(255, 255, 255, 30);
            }
            QLabel { color: #ddd; font-size: 13px; }
            QLabel#title { color: #fff; font-size: 15px; font-weight: bold; }
            QLabel#status { color: #aaa; font-size: 11px; }
            QLabel#err { color: #e8a0c8; font-size: 11px; }
            QListWidget {
                background: transparent; border: none; color: #ddd;
                font-size: 12px; outline: none;
            }
            QListWidget::item {
                padding: 7px 4px;
                border-bottom: 1px solid rgba(255,255,255,12);
            }
            QListWidget::item:selected {
                background: rgba(243, 156, 18, 90); border-radius: 4px;
                color: #fff;
            }
            QTextEdit {
                background: rgba(0, 0, 0, 80); color: #ddd;
                border: 1px solid rgba(255,255,255,22);
                border-radius: 6px; font-size: 12px; padding: 6px;
            }
            QPushButton {
                background: rgba(255, 255, 255, 22); color: #ddd;
                border: 1px solid rgba(255,255,255,30); border-radius: 5px;
                padding: 5px 10px; font-size: 11px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 42); }
            QPushButton#close {
                background: transparent; border: none;
                color: #aaa; font-size: 18px; padding: 0;
            }
            QPushButton#close:hover { color: #fff; }
        """)
        v = QVBoxLayout(frame)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(7)

        head = QHBoxLayout()
        title = QLabel("Voice2Text · 历史")
        title.setObjectName("title")
        head.addWidget(title)
        head.addStretch()
        close_btn = QPushButton("×")
        close_btn.setObjectName("close")
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.hide)
        head.addWidget(close_btn)
        v.addLayout(head)

        self.status = QLabel("")
        self.status.setObjectName("status")
        v.addWidget(self.status)
        self.err = QLabel("")
        self.err.setObjectName("err")
        self.err.setWordWrap(True)
        self.err.hide()
        v.addWidget(self.err)

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.itemDoubleClicked.connect(lambda _i: self._repaste())
        v.addWidget(self.list, 3)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(120)
        v.addWidget(self.detail, 1)

        actions = QHBoxLayout()
        b_ai = QPushButton("复制 AI 版")
        b_raw = QPushButton("复制原始")
        b_re = QPushButton("重粘贴")
        b_ai.clicked.connect(lambda: self._copy("cleaned"))
        b_raw.clicked.connect(lambda: self._copy("raw"))
        b_re.clicked.connect(self._repaste)
        actions.addWidget(b_ai)
        actions.addWidget(b_raw)
        actions.addWidget(b_re)
        v.addLayout(actions)

        self._refresh_status("loading")
        self._reload_list()

    def _refresh_status(self, state: str):
        msg = {
            "idle":    "空闲 · 按住 {} 录音".format(HOTKEY),
            "loading": "正在加载引擎...",
            "rec":     "🔴 录音中...",
            "proc":    "⏳ 处理中...",
        }.get(state, state)
        self.status.setText(
            f"{msg}   |   引擎 {CONFIG['stt']['backend']} · "
            f"AI {CONFIG['ai']['backend']}"
        )

    def _on_state(self, state: str):
        if state != "error":
            self.err.hide()
        self._refresh_status(state)

    def _on_error(self, msg: str):
        self.err.setText(f"⚠ {msg}")
        self.err.show()

    def _reload_list(self):
        self.list.clear()
        for item in reversed(self.history):
            flag = "⚠ " if item.get("ai_failed") else ""
            preview = item["cleaned"][:38].replace("\n", " ")
            label = f"{item['ts'][11:16]}  {item['duration']:>4}s  {flag}{preview}"
            li = QListWidgetItem(label)
            li.setData(Qt.ItemDataRole.UserRole, item)
            self.list.addItem(li)

    def _on_added(self, item: dict):
        self.history.append(item)
        self.history = self.history[-MAX_HISTORY:]
        self._reload_list()

    def _selected(self):
        li = self.list.currentItem()
        return None if li is None else li.data(Qt.ItemDataRole.UserRole)

    def _on_select(self):
        item = self._selected()
        if item is None:
            self.detail.clear()
            return
        flag = "（AI 清洗失败，下为原始转写）\n" if item.get("ai_failed") else ""
        self.detail.setPlainText(
            f"{flag}原始: {item['raw']}\n\n"
            f"AI  : {item['cleaned']}\n\n"
            f"耗时: STT {item['stt_sec']}s + AI {item['ai_sec']}s · "
            f"录音 {item['duration']}s"
        )

    def _copy(self, key: str):
        item = self._selected()
        if item:
            pyperclip.copy(item[key])
            log(f"copied {key}")

    def _repaste(self):
        item = self._selected()
        if item is None:
            return
        self.hide()
        QTimer.singleShot(180, lambda: self._do_paste(item["cleaned"]))

    def _do_paste(self, text: str):
        pyperclip.copy(text)
        time.sleep(0.06)
        send_paste()
        log("re-pasted")


# ===== Main =====
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    backend = Backend()
    panel = HistoryPanel(backend)
    ball = FloatingBall(panel, backend)
    ball.show()
    ball.show_intro_if_needed()

    backend.start_async()
    app.aboutToQuit.connect(backend.stop)
    log("UI ready, backend loading in background")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
