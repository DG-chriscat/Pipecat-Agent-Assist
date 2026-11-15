import os

import pytest
from harness.procstats import ProcSampler, parse_stat_cpu_ticks, parse_status_rss_kb, read_sample


def test_parse_stat_cpu_ticks_sums_utime_and_stime():
    # comm may contain spaces and parens; fields 14 and 15 (1-based) are utime, stime.
    stat = "1234 (python -m (x) y) S 1 2 3 4 5 6 7 8 9 10 150 50 0 0 20 0 9 0 100 2000 300"
    assert parse_stat_cpu_ticks(stat) == 200


def test_parse_status_rss_kb():
    status = "Name:\tpython\nVmPeak:\t  900 kB\nVmRSS:\t  123456 kB\nThreads:\t9\n"
    assert parse_status_rss_kb(status) == 123456


def test_read_sample_for_this_process():
    s = read_sample(os.getpid())
    assert s.cpu_secs > 0
    assert s.rss_mb > 1


def test_proc_sampler_summary_from_fake_samples():
    sampler = ProcSampler(pid=1)
    # (wall time, cpu seconds, rss MB)
    sampler.add(0.0, 1.0, 100.0)
    sampler.add(1.0, 1.2, 110.0)
    sampler.add(3.0, 1.8, 105.0)
    summary = sampler.summary(since=0.0)
    assert summary["avg_cpu_pct"] == pytest.approx(26.7, abs=0.05)  # 0.8 s CPU over 3 s
    assert summary["peak_cpu_pct"] == pytest.approx(30.0)  # 0.6 s over 2 s
    assert summary["peak_rss_mb"] == 110.0
    assert summary["samples"] == 3


def test_proc_sampler_summary_needs_two_samples():
    sampler = ProcSampler(pid=1)
    sampler.add(0.0, 1.0, 100.0)
    with pytest.raises(ValueError):
        sampler.summary(since=0.0)
