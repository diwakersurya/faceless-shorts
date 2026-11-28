"""
Text-to-Speech engines.
Microsoft Edge-TTS (the default): 100% free, zero API key required, neural quality, with word-boundary
timestamp extraction. Gemini TTS (optional, online): Google's voices, with estimated word timings.
"""

import array
import asyncio
import difflib
import os
import re
import wave
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
import edge_tts
from faceless_shorts.config import VOICES, DEFAULT_VOICE, GEMINI_VOICES, DEFAULT_GEMINI_VOICE

@dataclass
class WordTimestamp:
    word: str
    start_sec: float
    end_sec: float
    duration_sec: float

    def to_dict(self) -> Dict:
        return {
            "word": self.word,
            "start": round(self.start_sec, 3),
            "end": round(self.end_sec, 3),
            "duration": round(self.duration_sec, 3)
        }

@dataclass
class AudioResult:
    audio_path: str
    duration_sec: float
    words: List[WordTimestamp]
    text: str

class TTSEngine:
    def __init__(self, default_voice: str = DEFAULT_VOICE):
        self.default_voice = self._resolve_voice(default_voice)

    def _resolve_voice(self, voice_name_or_id: str) -> str:
        """Resolve short name (e.g. 'christopher') to full Edge-TTS voice ID."""
        if voice_name_or_id in VOICES:
            return VOICES[voice_name_or_id]["id"]
        # If it's already a full ID or unknown string, pass through
        return voice_name_or_id

    async def _synthesize_async(
        self,
        text: str,
        voice: str,
        output_path: str,
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz"
    ) -> AudioResult:
        """Asynchronously synthesize speech and extract word boundaries."""
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        
        # Strip markdown or special characters that confuse TTS
        cleaned_text = re.sub(r"[\*\_#\[\]\(\)]", "", text).strip()
        
        # For Hindi / Indic languages: normalize '।' and '?' to ',' so Edge-TTS neural voices
        # flow smoothly and continuously like a real human without inserting 1.2s dead stops
        if "hi-" in voice or any("\u0900" <= c <= "\u097F" for c in cleaned_text):
            cleaned_text = re.sub(r"[।\?]+", ", ", cleaned_text)
            cleaned_text = re.sub(r",\s*,+", ", ", cleaned_text)
        
        communicate = edge_tts.Communicate(
            text=cleaned_text,
            voice=voice,
            rate=rate,
            volume=volume,
            pitch=pitch,
            boundary="WordBoundary"
        )

        words: List[WordTimestamp] = []
        raw_audio = bytearray()

        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                raw_audio.extend(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # offset and duration in 100-nanosecond units (ticks)
                # 1 tick = 0.0000001 seconds = 0.1 microseconds
                start_sec = chunk["offset"] / 10_000_000.0
                duration_sec = chunk["duration"] / 10_000_000.0
                end_sec = start_sec + duration_sec
                word_text = chunk["text"].strip()
                
                if word_text:
                    words.append(WordTimestamp(
                        word=word_text,
                        start_sec=start_sec,
                        end_sec=end_sec,
                        duration_sec=duration_sec
                    ))

        with open(output_path, "wb") as f:
            f.write(raw_audio)

        # Estimate duration from last word timestamp or audio file
        total_duration = words[-1].end_sec + 0.3 if words else 1.0

        return AudioResult(
            audio_path=output_path,
            duration_sec=total_duration,
            words=words,
            text=cleaned_text
        )

    def synthesize(
        self,
        text: str,
        output_path: str,
        voice: Optional[str] = None,
        rate: str = "+0%"
    ) -> AudioResult:
        """Synchronous wrapper for speech synthesis."""
        selected_voice = self._resolve_voice(voice or self.default_voice)
        return asyncio.run(self._synthesize_async(
            text=text,
            voice=selected_voice,
            output_path=output_path,
            rate=rate
        ))

    def synthesize_scenes(
        self,
        scenes: List[Dict],
        output_dir: str,
        voice: Optional[str] = None,
        rate: str = "+5%"
    ) -> List[Tuple[Dict, AudioResult]]:
        """
        Synthesize individual audio clips for a list of scenes.
        Returns list of tuples (scene_data, audio_result).
        """
        os.makedirs(output_dir, exist_ok=True)
        results = []
        selected_voice = self._resolve_voice(voice or self.default_voice)

        for i, scene in enumerate(scenes):
            audio_filename = f"scene_{i + 1:02d}.mp3"
            audio_path = os.path.join(output_dir, audio_filename)
            res = self.synthesize(
                text=scene["text"],
                output_path=audio_path,
                voice=selected_voice,
                rate=rate
            )
            results.append((scene, res))

        return results


# ------------------------------------------------------------------ Gemini TTS
SILENCE_LEVEL = 400  # of 32767: samples quieter than this at either end of a clip count as silence
PUNCTUATION = ".,!?;:\"'“”‘’()…।—-"


def spread_words(text: str, start: float, end: float) -> List[WordTimestamp]:
    """Estimate when each word of *text* is spoken between *start* and *end* seconds.

    Each word gets a share of the time by its length, plus a little for the pause that punctuation
    brings. Used only for words whose real time could not be learned (see align_words and GeminiTTS).
    ponytail: an estimate. Measured against Edge-TTS's real word times it is 0.12 s off for the
    median word and over 0.4 s off for one word in ten.
    """
    tokens = text.split()
    if not tokens:
        return []
    weights = [len(t) + (5 if t[-1] in ".!?।…" else 3 if t[-1] in ",;:—" else 1) for t in tokens]
    per_weight = max(end - start, 0.0) / sum(weights)
    words, cursor = [], start
    for token, weight in zip(tokens, weights):
        spoken = len(token) * per_weight  # the rest of the share is the pause after the word
        words.append(WordTimestamp(
            word=token.strip(PUNCTUATION) or token,
            start_sec=cursor,
            end_sec=cursor + spoken,
            duration_sec=spoken,
        ))
        cursor += weight * per_weight
    return words


def voiced_span(samples: bytes, rate: int) -> Tuple[float, float]:
    """Seconds at which speech begins and ends inside 16-bit mono *samples* (the silence at both ends left out)."""
    levels = array.array("h", samples[: len(samples) // 2 * 2])
    loud = [i for i in (
        next((i for i, level in enumerate(levels) if abs(level) > SILENCE_LEVEL), None),
        next((i for i in range(len(levels) - 1, -1, -1) if abs(levels[i]) > SILENCE_LEVEL), None),
    ) if i is not None]
    if len(loud) < 2:
        return 0.0, len(levels) / rate
    return loud[0] / rate, (loud[1] + 1) / rate


def align_words(text: str, heard: List[Tuple[str, float, float]], start: float, end: float) -> List[WordTimestamp]:
    """Give every word of *text* a time from *heard*, the (word, start, end) list a transcription returned.

    A script word the transcription heard takes its real time. Words it heard differently ("99%" as
    "ninety nine percent") share the time between their nearest matched neighbours, by length.
    """
    tokens = text.split()
    plain = lambda word: re.sub(r"\W", "", word.lower())
    matcher = difflib.SequenceMatcher(None, [plain(t) for t in tokens], [plain(h[0]) for h in heard], autojunk=False)
    real: Dict[int, Tuple[float, float]] = {}
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            real[block.a + k] = heard[block.b + k][1:]
    words: List[WordTimestamp] = []
    i = 0
    while i < len(tokens):
        if i in real:
            word_start, word_end = real[i]
            words.append(WordTimestamp(tokens[i].strip(PUNCTUATION) or tokens[i], word_start, word_end, word_end - word_start))
            i += 1
            continue
        j = i
        while j < len(tokens) and j not in real:
            j += 1
        gap_start = words[-1].end_sec if words else start
        gap_end = real[j][0] if j < len(tokens) else end
        words += spread_words(" ".join(tokens[i:j]), gap_start, max(gap_start, gap_end))
        i = j
    return words


class GeminiTTS:
    """Voice-over from Gemini's speech model. Gemini does not say when each word is spoken, which the
    subtitles and the scene cuts need, so the voice-over is sent to its transcription model, which does."""

    def __init__(self, voice: str = DEFAULT_GEMINI_VOICE):
        self.voice = resolve_gemini_voice(voice)

    def synthesize(self, text: str, output_path: str, style_prompt: str = "") -> Optional[AudioResult]:
        """Speak *text* into a WAV file, with a time for each of its words. None when Gemini does not deliver."""
        from faceless_shorts.integrations import GeminiClient, GEMINI_TTS_RATE

        client = GeminiClient()
        cleaned = re.sub(r"[\*\_#\[\]\(\)]", "", text).strip()
        samples = client.speak(cleaned, self.voice, style_prompt=style_prompt)
        if not samples:
            return None
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with wave.open(output_path, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(GEMINI_TTS_RATE)
            out.writeframes(samples)
        speech_start, speech_end = voiced_span(samples, GEMINI_TTS_RATE)
        with open(output_path, "rb") as handle:
            heard = client.word_times(handle.read())
        if heard:
            words = align_words(text, heard, speech_start, speech_end)
        else:
            print("    [!] Word times could not be learned, so they are estimated: subtitles and scene cuts may run early or late.")
            words = spread_words(text, speech_start, speech_end)
        return AudioResult(
            audio_path=output_path,
            duration_sec=len(samples) / 2 / GEMINI_TTS_RATE,
            words=words,
            text=text,
        )


def resolve_gemini_voice(name: Optional[str]) -> str:
    """The Gemini voice called *name*, whatever its capitals; the default voice for any other name."""
    return next((v for v in GEMINI_VOICES if v.lower() == (name or "").lower()), DEFAULT_GEMINI_VOICE)


def probe_audio_duration(audio_path: str) -> float:
    """Return audio duration in seconds via ffprobe, or a safe fallback."""
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        val = float((out.stdout or "").strip())
        if val > 0:
            return val
    except Exception:
        pass
    return 15.0


def load_custom_voiceover(audio_path: str, full_text: str, language: str = "en") -> AudioResult:
    """Load a user-recorded voiceover file and align script words to it via Gemini/Whisper or estimation."""
    duration = probe_audio_duration(audio_path)
    heard: Optional[List[Tuple[str, float, float]]] = None
    try:
        from faceless_shorts.integrations import GeminiClient
        if GeminiClient.key_source() is not None:
            with open(audio_path, "rb") as handle:
                heard = GeminiClient().word_times(handle.read())
    except Exception:
        heard = None
    if heard:
        words = align_words(full_text, heard, 0.0, duration)
    else:
        words = spread_words(full_text, 0.0, duration)
    return AudioResult(audio_path=audio_path, duration_sec=duration, words=words, text=full_text)
