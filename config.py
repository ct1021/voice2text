"""Config loading: read config.toml, auto-generate from example on first run."""
import os
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
        "backend": "sensevoice",
        "model": "medium",
        "device": "cpu",
        "language": "zh",
        "volcengine": {
            "app_id_env": "VOLC_ASR_APP_ID",
            "access_token_env": "VOLC_ASR_ACCESS_TOKEN",
            "resource_id": "volc.bigasr.auc",
        },
    },
    "audio": {"device": "", "sample_rate": 16000},
    "ai": {
        "backend": "claude-sdk",
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
            "markdown": (
                "你是语音内容整理助手。把用户口述的内容整理成结构清晰的 "
                "Markdown：先给一个 ## 标题，再用列表分点；去掉口语冗余和"
                "口水词；专有名词严格以参考词典为准、不要写错；不要增加原文"
                "没有的信息。直接输出 Markdown 正文，不要解释。"
            ),
        },
    },
    "ui": {"ball_size": 44, "always_on_top": True, "demo_mode": False,
           "ball_size_demo": 80},
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


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from .env into os.environ (existing vars win)."""
    env_file = APP_DIR / ".env"
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


def load_config() -> dict:
    """Load config.toml, creating it from config.example.toml if absent.

    Also loads .env into os.environ. Missing config keys fall back to
    DEFAULTS, so callers can index freely.
    """
    _load_dotenv()
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
