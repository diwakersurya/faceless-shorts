"""
Optional adapters for the best free / open-source tools on GitHub.

Every integration in this module is OPTIONAL. Detection is FAST (subprocess
probes are capped at 3s, HTTP probes at 1.0s), nothing is ever downloaded
unless you explicitly ask for it, and every failure degrades to ``None`` /
``False`` instead of raising, so the core pipeline never crashes when a tool
is missing.

Projects credited (all free / open source):

- whisper.cpp — local speech-to-text, used through FFmpeg's built-in
  ``whisper`` filter (no pip install needed)
  https://github.com/ggerganov/whisper.cpp
- ComfyUI — local Stable Diffusion txt2img node UI
  https://github.com/comfyanonymous/ComfyUI
- FastVideo / FastMetal — local Apple Silicon (MLX) text-to-video
  https://github.com/hao-ai-lab/FastVideo
- mflux — local Apple Silicon (MLX) text-to-image (Z-Image-Turbo, FLUX.2 klein)
  https://github.com/filipstrand/mflux
- yt-dlp — feature-rich media downloader (B-roll / reference footage)
  https://github.com/yt-dlp/yt-dlp
- Ollama — local LLM runtime for script writing
  https://github.com/ollama/ollama
- Gemini API — Google's online models for scripts (free limits), images and video (paid)
  https://ai.google.dev/gemini-api/docs
- Pexels API — free stock video clips
  https://www.pexels.com/api/
- Pixabay API — free stock video clips (fallback)
  https://pixabay.com/api/docs/

Author: su6osec (https://github.com/su6osec)
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from typing import Any, Dict, List, Optional, Sequence, Tuple

from faceless_shorts.config import (
    DEFAULT_GEMINI_VIDEO_RESOLUTION,
    GEMINI_MODELS,
    GEMINI_VIDEO_PRICE,
    MFLUX_MODELS,
)
from urllib import error as urlerror
from urllib.parse import urlencode
from urllib import request as urlrequest

__all__ = [
    "ToolRegistry",
    "WhisperTranscriber",
    "ComfyUIClient",
    "FastMetalClient",
    "MfluxClient",
    "GeminiClient",
    "YtDlpHelper",
    "LLMScriptWriter",
    "StockBRoll",
    "doctor",
    "doctor_report",
]

# --------------------------------------------------------------------------- #
# Tunables: detection must be FAST, transfers may be slow, nothing crashes.
# --------------------------------------------------------------------------- #
SUBPROCESS_TIMEOUT = 3.0        # hard cap for any `ffmpeg` / `yt-dlp` probe
HTTP_TIMEOUT = 1.0              # hard cap for availability probes
HTTP_INFO_TIMEOUT = 5.0         # ComfyUI /object_info (local, can be large)
HTTP_SEARCH_TIMEOUT = 10.0      # stock API searches
HTTP_SUBMIT_TIMEOUT = 10.0      # ComfyUI POST /prompt
HTTP_POLL_TIMEOUT = 2.0         # ComfyUI /history polling
HTTP_IO_TIMEOUT = 30.0          # streaming downloads (urllib socket timeout)
COMFY_POLL_INTERVAL = 0.5       # seconds between /history polls
COMFY_POLL_TIMEOUT = 120.0      # total seconds to wait for a ComfyUI render
YTDLP_TIMEOUT = 300.0           # cap for a single yt-dlp download
TRANSCRIBE_TIMEOUT = 600.0      # cap for an ffmpeg whisper transcription
FASTMETAL_TIMEOUT = 1800.0      # cap for one FastMetal scene clip (text encode + denoise + decode)
MFLUX_TIMEOUT = 900.0           # cap for one mflux image (the first one also downloads the model)
GEMINI_TIMEOUTS = {"text": 120.0, "tts": 120.0, "transcribe": 120.0, "image": 180.0, "video": 900.0}  # cap for one Gemini request, by what it makes
GEMINI_BUSY_WAIT = 5.0          # pause before asking again when Gemini answers "busy" (HTTP 503)
GEMINI_TTS_RATE = 24000         # samples per second of the sound Gemini's speech models send
GEMINI_MIN_SECONDS = 3          # shortest and longest clip Gemini Omni Flash generates
GEMINI_MAX_SECONDS = 15

DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
DEFAULT_FASTMETAL_HOME = "~/fastmetal-video"
# --fast generates half the frames and interpolates the rest; a 16-18 GB Mac needs it
# (the full-frame decode runs out of GPU memory). Bigger Macs can set FACELESS_FASTMETAL_ARGS="".
DEFAULT_FASTMETAL_ARGS = "--fast"
FASTMETAL_FPS = 16              # native frame rate of the Wan 2.1 1.3B model
FASTMETAL_MAX_FRAMES = 81       # longest validated clip (~5 s)
FASTMETAL_MIN_FRAMES = 17
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"  # GEMINI_API_URL replaces it
GEMINI_API_REVISION = "2026-05-20"
DEFAULT_CHECKPOINT = "v1-5-pruned-emaonly.safetensors"
DEFAULT_NEGATIVE_PROMPT = "lowres, blurry, watermark, text"
HF_WHISPER_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
DEFAULT_LLM_MODEL = "gpt-4o-mini"

# Tool keys reported by doctor() / doctor_report().
TOOL_KEYS: Tuple[str, ...] = ("whisper", "comfyui", "mflux", "fastmetal", "gemini", "ytdlp", "llm", "stock")

# rich is an optional dependency; plain aligned text is the fallback.
try:  # pragma: no cover - exercised implicitly when rich is installed
    from rich.console import Console as _RichConsole
    from rich.table import Table as _RichTable
except Exception:  # pragma: no cover
    _RichConsole = None
    _RichTable = None


# --------------------------------------------------------------------------- #
# Tiny helpers (never raise, never hang)
# --------------------------------------------------------------------------- #
def _env(name: str, default: str = "") -> str:
    """Read an env var, treating missing/blank as unset."""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _http_request(
    url: str,
    data: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = HTTP_TIMEOUT,
) -> Optional[Tuple[int, bytes]]:
    """Perform a GET/POST and return ``(status, body_bytes)`` or ``None``.

    Never raises: network errors, DNS failures and timeouts all return None,
    HTTP error statuses are returned as-is so callers can inspect them.
    """
    try:
        payload = None
        if data is not None:
            payload = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        request = urlrequest.Request(url, data=payload)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        if payload is not None and not any(k.lower() == "content-type" for k in (headers or {})):
            request.add_header("Content-Type", "application/json")
        with urlrequest.urlopen(request, timeout=timeout) as response:
            return int(response.getcode() or 200), response.read()
    except urlerror.HTTPError as exc:  # HTTPError is a subclass of URLError
        try:
            body = exc.read()
        except Exception:
            body = b""
        return int(exc.code), body
    except Exception:
        return None


def _http_json(
    url: str,
    data: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = HTTP_TIMEOUT,
) -> Any:
    """GET/POST and parse the body as JSON. Returns parsed JSON or ``None``."""
    result = _http_request(url, data=data, headers=headers, timeout=timeout)
    if result is None:
        return None
    status, body = result
    if status != 200 or not body:
        return None
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return None


def _http_ok(url: str, timeout: float = HTTP_TIMEOUT, headers: Optional[Dict[str, str]] = None) -> bool:
    """True when *url* answers with a 2xx status within *timeout* seconds."""
    result = _http_request(url, headers=headers, timeout=timeout)
    return bool(result and 200 <= result[0] < 300)


def _download(url: str, dest_path: str, timeout: float = HTTP_IO_TIMEOUT,
              headers: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Stream *url* to *dest_path* atomically (``.part`` then rename).

    Returns the destination path on success, ``None`` on any failure.
    """
    part_path = dest_path + ".part"
    try:
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        request = urlrequest.Request(url, headers=headers or {})
        with urlrequest.urlopen(request, timeout=timeout) as response:
            with open(part_path, "wb") as handle:
                while True:
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    handle.write(chunk)
        os.replace(part_path, dest_path)
        return dest_path
    except Exception:
        try:
            os.unlink(part_path)
        except OSError:
            pass
        return None


