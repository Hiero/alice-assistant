"""Microphone capture, speech recognition and speech output. Everything runs on the CPU
so the voice stack leaves the GPU's memory to the language model.
"""

import asyncio
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel
from kokoro_onnx import Kokoro

DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
MODEL_DIR = DATA_HOME / "alice/models"
WHISPER_MODEL = os.environ.get("ALICE_WHISPER_MODEL", "small")
# A voice name, or a weighted blend like "af_bella:70,af_sky:30".
VOICE = os.environ.get("ALICE_VOICE", "af_bella:70,af_nicole:10,af_sky:10,bf_alice:10")
# Raising pitch and resonance together makes the voice sound younger.
VOICE_PITCH = float(os.environ.get("ALICE_VOICE_PITCH", "1.2"))
VOICE_PACE = float(os.environ.get("ALICE_VOICE_PACE", "1.03"))
KOKORO_LANG = {"pt": "pt-br", "en": "en-us"}
# "en" always answers in English; "auto" follows the user between English and Portuguese.
REPLY_LANGUAGE = os.environ.get("ALICE_REPLY_LANGUAGE", "en")

MIC_RATE = 16000
SPEECH_RATE = 24000
CHUNK_BYTES = MIC_RATE // 10 * 2  # 100 ms of 16-bit mono
NOISE_CHUNKS = 5
MIN_SPEECH_LEVEL = 0.012
END_SILENCE_CHUNKS = 8
NO_SPEECH_CHUNKS = 60
MAX_CHUNKS = 300
LANGUAGES = ("pt", "en")
# Lip sync: how loud her voice is, sent to the shell 30 times a second of audio.
MOUTH_FPS = 30
# Roughly how long PipeWire takes to start playing what pw-cat is given.
PLAYBACK_LATENCY = 0.08
CONFIDENT_DETECTION = 0.95

_load_lock = threading.Lock()
_whisper: WhisperModel | None = None
_kokoro: Kokoro | None = None


def load_models() -> tuple[WhisperModel, Kokoro]:
    global _whisper, _kokoro
    with _load_lock:
        if _whisper is None:
            _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=8)
        if _kokoro is None:
            _kokoro = Kokoro(str(MODEL_DIR / "kokoro-v1.0.onnx"), str(MODEL_DIR / "voices-v1.0.bin"))
        return _whisper, _kokoro


