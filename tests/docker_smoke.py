"""Compose-only model double and checks using the installed groundline package.

Mounted into the app and mock containers, never installed in groundline. All
application code and domain-profile resources must come from the built image.
"""

import argparse
import json
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from groundline_core.presets import load_preset
from groundline_core.profiles import load_builtin_profile

ANTHROPIC_KEY = "smoke-anthropic-key"
SOURCE = "Take the medicine once daily."
SUMMARY = "Take the medicine twice daily."


LEAN = "commission_reverse_lean"
CHECKS: list[tuple[str, str, dict[str, str]]] = [
    ("omissions", "omission_all_opus", {}),
    ("commissions", "commission_quick", {}),
    ("commissions", LEAN, {"preset": LEAN}),
]
"""Each request the smoke test sends: the endpoint, the preset that should run,
and anything the body adds to the source and summary."""
# One request per enabled model stage for this single-finding fixture.
MODEL_SETTINGS = {
    preset: {
        stage.step: stage.settings
        for stage in load_preset(preset).stages
        if stage.enabled and "model" in stage.settings
    }
    for _, preset, _ in CHECKS
}
PROMPTS = load_builtin_profile("clinical").texts
EXPECTED_CALLS = dict(
    Counter(
        settings["prompt"]
        for stages in MODEL_SETTINGS.values()
        for settings in stages.values()
    )
)
"""How many calls each prompt should get, by its name in the profile."""
MODELS = {
    settings["prompt"]: settings["model"]
    for stages in MODEL_SETTINGS.values()
    for settings in stages.values()
}
REPLIES: dict[str, dict[str, Any]] = {
    "extract_coarse_v1": {"facts": [{"id": "f1", "fact": SOURCE, "evidence": SOURCE}]},
    "rank_v1": {"facts": [{"id": "f1", "severity": "critical", "why": "Frequency"}]},
    "check_batch_v1": {
        "facts": [
            {"id": "f1", "verdict": "absent", "missing_confidence": 100, "quote": None}
        ]
    },
    "refute_v1": {"facts": [{"id": "f1", "found": "no", "quote": None}]},
    "quick_check_v1": {
        "issues": [
            {
                "quote": SUMMARY,
                "label": "contradicted",
                "confidence": 100,
                "importance": "critical",
                "evidence": SOURCE,
            }
        ]
    },
    "note_claims_v1": {"claims": [{"claim": SUMMARY, "quote": SUMMARY}]},
    "support_check_lean_v1": {
        "claims": [
            {
                "id": "c1",
                "label": "contradicted",
                "evidence": SOURCE,
                "not_supported_confidence": 100,
                "importance": "critical",
            }
        ]
    },
    "support_refute_v1": {
        "claims": [{"id": "c1", "supported": False, "evidence": None}]
    },
}
"""The scripted reply to each prompt, by its name in the profile."""
APP_URL = "http://127.0.0.1:8000"


def scripted_reply(model: str, prompt: str) -> tuple[str, dict[str, Any]]:
    """Recognize which of the real profile's prompts this is, from its first line,
    and return its name and scripted reply; reject unknown requests."""
    first_line = prompt.lstrip().split("\n", 1)[0]
    name = next(
        (n for n in EXPECTED_CALLS if PROMPTS[n].lstrip().startswith(first_line)),
        None,
    )
    if name is None or not first_line:
        raise ValueError("unrecognized stage prompt")
    if model != MODELS[name]:
        raise ValueError(f"unexpected model for {name}")
    for placeholder, text in (("{transcript}", SOURCE), ("{note}", SUMMARY)):
        if placeholder in PROMPTS[name] and text not in prompt:
            raise ValueError(f"fixture input missing from {name} prompt")
    return name, REPLIES[name]


class MockAnthropic(HTTPServer):
    def __init__(self, address: tuple[str, int]):
        super().__init__(address, Handler)
        self.calls: Counter[str] = Counter()
        self.errors: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status: int, body: object) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        server = cast(MockAnthropic, self.server)
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
        elif self.path == "/requests":
            self.send_json(200, {"calls": dict(server.calls), "errors": server.errors})
        else:
            self.send_json(404, {"error": "unknown mock route"})

    def do_POST(self) -> None:
        server = cast(MockAnthropic, self.server)
        try:
            if self.path != "/v1/messages":
                raise ValueError("unknown mock route")
            if self.headers.get("Authorization") != f"Bearer {ANTHROPIC_KEY}":
                raise ValueError("unexpected mock credential")
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
            )
            name, reply = scripted_reply(body["model"], body["messages"][0]["content"])
            server.calls[name] += 1
            if server.calls[name] > EXPECTED_CALLS[name]:
                raise ValueError(f"unexpected extra call to {name}")
        except (ValueError, KeyError, IndexError, TypeError) as error:
            server.errors.append(str(error))
            # 400 is terminal to the SDK: fixture mistakes must fail promptly.
            self.send_json(
                400,
                {
                    "type": "error",
                    "error": {"type": "invalid_request_error", "message": str(error)},
                },
            )
            return
        self.send_json(
            200,
            {
                "id": f"msg_smoke_{len(server.calls)}",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": [{"type": "text", "text": json.dumps(reply)}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        )


def request(
    method: str, url: str, headers: dict[str, str], body: str | None
) -> tuple[int, dict[str, Any]]:
    data = body.encode() if body is not None else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        response = urlopen(req, timeout=10)
    except HTTPError as error:
        response = error
    except (URLError, TimeoutError, OSError) as error:
        raise AssertionError(f"{method} {url}: connection failed: {error}") from error
    with response:
        raw = response.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except ValueError as error:
            raise AssertionError(
                f"{method} {url}: HTTP {response.code}, expected JSON: {raw}"
            ) from error
        return response.code, parsed


def verify_evaluation(endpoint: str, preset: str, body: dict[str, Any]) -> None:
    calls = len(MODEL_SETTINGS[preset])
    assert body["flagged"] is True, body
    assert body["evaluation_type"] == endpoint, body
    assert body["metadata"]["pipeline"] == preset, body
    assert body["findings"], body
    usage = body["metadata"]["usage"]
    assert usage["calls"] == calls, body
    assert usage["input_tokens"] == 100 * calls, body
    assert usage["output_tokens"] == 20 * calls, body
    assert usage["cost_usd"] > 0, body
    # Only the omission check counts facts.
    assert (body["counts"] is None) == (endpoint == "commissions"), body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--api-key", required=True)
    args = parser.parse_args()
    if args.command == "serve":
        with MockAnthropic(("0.0.0.0", 8080)) as server:
            server.serve_forever()
    else:
        headers = {
            "Authorization": f"Bearer {args.api_key}",
            "Content-Type": "application/json",
        }
        for endpoint, preset, extra in CHECKS:
            status, body = request(
                "POST",
                f"{APP_URL}/v1/{endpoint}",
                headers,
                json.dumps({"source": SOURCE, "summary": SUMMARY, **extra}),
            )
            assert status == 200, f"{preset}: expected 200, got {status}: {body}"
            verify_evaluation(endpoint, preset, body)
            print(f"ok: /v1/{endpoint} ran {preset}: expected findings and usage")
        status, body = request("GET", "http://anthropic-mock:8080/requests", {}, None)
        assert status == 200 and body == {"calls": EXPECTED_CALLS, "errors": []}, body
        print(
            "ok: all expected model calls reached the mock, with no unexpected requests"
        )


if __name__ == "__main__":
    main()
