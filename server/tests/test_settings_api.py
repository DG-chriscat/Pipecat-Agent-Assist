"""Settings API (P5-5, PRD AP-20): GET/PUT /settings, reset, voices, preview, and the
settings a call is launched with. No network: catalog and Deepgram are faked."""

from __future__ import annotations

import httpx
import pytest
from agent_assist_server import auth, bot_launcher, bot_settings, settings_api
from agent_assist_server import voice_catalog as vc
from agent_assist_server.app import create_app
from agent_assist_server.bot_launcher import BotLaunchRequest
from agent_assist_server.bot_settings import StaticVoiceCatalog, Voice
from fastapi.testclient import TestClient

CATALOG = StaticVoiceCatalog(
    {
        "aura-2": [Voice("aura-2-thalia-en", "Thalia"), Voice("aura-2-orion-en", "Orion")],
        "flux": [
            Voice("flux-hannah-en", "Hannah", "American", "Female"),
            Voice("flux-alexis-en", "Alexis", "American", "Female"),
        ],
    }
)
PASSWORD = "pw-for-tests"
SECRET = "test-session-secret-0123456789abcdef"


def make_app(env=None):
    app = create_app(env=env or {})
    app.state.voice_catalog = CATALOG
    app.state.settings_store = bot_settings.MemorySettingsStore(
        defaults=lambda: bot_settings.defaults({})
    )
    return app


@pytest.fixture
def app():
    return make_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def values(client) -> dict:
    return client.get("/settings").json()["values"]


def test_get_returns_defaults_packs_and_limits(client):
    body = client.get("/settings").json()
    assert body["values"] == bot_settings.defaults({})
    assert body["defaults"] == bot_settings.defaults({})
    assert body["is_default"] is True and body["updated_at"] is None
    packs = {p["id"]: p for p in body["packs"]}
    assert set(packs) == {"claims_status", "account_payments", "general_question"}
    claims = packs["claims_status"]
    assert claims["name"] == "Claim status"
    assert claims["questions"][0]["key"] == "claim_number"
    assert claims["questions"][0]["ask"].startswith("Do you have the claim number")
    assert body["limits"]["greeting_text"] == bot_settings.MAX_GREETING
    assert body["limits"]["hold_update_secs"] == [5, 600]


def test_get_lists_the_stt_model_choices_per_side(client):
    body = client.get("/settings").json()
    assert body["stt_models"] == {
        "intake": ["nova-3-general", "flux-general-en"],
        "assist": ["nova-3-general", "nova-3-medical", "flux-general-en"],
    }


def test_put_saves_stt_models_and_rejects_a_medical_intake(client):
    new = {**values(client), "stt_intake_model": "nova-3-general"}
    new["stt_assist_model"] = "nova-3-medical"
    r = client.put("/settings", json={"values": new})
    assert r.status_code == 200, r.text
    assert values(client)["stt_assist_model"] == "nova-3-medical"
    r = client.put("/settings", json={"values": {**new, "stt_intake_model": "nova-3-medical"}})
    assert r.status_code == 422
    assert set(r.json()["detail"]["errors"]) == {"stt_intake_model"}


def test_put_saves_and_the_next_get_returns_it(client, app):
    new = {**values(client), "tts_model": "aura-2", "tts_voice": "aura-2-orion-en"}
    r = client.put("/settings", json={"values": new, "agent": "Ana Ruiz"})
    assert r.status_code == 200, r.text
    assert r.json()["values"] == new and r.json()["is_default"] is False
    assert values(client) == new
    (event,) = app.state.settings_store.events
    assert event.agent == "Ana Ruiz" and set(event.changes) == {"tts_model", "tts_voice"}


def test_put_rejects_bad_settings_with_field_errors(client):
    bad = {
        **values(client),
        "tts_model": "flux",
        "tts_voice": "aura-2-thalia-en",
        "greeting_text": "Hello, how can I help?",
    }
    r = client.put("/settings", json={"values": bad})
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert set(errors) == {"tts_voice", "greeting_text"}
    assert values(client) == bot_settings.defaults({})


def test_put_with_no_values_object_is_a_422(client):
    assert client.put("/settings", json={"agent": "x"}).status_code == 422


def test_reset_goes_back_to_defaults(client):
    client.put("/settings", json={"values": {**values(client), "hold_update_secs": 30}})
    r = client.post("/settings/reset", json={"agent": "Ana"})
    assert r.status_code == 200 and r.json()["is_default"] is True
    assert values(client)["hold_update_secs"] == 60


def test_voices_lists_both_models(client):
    body = client.get("/settings/voices").json()
    assert [v["id"] for v in body["aura-2"]] == ["aura-2-thalia-en", "aura-2-orion-en"]
    assert body["flux"][0] == {
        "id": "flux-hannah-en",
        "name": "Hannah",
        "accent": "American",
        "gender": "Female",
    }


