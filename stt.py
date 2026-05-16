"""Speech-to-text engines. Selected via config [stt] backend.

Each engine implements STTEngine.transcribe(audio, glossary) -> str.
audio is a float32 mono numpy array at 16 kHz.
"""
from pathlib import Path

import numpy as np

APP_DIR = Path(__file__).resolve().parent
MODEL_DIR = APP_DIR / "models"


class STTEngine:
    """Interface for a speech-to-text engine."""

    def transcribe(self, audio: np.ndarray, glossary: str) -> str:
        raise NotImplementedError

    @property
    def name(self) -> str:
        return type(self).__name__


class FasterWhisperEngine(STTEngine):
    """faster-whisper. Model auto-downloads on first use. Default engine."""

    def __init__(self, model: str = "medium", device: str = "cpu",
                 language: str = "zh"):
        from faster_whisper import WhisperModel
        self._language = language
        compute_type = "int8" if device == "cpu" else "float16"
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        self._model = WhisperModel(
            model, device=device, compute_type=compute_type,
            download_root=str(MODEL_DIR),
        )

    def transcribe(self, audio: np.ndarray, glossary: str) -> str:
        segments, _ = self._model.transcribe(
            audio,
            language=self._language,
            initial_prompt=glossary,
            beam_size=1,
            vad_filter=True,
        )
        return "".join(s.text for s in segments).strip()


class SenseVoiceEngine(STTEngine):
    """SenseVoice via sherpa-onnx. Faster + better Chinese, but the model
    must be downloaded manually first (see README)."""

    def __init__(self, language: str = "zh"):
        import sherpa_onnx
        sv_dir = MODEL_DIR / "sensevoice"
        model = sv_dir / "model.int8.onnx"
        tokens = sv_dir / "tokens.txt"
        if not model.exists() or not tokens.exists():
            raise RuntimeError(
                f"SenseVoice 模型未找到（应在 {sv_dir}）。"
                "请见 README「切换 STT 引擎」一节下载模型，"
                "或把 config.toml 的 [stt] backend 改回 faster-whisper。"
            )
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model),
            tokens=str(tokens),
            num_threads=4,
            use_itn=True,
            language=language,
        )

    def transcribe(self, audio: np.ndarray, glossary: str) -> str:
        # SenseVoice has no initial_prompt mechanism; glossary is ignored here
        # (the AI cleanup step still corrects terms downstream).
        stream = self._recognizer.create_stream()
        stream.accept_waveform(16000, audio)
        self._recognizer.decode_stream(stream)
        return stream.result.text.strip()


def make_engine(stt_config: dict) -> STTEngine:
    """Build the STT engine described by config [stt]. May raise on bad config."""
    backend = stt_config.get("backend", "faster-whisper")
    language = stt_config.get("language", "zh")
    if backend == "faster-whisper":
        return FasterWhisperEngine(
            model=stt_config.get("model", "medium"),
            device=stt_config.get("device", "cpu"),
            language=language,
        )
    if backend == "sensevoice":
        return SenseVoiceEngine(language=language)
    raise ValueError(f"未知 STT 后端: {backend}")
