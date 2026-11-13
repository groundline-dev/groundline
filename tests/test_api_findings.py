"""The response both checks share: the same top-level fields, and findings that
mean the same thing whichever check made them (ALL-140)."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from groundline.api import common
from groundline.app import app
from tests.auth_helpers import HEADERS, FakeKeys
from tests.commissions_helpers import (
    DOSE,
    DOSE_ISSUE,
    DOSE_SOURCE,
    EXAM,
    EXAM_ISSUE,
    issue,
    issues_reply,
)
from tests.commissions_helpers import SUMMARY as COMMISSION_SUMMARY
from tests.commissions_helpers import Script as CommissionScript
from tests.fakes import FakeClient
from tests.omission_helpers import (
    SOURCE,
    SUMMARY,
    Script,
    check_reply,
    flagged_script,
)

http = TestClient(app, headers=HEADERS)

TOP_LEVEL = {"evaluation_type", "flagged", "score", "findings", "counts", "metadata"}
FINDING = {
    "type",
    "severity",
    "verdict",
    "confidence",
    "statement",
    "source_evidence",
    "summary_evidence",
    "evidence_found",
    "explanation",
}
EXPLANATIONS = {
    ("omission", "absent"): "The summary does not record this fact.",
    ("omission", "partial"): "The summary only partly records this fact.",
    ("commission", "unsupported"): "The source does not mention this claim.",
    ("commission", "contradicted"): "The source says something different.",
}
SECRET = "patient is HIV positive and takes tenofovir"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No real .env, and no client override left behind."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app.state, "api_keys", FakeKeys(), raising=False)
    yield
    app.dependency_overrides.clear()


def use_client(replies: Callable[[str], str]) -> None:
    client = FakeClient(replies)
    app.dependency_overrides[common.model_client] = lambda: client


def omissions(script: Script | None = None, **body: str) -> dict[str, Any]:
    use_client(script or flagged_script())
    response = http.post(
        "/v1/omissions", json={"source": SOURCE, "summary": SUMMARY, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()


def commissions(*issues: dict[str, Any], **body: str) -> dict[str, Any]:
    use_client(CommissionScript(commissions_check=issues_reply(*issues)))
    response = http.post(
        "/v1/commissions",
        json={"source": SOURCE, "summary": COMMISSION_SUMMARY, **body},
    )
    assert response.status_code == 200, response.text
    return response.json()


def without_usage(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metadata.items() if key != "usage"}


# --- R1: one structure ----------------------------------------------------------


def test_both_checks_answer_with_the_same_top_level_fields():
    assert set(omissions()) == set(commissions(DOSE_ISSUE)) == TOP_LEVEL


def test_each_check_names_its_evaluation_type():
    assert omissions()["evaluation_type"] == "omissions"
    assert commissions(DOSE_ISSUE)["evaluation_type"] == "commissions"


def test_usage_is_in_the_metadata_for_both_checks():
    assert omissions()["metadata"]["usage"]["calls"] == 4
    assert commissions(DOSE_ISSUE)["metadata"]["usage"]["calls"] == 1
    assert set(commissions(DOSE_ISSUE)["metadata"]["usage"]) == {
        "calls",
        "input_tokens",
        "output_tokens",
        "cost_usd",
    }


# --- R4: the agreed body --------------------------------------------------------


def test_a_flagged_omission_check_reports_its_score_findings_and_metadata():
    body = omissions()

    assert body["flagged"] is True
    assert body["score"] == pytest.approx(0.25)
    assert body["counts"] == {"total": 3, "present": 0, "partial": 1, "absent": 2}
    assert without_usage(body["metadata"]) == {
        "pipeline": "omission_all_opus",
        "alarm": {"score": 0.95, "threshold": 0.9, "level": "strict"},
    }
    first, second, third = body["findings"]
    assert first == {
        "type": "omission",
        "severity": "critical",
        "verdict": "absent",
        "confidence": 0.95,
        "statement": "Motrin 600 mg every 6 hours for a week.",
        "source_evidence": "motrin 600 mg every 6 hours",
        "summary_evidence": None,
        "evidence_found": True,
        "explanation": EXPLANATIONS["omission", "absent"],
    }
    assert second["verdict"] == "partial"
    assert second["statement"] == "Injured the finger skiing."
    assert second["summary_evidence"] == (
        "She was skiing and hyperextended her right index finger"
    )
    assert second["explanation"] == EXPLANATIONS["omission", "partial"]
    assert third["statement"] == "Pain is worse with movement."


def test_a_flagged_commission_check_reports_its_score_findings_and_metadata():
    body = commissions(EXAM_ISSUE, DOSE_ISSUE)

    assert body["flagged"] is True
    assert body["score"] == pytest.approx(0.1667, abs=1e-4)
    assert body["counts"] is None
    assert without_usage(body["metadata"]) == {
        "pipeline": "commission_quick",
        "alarm": {"score": 0.95, "threshold": 0.85, "level": "default"},
    }
    contradicted, unsupported = body["findings"]
    assert contradicted == {
        "type": "commission",
        "severity": "critical",
        "verdict": "contradicted",
        "confidence": 0.95,
        "statement": DOSE,
        "source_evidence": DOSE_SOURCE,
        "summary_evidence": None,
        "evidence_found": True,
        "explanation": EXPLANATIONS["commission", "contradicted"],
    }
    assert unsupported == {
        "type": "commission",
        "severity": "supporting",
        "verdict": "unsupported",
        "confidence": 0.7,
        "statement": EXAM,
        "source_evidence": None,
        "summary_evidence": None,
        "evidence_found": None,
        "explanation": EXPLANATIONS["commission", "unsupported"],
    }


def test_every_finding_has_the_same_fields_whichever_check_made_it():
    findings = omissions()["findings"] + commissions(EXAM_ISSUE, DOSE_ISSUE)["findings"]

    assert findings
    assert all(set(finding) == FINDING for finding in findings)


def test_a_check_with_nothing_to_report_scores_one_and_has_no_findings():
    no_facts = omissions(Script(extract='{"facts": []}'))
    no_claims = commissions()

    for body in (no_facts, no_claims):
        assert body["flagged"] is False
        assert body["score"] == 1.0
        assert body["findings"] == []
    assert no_facts["counts"] == {"total": 0, "present": 0, "partial": 0, "absent": 0}
    assert no_claims["counts"] is None


# --- R3: findings a program can use ---------------------------------------------


def test_a_quote_the_model_gives_for_an_absent_fact_is_not_reported():
    check = json.loads(check_reply({"f3": 95}))
    check["facts"][0].update(verdict="absent", quote="Motrin 600 mg every 6 hours")
    script = flagged_script()
    script.replies["check"] = [json.dumps(check)]

    absent = omissions(script)["findings"][0]

    assert absent["verdict"] == "absent"
    assert absent["summary_evidence"] is None


def test_evidence_the_model_gives_for_an_unsupported_claim_is_not_reported():
    stray = issue(EXAM, "unsupported", 70, "supporting", DOSE_SOURCE)

    unsupported = commissions(stray)["findings"][0]

    assert unsupported["verdict"] == "unsupported"
    assert unsupported["source_evidence"] is None
    assert unsupported["evidence_found"] is None


def test_each_explanation_is_one_of_the_fixed_sentences():
    findings = omissions()["findings"] + commissions(EXAM_ISSUE, DOSE_ISSUE)["findings"]

    for finding in findings:
        assert (
            finding["explanation"] == EXPLANATIONS[finding["type"], finding["verdict"]]
        )


def test_an_explanation_never_repeats_the_source_or_the_summary():
    body = omissions(source=SOURCE + "\n" + SECRET, summary=SUMMARY + "\n" + SECRET)

    explanations = " ".join(f["explanation"] for f in body["findings"])
    assert "tenofovir" not in explanations
    assert "Motrin" not in explanations


# --- R2: the meanings are written in the docs -----------------------------------


@pytest.mark.parametrize(
    ("model", "empty_case"),
    [("OmissionsResponse", "no facts"), ("CommissionsResponse", "no claims")],
)
def test_the_score_is_defined_in_the_docs(model: str, empty_case: str):
    schema = app.openapi()["components"]["schemas"][model]["properties"]["score"]

    for phrase in ("From 0 to 1", "Higher is better", "below 0.5", "alarm"):
        assert phrase in schema["description"]
    assert empty_case in schema["description"]


@pytest.mark.parametrize("model", ["OmissionsResponse", "CommissionsResponse"])
def test_the_flag_is_defined_in_the_docs(model: str):
    schemas = app.openapi()["components"]["schemas"]

    description = schemas[model]["properties"]["flagged"]["description"]

    assert "alarm" in description and "threshold" in description


def test_every_finding_field_is_described_in_the_docs():
    finding = app.openapi()["components"]["schemas"]["Finding"]

    assert set(finding["properties"]) == FINDING
    for name, field in finding["properties"].items():
        assert field.get("description"), name
    assert finding["properties"]["type"]["enum"] == ["omission", "commission"]
    assert finding["properties"]["verdict"]["enum"] == [
        "partial",
        "absent",
        "unsupported",
        "contradicted",
    ]
