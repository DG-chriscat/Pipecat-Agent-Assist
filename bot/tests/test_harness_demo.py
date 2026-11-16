"""P5-4: the demo run summary scores each run against the PRD success measures."""

from harness.demo import TARGETS, run_checks, scorecard, table_row

GOOD = {
    "run": 1,
    "persona": "Maria Alvarez",
    "pack": "claims_status",
    "identity_expected": ["verified"],
    "identity_result": "verified",
    "intake_secs": 84.2,
    "accept_to_caller_audio_secs": 1.41,
    "trigger_word_to_card_secs": 0.92,
    "card": "nbs:retro_auth_review:CLM-900001",
    "wrap_up_saved": True,
    "full_loop": True,
}


def test_a_good_run_passes_every_check():
    assert run_checks(GOOD) == {
        "full_loop": True,
        "intake": True,
        "accept": True,
        "card": True,
        "identity": True,
    }


def test_each_target_is_strict_less_than():
    r = dict(
        GOOD,
        intake_secs=TARGETS["intake_secs"],
        accept_to_caller_audio_secs=TARGETS["accept_secs"],
        trigger_word_to_card_secs=TARGETS["card_secs"],
    )
    c = run_checks(r)
    assert (c["intake"], c["accept"], c["card"]) == (False, False, False)


def test_missing_timing_fails_that_check_only():
    r = dict(GOOD, trigger_word_to_card_secs=None, full_loop=False)
    c = run_checks(r)
    assert c["card"] is False and c["full_loop"] is False
    assert c["intake"] and c["accept"] and c["identity"]


def test_identity_counts_any_expected_result():
    r = dict(
        GOOD, identity_expected=["close_match", "not_verified"], identity_result="not_verified"
    )
    assert run_checks(r)["identity"] is True
    assert run_checks(dict(r, identity_result="verified"))["identity"] is False


def test_table_row_marks_pass_and_fail_and_leaves_useful_blank():
    row = table_row(dict(GOOD, accept_to_caller_audio_secs=2.3))
    cells = [c.strip() for c in row.strip("|").split("|")]
    assert cells[0] == "1"
    assert "1.41" not in row and "2.30 fail" in row
    assert "0.92 pass" in row
    assert cells[-1] == ""  # cards rated useful: filled in by Chris's demo agents


def test_scorecard_against_the_prd_targets():
    runs = [dict(GOOD, run=i) for i in range(1, 10)]
    runs.append(dict(GOOD, run=10, full_loop=False, identity_result="not_verified"))
    card = scorecard(runs)
    assert card["full_loop"] == (9, 10, True)  # 9 of 10 needed
    assert card["identity"] == (9, 10, False)  # 10 of 10 needed
    assert card["intake_median_secs"] == 84.2
    assert card["accept_median_secs"] == 1.41
    assert card["card_max_secs"] == 0.92
