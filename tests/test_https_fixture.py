"""Bounded Docker subnet allocation retries only genuine address overlaps."""

import subprocess
from unittest.mock import patch

import pytest

from tests.https_smoke import reserve_proxy_network


def test_overlap_retries_another_subnet_with_the_same_prefix():
    with (
        patch("tests.https_smoke.secrets.randbelow", side_effect=[0, 1]),
        patch(
            "tests.https_smoke.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(
                    [], 1, "", "Pool overlaps with another one"
                ),
                subprocess.CompletedProcess([], 0, "network-id", ""),
            ],
        ) as run,
    ):
        subnet = reserve_proxy_network("fixture", 29)
    assert str(subnet) == "172.16.0.8/29"
    assert run.call_args_list[0].args[0][-2:] == ["172.16.0.0/29", "fixture"]
    assert run.call_args_list[1].args[0][-2:] == [str(subnet), "fixture"]


def test_daemon_failure_does_not_retry_or_hide_the_cause():
    with patch(
        "tests.https_smoke.subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, "", "Cannot connect to Docker"),
    ) as run:
        with pytest.raises(RuntimeError, match="Cannot connect to Docker"):
            reserve_proxy_network("fixture", 29)
    assert run.call_count == 1


def test_overlap_retries_are_bounded():
    with patch(
        "tests.https_smoke.subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, "", "Pool overlaps"),
    ) as run:
        with pytest.raises(RuntimeError, match="32 overlaps"):
            reserve_proxy_network("fixture", 29)
    assert run.call_count == 32
