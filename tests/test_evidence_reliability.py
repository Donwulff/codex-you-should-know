import json
from difflib import SequenceMatcher

import pytest

from you_should_know import assessment
from you_should_know.transcript import frame, read_snapshot, source_blocks


def finding_response(quotes):
    return {
        "no_finding": None,
        "finding": {
            "topic": "preferences overwritten",
            "summary": "Saving settings removed your preferences.",
            "explanation": "The tool result shows the overwrite.",
            "evidence": quotes,
            "agent_still_working": False,
            "assistant_claim": False,
        },
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


@pytest.mark.parametrize("confirmation", [False, True])
def test_prompts_preserve_literal_escapes_without_decoding_them(confirmation):
    quote = r'{"output":"first\nsecond\tC:\\tmp"}'
    transcript = frame("tool result: id=1", quote)
    candidate = assessment.Finding("topic", "summary", "explanation", [quote])
    prompt = (
        assessment.make_confirmation_prompt(candidate, transcript, "")
        if confirmation
        else assessment.make_prompt(transcript, [])
    )
    assert "Preserve literal backslashes already present in a block" in prompt
    assert "do not turn literal \\n or \\t into newline or tab characters" in prompt
    assert "After JSON decoding, each evidence string must equal the copied source text" in prompt
    # Check the actual JSON examples shown to the model, including Python string escaping.
    example_lines = prompt.split("Formatting examples only;", 1)[1].splitlines()
    examples = []
    for line, following in zip(example_lines, example_lines[1:], strict=False):
        if line.startswith("- Source text: "):
            source = line.removeprefix("- Source text: ")
            encoded = following.strip().removeprefix("JSON evidence string: ")
            examples.append((source, json.loads(encoded)))
    assert examples == [
        (r"first\nsecond", r"first\nsecond"),
        (r"12\t0\tGUIDE.md", r"12\t0\tGUIDE.md"),
    ]
    evidence = [quote]
    response = (
        {"confirmed": True, "reason": "Unresolved", "evidence": evidence}
        if confirmation
        else finding_response(evidence)
    )
    parse = assessment.parse_confirmation if confirmation else assessment.parse_finding
    assert parse(json.loads(json.dumps(response)), transcript)
    evidence[0] = quote.replace(r"\n", "\n").replace(r"\t", "\t")
    with pytest.raises(assessment.AssessmentError, match="does not occur"):
        parse(response, transcript)


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


@pytest.mark.parametrize("confirmation", [False, True])
def test_recent_requests_survive_both_transcript_and_model_input_limits(tmp_path, confirmation):
    payloads = [
        {"type": "message", "role": "user", "content": f"Old request {i}"} for i in range(3)
    ]
    requests = ["Current documentation task", "Preserve existing results", "Continue this review"]
    payloads.extend(
        {"type": "message", "role": "user", "content": request + "\n" + "😀" * 30000}
        for request in requests
    )
    payloads.append(
        {
            "type": "function_call_output",
            "call_id": "result",
            "output": "DATA" * 80000 + "\nLatest tool result",
        }
    )
    path = tmp_path / "synthetic-rollout.jsonl"
    path.write_text(
        "".join(
            json.dumps({"type": "response_item", "payload": payload}, ensure_ascii=False) + "\n"
            for payload in payloads
        )
    )
    text, _ = read_snapshot(path, 200000)
    assert len(text) <= 200000
    assert len(text.encode()) > assessment.MAX_PROMPT_BYTES
    if confirmation:
        candidate = assessment.Finding("topic", "summary", "explanation", ["Latest tool result"])
        # Confirmation can shorten an excerpt that was already shortened for
        # initial review; a message's clipped fragments must remain one source.
        _, text = assessment.prepare_prompt(text, [])
        subsequent = frame("tool result: id=later", "MORE" * 60000 + "\nSubsequent result")
        prompt, shown = assessment.prepare_confirmation_prompt(candidate, text, subsequent)
        assert "Subsequent result" in shown
    else:
        prompt, shown = assessment.prepare_prompt(text, [])
    assert len(prompt.encode()) <= assessment.MAX_PROMPT_BYTES
    bodies = list(source_blocks(shown))
    for request in requests:
        assert any(label == "user" and request in body for label, body in bodies)
        assert shown.count(request) == 1
    assert "Old request" not in shown
    assert sum(label == "user" for label, _ in bodies) == 3
    assert any(
        label.startswith("tool result:") and "Latest tool result" in body for label, body in bodies
    )


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


@pytest.mark.parametrize("value", [None, [], {"unexpected": "field\n\t\\n\x00漢😀"}])
def test_rejected_response_diagnostics_preserve_invalid_json_values(value):
    saved = assessment.rejected_response_diagnostics(value)
    assert not saved["truncated"]
    assert saved["length"] == len(saved["json"])
    assert saved["json"].isascii()
    assert not any(ord(char) < 32 for char in saved["json"])
    assert json.loads(saved["json"]) == value


@pytest.mark.parametrize("extra", [0, 1, 50000])
def test_rejected_response_diagnostics_bound_escaped_json(extra):
    limit = assessment.MAX_REJECTED_RESPONSE_CHARS
    value = {"unexpected": "😀" * 100}
    overhead = len(json.dumps(value, ensure_ascii=True, separators=(",", ":")))
    value["padding"] = "x" * (limit - overhead - len(',"padding":""') + extra)
    saved = assessment.rejected_response_diagnostics(value)
    assert saved["length"] == limit + extra
    assert len(saved["json"]) == limit
    assert saved["truncated"] == bool(extra)
    if not extra:
        assert json.loads(saved["json"]) == value


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