def test_preview_plays_the_sample_through_the_server(client, monkeypatch):
    seen = {}

    async def fake(model, voice, text, **kw):
        seen.update(model=model, voice=voice, text=text)
        return b"ID3audio"

    monkeypatch.setattr(vc, "synthesize", fake)
    r = client.post("/settings/preview", json={"tts_model": "flux", "tts_voice": "flux-hannah-en"})
    assert r.status_code == 200 and r.content == b"ID3audio"
    assert r.headers["content-type"] == "audio/mpeg"
    assert seen["model"] == "flux" and seen["voice"] == "flux-hannah-en"
    assert seen["text"] == settings_api.PREVIEW_LINE


def test_preview_can_read_the_greeting_being_edited(client, monkeypatch):
    seen = {}

    async def fake(model, voice, text, **kw):
        seen["text"] = text
        return b"x"

    monkeypatch.setattr(vc, "synthesize", fake)
    client.post(
        "/settings/preview",
        json={"tts_model": "aura-2", "tts_voice": "aura-2-orion-en", "text": "Hi there."},
    )
    assert seen["text"] == "Hi there."


def test_get_lists_the_flux_control_limits(client):
    limits = client.get("/settings").json()["limits"]
    assert limits["tts_speed"] == [bot_settings.MIN_TTS_SPEED, bot_settings.MAX_TTS_SPEED]
    assert limits["readback_speed"] == [0.8, 1.0]
    assert limits["speed_step"] == 0.05


def test_put_saves_flux_controls_and_rejects_a_bad_speed(client):
    new = {**values(client), "tts_speed": 1.1, "tts_expressivity": 1, "slow_readback": True}
    assert client.put("/settings", json={"values": new}).status_code == 200
    assert values(client)["tts_speed"] == 1.1 and values(client)["tts_expressivity"] == 1
    r = client.put("/settings", json={"values": {**new, "tts_speed": 1.42}})
    assert r.status_code == 422
    assert set(r.json()["detail"]["errors"]) == {"tts_speed"}


def _fake_synth(monkeypatch, calls):
    async def fake(model, voice, text, **kw):
        calls.append({"model": model, "voice": voice, "text": text, **kw})
        return f"<{len(calls)}>".encode()

    monkeypatch.setattr(vc, "synthesize", fake)


def test_flux_preview_reads_the_member_id_part_at_the_read_back_speed(client, monkeypatch):
    calls = []
    _fake_synth(monkeypatch, calls)
    r = client.post(
        "/settings/preview",
        json={
            "tts_model": "flux",
            "tts_voice": "flux-alexis-en",
            "tts_speed": 1.1,
            "tts_expressivity": 1,
            "slow_readback": True,
            "readback_speed": 0.9,
        },
    )
    assert r.status_code == 200 and r.content == b"<1><2>"
    greeting, readback = calls
    assert greeting["text"] == settings_api.PREVIEW_GREETING
    assert (greeting["speed"], greeting["expressivity"]) == (1.1, 1)
    assert readback["text"] == settings_api.PREVIEW_READBACK
    assert (readback["speed"], readback["expressivity"]) == (0.9, 1)
    whole = f"{settings_api.PREVIEW_GREETING} {settings_api.PREVIEW_READBACK}"
    assert settings_api.PREVIEW_LINE == whole


def test_flux_preview_without_slow_read_backs_is_one_line_at_the_speed(client, monkeypatch):
    calls = []
    _fake_synth(monkeypatch, calls)
    client.post(
        "/settings/preview",
        json={
            "tts_model": "flux",
            "tts_voice": "flux-alexis-en",
            "tts_speed": 1.1,
            "slow_readback": False,
            "readback_speed": 0.9,
        },
    )
    (only,) = calls
    assert only["text"] == settings_api.PREVIEW_LINE and only["speed"] == 1.1


def test_greeting_preview_is_one_line_at_the_speed(client, monkeypatch):
    calls = []
    _fake_synth(monkeypatch, calls)
    client.post(
        "/settings/preview",
        json={"tts_model": "flux", "tts_voice": "flux-alexis-en", "text": "Hi.", "tts_speed": 0.9},
    )
    (only,) = calls
    assert only["text"] == "Hi." and only["speed"] == 0.9


def test_aura_preview_ignores_the_flux_controls(client, monkeypatch):
    calls = []
    _fake_synth(monkeypatch, calls)
    client.post(
        "/settings/preview",
        json={
            "tts_model": "aura-2",
            "tts_voice": "aura-2-thalia-en",
            "tts_speed": 1.1,
            "tts_expressivity": 2,
            "slow_readback": True,
        },
    )
    (only,) = calls
    assert only["text"] == settings_api.PREVIEW_LINE
    assert only.get("speed") is None and only.get("expressivity") is None


def test_preview_rejects_flux_controls_out_of_range(client, monkeypatch):
    _fake_synth(monkeypatch, [])
    r = client.post(
        "/settings/preview",
        json={"tts_model": "flux", "tts_voice": "flux-alexis-en", "tts_speed": 1.45},
    )
    assert r.status_code == 422


