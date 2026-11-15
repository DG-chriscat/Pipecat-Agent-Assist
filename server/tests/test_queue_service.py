"""Queue service state machine (PRD QH-1 to QH-4, QH-10) with a fake clock."""

import pytest
from agent_assist_server.queue_service import (
    OFFER_TIMEOUT_SECS,
    AgentState,
    CallStatus,
    QueueError,
    QueueService,
)


class FakeClock:
    """now() plus call_later() timers that fire only when advance() passes them."""

    def __init__(self, start: float = 1000.0):
        self.t = start
        self._timers: list[list] = []  # [due, seq, fn, cancelled]
        self._seq = 0

    def now(self) -> float:
        return self.t

    def call_later(self, delay, fn):
        self._seq += 1
        timer = [self.t + delay, self._seq, fn, False]
        self._timers.append(timer)

        class Handle:
            def cancel(self_inner):
                timer[3] = True

        return Handle()

    def advance(self, secs: float) -> None:
        end = self.t + secs
        while True:
            due = sorted((t for t in self._timers if not t[3] and t[0] <= end), key=lambda t: t[:2])
            if not due:
                break
            timer = due[0]
            timer[3] = True
            self.t = timer[0]
            timer[2]()
        self.t = end


class FakeMinter:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    async def __call__(self, room_name: str, user_name: str):
        self.calls.append((room_name, user_name))
        if self.fail:
            raise RuntimeError("daily down")
        return f"token-{room_name}-{user_name}", 9999


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def minter():
    return FakeMinter()


@pytest.fixture
def svc(clock, minter):
    s = QueueService(clock=clock, mint_token=minter)
    s.events: list[dict] = []
    s.subscribe(s.events.append)
    return s


def room(n: int) -> str:
    return f"https://example.daily.co/caller-{n}"


def enqueue(svc, n: int, summary=None):
    return svc.enqueue_call(f"s{n}", room(n), summary or {"reason": f"r{n}"})


def available_agent(svc, clock, name: str, *, after: float = 1.0):
    a = svc.sign_in(name)
    svc.set_status(a.agent_id, AgentState.AVAILABLE)
    clock.advance(after)
    return a


def types(events):
    return [e["type"] for e in events]


# ---- QH-1: agent states -------------------------------------------------


def test_sign_in_starts_offline(svc):
    a = svc.sign_in("Ana")
    assert a.state is AgentState.OFFLINE
    assert a.name == "Ana"


def test_sign_in_same_name_returns_same_agent(svc):
    assert svc.sign_in("Ana").agent_id == svc.sign_in("Ana").agent_id


def test_sign_in_rejects_blank_name(svc):
    with pytest.raises(QueueError):
        svc.sign_in("   ")


def test_offline_agent_gets_no_offer(svc):
    svc.sign_in("Ana")
    enqueue(svc, 1)
    assert "offer" not in types(svc.events)
    assert svc.get_call("s1").status is CallStatus.QUEUED


@pytest.mark.parametrize(
    "path",
    [
        [AgentState.AVAILABLE, AgentState.OFFLINE],
        [AgentState.AVAILABLE, AgentState.OFFLINE, AgentState.AVAILABLE],
    ],
)
def test_allowed_manual_transitions(svc, path):
    a = svc.sign_in("Ana")
    for state in path:
        svc.set_status(a.agent_id, state)
    assert svc.get_agent(a.agent_id).state is path[-1]


@pytest.mark.parametrize("target", [AgentState.RINGING, AgentState.ON_CALL, AgentState.WRAP_UP])
def test_agent_cannot_set_system_states_from_offline(svc, target):
    a = svc.sign_in("Ana")
    with pytest.raises(QueueError):
        svc.set_status(a.agent_id, target)


def test_unknown_agent_raises(svc):
    with pytest.raises(QueueError):
        svc.set_status("nope", AgentState.AVAILABLE)


async def test_full_lifecycle_available_ringing_on_call_wrap_up_available(svc, clock):
    a = available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    assert svc.get_agent(a.agent_id).state is AgentState.RINGING
    res = await svc.accept(a.agent_id, "s1")
    assert res.token == "token-caller-1-Ana"
    assert svc.get_agent(a.agent_id).state is AgentState.ON_CALL
    assert svc.get_call("s1").status is CallStatus.WITH_AGENT
    svc.set_status(a.agent_id, AgentState.WRAP_UP)
    assert svc.get_agent(a.agent_id).state is AgentState.WRAP_UP
    assert svc.get_call("s1").status is CallStatus.ENDED
    svc.set_status(a.agent_id, AgentState.AVAILABLE)
    assert svc.get_agent(a.agent_id).state is AgentState.AVAILABLE