def _ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def _ffmpeg_version() -> Optional[str]:
    exe = _ffmpeg_path()
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-version"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    first = (proc.stdout or "").splitlines()
    first = first[0] if first else ""
    match = re.search(r"ffmpeg version (\S+)", first)
    if match:
        return match.group(1)
    return first.replace("ffmpeg version", "").strip() or None


def _ffmpeg_has_whisper_filter() -> bool:
    """True when the local ffmpeg build ships the ``whisper`` filter."""
    exe = _ffmpeg_path()
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except Exception:
        return False
    blob = (proc.stdout or "") + (proc.stderr or "")
    for line in blob.splitlines():
        parts = line.split()
        # Filter listing lines look like:  ".. whisper    A->A   description"
        if len(parts) >= 2 and parts[1] == "whisper":
            return True
    return False


def model_cache_dir() -> str:
    """Where whisper.cpp ggml models live (override: FACELESS_MODELS_DIR)."""
    custom = _env("FACELESS_MODELS_DIR")
    if custom:
        return custom
    return os.path.join(os.path.expanduser("~"), ".cache", "faceless-shorts", "models")


def user_settings_path() -> str:
    """Settings kept for this computer, shared by the CLI and the web app (override: FACELESS_SETTINGS_FILE)."""
    return _env("FACELESS_SETTINGS_FILE") or os.path.join(
        os.path.expanduser("~"), ".config", "faceless-shorts", "settings.json"
    )