def test_preview_checks_the_voice(client):
    r = client.post(
        "/settings/preview", json={"tts_model": "flux", "tts_voice": "aura-2-thalia-en"}
    )
    assert r.status_code == 422


def test_preview_failure_is_a_502_without_detail_leak(client, monkeypatch):
    async def fail(*a, **kw):
        raise vc.SpeakError("Deepgram speak returned 401")

    monkeypatch.setattr(vc, "synthesize", fail)
    r = client.post(
        "/settings/preview", json={"tts_model": "aura-2", "tts_voice": "aura-2-thalia-en"}
    )
    assert r.status_code == 502 and "preview" in r.json()["detail"].lower()


# ---- behind the login -----------------------------------------------------------------


@pytest.fixture
def locked():
    app = make_app({auth.PASSWORD_ENV: PASSWORD, auth.SECRET_ENV: SECRET})
    return app, TestClient(app, base_url="http://localhost")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/settings"),
        ("PUT", "/settings"),
        ("POST", "/settings/reset"),
        ("GET", "/settings/voices"),
        ("POST", "/settings/preview"),
    ],
)
def test_every_settings_route_needs_the_login(locked, method, path):
    _, c = locked
    assert c.request(method, path, json={}).status_code == 401


def test_bot_key_does_not_open_settings(locked):
    _, c = locked
    hdr = {"Authorization": f"Bearer {auth.bot_key(SECRET)}"}
    assert c.get("/settings", headers=hdr).status_code == 401


def test_the_change_is_logged_with_the_login_session(locked):
    app, c = locked
    c.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    cookie = c.cookies.get(auth.COOKIE_NAME)
    v = c.get("/settings").json()["values"]
    assert c.put("/settings", json={"values": {**v, "hold_update_secs": 20}}).status_code == 200
    (event,) = app.state.settings_store.events
    assert event.session_id == app.state.auth.session_id(cookie)
    assert event.session_id and event.session_id not in ("local", cookie)


def test_without_login_the_session_is_local(client, app):
    client.put("/settings", json={"values": {**values(client), "hold_update_secs": 20}})
    assert app.state.settings_store.events[0].session_id == settings_api.LOCAL_SESSION


# ---- the call gets the settings at room assignment ------------------------------------


class FakeLauncher:
    def __init__(self):
        self.launched: list[BotLaunchRequest] = []
        self.last_mode = "warm"

    async def launch(self, req):
        self.launched.append(req)
        return 1


async def test_launch_reads_the_settings_when_the_call_is_assigned():
    store = bot_settings.MemorySettingsStore(defaults=lambda: bot_settings.defaults({}))
    book = settings_api.CallSettingsBook(store)
    launcher = FakeLauncher()
    req = BotLaunchRequest("https://x.daily.co/r", "tok", "s1")
    v = (await store.get()).values
    await store.save(
        {**v, "tts_model": "flux", "tts_voice": "flux-hannah-en"}, session_id="x", agent=None
    )
    await bot_launcher.launch_in_background(launcher, req, settings=book)
    (sent,) = launcher.launched
    assert sent.settings["tts_voice"] == "flux-hannah-en"
    # A later change does not touch the call that already has its settings.
    await store.save({**v, "intake_pack": "account_payments"}, session_id="x", agent=None)
    assert book.for_session("s1")["intake_pack"] == "claims_status"


async def test_launch_goes_ahead_with_bot_defaults_when_settings_fail():
    class Broken:
        async def get(self):
            raise RuntimeError("db down")

    launcher = FakeLauncher()
    await bot_launcher.launch_in_background(
        launcher,
        BotLaunchRequest("https://x.daily.co/r", "tok", "s1"),
        settings=settings_api.CallSettingsBook(Broken()),
    )
    assert launcher.launched[0].settings is None


def test_browser_call_launches_with_the_saved_settings(app, monkeypatch):
    import json

    from agent_assist_server.daily import DailyRestClient

    def daily(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("/rooms"):
            name = body["name"]
            return httpx.Response(200, json={"name": name, "url": f"https://x.daily.co/{name}"})
        return httpx.Response(200, json={"token": "tok"})

    app.state.daily_client_factory = lambda: DailyRestClient(
        api_key="k", transport=httpx.MockTransport(daily)
    )
    launcher = FakeLauncher()
    app.state.bot_launcher = launcher
    c = TestClient(app)
    c.put(
        "/settings",
        json={
            "values": {
                **values(c),
                "greeting_text": (
                    "Hello. You're talking to an AI assistant, and a person will join after."
                ),
            }
        },
    )
    sid = c.post("/calls/browser").json()["session_id"]
    (req,) = launcher.launched
    assert req.settings["greeting_text"].startswith("Hello. You're talking to an AI")
    assert settings_api.get_call_settings(app).for_session(sid) == req.settings
