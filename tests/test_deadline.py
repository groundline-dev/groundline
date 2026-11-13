"""ALL-134 R3/R6/R7: cancellation and one deadline through real API/evaluate.

Short application allowances use generous watchdogs. The watchdog never stands
in for the application timeout: tests assert cancellation happened before it.
"""

import asyncio
from pathlib import Path
from typing import Any

import httpx2
import pytest

from groundline_core import evaluate, evaluate_sync, load_preset
from groundline_core.api import common
from groundline_core.app import app
from groundline_core.core import ModelReply
from groundline_core.core.errors import ModelTimeoutError
from groundline_core.settings import Settings
from tests.async_helpers import finish_before_watchdog
from tests.auth_helpers import BODY, HEADERS, FakeKeys
from tests.fakes import FakeClient
from tests.omission_helpers import not_flagged_script


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROUNDLINE_CHECK_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setattr(Settings, "db_auth_timeout_seconds", 0.5)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.setattr(app.state, "api_keys", FakeKeys(), raising=False)
    yield
    app.dependency_overrides.clear()


class HungClient:
    def __init__(self):
        self.started = 0
        self.cancelled = 0

    async def complete(
        self, prompt: str, *, model: str, effort: Any, max_tokens: int
    ) -> ModelReply:
        self.started += 1
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled += 1
        raise AssertionError("hung fake unexpectedly returned")


@pytest.mark.parametrize("route", ["/v1/omissions", "/v1/commissions"])
def test_hung_model_is_cancelled_and_answers_safe_504(route: str):
    async def run():
        model = HungClient()
        app.dependency_overrides[common.model_client] = lambda: model
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as http:
            response = await finish_before_watchdog(
                http.post(route, headers=HEADERS, json=BODY)
            )
            assert response.status_code == 504
            assert set(response.json()) == {"detail"}
            assert BODY["source"] not in response.text
            assert model.started == model.cancelled == 1

    asyncio.run(run())


@pytest.mark.parametrize("sync", [False, True])
def test_existing_python_entry_points_bound_caller_supplied_clients(sync: bool):
    model = HungClient()

    async def run():
        with pytest.raises(ModelTimeoutError):
            await finish_before_watchdog(
                evaluate("private source", "private summary", client=model)
            )
        assert model.started == model.cancelled == 1

    if not sync:
        asyncio.run(run())
    else:
        # A helper thread would leave work running after failure. Instead use a
        # client that stops itself after the watchdog, recording the distinction.
        class WatchdogClient(HungClient):
            async def complete(self, *args: Any, **kwargs: Any) -> ModelReply:
                try:
                    async with asyncio.timeout(3):
                        return await super().complete(*args, **kwargs)
                except TimeoutError:
                    raise AssertionError(
                        "evaluate_sync failed to enforce the check deadline"
                    ) from None

        model = WatchdogClient()
        with pytest.raises(ModelTimeoutError):
            evaluate_sync("private source", "private summary", client=model)
        assert model.cancelled == 1


def test_external_cancellation_propagates_without_another_call():
    async def run():
        model = HungClient()
        task = asyncio.create_task(evaluate("a", "b", client=model))
        while not model.started:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert model.started == model.cancelled == 1

    asyncio.run(run())


def test_independent_evaluations_do_not_inherit_an_expired_deadline():
    async def run():
        model = HungClient()
        # Both evaluations execute in this task, so a ContextVar leak is visible.
        watchdog_expired = False

        async def watchdog():
            nonlocal watchdog_expired
            await asyncio.sleep(3)
            watchdog_expired = True
            assert current is not None
            current.cancel()

        current = asyncio.current_task()
        assert current is not None
        guard = asyncio.create_task(watchdog())
        try:
            await evaluate("a", "b", client=model)
        except asyncio.CancelledError:
            assert not watchdog_expired, "first evaluation must enforce its deadline"
            raise
        except Exception:
            pass
        finally:
            guard.cancel()
            await asyncio.gather(guard, return_exceptions=True)
        assert model.started == model.cancelled == 1
        result = await evaluate(
            BODY["source"],
            BODY["summary"],
            client=FakeClient(not_flagged_script()),
            spec=load_preset("omission_all_opus"),
        )
        assert result.run.usage.calls == 4

    asyncio.run(run())


@pytest.mark.parametrize("route", ["/v1/omissions", "/v1/commissions"])
def test_authentication_timeout_is_503_without_model_work(route: str):
    class HungKeys(FakeKeys):
        cancelled = False

        async def authenticate(self, credential: str):
            self.calls.append(credential)
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

    async def run():
        keys = HungKeys()
        app.state.api_keys = keys
        model = HungClient()
        app.dependency_overrides[common.model_client] = lambda: model
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as http:
            response = await finish_before_watchdog(
                http.post(route, headers=HEADERS, json=BODY)
            )
            assert response.status_code == 503
            assert keys.cancelled
            assert len(keys.calls) == 1
            assert model.started == 0

    asyncio.run(run())