async def test_on_call_agent_cannot_go_available_directly(svc, clock):
    a = available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    await svc.accept(a.agent_id, "s1")
    with pytest.raises(QueueError):
        svc.set_status(a.agent_id, AgentState.AVAILABLE)


# ---- QH-2: one FIFO queue ------------------------------------------------


def test_calls_offered_in_arrival_order(svc, clock):
    for n in (1, 2, 3):
        enqueue(svc, n)
        clock.advance(1)
    a = available_agent(svc, clock, "Ana")
    assert svc.get_agent(a.agent_id).offer_call_id == "s1"
    assert [c.session_id for c in svc.waiting_calls()] == ["s1", "s2", "s3"]


def test_enqueue_same_session_twice_is_idempotent(svc):
    enqueue(svc, 1)
    enqueue(svc, 1, {"reason": "updated"})
    assert [c.session_id for c in svc.waiting_calls()] == ["s1"]
    assert svc.get_call("s1").summary == {"reason": "updated"}


def test_queued_event_carries_position(svc):
    enqueue(svc, 1)
    enqueue(svc, 2)
    queued = [e for e in svc.events if e["type"] == "call_queued"]
    assert [(e["call"]["session_id"], e["position"]) for e in queued] == [("s1", 1), ("s2", 2)]


def test_room_name_from_room_url(svc):
    enqueue(svc, 7)
    assert svc.get_call("s7").room_name == "caller-7"


def test_enqueue_rejects_bad_room_url(svc):
    with pytest.raises(QueueError):
        svc.enqueue_call("s1", "not a url", {})


# ---- QH-3: longest idle, 20 s, miss -> not available -----------------------


def test_offer_goes_to_longest_idle_agent(svc, clock):
    first = available_agent(svc, clock, "Ana", after=5)
    available_agent(svc, clock, "Ben", after=5)
    enqueue(svc, 1)
    assert svc.get_agent(first.agent_id).offer_call_id == "s1"


def test_idle_clock_resets_when_agent_becomes_available_again(svc, clock):
    ana = available_agent(svc, clock, "Ana", after=5)
    ben = available_agent(svc, clock, "Ben", after=5)
    svc.set_status(ana.agent_id, AgentState.OFFLINE)
    svc.set_status(ana.agent_id, AgentState.AVAILABLE)
    clock.advance(1)
    enqueue(svc, 1)
    assert svc.get_agent(ben.agent_id).offer_call_id == "s1"


def test_offer_event_has_expiry_20s_and_intake_summary(svc, clock):
    a = available_agent(svc, clock, "Ana")
    enqueue(svc, 1, {"reason": "claim denied"})
    offer = [e for e in svc.events if e["type"] == "offer"][-1]
    assert OFFER_TIMEOUT_SECS == 20
    assert offer["agent_id"] == a.agent_id
    assert offer["call"]["session_id"] == "s1"
    assert offer["call"]["summary"] == {"reason": "claim denied"}
    assert offer["expires_at"] == pytest.approx(clock.now() + 20)


def test_one_agent_rings_for_one_call_at_a_time(svc, clock):
    a = available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    enqueue(svc, 2)
    offers = [e for e in svc.events if e["type"] == "offer"]
    assert len(offers) == 1
    assert svc.get_agent(a.agent_id).offer_call_id == "s1"


def test_offer_not_accepted_in_20s_sets_agent_offline_and_rings_next(svc, clock):
    ana = available_agent(svc, clock, "Ana", after=5)
    ben = available_agent(svc, clock, "Ben", after=1)
    enqueue(svc, 1)
    clock.advance(19.9)
    assert svc.get_agent(ana.agent_id).state is AgentState.RINGING
    clock.advance(0.2)
    assert svc.get_agent(ana.agent_id).state is AgentState.OFFLINE
    assert svc.get_agent(ben.agent_id).offer_call_id == "s1"
    missed = [e for e in svc.events if e["type"] == "offer_missed"]
    assert missed == [
        {"type": "offer_missed", "agent_id": ana.agent_id, "session_id": "s1", "reason": "timeout"}
    ]


def test_missed_call_keeps_its_place_at_front(svc, clock):
    ana = available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    enqueue(svc, 2)
    clock.advance(21)
    assert svc.get_agent(ana.agent_id).state is AgentState.OFFLINE
    ben = available_agent(svc, clock, "Ben")
    assert svc.get_agent(ben.agent_id).offer_call_id == "s1"