async def record(stop: asyncio.Event) -> bytes:
    """Record until `stop` is set or the speaker goes quiet. Empty means nothing was said."""
    process = await asyncio.create_subprocess_exec(
        "pw-record",
        "--raw",
        "--format=s16",
        f"--rate={MIC_RATE}",
        "--channels=1",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    chunks: list[bytes] = []
    levels: list[float] = []
    heard_speech = False
    quiet_chunks = 0
    try:
        while not stop.is_set() and len(chunks) < MAX_CHUNKS:
            try:
                chunk = await process.stdout.readexactly(CHUNK_BYTES)
            except asyncio.IncompleteReadError as error:
                raise RuntimeError("The microphone stopped unexpectedly.") from error
            chunks.append(chunk)
            samples = np.frombuffer(chunk, np.int16).astype(np.float32) / 32768
            level = float(np.sqrt(np.mean(samples**2)))

            if len(levels) < NOISE_CHUNKS:
                levels.append(level)
                continue
            if level > max(float(np.median(levels)) * 3, MIN_SPEECH_LEVEL):
                heard_speech = True
                quiet_chunks = 0
            else:
                quiet_chunks += 1
            if heard_speech and quiet_chunks >= END_SILENCE_CHUNKS:
                break
            if not heard_speech and len(chunks) >= NO_SPEECH_CHUNKS:
                return b""
    finally:
        process.terminate()
        await process.wait()
    return b"".join(chunks)


def decode(audio: np.ndarray, language: str | None = None) -> tuple[str, float, object]:
    whisper, _ = load_models()
    segments, info = whisper.transcribe(audio, language=language, vad_filter=True, beam_size=1)
    segments = list(segments)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    score = float(np.mean([segment.avg_logprob for segment in segments])) if segments else float("-inf")
    return text, score, info


def transcribe(pcm: bytes) -> tuple[str, str]:
    """Return the text and its language, choosing only between Portuguese and English.

    Short phrases give the language detector little to go on, so when it is unsure both
    languages are tried and the transcription the model is more confident in wins.
    """
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
    text, score, info = decode(audio)
    if not text or (info.language in LANGUAGES and info.language_probability >= CONFIDENT_DETECTION):
        return text, info.language if info.language in LANGUAGES else "en"

    results = {info.language: (text, score)} if info.language in LANGUAGES else {}
    for language in LANGUAGES:
        if language not in results:
            results[language] = decode(audio, language)[:2]
    best = max(results, key=lambda language: results[language][1])
    return results[best][0], best


PT_WORDS = set(
    "o a os as um uma de do da dos das em no na nos nas para por com que não sim você eu ele ela "
    "é são está estou tem foi como mais mas seu sua meu minha isso esse esta aqui agora "
    "pronto olá oi obrigado desculpe posso ajudar".split()
)
EN_WORDS = set(
    "the a an of in on at to for with that this not yes you i he she it is are was has have "
    "been how more but your my here now done hello hi thanks sorry can help what and".split()
)


def guess_language(sentence: str, fallback: str) -> str:
    words = re.findall(r"\w+", sentence.lower())
    pt = sum(word in PT_WORDS for word in words) + 2 * len(re.findall(r"[ãõçáâêéíóôú]", sentence.lower()))
    en = sum(word in EN_WORDS for word in words)
    if pt == en:
        return fallback
    return "pt" if pt > en else "en"


def speakable(text: str) -> str:
    without_links = re.sub(r"https?://\S+", "", text)
    return re.sub(r"[*_#`~\[\]<>|]", "", without_links).strip()


def voice_style(kokoro: Kokoro) -> str | np.ndarray:
    if ":" not in VOICE:
        return VOICE
    parts = [part.split(":") for part in VOICE.split(",")]
    total = sum(float(weight) for _, weight in parts)
    return sum(kokoro.get_voice_style(name.strip()) * (float(weight) / total) for name, weight in parts)


def synthesize(sentence: str, language: str) -> bytes:
    _, kokoro = load_models()
    samples, _ = kokoro.create(
        sentence, voice=voice_style(kokoro), lang=KOKORO_LANG[language], speed=VOICE_PACE / VOICE_PITCH
    )
    # Playing the samples back faster raises pitch and resonance; the slower speed above offsets it.
    samples = np.interp(np.arange(0, len(samples), VOICE_PITCH), np.arange(len(samples)), samples)
    samples = samples / max(1e-6, float(np.abs(samples).max())) * 0.9
    return (samples * 32767).astype(np.int16).tobytes()


def mouth_levels(audio: bytes) -> list[float]:
    """How open her mouth is, from 0 to 1, for each 1/MOUTH_FPS second of the audio."""
    samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32767
    frame = SPEECH_RATE // MOUTH_FPS
    count = len(samples) // frame
    if not count:
        return []
    rms = np.sqrt(np.mean(samples[: count * frame].reshape(count, frame) ** 2, axis=1))
    # Speech peaks at about 0.2 RMS after synthesize's normalising; quiet breaths stay shut.
    levels = np.clip((rms - 0.015) / 0.17, 0, 1) ** 0.7
    return [round(float(level), 2) for level in levels]


class Speaker:
    """Speaks streamed text sentence by sentence, starting before the reply is finished.

    on_mouth, when given, receives a {"type": "mouth"} event for each sentence: its
    loudness envelope and how many seconds from now it starts playing.
    """

    def __init__(self, language: str, on_mouth: Callable[[dict], Awaitable[None]] | None = None) -> None:
        self.language = language
        self._on_mouth = on_mouth
        # When everything written to the player so far will have finished playing.
        self._playing_until = 0.0
        self.draining = False
        self._buffer = ""
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._player: asyncio.subprocess.Process | None = None
        self._worker = asyncio.create_task(self._run())

    def feed(self, text: str) -> None:
        self._buffer += text
        *sentences, self._buffer = re.split(r"(?<=[.!?…:])\s+|\n+", self._buffer)
        for sentence in sentences:
            self._queue.put_nowait(sentence)

    async def finish(self) -> None:
        """Wait until everything has been spoken."""
        self.draining = True
        self._queue.put_nowait(self._buffer)
        self._buffer = ""
        self._queue.put_nowait(None)
        await asyncio.gather(self._worker, return_exceptions=True)

    def stop(self) -> None:
        self._worker.cancel()
        if self._player is not None and self._player.returncode is None:
            self._player.kill()

    async def _run(self) -> None:
        try:
            while (sentence := await self._queue.get()) is not None:
                text = speakable(sentence)
                if not re.search(r"\w", text):
                    continue
                if REPLY_LANGUAGE == "auto":
                    self.language = guess_language(text, self.language)
                else:
                    self.language = REPLY_LANGUAGE
                audio = await asyncio.to_thread(synthesize, text, self.language)
                player = await self._ensure_player()
                await self._send_mouth(audio)
                player.stdin.write(audio)
                await player.stdin.drain()
            if self._player is not None:
                self._player.stdin.close()
                await self._player.wait()
        except (OSError, RuntimeError, ValueError) as error:
            print(f"speech failed: {error}", flush=True)

    async def _send_mouth(self, audio: bytes) -> None:
        now = time.monotonic()
        starts = max(now + PLAYBACK_LATENCY, self._playing_until)
        self._playing_until = starts + len(audio) / 2 / SPEECH_RATE
        if self._on_mouth is None:
            return
        event = {"type": "mouth", "delay": round(starts - now, 3), "fps": MOUTH_FPS, "levels": mouth_levels(audio)}
        try:
            await self._on_mouth(event)
        except ConnectionError:
            pass

    async def _ensure_player(self) -> asyncio.subprocess.Process:
        if self._player is None:
            self._player = await asyncio.create_subprocess_exec(
                "pw-cat",
                "--playback",
                "--raw",
                "--format=s16",
                f"--rate={SPEECH_RATE}",
                "--channels=1",
                "-",
                stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        return self._player
