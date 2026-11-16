"""Hold music (PRD QH-5): a loop mixed under the bot's voice while the caller waits.

`HoldMusicMixer` is the Daily output transport's audio mixer (`DailyParams.
audio_out_mixer`). It plays nothing until the hold controller enables it (the call is
queued) and stops when it is disabled (an agent accepted, the call ended). While on:

- it waits until the bot has finished the line it is saying (the handoff or requeue
  line), then fades in over FADE_IN_SECS;
- whenever the bot speaks over it (a hold update, or a short LLM reply if the caller
  talks), it ducks by DUCK_DB and comes back up after the bot goes quiet. Bot speech
  is the audio the transport hands to `mix()`, so no extra signalling is needed;
- disable ramps to zero within one output chunk (40 ms), with no click.

The output transport calls `mix()` for every chunk it sends; with no TTS audio queued
it passes silence, so the music keeps playing between hold updates.

Config (bot env):
    HOLD_MUSIC         on|off (default on)
    HOLD_MUSIC_VOLUME  0 to 1 (default 0.35), the music's gain under the bot
    HOLD_MUSIC_PATH    optional track that replaces the bundled loop

The bundled loop (`assets/hold_music.wav`) is our own, rendered by
`hold_music_gen.py`. Any mono or stereo WAV/FLAC/OGG at any sample rate works as an
override; it is mixed down to mono and resampled to the output rate on start.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr
from loguru import logger
from pipecat.audio.mixers.base_audio_mixer import BaseAudioMixer
from pipecat.frames.frames import MixerControlFrame, MixerEnableFrame, MixerUpdateSettingsFrame

DEFAULT_TRACK = Path(__file__).parent / "assets" / "hold_music.wav"
DEFAULT_VOLUME = 0.35
OFF_VALUES = {"off", "0", "false", "no"}

DUCK_DB = -12.0
SPEECH_PEAK = 300  # int16 peak above which the transport's audio is the bot talking
START_QUIET_SECS = 0.5  # after the line being said ends, music starts this much later
NO_LINE_SECS = 2.0  # no line came (or it ended before enable): start after this
RELEASE_QUIET_SECS = 0.4  # bot quiet this long before a duck lifts
FADE_IN_SECS = 1.5  # silence to full on start
DUCK_SECS = 0.1  # full to ducked
RELEASE_SECS = 0.5  # ducked to full


class HoldMusicMixer(BaseAudioMixer):
    def __init__(self, path: Path | str = DEFAULT_TRACK, *, volume: float = DEFAULT_VOLUME):
        self.path = Path(path)
        self.volume = volume
        self.enabled = False
        self._rate = 0
        self._track: np.ndarray | None = None
        self._pos = 0
        self._gain = 0.0  # current music gain, 0 to 1 (before volume)
        self._started = False  # the first fade in has begun since enable
        self._heard_line = False  # the bot spoke since enable
        self._quiet_secs = 0.0  # how long the bot has been quiet

    # ---- control ------------------------------------------------------------------

    def enable(self) -> None:
        if not self.enabled:
            logger.info("[hold music] on")
            self.enabled = True
            self._started = False
            self._heard_line = False
            self._quiet_secs = 0.0

    def disable(self) -> None:
        if self.enabled:
            logger.info("[hold music] off")
            self.enabled = False

    # ---- BaseAudioMixer ---------------------------------------------------------------

    async def start(self, sample_rate: int):
        self._rate = sample_rate
        self._track = await asyncio.to_thread(self._load, sample_rate)

    async def stop(self):
        pass

    async def process_frame(self, frame: MixerControlFrame):
        if isinstance(frame, MixerEnableFrame):
            self.enable() if frame.enable else self.disable()
        elif isinstance(frame, MixerUpdateSettingsFrame):
            if "volume" in frame.settings:
                self.volume = float(frame.settings["volume"])

    async def mix(self, audio: bytes) -> bytes:
        voice = np.frombuffer(audio, dtype=np.int16)
        n = len(voice)
        if n == 0 or self._track is None:
            return audio
        speaking = bool(np.abs(voice.astype(np.int32)).max() > SPEECH_PEAK)
        secs = n / self._rate
        self._quiet_secs = 0.0 if speaking else self._quiet_secs + secs
        target = self._target(speaking)
        if target == 0.0 and self._gain == 0.0:
            return audio
        gains = self._ramp(target, n, secs)
        idx = (self._pos + np.arange(n)) % len(self._track)
        self._pos = int((self._pos + n) % len(self._track))
        music = self._track[idx] * gains * self.volume
        return np.clip(voice + music, -32768, 32767).astype(np.int16).tobytes()

    # ---- internals ------------------------------------------------------------------

    def _target(self, speaking: bool) -> float:
        if not self.enabled:
            return 0.0
        if not self._started:
            # The line said with the handoff or requeue (it may not have started yet,
            # TTS takes a moment) plays in full before the music comes in.
            self._heard_line = self._heard_line or speaking
            wait = START_QUIET_SECS if self._heard_line else NO_LINE_SECS
            if self._quiet_secs < wait:
                return 0.0
            self._started = True
        if speaking or self._quiet_secs < RELEASE_QUIET_SECS:
            return 10 ** (DUCK_DB / 20)
        return 1.0

    def _ramp(self, target: float, n: int, secs: float) -> np.ndarray:
        """Per sample gains from the current gain toward `target`, at the rate for that
        move. Disable drops to zero within this chunk."""
        start = self._gain
        if not self.enabled:
            end = 0.0
        else:
            if target > start:
                ducked = 10 ** (DUCK_DB / 20)
                span = RELEASE_SECS if start >= ducked - 1e-6 else FADE_IN_SECS
                step = secs / span
                end = min(target, start + step)
            else:
                end = max(target, start - secs / DUCK_SECS)
        self._gain = end
        return np.linspace(start, end, n, endpoint=False) if start != end else np.full(n, end)

    def _load(self, rate: int) -> np.ndarray | None:
        try:
            data, file_rate = sf.read(str(self.path), dtype="float32", always_2d=True)
        except Exception as e:  # noqa: BLE001 - no music is better than no call
            logger.error(f"[hold music] cannot read {self.path}: {type(e).__name__}: {e}")
            return None
        mono = data.mean(axis=1)
        if file_rate != rate:
            mono = soxr.resample(mono, file_rate, rate)
        logger.info(f"[hold music] {self.path.name}: {len(mono) / rate:.1f} s at {rate} Hz")
        return mono.astype(np.float64) * 32767


def _volume(raw: str | None) -> float:
    if raw is None or raw.strip() == "":
        return DEFAULT_VOLUME
    try:
        return min(1.0, max(0.0, float(raw)))
    except ValueError:
        logger.warning(f"[hold music] HOLD_MUSIC_VOLUME={raw!r} is not a number; using default")
        return DEFAULT_VOLUME


def hold_music_from_env(env: Mapping[str, str] | None = None) -> HoldMusicMixer | None:
    """The mixer for this call, or None when hold music is off."""
    env = os.environ if env is None else env
    if (env.get("HOLD_MUSIC") or "on").strip().lower() in OFF_VALUES:
        return None
    path = Path(env["HOLD_MUSIC_PATH"]) if env.get("HOLD_MUSIC_PATH") else DEFAULT_TRACK
    if not path.is_file():
        logger.warning(f"[hold music] {path} not found; hold music off")
        return None
    return HoldMusicMixer(path, volume=_volume(env.get("HOLD_MUSIC_VOLUME")))
