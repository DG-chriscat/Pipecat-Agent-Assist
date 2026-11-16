"""Start one bot process per call (dial-in or browser caller).

The bot runs as a separate Python process (`python -m agent_assist_bot`), the same
pattern as Pipecat's Daily dial-in example where the server spawns one bot per room.
Non-secret settings go on argv; the meeting token goes through the child's
environment so it never shows up in `ps` output or logs. API keys are inherited
from the server's environment.

Warm pool (P5-2): a cold bot spends most of its start on imports and model loads
(Pipecat, Daily, Silero VAD, the tone reader), about 5 to 7 s before it can join.
`WarmBotPool` keeps WARM_BOT_POOL (default 1, max 2, 0 = off) bot processes started
with `--warm`: each loads everything, prints READY_LINE on stdout, then waits for one
JSON line on stdin with its room, token, and session. A call takes a ready one and
the pool starts a replacement. With none ready the call falls back to a cold start.
The assignment goes over the pipe, so the token stays off argv and out of the env.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass

from agent_assist_server import auth

logger = logging.getLogger(__name__)

BOT_MODULE = "agent_assist_bot"
TOKEN_ENV = "DAILY_ROOM_TOKEN"
WARM_FLAG = "--warm"
READY_LINE = "agent_assist_bot warm ready"
WARM_POOL_ENV = "WARM_BOT_POOL"
WARM_POOL_DEFAULT = 1
WARM_POOL_MAX = 2
# Let the bot that just took a call get its greeting out before the replacement's
# imports compete with it for the CPU.
REFILL_DELAY_SECS = 5.0
# A warm bot that exits before it is ready: retry after this, up to MAX_FAILURES in a
# row, then stop trying (calls go cold) until one gets ready again.
RETRY_DELAY_SECS = 10.0
MAX_FAILURES = 5
STOP_TIMEOUT_SECS = 10.0
# No warm bot ready but one loading (e.g. Fly just woke the machine for this call):
# wait this long for it before starting a cold one. Loading takes about 5 to 8 s, and
# a cold start next to it would load the same things while competing for the CPU.
WARM_WAIT_SECS = 12.0


@dataclass(frozen=True)
class BotLaunchRequest:
    """One bot process. call_id/call_domain are set for dial-in calls only; a browser
    caller (IN-12) leaves them None and the bot joins without dial-in settings."""

    room_url: str
    token: str
    session_id: str
    call_id: str | None = None
    call_domain: str | None = None


def build_bot_command(req: BotLaunchRequest, python: str | None = None) -> list[str]:
    cmd = [python or sys.executable, "-m", BOT_MODULE, "--room-url", req.room_url]
    if req.call_id:
        cmd += ["--call-id", req.call_id, "--call-domain", req.call_domain or ""]
    cmd += ["--session-id", req.session_id]
    return cmd


def build_warm_command(python: str | None = None) -> list[str]:
    return [python or sys.executable, "-m", BOT_MODULE, WARM_FLAG]


def build_warm_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The bot's env without a room token: API keys, and the bot key for the queue
    handoff endpoints, never the app password or the session secret itself (P2-6)."""
    env = dict(os.environ if base is None else base)
    env.pop(TOKEN_ENV, None)
    password = env.pop(auth.PASSWORD_ENV, "")
    secret = env.pop(auth.SECRET_ENV, "")
    env.pop(auth.BOT_KEY_ENV, None)
    if password and secret:
        env[auth.BOT_KEY_ENV] = auth.bot_key(secret)
    return env


def build_bot_env(req: BotLaunchRequest, base: dict[str, str] | None = None) -> dict[str, str]:
    env = build_warm_env(base)
    env[TOKEN_ENV] = req.token
    return env


