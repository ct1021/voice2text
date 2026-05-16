"""Config loading: read config.toml, auto-generate from example on first run."""
import tomllib
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "config.toml"
EXAMPLE_FILE = APP_DIR / "config.example.toml"

# Fallback defaults — used to fill any key missing from config.toml so callers
# never hit a KeyError. Keep in sync with config.example.toml.
DEFAULTS = {
    "hotkey": {"key": "caps lock"},
    "stt": {
        "backend": "faster-whisper",
        "model": "medium",
        "device": "cpu",
        "language": "zh",
    },
    "audio": {"device": "", "sample_rate": 16000},
    "ai": {
        "backend": "claude-sdk",
        "prompt": "default",
        "anthropic": {
            "api_key_env": "ANTHROPIC_API_KEY",
            "model": "claude-haiku-4-5-20251001",
        },
        "openai": {
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
        },
        "prompts": {
            "default": (
                "你是语音转写清洗专家。修正 ASR 转写错误（尤其技术专有名词），"
                "加合理标点，保持原意不增删。只输出清洗后的纯文本一行，不要解释。"
            ),
            "requirement": (
                "你是需求整理助手。把下面这段口述整理成 markdown 需求清单，"
                "每条以 - 开头，提炼要点、去掉口水话。只输出清单。"
            ),
            "translate": (
                "你是翻译助手。把下面这段中文翻译成自然流畅的英文。只输出译文。"
            ),
        },
    },
    "ui": {"ball_size": 44, "always_on_top": True},
}


def _merge(base: dict, override: dict) -> dict:
    """Deep-merge override onto base, returning a new dict."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    """Load config.toml, creating it from config.example.toml if absent.

    Any missing key falls back to DEFAULTS, so callers can index freely.
    """
    if not CONFIG_FILE.exists() and EXAMPLE_FILE.exists():
        try:
            CONFIG_FILE.write_text(
                EXAMPLE_FILE.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except Exception:
            pass

    user_config: dict = {}
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("rb") as f:
                user_config = tomllib.load(f)
        except Exception as e:
            print(f"[config] config.toml parse error, using defaults: {e}")

    return _merge(DEFAULTS, user_config)
