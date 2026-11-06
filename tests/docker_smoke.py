"""Compose-only model double and checks; runs with Python's standard library.

Mounted into the app and mock containers, never installed in groundline. All
application code and domain-profile resources must come from the built image.
"""

import argparse
import json
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ANTHROPIC_KEY = "smoke-anthropic-key"
SOURCE = "Take the medicine once daily."
SUMMARY = "Take the medicine twice daily."
EXPECTED_CALLS = {
    "extract": 1,
    "rank": 1,
    "check": 1,
    "refute": 1,
    "commissions_check": 1,
}


def scripted_reply(model: str, prompt: str) -> tuple[str, dict[str, Any]]:
    """Recognize the real profile's stage prompts; reject unknown requests."""
    if "<flagged_facts>" in prompt:
        step = "refute"
        reply = {"facts": [{"id": "f1", "found": "no", "quote": None}]}
    elif "<rubric>" in prompt:
        step = "rank"
        reply = {"facts": [{"id": "f1", "severity": "critical", "why": "Frequency"}]}
    elif "<note>" in prompt and "<facts>" in prompt:
        step = "check"
        reply = {
            "facts": [
                {
                    "id": "f1",
                    "verdict": "absent",
                    "missing_confidence": 95,
                    "quote": None,
                }
            ]
        }
    elif "<transcript>" in prompt and "<note>" in prompt:
        step = "commissions_check"
        reply = {
            "issues": [
                {
                    "quote": SUMMARY,
                    "label": "contradicted",
                    "confidence": 95,
                    "importance": "critical",
                    "evidence": SOURCE,
                }
            ]
        }
    elif "<transcript>" in prompt:
        step = "extract"
        reply = {"facts": [{"id": "f1", "fact": SOURCE, "evidence": SOURCE}]}
    else:
        raise ValueError("unrecognized stage prompt")
    expected_model = (
        "claude-haiku-5-5" if step == "commissions_check" else "claude-opus-5-5"
    )
    if model != expected_model:
        raise ValueError(f"unexpected model for {step}")
    expected_text = SUMMARY if step in {"check", "refute"} else SOURCE
    if expected_text not in prompt:
        raise ValueError(f"fixture input missing from {step} prompt")
    return step, reply


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
            step, reply = scripted_reply(body["model"], body["messages"][0]["content"])
            server.calls[step] += 1
            if server.calls[step] > EXPECTED_CALLS[step]:
                raise ValueError(f"unexpected extra call to {step}")
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
                "id": f"msg_smoke_{step}",
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
    with response:
        return response.code, json.load(response)


def verify_evaluation(endpoint: str, body: dict[str, Any]) -> None:
    omissions = endpoint == "omissions"
    preset = "omission_all_opus" if omissions else "commission_quick"
    calls = 4 if omissions else 1
    assert body["flagged"] is True, body
    assert body["pipeline"] == body["validated_preset"] == preset, body
    assert body["alarm"] == {
        "score": 0.95,
        "threshold": 0.9 if omissions else 0.85,
        "level": "strict" if omissions else "default",
    }, body
    if omissions:
        assert body["omissions"] == [
            {
                "fact": SOURCE,
                "severity": "critical",
                "verdict": "absent",
                "missing_confidence": 0.95,
                "quote": None,
            }
        ], body
        assert body["counts"] == {
            "total": 1,
            "present": 0,
            "partial": 0,
            "absent": 1,
        }, body
    else:
        assert body["commissions"] == [
            {
                "claim": SUMMARY,
                "severity": "critical",
                "support_verdict": "contradicted",
                "unsupported_confidence": 0.95,
                "evidence": SOURCE,
                "evidence_found": True,
            }
        ], body
    assert body["usage"]["calls"] == calls, body
    assert body["usage"]["input_tokens"] == 100 * calls, body
    assert body["usage"]["output_tokens"] == 20 * calls, body
    assert body["usage"]["cost_usd"] > 0, body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    probe = commands.add_parser("probe")
    probe.add_argument("status", type=int)
    probe.add_argument("method")
    probe.add_argument("path")
    probe.add_argument("--json")
    probe.add_argument("--header", action="append", default=[])
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--api-key", required=True)
    args = parser.parse_args()
    if args.command == "serve":
        with MockAnthropic(("0.0.0.0", 8080)) as server:
            server.serve_forever()
    elif args.command == "probe":
        headers = dict(header.split(":", 1) for header in args.header)
        headers = {name: value.strip() for name, value in headers.items()}
        headers["Content-Type"] = "application/json"
        status, body = request(
            args.method, "http://127.0.0.1:8000" + args.path, headers, args.json
        )
        assert status == args.status, (
            f"{args.method} {args.path}: expected {args.status}, got {status}: {body}"
        )
        print(f"ok: {args.method} {args.path} returned {status}")
    else:
        headers = {
            "Authorization": f"Bearer {args.api_key}",
            "Content-Type": "application/json",
        }
        for endpoint in ("omissions", "commissions"):
            status, body = request(
                "POST",
                f"http://127.0.0.1:8000/v1/{endpoint}",
                headers,
                json.dumps({"source": SOURCE, "summary": SUMMARY}),
            )
            assert status == 200, f"{endpoint}: expected 200, got {status}: {body}"
            verify_evaluation(endpoint, body)
            print(f"ok: /v1/{endpoint} returned expected findings, preset and usage")
        status, body = request("GET", "http://anthropic-mock:8080/requests", {}, None)
        assert status == 200 and body == {"calls": EXPECTED_CALLS, "errors": []}, body
        print("ok: all five model calls reached the mock, with no unexpected requests")


if __name__ == "__main__":
    main()