def load_user_settings() -> Dict[str, Any]:
    """Read the settings file; a missing or unreadable file is an empty dict."""
    try:
        with open(user_settings_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_user_settings(changes: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *changes* into the settings file. A value of None or "" removes that setting."""
    settings = load_user_settings()
    for key, value in changes.items():
        if value in (None, ""):
            settings.pop(key, None)
        else:
            settings[key] = value
    path = user_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # The file can hold an API key: only its owner may read it, from the moment it exists.
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    os.chmod(path, 0o600)  # a file made before keys were kept here may be readable by others
    return settings


def _extract_json(text: Any) -> Optional[Dict[str, Any]]:
    """Pull the first JSON object out of possibly fenced / prose-wrapped text."""
    if not isinstance(text, str) or not text.strip():
        return None
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.S)
    candidates = []
    if fence:
        candidates.append(fence.group(1).strip())
    candidates.append(stripped)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    # Fall back to the first balanced {...} block in the raw text.
    start = stripped.find("{")
    while start != -1:
        depth = 0
        end = None
        for index in range(start, len(stripped)):
            char = stripped[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end is not None:
            try:
                parsed = json.loads(stripped[start:end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
        start = stripped.find("{", start + 1)
    return None


# --------------------------------------------------------------------------- #
# Tool registry / doctor
# --------------------------------------------------------------------------- #
class ToolRegistry:
    """Fast availability probing for every optional tool."""

    @staticmethod
    def doctor() -> Dict[str, Dict[str, Any]]:
        """Return ``{tool_key: {available, version, kind, install, hint}}``."""
        report: Dict[str, Dict[str, Any]] = {}

        # --- whisper: local STT via FFmpeg's whisper.cpp filter -------------
        report["whisper"] = {
            "available": bool(WhisperTranscriber.available()),
            "version": _ffmpeg_version(),
            "kind": "local STT (ffmpeg whisper filter)",
            "install": "brew install ffmpeg (built with libwhisper)",
            "hint": "https://github.com/ggerganov/whisper.cpp - models via ensure_model()",
        }

        # --- comfyui: local txt2img ----------------------------------------
        comfy = ComfyUIClient()
        comfy_ok = comfy.available()
        comfy_version = None
        if comfy_ok:
            stats = _http_json(f"{comfy.base_url}/system_stats", timeout=HTTP_TIMEOUT)
            if isinstance(stats, dict):
                comfy_version = str(((stats.get("system") or {}).get("comfyui_version")) or "") or None
        report["comfyui"] = {
            "available": bool(comfy_ok),
            "version": comfy_version,
            "kind": "local text-to-image",
            "install": "git clone https://github.com/comfyanonymous/ComfyUI",
            "hint": "run it on :8188 or set FACELESS_COMFY_URL",
        }

        # --- mflux: local text-to-image ---------------------------------------
        mflux = MfluxClient()
        mflux_problem = mflux.problem()
        report["mflux"] = {
            "available": mflux_problem is None,
            "version": None,
            "kind": "local text-to-image (Apple Silicon)",
            "install": "uv tool install --python 3.12 mflux",
            "hint": mflux_problem[1] if mflux_problem else "--engine " + " | ".join(MFLUX_MODELS),
        }

        # --- fastmetal: local text-to-video ---------------------------------
        fastmetal_problem = FastMetalClient().problem()
        report["fastmetal"] = {
            "available": fastmetal_problem is None,
            "version": "1.3B-QAD" if fastmetal_problem is None else None,
            "kind": "local text-to-video (Apple Silicon)",
            "install": "https://github.com/hao-ai-lab/FastVideo + hf download FastVideo/FastMetal-1.3B-QAD",
            "hint": (
                f"{fastmetal_problem[0]} does not exist. {fastmetal_problem[1]}"
                if fastmetal_problem else "--video-engine fastmetal"
            ),
        }

        # --- gemini: Google's online models ------------------------------------
        gemini_problem = GeminiClient.problem()
        report["gemini"] = {
            "available": gemini_problem is None,
            "version": GeminiClient.model("text") if gemini_problem is None else None,
            "kind": "online script and voice (free limits), image and video (paid) models",
            "install": "key from https://aistudio.google.com/apikey",
            "hint": (
                gemini_problem[1] if gemini_problem
                else "--script-engine gemini | --tts-engine gemini | --engine gemini | --video-engine gemini"
            ),
        }

        # --- yt-dlp: media downloader ---------------------------------------
        ytdlp_version = YtDlpHelper.version()
        report["ytdlp"] = {
            "available": bool(YtDlpHelper.available()),
            "version": ytdlp_version,
            "kind": "B-roll media downloader",
            "install": "python -m pip install -U yt-dlp",
            "hint": "https://github.com/yt-dlp/yt-dlp",
        }

        # --- llm: Ollama or any OpenAI-compatible endpoint -------------------
        llm_backend = LLMScriptWriter._backend()
        llm_version = None
        if llm_backend is not None:
            if llm_backend[0] == "ollama":
                info = _http_json(f"{llm_backend[1]}/api/version", timeout=HTTP_TIMEOUT)
                if isinstance(info, dict) and info.get("version"):
                    llm_version = str(info["version"])
            else:
                llm_version = "openai-compatible"
        report["llm"] = {
            "available": llm_backend is not None,
            "version": llm_version,
            "kind": "free script-writing LLM",
            "install": "https://github.com/ollama/ollama -> ollama serve",
            "hint": "OLLAMA_HOST, or LLM_BASE_URL + LLM_API_KEY",
        }

        # --- stock: free-tier stock footage ---------------------------------
        pexels = bool(_env("PEXELS_API_KEY"))
        pixabay = bool(_env("PIXABAY_API_KEY"))
        providers = "+".join(
            name for name, present in (("pexels", pexels), ("pixabay", pixabay)) if present
        )
        report["stock"] = {
            "available": bool(pexels or pixabay),
            "version": providers or None,
            "kind": "free stock B-roll",
            "install": "free keys: pexels.com/api + pixabay.com/api/docs",
            "hint": "export PEXELS_API_KEY=... or PIXABAY_API_KEY=...",
        }

        return report

    @staticmethod
    def available(tool: str) -> bool:
        """Return whether *tool* (a doctor() key) is usable. Unknown -> False."""
        if not isinstance(tool, str):
            return False
        handlers = {
            "whisper": WhisperTranscriber.available,
            "comfyui": ComfyUIClient().available,
            "mflux": MfluxClient.available,
            "fastmetal": FastMetalClient.available,
            "gemini": GeminiClient.available,
            "ytdlp": YtDlpHelper.available,
            "llm": LLMScriptWriter.available,
            "stock": StockBRoll.available,
        }
        handler = handlers.get(tool)
        if handler is None:
            return False
        try:
            return bool(handler())
        except Exception:
            return False


def doctor() -> Dict[str, Dict[str, Any]]:
    """Module-level convenience wrapper around ``ToolRegistry.doctor()``."""
    return ToolRegistry.doctor()


# --------------------------------------------------------------------------- #
# whisper.cpp via FFmpeg's built-in `whisper` filter (no pip install needed)
# --------------------------------------------------------------------------- #
class WhisperTranscriber:
    """Local speech-to-text through ``ffmpeg -af whisper=...``."""

    SIZES: Tuple[str, ...] = ("tiny", "base", "small")

    @staticmethod
    def available() -> bool:
        """True when ffmpeg is present AND ships the ``whisper`` filter."""
        return _ffmpeg_has_whisper_filter()

    @staticmethod
    def _model_filename(size: str, language: Optional[str]) -> str:
        lang = (language or "").strip().lower()
        if lang == "en" or lang.startswith("en-"):
            return f"ggml-{size}.en.bin"
        return f"ggml-{size}.bin"

    @classmethod
    def _model_url(cls, size: str, language: Optional[str]) -> str:
        return f"{HF_WHISPER_BASE}/{cls._model_filename(size, language)}"

    @classmethod
    def ensure_model(cls, language: str = "en") -> Optional[str]:
        """Explicitly download the ggml model (huggingface, free).

        Called ONLY on demand - never automatically by ``transcribe()``.
        Streams to ``<cache>/*.part`` then renames atomically. Returns the
        absolute model path, or ``None`` on any failure.
        """
        size = _env("FACELESS_WHISPER_MODEL", "base")
        directory = model_cache_dir()
        destination = os.path.join(directory, cls._model_filename(size, language))
        if os.path.isfile(destination) and os.path.getsize(destination) > 0:
            return destination  # already cached: no network access
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception:
            return None
        return _download(cls._model_url(size, language), destination, timeout=HTTP_IO_TIMEOUT)

    @classmethod
    def _find_model(cls, model_size: str, language: Optional[str]) -> Optional[str]:
        """Locate a cached model for (size, language) without downloading."""
        directory = model_cache_dir()
        names: List[str] = []
        if language:
            names.append(cls._model_filename(model_size, language))
        plain = f"ggml-{model_size}.bin"
        english = f"ggml-{model_size}.en.bin"
        for name in (plain, english):
            if name not in names:
                names.append(name)
        for name in names:
            path = os.path.join(directory, name)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                return path
        return None

    @classmethod
    def transcribe(
        cls,
        media_path: Any,
        language: Optional[str] = None,
        out_format: str = "json",
        model_size: str = "base",
    ) -> Optional[Any]:
        """Run the ffmpeg ``whisper`` filter on *media_path*.

        Returns the parsed JSON dict (``out_format="json"``), a list of lines
        (text/srt), or ``None`` on any failure. NEVER downloads a model: when
        the model file is absent it simply returns ``None``.
        """
        try:
            if not media_path or not os.path.isfile(str(media_path)):
                return None
            if out_format not in ("json", "text", "srt"):
                return None  # unsupported by the whisper filter
            model_path = cls._find_model(model_size, language)
            if not model_path:
                return None  # no model cached -> do NOT auto-download
            exe = _ffmpeg_path()
            if not exe or not cls.available():
                return None
            destination = os.path.join(
                tempfile.gettempdir(), f"faceless_whisper_{uuid.uuid4().hex}.json"
            )
            filter_expr = (
                f"whisper=model={model_path}:format={out_format}:destination={destination}"
            )
            if language:
                filter_expr += f":language={language}"
            cmd = [
                exe,
                "-hide_banner",
                "-loglevel", "error",
                "-i", str(media_path),
                "-map", "0:a",
                "-af", filter_expr,
                "-f", "null", "-",
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=TRANSCRIBE_TIMEOUT,
                )
                if proc.returncode != 0:
                    return None
                if not os.path.isfile(destination):
                    return None
                try:
                    with open(destination, encoding="utf-8") as handle:
                        payload = handle.read()
                finally:
                    try:
                        os.unlink(destination)
                    except OSError:
                        pass
                if not payload.strip():
                    return None
                if out_format == "json":
                    parsed = json.loads(payload)
                    return parsed if isinstance(parsed, (dict, list)) else None
                return [line for line in payload.splitlines() if line.strip()]
            finally:
                if os.path.exists(destination):
                    try:
                        os.unlink(destination)
                    except OSError:
                        pass
        except Exception:
            return None


# --------------------------------------------------------------------------- #
# ComfyUI (local Stable Diffusion txt2img)
# --------------------------------------------------------------------------- #
class ComfyUIClient:
    """Minimal client for a stock local ComfyUI install."""

    def __init__(self, base_url: Optional[str] = None) -> None:
        self._base_url = (base_url or "").strip().rstrip("/") or None

    @property
    def base_url(self) -> str:
        """ComfyUI root: explicit arg > FACELESS_COMFY_URL > 127.0.0.1:8188."""
        if self._base_url:
            return self._base_url
        return _env("FACELESS_COMFY_URL").rstrip("/") or DEFAULT_COMFY_URL

    @staticmethod
    def available() -> bool:
        """True when a ComfyUI server answers on its /system_stats endpoint."""
        return _http_ok(f"{ComfyUIClient().base_url}/system_stats", timeout=HTTP_TIMEOUT)

    def _pick_checkpoint(self) -> Optional[str]:
        """Ask the server which checkpoints exist; prefer the v1.5 default."""
        names: List[str] = []
        for path in ("/object_info", "/object_info/CheckpointLoaderSimple"):
            info = _http_json(f"{self.base_url}{path}", timeout=HTTP_INFO_TIMEOUT)
            if not isinstance(info, dict):
                continue
            try:
                if path == "/object_info":
                    spec = info["CheckpointLoaderSimple"]
                else:
                    spec = info
                names = [str(name) for name in spec["input"]["required"]["checkpoint"][0]]
            except Exception:
                names = []
            if names:
                break
        if not names:
            return DEFAULT_CHECKPOINT
        if DEFAULT_CHECKPOINT in names:
            return DEFAULT_CHECKPOINT
        return names[0]

    @staticmethod
    def _first_image(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        outputs = entry.get("outputs") or {}
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            for image in node_output.get("images") or []:
                if isinstance(image, dict) and image.get("filename"):
                    return image
        return None

    def _wait_for_history(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + COMFY_POLL_TIMEOUT
        while time.monotonic() < deadline:
            history = _http_json(
                f"{self.base_url}/history/{prompt_id}", timeout=HTTP_POLL_TIMEOUT
            )
            if isinstance(history, dict) and history:
                entry = history.get(prompt_id)
                if not isinstance(entry, dict):
                    entry = next(iter(history.values()), None)
                if isinstance(entry, dict):
                    status = entry.get("status") or {}
                    if status.get("status_str") == "error":
                        return None
                    return entry
            time.sleep(COMFY_POLL_INTERVAL)
        return None

    def generate(
        self,
        prompt: str,
        output_path: str,
        width: int = 1080,
        height: int = 1920,
        seed: Optional[int] = None,
        negative: str = DEFAULT_NEGATIVE_PROMPT,
        steps: int = 20,
        cfg: float = 6.5,
    ) -> Optional[str]:
        """Run a stock txt2img workflow and save the PNG to *output_path*.

        Returns ``output_path`` on success, ``None`` on any error (including
        an unreachable server). All HTTP goes through urllib with timeouts.
        """
        try:
            if not prompt or not output_path:
                return None
            if not self.available():
                return None
            checkpoint = self._pick_checkpoint()
            if not checkpoint:
                return None
            if seed is None:
                seed = random.randrange(0, 2 ** 32)
            workflow: Dict[str, Any] = {
                "1": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": checkpoint},
                },
                "2": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": str(prompt), "clip": ["1", 1]},
                },
                "3": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": str(negative or ""), "clip": ["1", 1]},
                },
                "4": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {
                        "width": int(width),
                        "height": int(height),
                        "batch_size": 1,
                    },
                },
                "5": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": int(seed),
                        "steps": int(steps),
                        "cfg": float(cfg),
                        "sampler_name": "euler",
                        "scheduler": "karras",
                        "denoise": 1.0,
                        "model": ["1", 0],
                        "positive": ["2", 0],
                        "negative": ["3", 0],
                        "latent_image": ["4", 0],
                    },
                },
                "6": {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
                },
                "7": {
                    "class_type": "SaveImage",
                    "inputs": {
                        "images": ["6", 0],
                        "filename_prefix": "faceless_shorts",
                    },
                },
            }
            response = _http_json(
                f"{self.base_url}/prompt",
                data={"prompt": workflow},
                timeout=HTTP_SUBMIT_TIMEOUT,
            )
            if not isinstance(response, dict):
                return None
            prompt_id = response.get("prompt_id")
            if not prompt_id or response.get("error"):
                return None
            entry = self._wait_for_history(str(prompt_id))
            if not entry:
                return None
            image = self._first_image(entry)
            if not image:
                return None
            query = urlencode(
                {
                    "filename": image.get("filename", ""),
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", ""),
                }
            )
            return _download(
                f"{self.base_url}/view?{query}",
                str(output_path),
                timeout=HTTP_IO_TIMEOUT,
            )
        except Exception:
            return None


# --------------------------------------------------------------------------- #
# mflux (local Apple Silicon text-to-image)
# --------------------------------------------------------------------------- #
class MfluxClient:
    """Generates images with a local model through the mflux command-line tools."""

    def __init__(self, model: Optional[str] = None) -> None:
        # *model* is an image-engine key; anything else means the first (fastest) model.
        self.key = model if model in MFLUX_MODELS else next(iter(MFLUX_MODELS))
        self.spec: Dict[str, Any] = MFLUX_MODELS[self.key]

    def command(self, edit: bool = False) -> Optional[str]:
        """Full path of this model's mflux command (edit=True: the one that takes reference photos), or None when mflux is not installed."""
        name = str(self.spec["edit_command"] if edit and self.spec.get("edit_command") else self.spec["command"])
        # `uv tool install` puts commands in ~/.local/bin, which is not always on the PATH of the app that runs us.
        tool_dir = os.path.join(os.path.expanduser("~"), ".local", "bin")
        return shutil.which(name) or shutil.which(name, path=tool_dir)

    def problem(self) -> Optional[Tuple[str, str]]:
        """What is in the way, as (what, how to fix it). None when images can be generated."""
        if self.command() is None:
            return (
                str(self.spec["command"]),
                "mflux is not installed. Run: uv tool install --python 3.12 mflux",
            )
        return None

    @staticmethod
    def available() -> bool:
        """True when mflux is installed (the model itself is downloaded the first time it is used)."""
        return MfluxClient().problem() is None

    def generate(
        self,
        prompt: str,
        output_path: str,
        seed: int = 42,
        portrait: bool = True,
        refs: Sequence[str] = (),
    ) -> Optional[str]:
        """Generate one image into *output_path* (any format Pillow writes). *refs*: photos the image must
        show (your models), used when this model can take them. None on any failure."""
        problem = self.problem()
        if problem:
            print(f"    [!] mflux is not ready: {problem[1]}")
            return None
        width, height = int(self.spec["width"]), int(self.spec["height"])
        if not portrait:
            width, height = height, width
        output_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        raw_path = output_path + ".mflux.png"  # mflux writes PNG; the pipeline wants its own name and format
        refs = list(refs) if self.spec.get("edit_command") else []
        cmd = [
            str(self.command(edit=bool(refs))),
            "--model", str(self.spec["model"]),
            "--prompt", prompt,
            "--width", str(width),
            "--height", str(height),
            "--steps", str(self.spec["steps"]),
            "--seed", str(seed),
            "--output", raw_path,
        ]
        if self.spec.get("base_model"):
            cmd += ["--base-model", str(self.spec["base_model"])]
        if refs:
            cmd += ["--image-paths", *refs]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=MFLUX_TIMEOUT)
            if proc.returncode != 0 or not os.path.exists(raw_path):
                lines = (proc.stderr or proc.stdout or "").strip().splitlines()
                print(f"    [!] mflux failed: {lines[-1] if lines else 'no output'}")
                return None
            from PIL import Image

            with Image.open(raw_path) as image:
                image.convert("RGB").save(output_path, quality=95)
            return output_path
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"    [!] mflux could not run ({exc}).")
            return None
        finally:
            if os.path.exists(raw_path):
                os.remove(raw_path)


# --------------------------------------------------------------------------- #
# FastMetal (local Apple Silicon text-to-video via the FastVideo MLX runtime)
# --------------------------------------------------------------------------- #
class FastMetalClient:
    """Runs a local FastMetal-1.3B install as a subprocess (it lives in its own venv).

    Expected layout under the install root::

        <home>/FastVideo/            clone of github.com/hao-ai-lab/FastVideo with a .venv
        <home>/FastMetal-1.3B-QAD/   hf download FastVideo/FastMetal-1.3B-QAD
    """

    def __init__(self, home: Optional[str] = None) -> None:
        self._home = (home or "").strip() or None

    @property
    def home(self) -> str:
        """Install root: explicit arg > FACELESS_FASTMETAL_HOME > saved setting > ~/fastmetal-video."""
        saved = str(load_user_settings().get("fastmetal_home") or "")
        return os.path.expanduser(self._home or _env("FACELESS_FASTMETAL_HOME") or saved or DEFAULT_FASTMETAL_HOME)

    def _paths(self) -> Tuple[str, str, str]:
        repo = os.path.join(self.home, "FastVideo")
        return (
            os.path.join(repo, ".venv", "bin", "python"),
            os.path.join(repo, "examples", "inference", "basic", "mlx_wan_prompt_to_video.py"),
            os.path.join(self.home, "FastMetal-1.3B-QAD"),
        )

    def _checks(self) -> List[Tuple[str, str]]:
        """Every path the install needs, outermost first, each with the command that creates it."""
        python, script, model = self._paths()
        repo = os.path.join(self.home, "FastVideo")
        hf = os.path.join(repo, ".venv", "bin", "hf")
        return [
            (self.home, "Install FastMetal there (see the README), or say where it is: "
                        "python -m faceless_shorts config --fastmetal-home <folder>"),
            (script, "The FastVideo code is not there. Run: "
                     f"git clone --depth 1 https://github.com/hao-ai-lab/FastVideo.git {shlex.quote(repo)}"),
            (python, "FastVideo's Python environment was not created. Run: "
                     f"cd {shlex.quote(repo)} && uv venv --python 3.12 --seed && "
                     'uv pip install --python .venv/bin/python -e ".[mlx]"'),
            (model, "The model is not downloaded. Run: "
                    f"{shlex.quote(hf)} download FastVideo/FastMetal-1.3B-QAD --local-dir {shlex.quote(model)}"),
        ]

    def missing(self) -> List[str]:
        """Paths the install lacks (empty list = ready to generate)."""
        return [path for path, _ in self._checks() if not os.path.exists(path)]

    def problem(self) -> Optional[Tuple[str, str]]:
        """The first thing the install lacks, as (path, how to fix it). None when it is ready."""
        for path, fix in self._checks():
            if not os.path.exists(path):
                return path, fix
        return None

    @staticmethod
    def available() -> bool:
        """True when the FastVideo venv, the generate script and the model are all present."""
        return not FastMetalClient().missing()

    @staticmethod
    def extra_args() -> List[str]:
        """Extra generator flags: FACELESS_FASTMETAL_ARGS when set (even empty), else --fast."""
        raw = os.environ.get("FACELESS_FASTMETAL_ARGS")
        return shlex.split(DEFAULT_FASTMETAL_ARGS if raw is None else raw)

    @staticmethod
    def frames_for(duration: float) -> int:
        """Frames to request for *duration* seconds: 16 fps, on the model's 4k+1 grid, capped at 81."""
        need = max(FASTMETAL_MIN_FRAMES, math.ceil(max(duration, 0.0) * FASTMETAL_FPS))
        return min(FASTMETAL_MAX_FRAMES, (need + 2) // 4 * 4 + 1)

    def generate(
        self,
        prompt: str,
        output_path: str,
        duration: float,
        portrait: bool = True,
    ) -> Optional[str]:
        """Render one silent clip covering *duration* seconds (at most ~5 s). None on any failure."""
        problem = self.problem()
        if problem:
            print(f"    [!] FastMetal is not ready: {problem[0]} does not exist. {problem[1]}")
            return None
        python, script, model = self._paths()
        height, width = (832, 480) if portrait else (480, 832)
        output_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        if os.path.exists(output_path):
            os.remove(output_path)  # a stale clip must not pass for a fresh one
        cmd = [
            python, script,
            "--model-root", model,
            "--mlx-checkpoint", model,
            "--prompt", prompt,
            "--height", str(height),
            "--width", str(width),
            "--num-frames", str(self.frames_for(duration)),
            # The 11 GB text encoder runs in its own process so its memory is freed before denoising.
            "--prompt-encode-mode", "subprocess",
            "--output-path", output_path,
            *self.extra_args(),
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=os.path.join(self.home, "FastVideo"),
                capture_output=True,
                text=True,
                timeout=FASTMETAL_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"    [!] FastMetal could not run ({exc}).")
            return None
        if proc.returncode != 0 or not os.path.exists(output_path):
            lines = (proc.stderr or proc.stdout or "").strip().splitlines()
            print(f"    [!] FastMetal failed: {lines[-1] if lines else 'no output'}")
            return None
        return output_path


# --------------------------------------------------------------------------- #
# Gemini (Google's online models: script text, images, video clips)
# --------------------------------------------------------------------------- #
class GeminiClient:
    """Scripts, images and video clips from the Gemini API: one key, one endpoint (Interactions)."""

    @staticmethod
    def key_source() -> Optional[Tuple[str, str]]:
        """The API key and where it comes from: the saved setting first, then the environment. None without a key."""
        saved = str(load_user_settings().get("gemini_api_key") or "").strip()
        if saved:
            return saved, "settings"
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            if _env(name):
                return _env(name), name
        return None

    @staticmethod
    def base_url() -> str:
        return (_env("GEMINI_API_URL") or GEMINI_API_URL).rstrip("/")

    @staticmethod
    def model(kind: str) -> str:
        """Model id for "text", "image" or "video": GEMINI_<KIND>_MODEL when set, else the one in config."""
        return _env(f"GEMINI_{kind.upper()}_MODEL") or str(GEMINI_MODELS[kind]["model"])

    @classmethod
    def problem(cls) -> Optional[Tuple[str, str]]:
        """What is in the way, as (what, how to fix it). None when there is a key to send."""
        if cls.key_source() is None:
            return (
                "Gemini API key",
                "No Gemini API key is saved. Create one at https://aistudio.google.com/apikey, then save it "
                "in the web app's Settings, or with: python -m faceless_shorts config --gemini-key -",
            )
        return None

    @classmethod
    def available(cls) -> bool:
        """True when a key is saved or in the environment (whether Google accepts it is known only by asking)."""
        return cls.problem() is None

    @classmethod
    def check_key(cls, key: str) -> Tuple[Optional[bool], str]:
        """Ask Google whether *key* is accepted (a free request). (True/False, why); None when Google cannot be reached."""
        result = _http_request(
            f"{cls.base_url()}/models?pageSize=1", headers={"x-goog-api-key": key}, timeout=HTTP_SEARCH_TIMEOUT
        )
        if result is None:
            return None, "Google could not be reached to check the key."
        status, raw = result
        if status == 200:
            return True, "Google accepts the key."
        return False, f"Google does not accept this key: {cls._error_message(status, raw)}"

    @staticmethod
    def _error_message(status: int, raw: bytes) -> str:
        try:
            return str(json.loads(raw.decode("utf-8", "replace"))["error"]["message"])
        except Exception:
            return f"HTTP {status}"

    def _ask(
        self,
        kind: str,
        model: str,
        prompt: Any,
        response_format: Optional[Dict[str, Any]] = None,
        system: str = "",
        generation_config: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """One request. Returns (what the model produced, HTTP status); says why and returns no parts on failure."""
        source = self.key_source()
        if source is None:
            print(f"    [!] Gemini is not ready: {self.problem()[1]}")
            return [], 0
        body: Dict[str, Any] = {"model": model, "input": prompt}
        if response_format:
            body["response_format"] = response_format
        if system:
            body["system_instruction"] = system
        if generation_config:
            body["generation_config"] = generation_config
        headers = {"x-goog-api-key": source[0], "Api-Revision": GEMINI_API_REVISION}
        for attempt in (1, 2):
            result = _http_request(
                f"{self.base_url()}/interactions", data=body, headers=headers, timeout=GEMINI_TIMEOUTS[kind]
            )
            if result is None:
                print("    [!] Gemini did not answer (no connection, or it took too long).")
                return [], 0
            status, raw = result
            if status != 503 or attempt == 2:
                break
            time.sleep(GEMINI_BUSY_WAIT)  # "experiencing high demand": usually over in seconds
        if status == 503:
            print(f"    [!] Gemini ({model}) is busy right now.")
            return [], status
        if status != 200:
            message = self._error_message(status, raw)
            # Google's free limits give the image and video models an allowance of zero.
            hint = " Images and video need a key with billing set up (README: Online Mode with Gemini)." if "limit: 0" in message else ""
            print(f"    [!] Gemini ({model}) refused: {message}{hint}")
            return [], status
        try:
            steps = json.loads(raw.decode("utf-8", "replace")).get("steps") or []
        except ValueError:
            steps = []
        parts = [part for step in steps if step.get("type") == "model_output" for part in step.get("content") or []]
        if not parts:
            print(f"    [!] Gemini ({model}) answered with nothing usable.")
        return parts, status

    def write_json(self, prompt: str, system: str = "") -> Optional[Dict[str, Any]]:
        """Ask the text model for a JSON object. The fallback model is asked when the first one is busy."""
        models = [self.model("text")]
        if GEMINI_MODELS["text"]["fallback"] not in models:
            models.append(str(GEMINI_MODELS["text"]["fallback"]))
        for model in models:
            if model != models[0]:
                print(f"    → Asking {model} instead.")
            parts, status = self._ask("text", model, prompt, system=system)
            if parts:
                return _extract_json("".join(str(part.get("text") or "") for part in parts if part.get("type") == "text"))
            if status != 503:
                break
        return None

    def _media(
        self, kind: str, prompt: Any, response_format: Dict[str, Any], generation_config: Optional[Dict[str, Any]] = None
    ) -> Optional[bytes]:
        made = "audio" if kind == "tts" else kind  # what the API calls the thing this kind of model makes
        parts, _ = self._ask(
            kind, self.model(kind), prompt,
            response_format={"type": made, **response_format}, generation_config=generation_config,
        )
        data = next((part.get("data") for part in parts if part.get("type") == made and part.get("data")), None)
        if parts and not data:
            print(f"    [!] Gemini answered without any {made}.")
        try:
            return base64.b64decode(data) if data else None
        except ValueError:
            return None

    def speak(self, text: str, voice: str, style_prompt: str = "") -> Optional[bytes]:
        """Speak *text* in one of Gemini's voices. Returns the sound as raw samples
        (GEMINI_TTS_RATE per second, one channel, 16-bit), or None on any failure."""
        spoken_input = f"Say {style_prompt.strip()}: {text}" if (style_prompt and style_prompt.strip()) else text
        data = self._media("tts", spoken_input, {}, generation_config={"speech_config": [{"voice": voice}]})
        if not data:
            return None
        try:
            with wave.open(io.BytesIO(data)) as sound:
                if (sound.getframerate(), sound.getnchannels(), sound.getsampwidth()) != (GEMINI_TTS_RATE, 1, 2):
                    print("    [!] Gemini sent sound in a format this tool does not expect.")
                    return None
                return sound.readframes(sound.getnframes())
        except (wave.Error, EOFError) as exc:
            print(f"    [!] The sound Gemini sent could not be read ({exc}).")
            return None

    def word_times(self, wav: bytes) -> List[Tuple[str, float, float]]:
        """When each word in a WAV recording is spoken, as (word, start, end) in seconds. Empty on any failure."""
        parts, _ = self._ask(
            "transcribe", self.model("transcribe"),
            [{"type": "audio", "data": base64.b64encode(wav).decode("ascii"), "mime_type": "audio/wav"}],
            generation_config={"transcription_config": {"mode": {"type": "verbatim", "timestamp_granularities": ["word"]}}},
        )
        try:
            return [
                (str(note["text"]), float(note["start_offset"].rstrip("s")), float(note["end_offset"].rstrip("s")))
                for part in parts for note in part.get("annotations") or [] if note.get("type") == "word_info"
            ]
        except (KeyError, ValueError, AttributeError):
            return []

    @staticmethod
    def _with_images(prompt: str, images: Sequence[str]) -> Any:
        """The request input: the prompt alone, or the prompt followed by each picture, in order."""
        if not images:
            return prompt
        parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in images:
            with open(path, "rb") as handle:
                data = base64.b64encode(handle.read()).decode("ascii")
            parts.append({"type": "image", "data": data, "mime_type": "image/png" if path.lower().endswith(".png") else "image/jpeg"})
        return parts

    def look(self, image_path: str, question: str) -> Optional[str]:
        """Ask the text model about a picture (free limits). The answer as text, or None."""
        try:
            prompt = self._with_images(question, [image_path])
        except OSError:
            return None
        for model in dict.fromkeys([self.model("text"), str(GEMINI_MODELS["text"]["fallback"])]):
            parts, status = self._ask("text", model, prompt)
            if parts:
                return "".join(str(part.get("text") or "") for part in parts if part.get("type") == "text").strip() or None
            if status != 503:
                break
        return None

    def generate_image(self, prompt: str, output_path: str, portrait: bool = True, refs: Sequence[str] = ()) -> Optional[str]:
        """Generate one image into *output_path* (any format Pillow writes). *refs*: photos the image must
        show (your models), sent along in order. None on any failure."""
        data = self._media("image", self._with_images(prompt, refs), {"aspect_ratio": "9:16" if portrait else "16:9", "image_size": "1K"})
        if not data:
            return None
        try:
            from PIL import Image

            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with Image.open(io.BytesIO(data)) as image:
                image.convert("RGB").save(output_path, quality=95)
            return output_path
        except Exception as exc:
            print(f"    [!] The image Gemini sent could not be saved ({exc}).")
            return None

    @staticmethod
    def seconds_for(duration: float) -> int:
        """Whole seconds to ask for so the clip covers *duration*: the model makes 3 to 15."""
        return min(GEMINI_MAX_SECONDS, max(GEMINI_MIN_SECONDS, math.ceil(max(duration, 0.0))))

    @staticmethod
    def video_resolution() -> str:
        chosen = _env("GEMINI_VIDEO_RESOLUTION")
        return chosen if chosen in GEMINI_VIDEO_PRICE else DEFAULT_GEMINI_VIDEO_RESOLUTION

    def generate(
        self, prompt: str, output_path: str, duration: float, portrait: bool = True, start_image: Optional[str] = None
    ) -> Optional[str]:
        """Generate one clip (same call as FastMetalClient.generate) covering *duration* seconds (at most 15) into
        *output_path*, opening on *start_image* when one is given. None on any failure."""
        data = self._media("video", self._with_images(prompt, [start_image] if start_image else []), {
            "aspect_ratio": "9:16" if portrait else "16:9",
            "duration": f"{self.seconds_for(duration)}s",
            "resolution": self.video_resolution(),
        })
        if not data:
            return None
        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "wb") as handle:
                handle.write(data)
            return output_path
        except OSError as exc:
            print(f"    [!] The clip Gemini sent could not be saved ({exc}).")
            return None


# --------------------------------------------------------------------------- #
# yt-dlp (B-roll / reference media sourcing)
# --------------------------------------------------------------------------- #
class YtDlpHelper:
    """Thin wrapper around the yt-dlp CLI (or ``python -m yt_dlp``)."""

    @staticmethod
    def _cmd() -> Optional[List[str]]:
        exe = shutil.which("yt-dlp")
        if exe:
            return [exe]
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "yt_dlp", "--version"],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT,
            )
        except Exception:
            return None
        if proc.returncode == 0:
            return [sys.executable, "-m", "yt_dlp"]
        return None

    @classmethod
    def available(cls) -> bool:
        """True when a working yt-dlp is on PATH or importable."""
        return cls._cmd() is not None

    @classmethod
    def version(cls) -> Optional[str]:
        cmd = cls._cmd()
        if not cmd:
            return None
        try:
            proc = subprocess.run(
                cmd + ["--version"],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT,
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        return (proc.stdout or "").strip().splitlines()[0] or None

    @classmethod
    def download(
        cls,
        url: str,
        out_dir: str = ".",
        audio_only: bool = False,
        max_seconds: Optional[int] = None,
    ) -> Optional[str]:
        """Download *url* into *out_dir*; returns the file path or ``None``."""
        try:
            if not url or not cls.available():
                return None
            cmd = cls._cmd()
            if not cmd:
                return None
            out_dir = str(out_dir)
            os.makedirs(out_dir, exist_ok=True)
            template = os.path.join(out_dir, "%(title).80s_%(id)s.%(ext)s")
            full = cmd + [
                "--no-playlist",
                "--no-warnings",
                "-o", template,
                "--print", "after_move:filepath",
            ]
            if audio_only:
                full += ["-x", "--audio-format", "m4a"]
            if max_seconds:
                full += ["--download-sections", f"*0-{int(max_seconds)}"]
            full.append(url)
            started = time.time()
            proc = subprocess.run(
                full,
                capture_output=True,
                text=True,
                timeout=YTDLP_TIMEOUT,
            )
            if proc.returncode != 0:
                return None
            # yt-dlp prints the final path thanks to --print after_move:filepath
            for line in reversed((proc.stdout or "").splitlines()):
                candidate = line.strip().strip('"')
                if candidate and os.path.isfile(candidate):
                    return candidate
            # Fallback: newest file created by this run.
            newest, newest_mtime = None, started - 1.0
            for name in os.listdir(out_dir):
                path = os.path.join(out_dir, name)
                if os.path.isfile(path):
                    mtime = os.path.getmtime(path)
                    if mtime > newest_mtime:
                        newest, newest_mtime = path, mtime
            return newest
        except Exception:
            return None


# --------------------------------------------------------------------------- #
# Optional FREE LLM: Ollama or any OpenAI-compatible endpoint
# --------------------------------------------------------------------------- #
class LLMScriptWriter:
    """Generate script JSON via a local Ollama model or an OpenAI-compatible API."""

    DEFAULT_SYSTEM = (
        "You are a scriptwriter for short-form faceless videos "
        "(YouTube Shorts / Reels / TikTok). Reply with ONLY a JSON object."
    )

    @staticmethod
    def _ollama_base() -> str:
        host = _env("OLLAMA_HOST") or DEFAULT_OLLAMA_URL
        if "://" not in host:
            host = "http://" + host
        return host.rstrip("/")

    @classmethod
    def _backend(cls) -> Optional[Tuple[str, str]]:
        """Return ``("ollama", base)`` / ``("openai", base)`` or ``None``."""
        base = cls._ollama_base()
        if _http_ok(f"{base}/api/tags", timeout=HTTP_TIMEOUT):
            return ("ollama", base)
        openai_base = _env("LLM_BASE_URL")
        if openai_base and _env("LLM_API_KEY"):
            return ("openai", openai_base.rstrip("/"))
        return None

    @classmethod
    def available(cls) -> bool:
        """True when Ollama answers, or LLM_BASE_URL + LLM_API_KEY are set."""
        return cls._backend() is not None

    @staticmethod
    def _build_prompt(topic: str, language: str, system_prompt: str) -> str:
        return (
            f"Write a viral short-form video script about: {topic}\n"
            f"Language: {language or 'en'}\n"
            "Return a JSON object with keys: title, hook, scenes (array of strings), "
            "cta. Keep it under 60 seconds of narration."
            + (f"\nExtra instructions: {system_prompt}" if system_prompt else "")
        )

    @classmethod
    def _via_ollama(cls, base: str, prompt: str, system: str, model: Optional[str]) -> Optional[Dict[str, Any]]:
        chosen = model
        if not chosen:
            tags = _http_json(f"{base}/api/tags", timeout=HTTP_TIMEOUT)
            if isinstance(tags, dict):
                models = tags.get("models") or []
                if models and isinstance(models[0], dict):
                    chosen = str(models[0].get("name") or "") or None
        if not chosen:
            return None
        response = _http_json(
            f"{base}/api/generate",
            data={
                "model": chosen,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "format": "json",
            },
            timeout=HTTP_IO_TIMEOUT,
        )
        if not isinstance(response, dict) or not response.get("response"):
            return None
        return _extract_json(response.get("response"))

    @classmethod
    def _via_openai(cls, base: str, prompt: str, system: str, model: Optional[str]) -> Optional[Dict[str, Any]]:
        response = _http_json(
            f"{base}/chat/completions",
            data={
                "model": model or _env("LLM_MODEL") or DEFAULT_LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
            },
            headers={"Authorization": f"Bearer {_env('LLM_API_KEY')}"},
            timeout=HTTP_IO_TIMEOUT,
        )
        if not isinstance(response, dict):
            return None
        try:
            content = response["choices"][0]["message"]["content"]
        except Exception:
            return None
        return _extract_json(content)

    @classmethod
    def write_script(
        cls,
        topic: str,
        language: str = "en",
        system_prompt: str = "",
        model: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Ask the first reachable free LLM for script JSON (dict or None)."""
        try:
            if not topic:
                return None
            backend = cls._backend()
            if backend is None:
                return None
            kind, base = backend
            system = (system_prompt or "").strip() or cls.DEFAULT_SYSTEM
            prompt = cls._build_prompt(topic, language, system_prompt or "")
            if kind == "ollama":
                return cls._via_ollama(base, prompt, system, model)
            return cls._via_openai(base, prompt, system, model)
        except Exception:
            return None


# --------------------------------------------------------------------------- #
# Free-tier stock footage (Pexels + Pixabay)
# --------------------------------------------------------------------------- #
class StockBRoll:
    """Download free portrait stock clips for B-roll."""

    @staticmethod
    def available() -> bool:
        """True when a free Pexels or Pixabay API key is configured."""
        return bool(_env("PEXELS_API_KEY") or _env("PIXABAY_API_KEY"))

    @staticmethod
    def _out_dir() -> str:
        custom = _env("FACELESS_STOCK_DIR")
        if custom:
            return custom
        return os.path.join(os.path.expanduser("~"), ".cache", "faceless-shorts", "stock")

    @classmethod
    def _search_pexels(cls, query: str, orientation: str) -> Optional[str]:
        key = _env("PEXELS_API_KEY")
        if not key:
            return None
        params = urlencode(
            {"query": query, "orientation": orientation or "portrait", "per_page": 5}
        )
        data = _http_json(
            f"https://api.pexels.com/videos/search?{params}",
            headers={"Authorization": key},
            timeout=HTTP_SEARCH_TIMEOUT,
        )
        if not isinstance(data, dict):
            return None
        for video in data.get("videos") or []:
            if not isinstance(video, dict):
                continue
            files = [f for f in (video.get("video_files") or []) if isinstance(f, dict) and f.get("link")]
            hd = [f for f in files if (f.get("height") or 0) >= 1280]
            pick = hd[0] if hd else (files[0] if files else None)
            if pick:
                return str(pick["link"])
        return None

    @classmethod
    def _search_pixabay(cls, query: str) -> Optional[str]:
        key = _env("PIXABAY_API_KEY")
        if not key:
            return None
        params = urlencode({"key": key, "q": query, "per_page": 5})
        data = _http_json(
            f"https://pixabay.com/api/videos/?{params}",
            timeout=HTTP_SEARCH_TIMEOUT,
        )
        if not isinstance(data, dict):
            return None
        for hit in data.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            videos = hit.get("videos") or {}
            for size in ("large", "medium", "small", "tiny"):
                url = (videos.get(size) or {}).get("url")
                if url:
                    return str(url)
        return None

    @classmethod
    def search_clip(cls, query: str, orientation: str = "portrait") -> Optional[str]:
        """Search Pexels (then Pixabay) and download the first clip.

        Returns the local ``.mp4`` path, or ``None`` when no key is set or
        anything fails. Never raises.
        """
        try:
            if not query or not cls.available():
                return None
            link = cls._search_pexels(str(query), orientation) or cls._search_pixabay(str(query))
            if not link:
                return None
            out_dir = cls._out_dir()
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception:
                return None
            slug = re.sub(r"[^a-z0-9]+", "_", str(query).lower()).strip("_")[:40] or "clip"
            destination = os.path.join(out_dir, f"{slug}_{uuid.uuid4().hex[:8]}.mp4")
            return _download(link, destination, timeout=HTTP_IO_TIMEOUT)
        except Exception:
            return None


# --------------------------------------------------------------------------- #
# Doctor report
# --------------------------------------------------------------------------- #
def _plain_table(headers: Tuple[str, ...], rows: List[Tuple[str, ...]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))

    def fmt(row: Tuple[str, ...]) -> str:
        return "  ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row)).rstrip()

    lines = [fmt(headers), "  ".join("-" * width for width in widths)]
    lines.extend(fmt(row) for row in rows)
    return "\n".join(lines)


def doctor_report() -> str:
    """Build a readable plain-text table of :func:`doctor` results."""
    data = doctor()
    headers = ("tool", "status", "version", "kind", "install", "hint")
    rows: List[Tuple[str, ...]] = []
    for key in TOOL_KEYS:
        info = data.get(key) or {}
        rows.append(
            (
                key,
                "OK" if info.get("available") else "MISSING",
                str(info.get("version") or "-"),
                str(info.get("kind") or "-"),
                str(info.get("install") or "-"),
                str(info.get("hint") or "-"),
            )
        )
    title = "faceless-shorts - optional tool doctor"
    if _RichTable is not None and _RichConsole is not None:
        try:
            table = _RichTable(title=title, show_header=True, show_lines=False)
            table.add_column("tool", style="cyan", no_wrap=True)
            table.add_column("status", no_wrap=True)
            table.add_column("version", no_wrap=True)
            table.add_column("kind")
            table.add_column("install", overflow="fold")
            table.add_column("hint", overflow="fold")
            for row in rows:
                style = "green" if row[1] == "OK" else "red"
                table.add_row(row[0], f"[{style}]{row[1]}[/]", *row[2:])
            buffer = io.StringIO()
            console = _RichConsole(
                file=buffer,
                width=120,
                force_terminal=False,
                color_system=None,
                highlight=False,
            )
            console.print(table)
            rendered = buffer.getvalue().strip()
            if rendered:
                return rendered
        except Exception:
            pass  # fall back to plain text
    return title + "\n" + _plain_table(headers, rows)
