import pytest

from groundline.evaluators.report import format_cost


@pytest.mark.parametrize(
    "dollars,expected",
    [
        (0, "$0.00"),  # nothing was paid for, such as a fully cached run
        (0.29, "$0.29"),
        (1.5, "$1.50"),
        (0.01, "$0.01"),
        (0.0099, "$0.0099"),  # under a cent: four decimals
        (0.0020, "$0.0020"),
        (0.0013, "$0.0013"),
        (0.00005, "$0.0001"),
        (0.00004, "<$0.0001"),  # too small for four decimals, but not free
    ],
)
def test_cost_formatting(dollars: float, expected: str):
    assert format_cost(dollars) == expected
