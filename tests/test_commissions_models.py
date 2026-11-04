from groundline.evaluators.commissions.models import Commission, ranked


def commission(id: str, severity: str, confidence: float) -> Commission:
    return Commission.model_validate(
        {
            "id": id,
            "quote": id,
            "support_verdict": "unsupported",
            "unsupported_confidence": confidence,
            "severity": severity,
        }
    )


def test_commissions_are_ranked_by_severity_then_highest_confidence():
    found = [
        commission("peripheral-high", "peripheral", 0.99),
        commission("supporting", "supporting", 0.5),
        commission("critical-low", "critical", 0.6),
        commission("critical-high", "critical", 0.9),
    ]

    assert [c.id for c in ranked(found)] == [
        "critical-high",
        "critical-low",
        "supporting",
        "peripheral-high",
    ]


def test_ranking_leaves_the_original_list_alone():
    found = [commission("b", "supporting", 0.5), commission("a", "critical", 0.5)]

    ranked(found)

    assert [c.id for c in found] == ["b", "a"]
