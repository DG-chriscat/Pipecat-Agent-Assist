from harness.intake_script import MARIA_ALVAREZ, next_line


def test_next_line_starts_with_identity():
    assert next_line(None) == "identity"
    assert next_line({}) == "identity"


def test_next_line_follows_the_first_missing_field():
    assert next_line({"full_name": "Maria Alvarez"}) == "reason"
    saved = {"full_name": "x", "reason": "y", "claim_number": "CLM-900001"}
    assert next_line(saved) == "service_date"


def test_next_line_reads_back_once_every_field_is_saved():
    saved = dict.fromkeys(
        ["full_name", "reason", "claim_number", "service_date", "claim_concern"], "x"
    )
    assert next_line(saved) == "readback"


def test_script_has_a_line_for_every_step():
    steps = {next_line(None), "reason", "claim_number", "service_date", "claim_concern"}
    assert steps | {"readback"} <= set(MARIA_ALVAREZ)
