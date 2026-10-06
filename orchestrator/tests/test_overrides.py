import pytest

from sdlc.agents.overrides import (
    Command,
    Ruling,
    describe,
    effective_tier,
    floor_of,
    parse_commands,
    rule,
)
from sdlc.agents.signoff import COMMENT_MARKER
from sdlc.tables import PullRequest
from sdlc.tiers import load_policy

SHA = "a" * 40
NEW_SHA = "b" * 40
PERSON = {"login": "pat", "type": "User"}
BOT = {"login": "pragmattie-orchestrator[bot]", "type": "Bot"}
REASON = "Only a copy change in a tooltip."


@pytest.fixture(scope="module")
def policy():
    return load_policy()


def _command(tier, reason=REASON, comment_id=1):
    return Command(comment_id=comment_id, actor="pat", tier=tier, reason=reason, url=None)


def _comment(body, comment_id=1, user=PERSON):
    return {
        "id": comment_id,
        "body": body,
        "user": user,
        "html_url": f"https://x.example/{comment_id}",
    }


def _ruling(tier, *, current, floor="T0", head_sha=SHA, reason=REASON, comment_id=1):
    return rule(_command(tier, reason, comment_id), current=current, floor=floor, head_sha=head_sha)


def test_a_higher_tier_is_a_raise_and_accepted():
    ruling = _ruling("T3", current="T1", reason="")
    assert (ruling.direction, ruling.accepted) == ("raise", True)
    assert ruling.why == "Raised from T1 to T3."
    assert (ruling.from_tier, ruling.to_tier, ruling.head_sha) == ("T1", "T3", SHA)


def test_the_same_tier_changes_nothing():
    ruling = _ruling("T2", current="T2", reason="")
    assert (ruling.direction, ruling.accepted) == ("same", True)
    assert ruling.why == "Already T2; nothing changed."


def test_a_lowering_below_the_floor_is_rejected_even_with_a_long_reason():
    ruling = _ruling("T1", current="T2", floor="T2", reason="A very long and careful reason. " * 3)
    assert (ruling.direction, ruling.accepted) == ("lower", False)
    assert ruling.why == (
        "Rejected: a policy floor keeps this PR at T2 or above. "
        "Floors are set in policies/tiers.yaml, not by comment."
    )


def test_a_lowering_without_a_written_reason_is_rejected():
    ruling = _ruling("T1", current="T2", reason="trust me")
    assert (ruling.direction, ruling.accepted) == ("lower", False)
    assert ruling.why == "Rejected: lowering a tier needs a written reason, e.g. `/tier T1 <why>`."


def test_a_lowering_with_a_reason_is_accepted_for_this_commit():
    ruling = _ruling("T0", current="T2")
    assert (ruling.direction, ruling.accepted) == ("lower", True)
    assert ruling.why == "Lowered from T2 to T0, for this commit only."


def test_a_raise_sticks_on_a_later_commit():
    raised = _ruling("T3", current="T1")
    assert effective_tier("T1", "T0", [raised], SHA) == "T3"
    assert effective_tier("T1", "T0", [raised], NEW_SHA) == "T3"


def test_a_raise_only_counts_when_it_raises():
    raised = _ruling("T2", current="T1")
    assert effective_tier("T3", "T0", [raised], NEW_SHA) == "T3"


def test_a_lowering_doesnt_carry_to_a_later_commit():
    lowered = _ruling("T0", current="T2")
    assert effective_tier("T2", "T0", [lowered], SHA) == "T0"
    assert effective_tier("T2", "T0", [lowered], NEW_SHA) == "T2"


def test_a_later_raise_above_an_earlier_lowering_wins():
    lowered = _ruling("T0", current="T2", comment_id=1)
    raised = _ruling("T3", current="T0", comment_id=2)
    assert effective_tier("T2", "T0", [lowered, raised], SHA) == "T3"


def test_rejected_rulings_change_nothing():
    rejected = _ruling("T0", current="T2", reason="no")
    assert effective_tier("T2", "T0", [rejected], SHA) == "T2"


def test_the_floor_always_holds():
    lowered = _ruling("T0", current="T1")  # ruled before the floor applied
    assert effective_tier("T0", "T2", [], SHA) == "T2"
    assert effective_tier("T1", "T2", [lowered], SHA) == "T2"


def test_an_agents_own_comment_mentioning_tier_is_ignored():
    comments = [
        _comment(f"{COMMENT_MARKER}\n/tier T3 raise with this", 1, BOT),
        _comment(f"{COMMENT_MARKER}\n/tier T0 copied by a person's account", 2, PERSON),
        _comment("/tier T0 from a bot without the marker", 3, BOT),
    ]
    assert parse_commands(comments, [COMMENT_MARKER]) == []


def test_any_case_and_a_dash_parse():
    [command] = parse_commands([_comment("/TIER t2 - only docs move")], [COMMENT_MARKER])
    assert command == Command(
        comment_id=1, actor="pat", tier="T2", reason="only docs move", url="https://x.example/1"
    )


def test_text_before_the_command_on_another_line_is_fine():
    body = "Looked at this again.\n  /tier T3: it touches the forecast maths"
    [command] = parse_commands([_comment(body)], [COMMENT_MARKER])
    assert (command.tier, command.reason) == ("T3", "it touches the forecast maths")


def test_commands_come_oldest_first_and_other_comments_are_skipped():
    comments = [
        _comment("/tier T3", 1),
        _comment("Looks fine to me; no /tier here.", 2),
        _comment("/tier T4 isn't a tier", 3),
        _comment("/tier T1 — a long enough reason", 4, {"login": None, "type": "User"}),
    ]
    commands = parse_commands(comments, [COMMENT_MARKER])
    assert [(c.comment_id, c.tier, c.actor) for c in commands] == [
        (1, "T3", "pat"),
        (4, "T1", "unknown"),
    ]
    assert commands[1].reason == "a long enough reason"


def test_a_long_reason_is_cut_to_500_characters():
    [command] = parse_commands([_comment("/tier T1 " + "x" * 600)], [])
    assert len(command.reason) == 500


def test_a_ruling_round_trips_through_its_record():
    ruling = _ruling("T0", current="T2")
    assert Ruling.from_record(ruling.as_record()) == ruling


def test_describe():
    assert describe(_ruling("T0", current="T2")) == (
        f'- `/tier T0` by @pat "{REASON}": Lowered from T2 to T0, for this commit only.'
    )
    assert describe(_ruling("T3", current="T1", reason="")) == (
        "- `/tier T3` by @pat: Raised from T1 to T3."
    )


@pytest.mark.parametrize(
    ("fields", "floor"),
    [
        ({"module": "leads"}, "T0"),
        ({"module": "pipeline"}, "T2"),
        ({"module": "pipeline", "touches_migration": True}, "T3"),
        ({"module": None, "touches_governance": True}, "T2"),
    ],
)
def test_floor_of(policy, fields, floor):
    pr = PullRequest(
        **{"touches_migration": False, "docs_only": False, "touches_governance": False, **fields}
    )
    assert floor_of(policy, pr) == floor
