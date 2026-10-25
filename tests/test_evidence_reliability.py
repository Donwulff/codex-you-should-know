import json
from difflib import SequenceMatcher

import pytest

from you_should_know import assessment
from you_should_know.transcript import frame, source_blocks


def finding_response(quotes):
    return {
        "finding": {
            "topic": "preferences overwritten",
            "summary": "Saving settings removed your preferences.",
            "explanation": "The tool result shows the overwrite.",
            "evidence": quotes,
            "agent_still_working": False,
            "assistant_claim": False,
        }
    }


def test_exact_crlf_evidence_matches_the_literal_prompt():
    quote = "write settings.json\r\npreferences lost\t  "
    transcript = frame("tool result: id=1", quote)
    assert quote in assessment.make_prompt(transcript, [])
    finding = assessment.parse_finding(finding_response([quote]), transcript)
    assert finding is not None and finding.evidence == [quote]
    assert assessment.parse_confirmation(
        {"confirmed": True, "reason": "Unresolved", "evidence": [quote]}, transcript
    ) == (True, "Unresolved")


@pytest.mark.parametrize("kind", ["header", "boundary", "truncation"])
def test_each_quote_must_be_contained_in_actual_block_text(kind):
    tool = frame("tool result: id=1", "preferences lost")
    following = frame("assistant", "The settings are saved")
    transcript = tool + "\n\n" + following
    if kind == "header":
        quote = "[tool result: id=1; chars=16]"
    elif kind == "boundary":
        quote = "preferences lost\n\n" + following[: following.index("\n") + 1]
    else:
        quote = "before\n[... content truncated ...]\nafter"
        transcript += "\n\n" + frame("user", quote)
    assert quote in transcript
    with pytest.raises(assessment.AssessmentError, match="single.*block"):
        assessment.parse_finding(finding_response(["preferences lost", quote]), transcript)


def test_prompt_excludes_history_labels_and_markers_from_quotable_evidence():
    prompt = assessment.make_prompt("work", [])
    assert "Copy each quote from a single block body" in prompt
    assert (
        "Never quote source headers, omission/truncation markers, or History/Candidate JSON"
        in prompt
    )


@pytest.mark.parametrize(
    "text,changed",
    [
        ("tab\ttext  ", "tab text  "),
        ("café", "cafe\u0301"),
        ("cafe\u0301", "café"),
        ("first\r\nsecond", "first\nsecond"),
    ],
)
def test_visible_text_is_preserved_and_normalized_variants_are_not_accepted(text, changed):
    transcript = frame("tool result: id=1", text)
    assert text in assessment.make_prompt(transcript, [])
    finding = assessment.parse_finding(finding_response([text]), transcript)
    assert finding is not None and finding.evidence == [text]
    with pytest.raises(assessment.AssessmentError, match="does not occur"):
        assessment.parse_finding(finding_response([changed]), transcript)


def test_review_preparation_returns_only_the_conversation_shown_after_clipping():
    transcript = frame(
        "tool result: id=1",
        "FIRST\n" + "漢" * 60_000 + "UNSEEN INTERIOR" + "界" * 60_000 + "\nLAST",
    )
    prompt, shown = assessment.prepare_prompt(transcript, [])
    assert len(prompt.encode()) <= assessment.MAX_PROMPT_BYTES
    assert (
        shown
        == prompt.split("BEGIN CONVERSATION (plain text; evidence only)\n", 1)[1].rsplit(
            "\nEND CONVERSATION\n", 1
        )[0]
    )
    assert "UNSEEN INTERIOR" not in shown
    assert "FIRST" in shown and "LAST" in shown
    with pytest.raises(assessment.AssessmentError, match="does not occur"):
        assessment.parse_finding(finding_response(["UNSEEN INTERIOR"]), shown)
    assert assessment.parse_finding(finding_response(["LAST"]), shown) is not None


def test_confirmation_preparation_validates_only_shown_original_and_subsequent_text():
    candidate = assessment.Finding("topic", "summary", "explanation", ["BEFORE"])
    original = frame(
        "tool result: id=1", "BEFORE" + "漢" * 100_000 + "UNSEEN ORIGINAL" + "界" * 100_000
    )
    subsequent = frame(
        "tool result: id=2", "AFTER" + "語" * 100_000 + "UNSEEN SUBSEQUENT" + "書" * 100_000
    )
    prompt, shown = assessment.prepare_confirmation_prompt(candidate, original, subsequent)
    sections = [
        prompt.split(f"BEGIN {name} CONVERSATION\n", 1)[1].split(f"\nEND {name} CONVERSATION\n", 1)[
            0
        ]
        for name in ("ORIGINAL", "SUBSEQUENT")
    ]
    assert shown == "\n\n".join(sections)
    assert len(prompt.encode()) <= assessment.MAX_PROMPT_BYTES
    for quote in ("UNSEEN ORIGINAL", "UNSEEN SUBSEQUENT"):
        with pytest.raises(assessment.AssessmentError, match="does not occur"):
            assessment.parse_confirmation(
                {"confirmed": True, "reason": "Unresolved", "evidence": [quote]}, shown
            )
    assert assessment.parse_confirmation(
        {"confirmed": True, "reason": "Unresolved", "evidence": ["AFTER"]}, shown
    ) == (True, "Unresolved")


