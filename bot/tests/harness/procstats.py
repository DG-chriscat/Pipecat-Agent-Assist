"""CPU and memory sampling of another process via /proc (Linux only, no psutil)."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field

_CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def parse_stat_cpu_ticks(stat: str) -> int:
    """utime + stime in clock ticks from a /proc/<pid>/stat line."""
    # comm (field 2) is in parens and may contain spaces; split after the last ')'.
    fields = stat[stat.rindex(")") + 2 :].split()
    # fields[0] is state (field 3), so utime (14) and stime (15) are at 11 and 12.
    return int(fields[11]) + int(fields[12])


def parse_status_rss_kb(status: str) -> int:
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise ValueError("VmRSS not found")


@dataclass(frozen=True)
class Sample:
    wall: float
    cpu_secs: float
    rss_mb: float


def read_sample(pid: int) -> Sample:
    with open(f"/proc/{pid}/stat") as f:
        ticks = parse_stat_cpu_ticks(f.read())
    with open(f"/proc/{pid}/status") as f:
        rss_kb = parse_status_rss_kb(f.read())
    return Sample(time.time(), ticks / _CLK_TCK, rss_kb / 1024)


@dataclass
class ProcSampler:
    """Samples a process periodically. CPU % is of one core (100 = one full core)."""

    pid: int
    samples: list[Sample] = field(default_factory=list)

    def add(self, wall: float, cpu_secs: float, rss_mb: float) -> None:
        self.samples.append(Sample(wall, cpu_secs, rss_mb))

    async def run(self, interval: float = 0.5) -> None:
        while True:
            try:
                self.samples.append(read_sample(self.pid))
            except (FileNotFoundError, ProcessLookupError):
                return
            await asyncio.sleep(interval)

    def summary(self, since: float, until: float = float("inf")) -> dict:
        s = [x for x in self.samples if since <= x.wall <= until]
        if len(s) < 2:
            raise ValueError("need at least two samples in the window")
        avg = (s[-1].cpu_secs - s[0].cpu_secs) / (s[-1].wall - s[0].wall) * 100
        steps = zip(s, s[1:], strict=False)
        peak = max((b.cpu_secs - a.cpu_secs) / (b.wall - a.wall) * 100 for a, b in steps)
        return {
            "avg_cpu_pct": round(avg, 1),
            "peak_cpu_pct": round(peak, 1),
            "peak_rss_mb": round(max(x.rss_mb for x in s), 1),
            "samples": len(s),
        }
