"""Floor manager tests: modes, arbitration, limits, determinism. No model."""

from __future__ import annotations

import pytest
from home_jarvis.brain import ScriptedBrain, parse_duet, parse_review
from home_jarvis.config import ConfigError, load
from home_jarvis.events import EventLog
from home_jarvis.floor import FloorManager, split_sentences


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


REPLY = "First point. Second point. Third point. Fourth point."


def make(brain, profile="home_text", **glob):
    clock = Clock()
    cfg = load({"global": {"profile": profile, **glob}})
    return FloorManager(brain, cfg, EventLog(clock=clock), clock=clock), clock


def kinds(events, skip_state=True):
    return [e.kind for e in events if not (skip_state and e.kind == "state")]


def by_kind(events, kind):
    return [e for e in events if e.kind == kind]


def test_split_sentences():
    assert split_sentences("One. Two? Three! four") == ["One.", "Two?", "Three! four"]
    assert split_sentences("") == []


def test_interrupt_cuts_at_sentence_boundary_and_speaker_yields():
    brain = ScriptedBrain(
        replies={"finch": [REPLY]},
        reviews=[
            {
                "act": "interrupt",
                "at": 1,
                "pressure": 0.9,
                "source": "cost",
                "text": "Stop there, that costs too much.",
            }
        ],
    )
    floor, _ = make(brain)
    ev = floor.run_turn("Should I compare these two options?")
    says = [e.text for e in by_kind(ev, "say")]
    assert says == ["First point.", "Second point."]
    inter = by_kind(ev, "interject")[0]
    assert inter.speaker == "root" and inter.pressure == 0.9
    assert inter.data["dropped"] == ["Third point.", "Fourth point."]
    assert by_kind(ev, "yield")[0].speaker == "finch"
    assert kinds(ev)[-1] == "turn"


def test_low_pressure_interrupt_degrades_to_comment():
    brain = ScriptedBrain(
        replies={"finch": [REPLY]},
        reviews=[{"act": "interrupt", "at": 0, "pressure": 0.3, "text": "Hmm, maybe."}],
    )
    floor, _ = make(brain)
    ev = floor.run_turn("Should I compare?")
    assert not by_kind(ev, "interject")
    assert len(by_kind(ev, "say")) == 4
    c = by_kind(ev, "comment")[0]
    assert "degraded from interject" in c.data["reason"]


def test_low_pressure_interrupt_degrades_to_hand_when_enabled():
    brain = ScriptedBrain(
        replies={"finch": [REPLY]},
        reviews=[{"act": "interrupt", "at": 0, "pressure": 0.3, "text": "Can I add?"}],
    )
    floor, _ = make(brain, profile="focus")
    ev = floor.run_turn("Should I compare?")
    assert by_kind(ev, "hand")[0].text == "Can I add?"
    granted = floor.grant()
    assert any(e.kind == "say" and e.data.get("granted") for e in granted)


def test_cooldown_blocks_second_interjection_unless_named():
    review = {
        "act": "interrupt",
        "at": 0,
        "pressure": 0.9,
        "source": "x",
        "text": "Cut.",
    }
    brain = ScriptedBrain(
        replies={"finch": [REPLY, REPLY, REPLY]}, reviews=[review, review, review]
    )
    floor, clock = make(brain)
    assert by_kind(floor.run_turn("Should I plan?"), "interject")
    clock.t += 10
    second = floor.run_turn("Should I plan again?")
    assert not by_kind(second, "interject")
    assert by_kind(second, "comment")  # degraded, still visible
    clock.t += 200
    third = floor.run_turn("Should I plan once more?")
    # hysteresis: same source needs 0.80 now; 0.9 still passes
    assert by_kind(third, "interject")


def test_named_persona_wins_bid():
    brain = ScriptedBrain(replies={"root": ["On it."]})
    floor, _ = make(brain)
    ev = floor.run_turn("Root, what do you think about the research plan?")
    bid = by_kind(ev, "bid")[0]
    assert bid.speaker == "root" and bid.data["named"] == "root"
    assert by_kind(ev, "say")[0].speaker == "root"


def test_build_back_and_forth_until_pass():
    brain = ScriptedBrain(
        replies={
            "finch": ["Use the table first.", "Then the text follows."],
            "root": [],
        },
        reviews=[{"act": "build", "pressure": 0.5, "text": "And block two mornings."}],
    )
    floor, _ = make(brain, max_exchanges=3)
    ev = floor.run_turn("How should I plan the paper?")
    builds = [(e.speaker, e.text) for e in by_kind(ev, "build")]
    assert builds == [
        ("root", "And block two mornings."),
        ("finch", "Then the text follows."),
    ]
    # root's next build-back returned PASS, so the exchange stopped