def test_unexpected_bare_timeout_is_500_not_upstream_504():
    class BrokenClient:
        async def complete(self, prompt: str, **kwargs: Any) -> ModelReply:
            raise TimeoutError("private application failure")

    async def run():
        app.dependency_overrides[common.model_client] = lambda: BrokenClient()
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as http:
            response = await http.post("/v1/commissions", headers=HEADERS, json=BODY)
        assert response.status_code == 500
        assert "private application failure" not in response.text

    asyncio.run(run())


@pytest.mark.parametrize("corrective", [False, True])
def test_shared_deadline_preserves_known_usage_across_calls(corrective: bool):
    class BilledThenHung(HungClient):
        async def complete(self, prompt: str, **kwargs: Any) -> ModelReply:
            if not self.started:
                self.started += 1
                return ModelReply(
                    text="not json" if corrective else not_flagged_script()(prompt),
                    model="claude-opus-5-5",
                    input_tokens=12345,
                    output_tokens=300,
                )
            return await super().complete(prompt, **kwargs)

    async def run():
        model = BilledThenHung()
        app.dependency_overrides[common.model_client] = lambda: model
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as http:
            response = await finish_before_watchdog(
                http.post("/v1/omissions", headers=HEADERS, json=BODY)
            )
            assert response.status_code == 504
            assert model.started == 2
            assert model.cancelled == 1
            assert "recorded" in response.json()["detail"].lower()
            assert "12,345" in response.text or "12345" in response.text

    asyncio.run(run())


def test_authentication_time_is_part_of_check_allowance(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("GROUNDLINE_CHECK_TIMEOUT_SECONDS", "0.3")
    monkeypatch.setattr(Settings, "db_auth_timeout_seconds", 1)

    class SlowKeys(FakeKeys):
        async def authenticate(self, credential: str):
            await asyncio.sleep(0.2)
            return await super().authenticate(credential)

    class SlowModel:
        cancelled = False

        async def complete(self, prompt: str, **kwargs: Any) -> ModelReply:
            try:
                await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            return ModelReply(
                text='{"issues": []}',
                model="claude-opus-5-5",
                input_tokens=1,
                output_tokens=1,
            )

    async def run():
        app.state.api_keys = SlowKeys()
        model = SlowModel()
        app.dependency_overrides[common.model_client] = lambda: model
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as http:
            response = await http.post("/v1/commissions", headers=HEADERS, json=BODY)
        assert response.status_code == 504
        assert model.cancelled, (
            "evaluation must not restart the allowance after authentication"
        )

    asyncio.run(run())


def test_overlapping_requests_have_independent_deadlines():
    class LaterClient:
        async def complete(self, prompt: str, **kwargs: Any) -> ModelReply:
            await asyncio.sleep(0.35)
            return ModelReply(
                text='{"issues": []}',
                model="claude-opus-5-5",
                input_tokens=1,
                output_tokens=1,
            )

    async def run():
        first_model = HungClient()
        clients = iter([first_model, LaterClient()])
        app.dependency_overrides[common.model_client] = lambda: next(clients)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as http:
            first = asyncio.create_task(
                http.post("/v1/commissions", headers=HEADERS, json=BODY)
            )
            async with asyncio.timeout(3):
                while not first_model.started:
                    await asyncio.sleep(0)
            await asyncio.sleep(0.3)
            second = asyncio.create_task(
                http.post("/v1/commissions", headers=HEADERS, json=BODY)
            )
            tasks = {first, second}
            done, pending = await asyncio.wait(tasks, timeout=3)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            assert done == tasks, "each request must enforce its own deadline"
            assert first.result().status_code == 504
            assert second.result().status_code == 200
            assert first_model.cancelled == 1

    asyncio.run(run())


@pytest.mark.parametrize("owner_cancels", [False, True])
def test_cleanup_failure_preserves_timeout_or_owner_cancellation(owner_cancels: bool):
    """A client's cleanup error must not replace the reason work was stopped."""

    class NoisyClient(HungClient):
        async def complete(self, prompt: str, **kwargs: Any) -> ModelReply:
            try:
                return await super().complete(prompt, **kwargs)
            finally:
                raise RuntimeError("private cleanup failure")

    async def run():
        model = NoisyClient()
        task = asyncio.create_task(evaluate("a", "b", client=model))
        try:
            async with asyncio.timeout(3):
                while not model.started:
                    await asyncio.sleep(0)
            if owner_cancels:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                done, _ = await asyncio.wait({task}, timeout=3)
                assert done, "the application deadline must beat the watchdog"
                with pytest.raises(ModelTimeoutError):
                    task.result()
            assert model.started == model.cancelled == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