def warm_pool_size(env: Mapping[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get(WARM_POOL_ENV, "").strip()
    try:
        size = int(raw) if raw else WARM_POOL_DEFAULT
    except ValueError:
        logger.warning("%s=%r is not a number; using %s", WARM_POOL_ENV, raw, WARM_POOL_DEFAULT)
        size = WARM_POOL_DEFAULT
    return max(0, min(WARM_POOL_MAX, size))


@dataclass
class _WarmBot:
    proc: asyncio.subprocess.Process
    reader: asyncio.Task | None = None
    ready: bool = False
    taken: bool = False


class WarmBotPool:
    """Keeps `size` warm bot processes ready for the next call."""

    def __init__(
        self,
        size: int,
        *,
        command: Callable[[], list[str]] = build_warm_command,
        env: Callable[[], dict[str, str]] = build_warm_env,
        refill_delay: float = REFILL_DELAY_SECS,
        retry_delay: float = RETRY_DELAY_SECS,
        max_failures: int = MAX_FAILURES,
    ) -> None:
        self.size = size
        self._command = command
        self._env = env
        self._refill_delay = refill_delay
        self._retry_delay = retry_delay
        self._max_failures = max_failures
        self._bots: list[_WarmBot] = []  # spawned, not yet taken
        self._tasks: set[asyncio.Task] = set()
        self._call_readers: set[asyncio.Task] = set()  # stdout readers of bots in a call
        self._pending_refills = 0
        self._closed = False
        self._changed = asyncio.Event()  # a warm bot got ready or exited
        self.failures = 0  # warm bots in a row that exited before they were ready

    # ---- state ----------------------------------------------------------------------

    @property
    def _idle(self) -> list[_WarmBot]:
        return [b for b in self._bots if b.ready and b.proc.returncode is None]

    @property
    def ready_count(self) -> int:
        return len(self._idle)

    @property
    def starting_count(self) -> int:
        """Warm bots still loading (spawned, alive, not ready)."""
        return len([b for b in self._bots if not b.ready and b.proc.returncode is None])

    def idle_pids(self) -> list[int]:
        return [b.proc.pid for b in self._idle]

    # ---- lifecycle ------------------------------------------------------------------

    async def start(self) -> None:
        logger.info("warm bot pool: keeping %s ready", self.size)
        self._fill()

    async def close(self) -> None:
        """Stop the warm bots that have no call yet. Bots in a call keep running."""
        self._closed = True
        for task in list(self._tasks):
            task.cancel()
        bots, self._bots = self._bots, []
        for b in bots:
            if b.proc.returncode is None:
                b.proc.terminate()
        for b in bots:
            try:
                await asyncio.wait_for(b.proc.wait(), STOP_TIMEOUT_SECS)
            except TimeoutError:
                b.proc.kill()
                await b.proc.wait()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def take(self) -> asyncio.subprocess.Process | None:
        """A ready warm bot for a call (and a refill scheduled), or None."""
        for b in self._idle:
            b.taken = True
            self._bots.remove(b)
            # Its stdout reader now belongs to the call: close() must not cancel it.
            if b.reader is not None and b.reader in self._tasks:
                self._tasks.discard(b.reader)
                self._call_readers.add(b.reader)
                b.reader.add_done_callback(self._call_readers.discard)
            self._spawn_task(self._refill_after(self._refill_delay))
            return b.proc
        return None

    def readers_for_taken_bots(self) -> int:
        return len(self._call_readers)

    async def take_or_wait(self, timeout: float) -> asyncio.subprocess.Process | None:
        """`take()`, or when none is ready but one is loading, wait up to `timeout` for
        it: a half loaded warm bot is closer to joining than a cold start from zero."""
        proc = self.take()
        if proc is not None or timeout <= 0:
            return proc
        end = asyncio.get_running_loop().time() + timeout
        while self.starting_count and not self._closed:
            left = end - asyncio.get_running_loop().time()
            if left <= 0:
                break
            self._changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), left)
            proc = self.take()
            if proc is not None:
                return proc
        return None

    # ---- internals ------------------------------------------------------------------

    def _spawn_task(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _wanted(self) -> int:
        return self.size - len(self._bots) - self._pending_refills

    def _fill(self) -> None:
        if self._closed or self.failures >= self._max_failures:
            return
        for _ in range(max(0, self._wanted())):
            self._spawn_task(self._run_one())

    async def _refill_after(self, delay: float) -> None:
        self._pending_refills += 1
        try:
            await asyncio.sleep(delay)
        finally:
            self._pending_refills -= 1
        self._fill()

    async def _run_one(self) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._command(),
                env=self._env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            logger.error("warm bot failed to start: %s", e)
            self._note_failure()
            return
        bot = _WarmBot(proc, reader=asyncio.current_task())
        self._bots.append(bot)
        logger.info("warm bot starting pid=%s", proc.pid)
        assert proc.stdout is not None
        # Keep reading stdout for the bot's whole life (its call included) so the pipe
        # never fills. Once taken, this reader is no longer the pool's to cancel.
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if not bot.ready and line == READY_LINE:
                bot.ready = True
                self.failures = 0
                self._changed.set()
                logger.info("warm bot ready pid=%s", proc.pid)
            elif line:
                print(line, flush=True)
        await proc.wait()
        self._changed.set()
        if bot.taken or self._closed:
            return
        # Died while idle: replace it.
        if bot in self._bots:
            self._bots.remove(bot)
        logger.warning(
            "warm bot pid=%s exited %s before a call (ready=%s)",
            proc.pid,
            proc.returncode,
            bot.ready,
        )
        if not bot.ready:
            self._note_failure()
            return
        self._fill()

    def _note_failure(self) -> None:
        self.failures += 1
        if self.failures >= self._max_failures:
            logger.error(
                "warm bot pool: %s warm bots failed in a row; calls start cold", self.failures
            )
            return
        self._spawn_task(self._refill_after(self._retry_delay))


async def assign(proc: asyncio.subprocess.Process, req: BotLaunchRequest) -> None:
    """Hand a warm bot its call: one JSON line on stdin, then EOF."""
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(asdict(req)) + "\n").encode())
    await proc.stdin.drain()
    proc.stdin.close()


