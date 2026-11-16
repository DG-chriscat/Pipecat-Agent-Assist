"""A daily-python participant for live tests.

Joins a room under a display name, plays 16-bit mono PCM (e.g. a WAV) as its
microphone, and records every remote participant's microphone audio with
wall-clock timestamps so tests can measure when someone started talking.

Usage:
    async with HarnessParticipant("caller") as caller:
        await caller.join(room_url)
        bot_id = await caller.wait_for_participant("Vitals Assistant")
        ...
        await caller.play(pcm)
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from daily import AudioData, CallClient, CustomAudioSource, CustomAudioTrack, Daily, EventHandler

from harness.audio import find_speech_segments, read_wav, write_wav

SAMPLE_RATE = 16000
CHUNK_MS = 20

_daily_lock = threading.Lock()
_daily_initialized = False


def _ensure_daily() -> None:
    global _daily_initialized
    with _daily_lock:
        if not _daily_initialized:
            try:
                Daily.init()
            except Exception:
                # Already initialized elsewhere in this process (e.g. by Pipecat).
                pass
            _daily_initialized = True


@dataclass
class ParticipantInfo:
    id: str
    user_name: str
    joined_at: float
    left_at: float | None = None


@dataclass
class Recording:
    """Timestamped audio received from one remote participant."""

    chunks: list[tuple[float, bytes]] = field(default_factory=list)

    def pcm(self) -> bytes:
        return b"".join(c for _, c in self.chunks)

    def segments(self, **kwargs) -> list[tuple[float, float]]:
        return find_speech_segments(self.chunks, sample_rate=SAMPLE_RATE, **kwargs)

    def first_speech_after(self, t: float, **kwargs) -> float | None:
        for start, _end in self.segments(**kwargs):
            if start >= t:
                return start
        return None


class _Handler(EventHandler):
    def __init__(self, owner: HarnessParticipant):
        super().__init__()
        self._owner = owner

    def on_participant_joined(self, participant: Mapping[str, Any]) -> None:
        self._owner._on_participant_joined(participant)

    def on_participant_left(self, participant: Mapping[str, Any], reason: str) -> None:
        self._owner._on_participant_left(participant)

    def on_app_message(self, message: Any, sender: str) -> None:
        self._owner._on_app_message(message, sender)


class HarnessParticipant:
    def __init__(self, user_name: str, *, sample_rate: int = SAMPLE_RATE):
        _ensure_daily()
        self.user_name = user_name
        self.sample_rate = sample_rate
        self.joined_at: float | None = None
        self.participants: dict[str, ParticipantInfo] = {}
        self.recordings: dict[str, Recording] = {}
        # (wall time, sender, message) for every Daily app message this client got.
        self.app_messages: list[tuple[float, str, Any]] = []
        self._loop = asyncio.get_running_loop()
        self._changed = asyncio.Event()
        self._source = CustomAudioSource(sample_rate, 1)
        self._track = CustomAudioTrack(self._source)
        self._handler = _Handler(self)
        self._client = CallClient(event_handler=self._handler)

    async def __aenter__(self) -> HarnessParticipant:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.leave()

    # ---- call lifecycle -------------------------------------------------

    async def join(self, room_url: str, token: str | None = None) -> None:
        self._client.set_user_name(self.user_name)
        self._client.update_subscription_profiles(
            {"base": {"camera": "unsubscribed", "microphone": "subscribed"}}
        )
        fut = self._loop.create_future()
        self._client.join(
            room_url,
            token,
            client_settings={
                "inputs": {
                    "camera": False,
                    "microphone": {
                        "isEnabled": True,
                        "settings": {"customTrack": {"id": self._track.id}},
                    },
                },
                "publishing": {"camera": False, "microphone": True},
            },
            completion=self._completion(fut),
        )
        _data, error = await fut
        if error:
            raise RuntimeError(f"{self.user_name} failed to join: {error}")
        self.joined_at = time.time()
        for pid, p in self._client.participants().items():
            if pid != "local":
                self._on_participant_joined(p)

    async def leave(self) -> None:
        if self._client is None:
            return
        fut = self._loop.create_future()
        self._client.leave(completion=self._completion(fut))
        try:
            await asyncio.wait_for(fut, 10)
        except TimeoutError:
            pass
        self._client.release()
        self._client = None

    # ---- participants ---------------------------------------------------

    async def wait_for_participant(self, user_name: str, timeout: float = 30) -> ParticipantInfo:
        async def _wait():
            while True:
                for p in self.participants.values():
                    if p.user_name == user_name:
                        return p
                self._changed.clear()
                await self._changed.wait()

        return await asyncio.wait_for(_wait(), timeout)

    def _on_participant_joined(self, participant: Mapping[str, Any]) -> None:
        pid = participant["id"]
        name = participant.get("info", {}).get("userName", "")
        now = time.time()

        def _apply():
            if pid in self.participants:
                return
            self.participants[pid] = ParticipantInfo(pid, name, now)
            self._start_recording(pid)
            self._changed.set()

        self._loop.call_soon_threadsafe(_apply)

    def _on_participant_left(self, participant: Mapping[str, Any]) -> None:
        pid = participant["id"]
        now = time.time()

        def _apply():
            p = self.participants.get(pid)
            if p is not None and p.left_at is None:
                p.left_at = now
                self._changed.set()

        self._loop.call_soon_threadsafe(_apply)

    async def wait_for_participant_left(
        self, user_name: str, timeout: float = 30
    ) -> ParticipantInfo:
        """Wait until a participant with this name, seen in the room, has left."""

        async def _wait():
            while True:
                for p in self.participants.values():
                    if p.user_name == user_name and p.left_at is not None:
                        return p
                self._changed.clear()
                await self._changed.wait()

        return await asyncio.wait_for(_wait(), timeout)

    # ---- app messages ---------------------------------------------------

    def _on_app_message(self, message: Any, sender: str) -> None:
        now = time.time()

        def _apply():
            self.app_messages.append((now, sender, message))
            self._changed.set()

        self._loop.call_soon_threadsafe(_apply)

    async def wait_for_app_messages(self, count: int, timeout: float = 30) -> None:
        async def _wait():
            while len(self.app_messages) < count:
                self._changed.clear()
                await self._changed.wait()

        await asyncio.wait_for(_wait(), timeout)

    # ---- audio ----------------------------------------------------------

    def _start_recording(self, participant_id: str) -> None:
        rec = self.recordings.setdefault(participant_id, Recording())

        def on_audio(_pid: str, audio: AudioData, _source: str) -> None:
            # Timestamp the start of the chunk, not when the callback fired.
            frames = audio.audio_frames
            dur = len(frames) / 2 / self.sample_rate
            rec.chunks.append((time.time() - dur, frames))

        self._client.set_audio_renderer(
            participant_id,
            on_audio,
            audio_source="microphone",
            sample_rate=self.sample_rate,
            callback_interval_ms=CHUNK_MS,
        )

    def recording_for(self, user_name: str) -> Recording:
        for pid, p in self.participants.items():
            if p.user_name == user_name:
                return self.recordings[pid]
        raise KeyError(user_name)

    async def play(self, pcm: bytes) -> tuple[float, float]:
        """Play 16-bit mono PCM at self.sample_rate in real time. Returns (start, end)."""
        chunk = self.sample_rate * 2 * 100 // 1000  # 100 ms
        start = time.time()
        for i in range(0, len(pcm), chunk):
            fut = self._loop.create_future()
            self._source.write_frames(pcm[i : i + chunk], completion=self._completion(fut))
            await fut
        return start, time.time()

    async def play_wav(self, path: Path | str) -> tuple[float, float]:
        pcm, sr = read_wav(Path(path).read_bytes())
        if sr != self.sample_rate:
            raise ValueError(f"WAV is {sr} Hz, harness expects {self.sample_rate} Hz")
        return await self.play(pcm)

    def save_recording(self, user_name: str, path: Path | str) -> None:
        write_wav(path, self.recording_for(user_name).pcm(), self.sample_rate)

    # ---- helpers --------------------------------------------------------

    def _completion(self, fut: asyncio.Future):
        loop = self._loop

        def _cb(*args):
            def _set():
                if not fut.done():
                    fut.set_result(args if len(args) != 1 else args[0])

            loop.call_soon_threadsafe(_set)

        return _cb