def test_timeout_with_no_other_agent_leaves_call_waiting(svc, clock):
    available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    clock.advance(25)
    call = svc.get_call("s1")
    assert call.status is CallStatus.QUEUED
    assert call.offered_to is None


async def test_accept_after_timeout_is_rejected(svc, clock):
    a = available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    clock.advance(21)
    with pytest.raises(QueueError):
        await svc.accept(a.agent_id, "s1")


async def test_accept_cancels_the_offer_timer(svc, clock):
    a = available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    clock.advance(10)
    await svc.accept(a.agent_id, "s1")
    clock.advance(30)
    assert svc.get_agent(a.agent_id).state is AgentState.ON_CALL
    assert "offer_missed" not in types(svc.events)


# ---- decline --------------------------------------------------------------


def test_decline_sets_agent_offline_and_rings_next(svc, clock):
    ana = available_agent(svc, clock, "Ana", after=5)
    ben = available_agent(svc, clock, "Ben")
    enqueue(svc, 1)
    svc.decline(ana.agent_id, "s1")
    assert svc.get_agent(ana.agent_id).state is AgentState.OFFLINE
    assert svc.get_agent(ben.agent_id).offer_call_id == "s1"
    assert [e for e in svc.events if e["type"] == "offer_missed"][0]["reason"] == "declined"


def test_decline_wrong_call_is_rejected(svc, clock):
    a = available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    with pytest.raises(QueueError):
        svc.decline(a.agent_id, "s2")


def test_going_offline_while_ringing_counts_as_decline(svc, clock):
    ana = available_agent(svc, clock, "Ana", after=5)
    ben = available_agent(svc, clock, "Ben")
    enqueue(svc, 1)
    svc.set_status(ana.agent_id, AgentState.OFFLINE)
    assert svc.get_agent(ben.agent_id).offer_call_id == "s1"


def test_declined_timer_does_not_fire_later(svc, clock):
    ana = available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    svc.decline(ana.agent_id, "s1")
    clock.advance(30)
    assert len([e for e in svc.events if e["type"] == "offer_missed"]) == 1


# ---- accept (QH-6, QH-7 data side) ----------------------------------------


async def test_accept_mints_token_for_room_and_returns_summary(svc, clock, minter):
    a = available_agent(svc, clock, "Ana")
    enqueue(svc, 1, {"reason": "claim denied", "member_id": "EVM100231"})
    res = await svc.accept(a.agent_id, "s1")
    assert minter.calls == [("caller-1", "Ana")]
    assert res.session_id == "s1"
    assert res.room_url == room(1)
    assert res.expires_at == 9999
    assert res.summary == {"reason": "claim denied", "member_id": "EVM100231"}
    accepted = [e for e in svc.events if e["type"] == "call_accepted"]
    assert accepted[0]["agent_id"] == a.agent_id
    assert accepted[0]["call"]["room_name"] == "caller-1"
    assert "token" not in str(accepted)  # tokens never broadcast


async def test_accept_by_wrong_agent_is_rejected(svc, clock):
    available_agent(svc, clock, "Ana")
    ben = svc.sign_in("Ben")
    enqueue(svc, 1)
    with pytest.raises(QueueError):
        await svc.accept(ben.agent_id, "s1")


async def test_accept_removes_call_from_waiting(svc, clock):
    a = available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    await svc.accept(a.agent_id, "s1")
    assert svc.waiting_calls() == []
    call = svc.get_call("s1")
    assert call.agent_id == a.agent_id
    assert call.answered_at == clock.now()


async def test_token_failure_puts_call_back_and_agent_available(clock):
    s = QueueService(clock=clock, mint_token=FakeMinter(fail=True))
    a = available_agent(s, clock, "Ana")
    enqueue(s, 1)
    with pytest.raises(QueueError):
        await s.accept(a.agent_id, "s1")
    call = s.get_call("s1")
    assert call.status is CallStatus.QUEUED
    # Agent is not at fault: back to available, and the call is re-offered.
    assert s.get_agent(a.agent_id).state is AgentState.RINGING
    assert s.get_agent(a.agent_id).offer_call_id == "s1"


# ---- caller leaves ----------------------------------------------------------


def test_caller_hangs_up_while_waiting_abandons_call(svc):
    enqueue(svc, 1)
    svc.end_call("s1")
    assert svc.get_call("s1").status is CallStatus.ABANDONED
    assert svc.waiting_calls() == []


