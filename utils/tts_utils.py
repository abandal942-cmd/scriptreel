import asyncio
import os
import edge_tts

# Coqui's TOS prompt is interactive (asks y/n on first model download) — must be
# pre-accepted via env var or it will hang a server process with no terminal.
os.environ.setdefault("COQUI_TOS_AGREED", "1")

# A curated shortlist of default voices users can pick from.
# Full list: run `edge-tts --list-voices` to see hundreds more.
DEFAULT_VOICES = [
    {"id": "en-US-AriaNeural", "label": "Aria (US, Female)"},
    {"id": "en-US-GuyNeural", "label": "Guy (US, Male)"},
    {"id": "en-GB-SoniaNeural", "label": "Sonia (UK, Female)"},
    {"id": "en-GB-RyanNeural", "label": "Ryan (UK, Male)"},
    {"id": "hi-IN-SwaraNeural", "label": "Swara (Hindi, Female)"},
    {"id": "hi-IN-MadhurNeural", "label": "Madhur (Hindi, Male)"},
]

# XTTS-v2 supports these language codes for cloned narration.
CLONE_LANGUAGES = {
    "en", "hi", "es", "fr", "de", "it", "pt", "pl", "tr", "ru",
    "nl", "cs", "ar", "zh-cn", "ja", "ko",
}

_xtts_model = None  # loaded lazily — the checkpoint is ~2GB, don't load at import time


def _get_xtts_model():
    """
    Lazily load the Coqui XTTS-v2 model (downloads ~2GB on first call, cached
    afterwards under ~/.local/share/tts). CPU inference is slow (tens of
    seconds per sentence) — a GPU host is strongly recommended for production use.
    """
    global _xtts_model
    if _xtts_model is None:
        from TTS.api import TTS  # imported lazily so app startup stays fast
        _xtts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
    return _xtts_model


def clone_tts(text: str, reference_audio_path: str, out_path: str, language: str = "en"):
    """
    Generate narration audio that reads `text` aloud in the voice captured by
    `reference_audio_path` (a short sample of someone speaking), using Coqui
    XTTS-v2 voice cloning.

    Note: unlike generate_tts(), this does not return per-word timestamps —
    XTTS doesn't expose word-boundary events. Captions will fall back to
    even-spacing across the narration length (handled already in captions
    logic), which is a little less precise but still reads fine.
    """
    if not os.path.isfile(reference_audio_path) or os.path.getsize(reference_audio_path) == 0:
        raise RuntimeError("Reference voice sample is missing or empty.")
    if language not in CLONE_LANGUAGES:
        language = "en"

    try:
        model = _get_xtts_model()
        model.tts_to_file(
            text=text,
            speaker_wav=reference_audio_path,
            language=language,
            file_path=out_path,
        )
    except Exception as e:
        raise RuntimeError(f"Voice cloning failed: {e}")

    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("Voice cloning produced no audio output.")


def generate_tts(text: str, voice_id: str, out_path: str):
    """
    Generate narration audio from text using edge-tts (free, no API key).
    Also returns a list of word-level timestamps (from edge-tts's own
    WordBoundary events) so captions can be synced to the actual speech,
    word by word, instead of just showing the whole block of text at once.

    Returns: list of {"text": str, "start": float_seconds, "duration": float_seconds}
    """
    word_boundaries = []

    async def _run():
        communicate = edge_tts.Communicate(text, voice_id)
        with open(out_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word_boundaries.append({
                        "text": chunk["text"],
                        "start": chunk["offset"] / 10_000_000,      # 100ns ticks -> seconds
                        "duration": chunk["duration"] / 10_000_000,
                    })

    asyncio.run(_run())
    return word_boundaries