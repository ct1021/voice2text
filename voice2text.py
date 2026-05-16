"""voice2text v2 - PyQt6 floating ball + history panel.

Hold CapsLock to dictate. Release to transcribe -> Claude clean -> paste.
A draggable floating ball shows state (gray/red/orange).
Click the ball to toggle the history panel.
"""
import asyncio
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
import keyboard as kb
import winsound

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt6.QtGui import QPainter, QColor, QAction
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget,
    QListWidgetItem, QLabel, QPushButton, QTextEdit, QMenu, QFrame,
)
from faster_whisper import WhisperModel
from claude_agent_sdk import (
    query, ClaudeAgentOptions, AssistantMessage, TextBlock,
)

# ===== Paths =====
MODEL_DIR = APP_DIR / "models"
HISTORY_FILE = APP_DIR / "history.jsonl"
CONFIG_FILE = APP_DIR / "ui_config.json"

# ===== Audio / Model =====
HOTKEY = "caps lock"
SAMPLE_RATE = 16000
MODEL_NAME = "medium"
MIN_DURATION_SEC = 0.3
MAX_HISTORY = 100

DEFAULT_GLOSSARY = (
    "Claude Code, Claude, ChatGPT, Codex, Cursor, GitHub, Git, "
    "Python, JavaScript, TypeScript, Node.js, Docker, WSL, PowerShell, "
    "Linux, Ubuntu, API, SDK, MCP, LLM, prompt, token, "
    "faster-whisper, SenseVoice, Whisper, ASR, TTS, PyQt, npm, pip, uv"
)


def _load_glossary() -> str:
    """Load user glossary.txt (gitignored, personal terms) or fall back."""
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

SYSTEM_PROMPT = f"""你是语音转写清洗专家。任务：
1. 修正 ASR 转写错误，特别是技术专有名词（参考下方词典）
2. 加合理标点
3. 保持原意，不增删信息

专有名词词典：
{GLOSSARY}

输出：只输出清洗后的纯文本一行，不要解释，不要任何前缀或后缀。"""

COLORS = {
    "idle":    QColor(140, 140, 140),
    "loading": QColor(100, 130, 200),
    "rec":     QColor(231, 76, 60),
    "proc":    QColor(243, 156, 18),
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    defaults = {"ball_x": -1, "ball_y": -1}
    if CONFIG_FILE.exists():
        try:
            defaults.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return defaults


def save_config(cfg: dict) -> None:
    try:
        CONFIG_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        log(f"save_config failed: {e}")


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
    state = pyqtSignal(str)
    history_added = pyqtSignal(dict)


bus = Bus()


# ===== Backend =====
class Backend:
    def __init__(self):
        self.audio_q: queue.Queue = queue.Queue()
        self.recording = False
        self.processing_lock = threading.Lock()
        self.model = None
        self.stream = None
        self.ready = False

    def start_async(self):
        threading.Thread(target=self._init, daemon=True).start()

    def _init(self):
        try:
            log("loading faster-whisper medium...")
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            self.model = WhisperModel(
                MODEL_NAME, device="cpu", compute_type="int8",
                download_root=str(MODEL_DIR),
            )
            log("model ready")
            self.stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, callback=self._audio_cb,
            )
            self.stream.start()
            kb.on_press_key(HOTKEY, self._on_press, suppress=True)
            kb.on_release_key(HOTKEY, self._on_release, suppress=True)
            self.ready = True
            bus.state.emit("idle")
            log("backend ready - hold CapsLock to dictate")
        except Exception as e:
            log(f"backend init failed: {e}")

    def stop(self):
        try:
            kb.unhook_all()
        except Exception:
            pass
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass

    def _audio_cb(self, indata, frames, time_info, status):
        if self.recording:
            self.audio_q.put(indata.copy())

    def _on_press(self, _e):
        if self.recording:
            return
        if self.processing_lock.locked():
            log("REC ignored - busy")
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
            return
        self.recording = True
        while not self.audio_q.empty():
            try:
                self.audio_q.get_nowait()
            except queue.Empty:
                break
        log("REC start")
        bus.state.emit("rec")

    def _on_release(self, _e):
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
            segments, _ = self.model.transcribe(
                audio, language="zh", initial_prompt=GLOSSARY,
                beam_size=1, vad_filter=True,
            )
            raw = "".join(s.text for s in segments).strip()
            stt_sec = time.time() - t0
            log(f"STT {stt_sec:.2f}s -> {raw}")
            if not raw:
                log("skip empty transcription")
                return
            t1 = time.time()
            try:
                cleaned = asyncio.run(self._clean(raw))
                ai_sec = time.time() - t1
                log(f"AI {ai_sec:.2f}s -> {cleaned}")
            except Exception as e:
                log(f"AI failed {e}")
                cleaned = raw
                ai_sec = 0.0
            pyperclip.copy(cleaned)
            time.sleep(0.08)
            kb.send("ctrl+v")
            try:
                winsound.MessageBeep(winsound.MB_OK)
            except Exception:
                pass
            item = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": round(duration, 1),
                "stt_sec": round(stt_sec, 2),
                "ai_sec": round(ai_sec, 2),
                "raw": raw,
                "cleaned": cleaned,
            }
            append_history(item)
            bus.history_added.emit(item)
            log("done pasted")
        finally:
            self.processing_lock.release()
            bus.state.emit("idle")

    async def _clean(self, text: str) -> str:
        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT, max_turns=1, allowed_tools=[],
        )
        parts: list[str] = []
        async for msg in query(prompt=text, options=options):
            if isinstance(msg, AssistantMessage):
                for b in msg.content:
                    if isinstance(b, TextBlock):
                        parts.append(b.text)
        return "".join(parts).strip()


