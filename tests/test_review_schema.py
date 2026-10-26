import json
from pathlib import Path

import pytest

from you_should_know.assessment import AssessmentError, parse_confirmation, parse_finding
from you_should_know.transcript import frame

RESOURCES = Path(__file__).resolve().parents[1] / "resources"


def finding_fields():
    schema = json.loads((RESOURCES / "finding.schema.json").read_text())
    return schema["properties"]["finding"]["anyOf"][1]["properties"]


@pytest.mark.parametrize(
    "field,maximum", [("topic", 160), ("summary", 280), ("explanation", 1600), ("evidence", 500)]
)
def test_finding_schema_bounds_text_at_the_parser_limits(field, maximum):
    fields = finding_fields()
    schema = fields[field]["items"] if field == "evidence" else fields[field]
    assert schema["minLength"] == 1
    assert schema["maxLength"] == maximum
    for length in (maximum, maximum + 1):
        text = "漢" * (length - 2) + "\r\n"
        value = {
            "topic": "topic",
            "summary": "summary",
            "explanation": "explanation",
            "evidence": [text],
            "agent_still_working": False,
            "assistant_claim": False,
        }
        value[field] = [text] if field == "evidence" else text
        if field != "evidence":
            value["evidence"] = ["tool evidence"]
        transcript = frame("tool result: id=1", text if field == "evidence" else "tool evidence")
        if length == maximum:
            if field != "evidence":
                value[field] = text.replace("\r", "x")
            assert parse_finding({"finding": value}, transcript) is not None
        else:
            with pytest.raises(AssessmentError, match=f"invalid {field}"):
                parse_finding({"finding": value}, transcript)


def test_finding_schema_limits_quote_count():
    schema = finding_fields()["evidence"]
    assert schema["minItems"] == 1
    assert schema["maxItems"] == 3


def test_confirmation_schema_allows_withdrawal_without_evidence_and_bounds_quotes():
    schema = json.loads((RESOURCES / "confirmation.schema.json").read_text())["properties"]
    assert schema["reason"]["minLength"] == 1
    assert schema["reason"]["maxLength"] == 800
    assert schema["evidence"]["minItems"] == 0
    assert schema["evidence"]["maxItems"] == 3
    assert schema["evidence"]["items"]["minLength"] == 1
    assert schema["evidence"]["items"]["maxLength"] == 500
    result = {"confirmed": False, "reason": "The assistant fixed it.", "evidence": []}
    assert parse_confirmation(result, "") == (False, result["reason"])
    result["confirmed"] = True
    with pytest.raises(AssessmentError, match="one to three"):
        parse_confirmation(result, "")