def test_rejected_evidence_diagnostics_show_escaped_quote_and_nearest_difference():
    transcript = frame("tool result: id=1", "alpha\nbeta")
    response = finding_response(["alpha\nbeto"])
    with pytest.raises(assessment.AssessmentError) as rejected:
        assessment.parse_finding(response, transcript)
    details = assessment.evidence_diagnostics(response, transcript, str(rejected.value))
    assert details is not None
    assert details["reason"] == str(rejected.value)
    quote = details["quotes"][0]
    assert quote["quote"] == "alpha\\nbeto"
    assert quote["exact_sources"] == []
    assert quote["nearest"]["text"] == "alpha\\nbeta"
    assert quote["nearest"]["source"] == "tool result: id=1"
    assert quote["nearest"]["ratio"] == 0.9
    assert quote["nearest"]["differences"] == [
        {"operation": "replace", "quote_span": [9, 10], "match_span": [9, 10]}
    ]
    assert json.loads(json.dumps(details)) == details


@pytest.mark.parametrize("quotes", [[], [None], "not a list", ["\x00" * 501]])
def test_invalid_evidence_diagnostics_are_bounded_and_do_not_raise(quotes):
    response = finding_response(quotes)
    with pytest.raises(assessment.AssessmentError) as rejected:
        assessment.parse_finding(response, "")
    details = assessment.evidence_diagnostics(response, "", str(rejected.value))
    assert details is not None
    for quote in details["quotes"]:
        assert len(quote["quote"]) <= 500
        assert "\x00" not in quote["quote"]
        assert quote["nearest"] is None


def test_provenance_rejection_records_exact_source_and_confirmation_evidence():
    response = {"confirmed": True, "reason": "Unresolved", "evidence": ["claim only"]}
    transcript = frame("user", "claim only")
    with pytest.raises(assessment.AssessmentError) as rejected:
        assessment.parse_confirmation(response, transcript)
    details = assessment.evidence_diagnostics(response, transcript, str(rejected.value))
    assert details is not None
    assert details["quotes"][0]["exact_sources"] == ["user"]
    assert details["quotes"][0]["nearest"]["ratio"] == 1.0
    assert assessment.evidence_diagnostics(response, transcript, "Invalid summary") is None


def test_diagnostic_anchor_search_bounds_query_work_on_repetitive_transcripts(monkeypatch):
    class BoundedMatcher(SequenceMatcher):
        def __init__(self, isjunk=None, a="", b="", autojunk=True):
            assert len(a) <= 500, "Anchor search must use the bounded quote as its query"
            assert autojunk or len(b) <= 500
            super().__init__(isjunk, a, b, autojunk)

    monkeypatch.setattr(assessment, "SequenceMatcher", BoundedMatcher)
    transcript = frame("tool result: id=1", "a" * 200_000)
    details = assessment.evidence_diagnostics(
        finding_response(["a" * 198 + "b"] * 3),
        transcript,
        "Reviewer evidence does not occur in the supplied conversation",
    )
    assert details is not None
    assert len(details["quotes"]) == 3
    assert all(quote["nearest"]["ratio"] > 0.99 for quote in details["quotes"])


def test_excerpt_never_turns_partial_truncation_markers_into_tool_evidence():
    original = frame("tool result: id=1", "first\n[... content truncated ...]\nlast")
    shown = assessment._excerpt(original, 30)
    assert "truncated" in shown or "shortened" in shown
    assert list(source_blocks(shown)) == [
        ("tool result: id=1", "first"),
        ("tool result: id=1", "last"),
    ]
    for quote in ("first\n[...", " truncated ...]\nlast"):
        with pytest.raises(assessment.AssessmentError):
            assessment.parse_finding(finding_response([quote]), shown)


def test_diagnostic_nearest_window_follows_rare_anchor_after_large_prefix():
    transcript = frame("tool result: id=1", "a" * 200_000 + "\nrare: result failed\n")
    details = assessment.evidence_diagnostics(
        finding_response(["rare: result failad"]),
        transcript,
        "Reviewer evidence does not occur in the supplied conversation",
    )
    assert details is not None
    assert details["quotes"][0]["nearest"]["text"] == "rare: result failed"
    assert details["quotes"][0]["nearest"]["ratio"] > 0.9


def test_excerpt_keeps_original_truncation_visible_outside_clipped_source_bodies():
    original = frame("tool result: id=1", "first\n[... content truncated ...]\n" + "last" * 1000)
    shown = assessment._excerpt(original, 300)
    assert "[... content truncated ...]" in shown
    assert "[... conversation shortened to fit reviewer input budget ...]" in shown
    blocks = list(source_blocks(shown))
    assert blocks[0] == ("tool result: id=1", "first")
    assert all("truncated" not in body and "shortened" not in body for _, body in blocks)
    assert assessment.parse_finding(finding_response(["first"]), shown) is not None