# ===== Floating ball =====
class FloatingBall(QWidget):
    BALL_SIZE = 44

    def __init__(self, panel: "HistoryPanel"):
        super().__init__()
        self.panel = panel
        self.state = "loading"
        self.drag_offset = None
        self._moved = False
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(self.BALL_SIZE, self.BALL_SIZE)
        self.setToolTip("Voice2Text · 加载中...")
        cfg = load_config()
        if cfg["ball_x"] >= 0:
            self.move(cfg["ball_x"], cfg["ball_y"])
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - 80, screen.bottom() - 200)
        bus.state.connect(self._on_state)
        # pulse animation timer
        self._pulse = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(80)

    def _tick(self):
        if self.state in ("rec", "proc", "loading"):
            self._pulse = (self._pulse + 1) % 20
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = COLORS.get(self.state, COLORS["idle"])
        # outer halo (pulse on active states)
        halo = QColor(color)
        if self.state in ("rec", "proc", "loading"):
            alpha = 60 + int(60 * abs(self._pulse - 10) / 10)
        else:
            alpha = 60
        halo.setAlpha(alpha)
        p.setBrush(halo)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, self.BALL_SIZE, self.BALL_SIZE)
        # inner solid
        p.setBrush(color)
        p.drawEllipse(8, 8, self.BALL_SIZE - 16, self.BALL_SIZE - 16)
        # white center dot
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(18, 18, self.BALL_SIZE - 36, self.BALL_SIZE - 36)

    def _on_state(self, state: str):
        self.state = state
        tips = {
            "idle":    "Voice2Text · 空闲（按住 CapsLock 说话）",
            "loading": "Voice2Text · 加载中...",
            "rec":     "Voice2Text · 录音中",
            "proc":    "Voice2Text · 处理中",
        }
        self.setToolTip(tips.get(state, "Voice2Text"))
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = e.globalPosition().toPoint() - self.pos()
            self._moved = False
        elif e.button() == Qt.MouseButton.RightButton:
            self._show_menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and self.drag_offset is not None:
            self.move(e.globalPosition().toPoint() - self.drag_offset)
            self._moved = True

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if not self._moved:
                self._toggle_panel()
            else:
                # save new position
                cfg = load_config()
                cfg["ball_x"] = self.x()
                cfg["ball_y"] = self.y()
                save_config(cfg)
            self.drag_offset = None
            self._moved = False

    def _toggle_panel(self):
        if self.panel.isVisible():
            self.panel.hide()
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            x = self.x() - self.panel.width() - 10
            if x < screen.left() + 4:
                x = self.x() + self.BALL_SIZE + 10
            y = self.y() + self.BALL_SIZE // 2 - self.panel.height() // 2
            if y < screen.top() + 4:
                y = screen.top() + 4
            if y + self.panel.height() > screen.bottom() - 4:
                y = screen.bottom() - 4 - self.panel.height()
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
        """)
        a1 = QAction("显示历史", self)
        a1.triggered.connect(self._toggle_panel)
        menu.addAction(a1)
        a2 = QAction("打开日志", self)
        a2.triggered.connect(lambda: __import__("os").startfile(LOG_PATH))
        menu.addAction(a2)
        a3 = QAction("打开文件夹", self)
        a3.triggered.connect(lambda: __import__("os").startfile(str(APP_DIR)))
        menu.addAction(a3)
        menu.addSeparator()
        aq = QAction("退出", self)
        aq.triggered.connect(QApplication.quit)
        menu.addAction(aq)
        menu.exec(pos)


# ===== History panel =====
class HistoryPanel(QWidget):
    WIDTH = 440
    HEIGHT = 500

    def __init__(self):
        super().__init__()
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
        v.setSpacing(8)

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

        self.status = QLabel("空闲 · 按住 CapsLock 录音")
        self.status.setObjectName("status")
        v.addWidget(self.status)

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.itemDoubleClicked.connect(lambda _i: self._repaste())
        v.addWidget(self.list, 3)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(120)
        v.addWidget(self.detail, 1)

        actions = QHBoxLayout()
        self.btn_copy_ai = QPushButton("复制 AI 版")
        self.btn_copy_raw = QPushButton("复制原始")
        self.btn_repaste = QPushButton("重粘贴")
        self.btn_copy_ai.clicked.connect(lambda: self._copy("cleaned"))
        self.btn_copy_raw.clicked.connect(lambda: self._copy("raw"))
        self.btn_repaste.clicked.connect(self._repaste)
        actions.addWidget(self.btn_copy_ai)
        actions.addWidget(self.btn_copy_raw)
        actions.addWidget(self.btn_repaste)
        v.addLayout(actions)

        self._reload_list()

    def _reload_list(self):
        self.list.clear()
        for item in reversed(self.history):
            preview = item["cleaned"][:40].replace("\n", " ")
            label = f"{item['ts'][11:16]}  {item['duration']:>4}s  {preview}"
            li = QListWidgetItem(label)
            li.setData(Qt.ItemDataRole.UserRole, item)
            self.list.addItem(li)

    def _on_added(self, item: dict):
        self.history.append(item)
        self.history = self.history[-MAX_HISTORY:]
        self._reload_list()

    def _on_state(self, state: str):
        msg = {
            "idle":    "空闲 · 按住 CapsLock 录音",
            "loading": "正在加载 Whisper 模型...",
            "rec":     "🔴 录音中...",
            "proc":    "⏳ 处理中（转写 + AI 清洗）...",
        }
        self.status.setText(msg.get(state, state))

    def _selected_item(self):
        li = self.list.currentItem()
        return None if li is None else li.data(Qt.ItemDataRole.UserRole)

    def _on_select(self):
        item = self._selected_item()
        if item is None:
            self.detail.clear()
            return
        text = (
            f"原始: {item['raw']}\n\n"
            f"AI  : {item['cleaned']}\n\n"
            f"耗时: STT {item['stt_sec']}s + AI {item['ai_sec']}s · 录音 {item['duration']}s"
        )
        self.detail.setPlainText(text)

    def _copy(self, key: str):
        item = self._selected_item()
        if item is None:
            return
        pyperclip.copy(item[key])
        log(f"copied {key}")

    def _repaste(self):
        item = self._selected_item()
        if item is None:
            return
        self.hide()
        QTimer.singleShot(180, lambda: self._do_paste(item["cleaned"]))

    def _do_paste(self, text: str):
        pyperclip.copy(text)
        time.sleep(0.06)
        kb.send("ctrl+v")
        log("re-pasted")


# ===== Main =====
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    backend = Backend()
    panel = HistoryPanel()
    ball = FloatingBall(panel)
    ball.show()

    # load model in background so UI is responsive immediately
    backend.start_async()

    app.aboutToQuit.connect(backend.stop)
    log("UI ready, model loading in background")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
