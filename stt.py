"""Speech-to-text engines. Selected via config [stt] backend.

Each engine implements STTEngine.transcribe(audio, glossary) -> str.
audio is a float32 mono numpy array at 16 kHz.
"""
from pathlib import Path

import numpy as np

APP_DIR = Path(__file__).resolve().parent
MODEL_DIR = APP_DIR / "models"

# SenseVoice int8 模型（sherpa-onnx 官方发布，约 230MB）
SENSEVOICE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"
)


def _download_sensevoice(sv_dir: Path) -> None:
    """首次使用 SenseVoice 时自动下载并解压模型到 models/sensevoice/。"""
    import tarfile
    import urllib.request

    sv_dir.mkdir(parents=True, exist_ok=True)
    archive = sv_dir / "_sensevoice.tar.bz2"
    print("[voice2text] 首次使用 SenseVoice，正在下载语音模型（约 230MB），请稍候…")

    def _hook(blocks: int, bsize: int, total: int) -> None:
        if total > 0:
            pct = min(100, blocks * bsize * 100 // total)
            print(f"\r  下载中… {pct}%", end="", flush=True)

    try:
        urllib.request.urlretrieve(SENSEVOICE_URL, archive, _hook)
        print()
        with tarfile.open(archive, "r:bz2") as tar:
            for m in tar.getmembers():
                base = Path(m.name).name
                if base in ("model.int8.onnx", "tokens.txt"):
                    m.name = base  # 扁平化，去掉压缩包内层目录
                    tar.extract(m, sv_dir)
    finally:
        if archive.exists():
            archive.unlink()
    if not (sv_dir / "model.int8.onnx").exists():
        raise RuntimeError("下载完成但未找到 model.int8.onnx")


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
    """SenseVoice via sherpa-onnx. Default engine — fast + accurate Chinese.
    Model (~230MB) auto-downloads on first use."""

    def __init__(self, language: str = "zh"):
        import sherpa_onnx
        sv_dir = MODEL_DIR / "sensevoice"
        model = sv_dir / "model.int8.onnx"
        tokens = sv_dir / "tokens.txt"
        if not model.exists() or not tokens.exists():
            try:
                _download_sensevoice(sv_dir)
            except Exception as e:
                raise RuntimeError(
                    f"SenseVoice 模型下载失败：{e}\n"
                    "请检查网络后重启重试；或把 config.toml 的 [stt] backend "
                    "改成 faster-whisper（首次会自动下载约 1.5GB 模型）。"
                ) from e
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


class VolcengineEngine(STTEngine):
    """火山引擎大模型录音文件识别标准版（云端）。

    submit 提交任务 + query 轮询结果两步；音频会上传到火山云端。
    凭证从 .env 读（VOLC_ASR_APP_ID / VOLC_ASR_ACCESS_TOKEN）。
    """

    SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
    QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"

    def __init__(self, app_id_env: str = "VOLC_ASR_APP_ID",
                 access_token_env: str = "VOLC_ASR_ACCESS_TOKEN",
                 resource_id: str = "volc.bigasr.auc"):
        import os
        self._app_id = os.environ.get(app_id_env, "").strip()
        self._access_token = os.environ.get(access_token_env, "").strip()
        if not self._app_id or not self._access_token:
            raise RuntimeError(
                f"火山引擎凭证缺失：请在 .env 设置 {app_id_env} 与 {access_token_env}"
            )
        self._resource_id = resource_id

    @staticmethod
    def _to_wav_bytes(audio: np.ndarray) -> bytes:
        """float32 [-1,1] mono 16k -> 16-bit PCM WAV 字节串。"""
        import io
        import wave
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()

    def _post(self, url: str, headers: dict, payload: dict):
        """POST JSON，返回 (响应头对象, 响应体文本)。"""
        import json
        import urllib.error
        import urllib.request
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.headers, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            raise RuntimeError(f"火山引擎 HTTP {e.code}: {detail}")
        except Exception as e:
            raise RuntimeError(f"火山引擎请求失败: {e}")

    def _auth_headers(self, task_id: str) -> dict:
        return {
            "Content-Type": "application/json",
            "X-Api-App-Key": self._app_id,
            "X-Api-Access-Key": self._access_token,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Request-Id": task_id,
        }

    def transcribe(self, audio: np.ndarray, glossary: str) -> str:
        # glossary 不在此处使用；云端识别后由下游 AI 清洗纠正术语。
        import base64
        import json
        import time
        import uuid

        audio_b64 = base64.b64encode(self._to_wav_bytes(audio)).decode()
        task_id = str(uuid.uuid4())

        # 1) 提交任务
        submit_headers = self._auth_headers(task_id)
        submit_headers["X-Api-Sequence"] = "-1"
        submit_body = {
            "user": {"uid": self._app_id},
            "audio": {"format": "wav", "rate": 16000, "data": audio_b64},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
            },
        }
        hdrs, _ = self._post(self.SUBMIT_URL, submit_headers, submit_body)
        code = hdrs.get("X-Api-Status-Code", "")
        if code != "20000000":
            raise RuntimeError(
                f"火山引擎 submit 失败 (status={code}): "
                f"{hdrs.get('X-Api-Message', '')}"
            )
        logid = hdrs.get("X-Tt-Logid", "")

        # 2) 轮询结果（短音频通常几秒内完成）
        query_headers = self._auth_headers(task_id)
        query_headers["X-Tt-Logid"] = logid
        for _ in range(60):
            hdrs, raw = self._post(self.QUERY_URL, query_headers, {})
            code = hdrs.get("X-Api-Status-Code", "")
            if code == "20000000":
                body = json.loads(raw) if raw.strip() else {}
                result = body.get("result") or {}
                return (result.get("text") or "").strip()
            if code == "20000003":
                return ""  # 无有效语音（静音 / 太短）
            if code in ("20000001", "20000002"):
                time.sleep(0.5)
                continue
            raise RuntimeError(
                f"火山引擎 query 失败 (status={code}): "
                f"{hdrs.get('X-Api-Message', '')}"
            )
        raise RuntimeError("火山引擎 query 轮询超时")


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
    if backend == "volcengine":
        v = stt_config.get("volcengine", {})
        return VolcengineEngine(
            app_id_env=v.get("app_id_env", "VOLC_ASR_APP_ID"),
            access_token_env=v.get("access_token_env", "VOLC_ASR_ACCESS_TOKEN"),
            resource_id=v.get("resource_id", "volc.bigasr.auc"),
        )
    raise ValueError(f"未知 STT 后端: {backend}")