class BotLauncher:
    """Starts the bot for a call: a warm one from the pool when ready, else cold.
    Swap for a fake in tests."""

    def __init__(
        self,
        pool: WarmBotPool | None = None,
        *,
        command_for: Callable[[BotLaunchRequest], list[str]] = build_bot_command,
        warm_wait: float = WARM_WAIT_SECS,
    ) -> None:
        self.pool = pool
        self._command_for = command_for
        self._warm_wait = warm_wait
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.last_mode: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> BotLauncher:
        size = warm_pool_size(env)
        return cls(WarmBotPool(size) if size else None)

    async def start(self) -> None:
        if self.pool is not None:
            await self.pool.start()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()

    async def launch(self, req: BotLaunchRequest) -> int:
        proc = await self.pool.take_or_wait(self._warm_wait) if self.pool is not None else None
        mode = "warm"
        if proc is not None:
            try:
                await assign(proc, req)
            except (BrokenPipeError, ConnectionResetError) as e:
                logger.warning("warm bot pid=%s lost before assignment: %s", proc.pid, e)
                proc = None
        if proc is None:
            proc = await asyncio.create_subprocess_exec(
                *self._command_for(req), env=build_bot_env(req)
            )
            mode = "cold"
        self.processes[req.session_id] = proc
        self.last_mode = mode
        logger.info(
            "bot started session=%s room=%s pid=%s mode=%s",
            req.session_id,
            req.room_url,
            proc.pid,
            mode,
        )
        return proc.pid


def get_launcher(app) -> BotLauncher:
    """The app's launcher: the one the lifespan started, a test's fake, or (outside the
    lifespan) a cold-only one."""
    launcher = getattr(app.state, "bot_launcher", None)
    if launcher is None:
        launcher = BotLauncher()
        app.state.bot_launcher = launcher
    return launcher


async def launch_in_background(launcher: BotLauncher, req: BotLaunchRequest) -> None:
    """Run after the HTTP response: the launch may wait for a warm bot that is still
    loading. Logs the `bot_launched` timing line (with warm or cold); errors are
    logged, since there is no request left to fail."""
    try:
        await launcher.launch(req)
    except Exception:
        logger.exception("bot launch failed session=%s", req.session_id)
        return
    log_timing("bot_launched", session=req.session_id, mode=getattr(launcher, "last_mode", None))


def log_timing(event: str, *, log: logging.Logger | None = None, **fields: object) -> float:
    """Call timeline line, same format as agent_assist_bot.timing (PRD IN-1 test)."""
    t = time.time()
    extra = "".join(f" {k}={v}" for k, v in fields.items() if v not in (None, ""))
    (log or logger).info("[timing] event=%s t=%.3f%s", event, t, extra)
    return t