def test_caller_hangs_up_while_ringing_frees_agent(svc, clock):
    a = available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    svc.end_call("s1")
    agent = svc.get_agent(a.agent_id)
    assert agent.state is AgentState.AVAILABLE
    assert agent.offer_call_id is None
    clock.advance(30)
    assert svc.get_agent(a.agent_id).state is AgentState.AVAILABLE


async def test_caller_hangs_up_on_call_moves_agent_to_wrap_up(svc, clock):
    a = available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    await svc.accept(a.agent_id, "s1")
    svc.end_call("s1")
    assert svc.get_call("s1").status is CallStatus.ENDED
    assert svc.get_agent(a.agent_id).state is AgentState.WRAP_UP


def test_end_unknown_call_is_noop(svc):
    svc.end_call("nope")


# ---- snapshot and events ---------------------------------------------------


def test_snapshot_lists_queue_and_agents(svc, clock):
    available_agent(svc, clock, "Ana")
    enqueue(svc, 1)
    enqueue(svc, 2)
    snap = svc.snapshot()
    assert [c["session_id"] for c in snap["queue"]] == ["s1", "s2"]
    assert snap["queue"][0]["offered_to"] is not None
    assert [(a["name"], a["state"]) for a in snap["agents"]] == [("Ana", "ringing")]


def test_every_agent_change_emits_agent_updated(svc):
    a = svc.sign_in("Ana")
    svc.set_status(a.agent_id, AgentState.AVAILABLE)
    updates = [e["agent"]["state"] for e in svc.events if e["type"] == "agent_updated"]
    assert updates == ["offline", "available"]


def test_listener_errors_do_not_break_the_service(svc):
    def boom(_event):
        raise RuntimeError("listener bug")

    svc.subscribe(boom)
    enqueue(svc, 1)
    assert svc.get_call("s1").status is CallStatus.QUEUED


def test_unsubscribe(svc):
    seen = []
    svc.subscribe(seen.append)
    svc.unsubscribe(seen.append)
    enqueue(svc, 1)
    assert seen == []


# ---- QH-10: 5 calls across 3 agents ---------------------------------------


async def test_five_calls_three_agents_all_served_in_order(svc, clock):
    agents = [available_agent(svc, clock, n) for n in ("Ana", "Ben", "Cy")]
    for n in range(1, 6):
        enqueue(svc, n)
        clock.advance(1)

    served: list[tuple[str, str]] = []
    on_call: set[str] = set()
    for _round in range(10):
        ringing = [
            svc.get_agent(a.agent_id)
            for a in agents
            if svc.get_agent(a.agent_id).state is AgentState.RINGING
        ]
        if not ringing and not svc.waiting_calls():
            break
        # Each ringing agent accepts, talks, wraps up, and goes available again.
        for agent in ringing:
            call_id = agent.offer_call_id
            res = await svc.accept(agent.agent_id, call_id)
            on_call.add(agent.agent_id)
            # No agent ever holds two calls.
            assert sum(c.agent_id == agent.agent_id for c in svc.active_calls()) == 1
            served.append((res.session_id, agent.name))
        clock.advance(30)
        for agent in ringing:
            svc.set_status(agent.agent_id, AgentState.WRAP_UP)
            svc.set_status(agent.agent_id, AgentState.AVAILABLE)
            clock.advance(1)

    assert [s for s, _ in served] == ["s1", "s2", "s3", "s4", "s5"]
    assert {n for _, n in served[:3]} == {"Ana", "Ben", "Cy"}
    assert all(svc.get_call(f"s{n}").status is CallStatus.ENDED for n in range(1, 6))
    assert on_call == {a.agent_id for a in agents}


async def test_five_calls_three_agents_with_a_miss(svc, clock):
    ana = available_agent(svc, clock, "Ana", after=3)
    ben = available_agent(svc, clock, "Ben", after=2)
    cy = available_agent(svc, clock, "Cy", after=1)
    for n in range(1, 6):
        enqueue(svc, n)
    # Ana and Ben accept; Cy lets s3 ring out and goes offline.
    await svc.accept(ana.agent_id, "s1")
    await svc.accept(ben.agent_id, "s2")
    clock.advance(21)
    assert svc.get_agent(cy.agent_id).state is AgentState.OFFLINE
    assert [c.session_id for c in svc.waiting_calls()] == ["s3", "s4", "s5"]
    # Ana finishes first and is offered s3 (front of queue), not s4.
    svc.set_status(ana.agent_id, AgentState.WRAP_UP)
    svc.set_status(ana.agent_id, AgentState.AVAILABLE)
    assert svc.get_agent(ana.agent_id).offer_call_id == "s3"
