"""Flux voice controls read back test (P5-8, AP-22): settings, URL, scoring. No network."""

import pytest
from harness import readback as rb


def test_settings_cover_speeds_and_expressivity_extremes():
    labels = [s.label for s in rb.FLUX_CONTROL_SETTINGS]
    assert labels == [
        "speed 0.8",
        "speed 0.9",
        "speed 1.0",
        "speed 1.1",
        "speed 1.2",
        "expressivity -2",
        "expressivity 2",
    ]
    by_label = {s.label: s for s in rb.FLUX_CONTROL_SETTINGS}
    assert by_label["speed 1.0"].expressivity == 0
    assert by_label["expressivity -2"].speed == 1.0
    assert all(s.voice == "flux-alexis-en" for s in rb.FLUX_CONTROL_SETTINGS)


def test_speak_url_carries_speed_and_expressivity_for_flux():
    url = rb.speak_url("flux-alexis-en", speed=1.1, expressivity=-2)
    assert url.startswith("wss://api.deepgram.com/v2/speak?model=flux-alexis-en")
    assert "speed=1.1" in url and "expressivity=-2" in url
    plain = rb.speak_url("flux-alexis-en")
    assert "speed" not in plain and "expressivity" not in plain


def test_control_lines_add_the_verified_garble_line():
    keys = [line.key for line in rb.CONTROL_LINES]
    assert keys[: len(rb.LINES)] == [line.key for line in rb.LINES]
    verified = rb.CONTROL_LINES[-1]
    assert verified.text == "Thanks, you are verified."
    assert [e.kind for e in verified.entities] == ["verified"]


@pytest.mark.parametrize(
    ("heard", "ok"),
    [
        ("Thanks, you are verified.", True),
        ("Thanks. You're verified.", True),
        ("Thanks, you are very fied.", False),
        ("Thanks, you are ver.", False),
    ],
)
def test_verified_matcher(heard, ok):
    assert rb.MATCHERS["verified"](heard) is ok


def test_rescore_knows_the_verified_line():
    r = rb.LineResult("speed 1.0", "verified", 0, "Thanks, you're verified.", 0.1, {})
    (out,) = rb.rescore([r])
    assert out.entities == {"verified": True}