def test_addendum_and_correct_are_post_turn():
    brain = ScriptedBrain(
        replies={"finch": ["Due on the 30th.", "All set."]},
        reviews=[
            {
                "act": "correct",
                "pressure": 0.5,
                "source": "memory",
                "text": "It moved to December.",
            },
            {"act": "add", "pressure": 0.4, "text": "Notes are in projects/."},
        ],
    )
    floor, clock = make(brain)
    ev = floor.run_turn("When is the deadline, should I plan?")
    c = by_kind(ev, "correct")[0]
    assert c.speaker == "root" and c.pressure >= 0.75
    assert kinds(ev).index("correct") > kinds(ev).index("say")
    clock.t += 1
    ev2 = floor.run_turn("Should I plan the notes?")
    assert by_kind(ev2, "add")[0].text == "Notes are in projects/."


def test_mute_turns_spoken_acts_into_nothing_spoken():
    brain = ScriptedBrain(
        replies={"finch": [REPLY]},
        reviews=[{"act": "build", "pressure": 0.9, "text": "Me too."}],
    )
    floor, _ = make(brain)
    floor.mute("root", 30)
    ev = floor.run_turn("Should I plan?")
    assert not by_kind(ev, "build")
    assert ("review", "root") not in brain.calls  # muted listener is not even asked


def test_hard_cut_on_safety_word_stops_before_any_sentence():
    brain = ScriptedBrain(replies={"finch": [REPLY]})
    floor, _ = make(brain, profile="home_voice")
    ev = floor.run_turn("Should I plan dinner? The stove is on and I smell smoke")
    assert by_kind(ev, "cut")[0].source == "safety"
    assert not by_kind(ev, "say")


def test_cost_signal_makes_root_raise_hand_without_review_line():
    brain = ScriptedBrain(
        replies={"finch": ["The camera is 900 euros, a fair price."]},
        reviews=[{"act": "none"}],
    )
    floor, _ = make(brain, profile="focus")
    ev = floor.run_turn("Should I compare the camera options?")
    hand = by_kind(ev, "hand")[0]
    assert hand.speaker == "root" and hand.source == "cost"


def test_one_per_turn_and_daily_cap():
    review = {"act": "add", "pressure": 0.9, "text": "Extra."}
    brain = ScriptedBrain(replies={"finch": [REPLY] * 3}, reviews=[review] * 3)
    floor, clock = make(brain, daily_cap=1)
    assert by_kind(floor.run_turn("Should I plan?"), "add")
    clock.t += 1
    ev = floor.run_turn("Should I plan more?")
    assert not by_kind(ev, "add") and by_kind(ev, "comment")


def test_duet_writes_both_voices_and_switches():
    brain = ScriptedBrain(
        duets=[
            [
                ("finch", "Yes, but not tonight."),
                ("root", "Tonight is when bad buys happen."),
                ("finch", "Saturday, then."),
            ]
        ]
    )
    floor, _ = make(brain, profile="duet")
    ev = floor.run_turn("Should I buy it?")
    assert [e.speaker for e in by_kind(ev, "interject")] == ["root", "finch"]


def test_config_rejects_duet_with_interrupt_and_accepts_04_aliases():
    with pytest.raises(ConfigError):
        load(
            {
                "profiles": {"bad": {"enabled": ["s1_duet", "s3_sentence_barge_in"]}},
                "global": {"profile": "bad"},
            }
        )
    cfg = load(
        {
            "profiles": {"night": {"enabled": ["s6_aside", "s4_safety"]}},
            "global": {"profile": "night"},
        }
    )
    assert cfg.enabled == ("comment", "hard_cut")
    with pytest.raises(ConfigError):
        load({"global": {"profile": "nope"}})


def test_live_switch_emits_config_event():
    floor, _ = make(ScriptedBrain())
    floor.set_config(floor.config.with_modes(disable=["interrupt"]), who="hud", how="off")
    ev = floor.log.recent(1)[0]
    assert ev.kind == "config" and "interrupt" not in ev.data["enabled"]


def test_replay_is_deterministic():
    def run():
        brain = ScriptedBrain(
            replies={"finch": [REPLY, REPLY]},
            reviews=[
                {"act": "interrupt", "at": 2, "pressure": 0.8, "text": "Wait."},
                {"act": "build", "pressure": 0.5, "text": "Also this."},
            ],
        )
        floor, clock = make(brain)
        out = floor.run_turn("Should I plan A?")
        clock.t += 5
        out += floor.run_turn("Should I plan B?")
        return [(e.kind, e.speaker, e.text, e.data.get("state")) for e in out]

    assert run() == run()


def test_parsers_tolerate_noise():
    r = parse_review('Sure!\n```json\n{"act": "INTERRUPT", "pressure": 3, "at": "2"}\n```')
    assert r["act"] == "interrupt" and r["pressure"] == 1.0 and r["at"] == 2
    assert parse_review("no json")["act"] == "none"
    assert parse_duet("[FINCH] a b.\n[ROOT] c.\n[FINCH] d") == [
        ("finch", "a b."),
        ("root", "c."),
        ("finch", "d"),
    ]
