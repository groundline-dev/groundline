# groundline-client

The Python SDK for the groundline API: one `GroundlineClient` with a method per
endpoint, typed requests and responses, sync and async.

## Disclaimer

groundline's results help a person review a summary. A summary that isn't
flagged can still leave out or misstate important facts, so check it before
relying on it. You're responsible for any decision made using groundline's
results. groundline is provided "as is", and its authors accept no liability for
outcomes from its use (see sections 7 and 8 of the Apache License 2.0).

The API's built-in clinical profile adds a disclaimer sentence after the first
one: "They aren't medical advice or a clinical decision." Every check's response
carries the full text in `metadata.disclaimer`: show it next to the result
wherever people read it.

## Install

The SDK needs Python 3.13 or later. Install it from PyPI:

<!-- not run: installs the package -->
```sh
pip install groundline-client
```

or, in a project managed with uv:

<!-- not run: installs the package -->
```sh
uv add groundline-client
```

It installs only `attrs` and `httpx2`: no pipelines, no Anthropic SDK and no server.

## SDK or library

groundline has two Python packages. This one, the SDK, calls a groundline
deployment over HTTP. The library, `groundline-core`, runs the checks inside your
own process (see the
[main README](https://github.com/groundline-dev/groundline#using-it-from-python)).
The difference that matters most is who pays for the model calls:

| | SDK (`groundline-client`) | Library (`groundline-core`) |
| --- | --- | --- |
| Where a check runs | On a groundline deployment that someone operates | In your own Python process |
| Whose Anthropic API key pays | The deployment's. Its operator pays Anthropic for every check, and you need no Anthropic key | Yours. You set `ANTHROPIC_API_KEY`, and Anthropic bills you |
| What you need | The deployment's URL and a groundline API key from its operator | An Anthropic API key |
| What it installs | `attrs` and `httpx2` | The pipelines and the Anthropic SDK |

Use the SDK when someone else runs groundline for you, or when several apps
share one deployment. Use the library when you want checks inside your own app
and are happy to pay Anthropic directly.

## Connect

Give the client your deployment's URL and an API key. Either set the two
environment variables:

<!-- not run: sets environment variables in a shell -->
```sh
export GROUNDLINE_API_URL="https://groundline.example.com"
export GROUNDLINE_API_KEY="..."
```

The client reads them when you create it, so `GroundlineClient()` needs no
arguments (see [Run a check](#run-a-check)). Or pass them as arguments, which win
over the environment variables:

<!-- not run: only creates a client, so it makes no call -->
```python
import os

from groundline_client import GroundlineClient

client = GroundlineClient(
    api_url="https://groundline.example.com",
    api_key=os.environ["GROUNDLINE_API_KEY"],
)
```

Don't write the API key as a literal in your code or type it on a command line:
both end up in git or your shell history. Read it from your secret store or
environment.

If the URL or API key is missing, blank, or can't be used, creating the client
fails at once with a message naming the setting. A blank argument is an error: it
doesn't fall back to the environment variable, because a blank value usually
means a secret that didn't load. The URL must be an `http://` or `https://`
address with a host, and no user name, password, query string or fragment.

The client doesn't print the API key in its `repr`, its error messages or its
tracebacks, including the local variables that tools such as pytest
`--showlocals` and Sentry can print. Its tests check this, and that the key stays
out of the logs of the libraries it uses. A variable of your own that holds the
key can still show up in those tools, and that part is up to you.

## Run a check

Every example below reads the URL and API key from the environment variables
above and runs as written. Start with `health()`: it fails fast when the URL is
wrong, and prints `ok` when the deployment is up.

```python
from groundline_client import GroundlineClient

with GroundlineClient() as client:
    print(client.health()["status"])
```

### Omissions

`evaluate_omissions` finds the facts in the source that the summary leaves out.

```python
from groundline_client import GroundlineClient, OmissionsRequest

SOURCE = (
    "Doctor: Any allergies? Patient: Penicillin, it gives me a rash. "
    "Doctor: Your knee is sprained. Take Motrin 600 mg every 6 hours for a week, "
    "and come back if it isn't better in two weeks."
)
SUMMARY = (
    "Sprained knee. Motrin 600 mg every 6 hours for a week. "
    "Return if not improving in two weeks."
)

with GroundlineClient() as client:
    result = client.evaluate_omissions(OmissionsRequest(source=SOURCE, summary=SUMMARY))

print("Flagged:", result.flagged)
for finding in result.findings:
    print(finding.verdict.value, finding.statement)
```

The async version has the same shape. Use `async with`:

```python
import asyncio

from groundline_client import GroundlineClient, OmissionsRequest

SOURCE = (
    "Doctor: Any allergies? Patient: Penicillin, it gives me a rash. "
    "Doctor: Your knee is sprained. Take Motrin 600 mg every 6 hours for a week, "
    "and come back if it isn't better in two weeks."
)
SUMMARY = (
    "Sprained knee. Motrin 600 mg every 6 hours for a week. "
    "Return if not improving in two weeks."
)


async def main() -> None:
    async with GroundlineClient() as client:
        result = await client.evaluate_omissions_async(
            OmissionsRequest(source=SOURCE, summary=SUMMARY)
        )
    print("Flagged:", result.flagged)
    for finding in result.findings:
        print(finding.verdict.value, finding.statement)


asyncio.run(main())
```

### Commissions

`evaluate_commissions` finds the claims in the summary that the source doesn't
support. This summary says 800 mg where the source says 600 mg.

```python
from groundline_client import CommissionsRequest, GroundlineClient

SOURCE = (
    "Doctor: Any allergies? Patient: Penicillin, it gives me a rash. "
    "Doctor: Your knee is sprained. Take Motrin 600 mg every 6 hours for a week, "
    "and come back if it isn't better in two weeks."
)
SUMMARY = (
    "Sprained knee. Allergic to penicillin. Motrin 800 mg every 6 hours for a week. "
    "Return if not improving in two weeks."
)

with GroundlineClient() as client:
    result = client.evaluate_commissions(
        CommissionsRequest(source=SOURCE, summary=SUMMARY)
    )

print("Flagged:", result.flagged)
for finding in result.findings:
    print(finding.verdict.value, finding.statement)
```

and the async version:

```python
import asyncio

from groundline_client import CommissionsRequest, GroundlineClient

SOURCE = (
    "Doctor: Any allergies? Patient: Penicillin, it gives me a rash. "
    "Doctor: Your knee is sprained. Take Motrin 600 mg every 6 hours for a week, "
    "and come back if it isn't better in two weeks."
)
SUMMARY = (
    "Sprained knee. Allergic to penicillin. Motrin 800 mg every 6 hours for a week. "
    "Return if not improving in two weeks."
)


async def main() -> None:
    async with GroundlineClient() as client:
        result = await client.evaluate_commissions_async(
            CommissionsRequest(source=SOURCE, summary=SUMMARY)
        )
    print("Flagged:", result.flagged)
    for finding in result.findings:
        print(finding.verdict.value, finding.statement)


asyncio.run(main())
```

If `health()` works but a check raises `ServerUnavailableError` (a 503), the
deployment is up and can't run checks. Its Anthropic API key is usually missing or
was rejected, which only the person who runs it can fix.

A check can take up to two minutes, because it makes several model calls. A
flagged summary is a normal result: read `flagged`, `score` and `findings`, and
show `metadata.disclaimer` next to them.

## Methods and connections

Each method has an `_async` twin (`await client.health_async()`). Use `async with`
if you call them: it closes both the sync and the async connections. A plain
`with` can only close the sync ones, and warns if an async connection is left
open. A client can't be used in a `with` block after it has made a call, or after
its block has ended, so make the calls inside the block.

`health()` and `ready()` send the API key too, though those two routes are public
and don't need it.

A method returns the parsed response model, or raises an error (see
[Errors](#errors)). A flagged summary is a normal result, not an error.

## Errors

Every error the SDK raises on purpose is a `GroundlineError`. Catch that to
handle all of them, or catch a subclass to decide what to do:

| Error | Raised for | What you can do |
| --- | --- | --- |
| `AuthenticationError` | a 401 | Fix the API key. |
| `AuthenticationError` | a 403 | Check any proxy or firewall in front of the API: the API itself never sends a 403. |
| `RateLimitError` | a 429 | Wait `error.retry_after` seconds (`None` if the answer gave none). |
| `InvalidRequestError` | a 400 or 422 | Fix the request; `error.detail` says where. |
| `RequestTooLargeError` | a 413 | Send less text. |
| `ServerUnavailableError` | a 500, 502 or 503, or the server couldn't be reached | Try again later. |
| `GroundlineTimeoutError` | a 504 or 408, or the call ran out of its time limits | Try again later, or raise the limits. |
| `GroundlineError` itself | Anything else: a redirect, a 404, a status the API doesn't document, or an answer the SDK can't read | Check the URL. |

`GroundlineSetupError` (a bad URL, key or option when you create the client) is
a `GroundlineError` too, and still a `ValueError`. An error that doesn't come
from the HTTP layer, such as one in your own code, passes through as it is.

### Handling a 429

The API itself never sends a 429. One comes from something in front of it, such
as a proxy or gateway with its own rate limit. The SDK has already retried it by
then (see [Retries and time limits](#retries-and-time-limits)), honoring a
`Retry-After` header, so a `RateLimitError` means it gave up. Wait
`error.retry_after` seconds before you call again, and use a pause of your own
when it is `None`:

<!-- fake: 429 -->
```python
from groundline_client import (
    GroundlineClient,
    GroundlineError,
    OmissionsRequest,
    RateLimitError,
)

SOURCE = "Doctor: Take Motrin 600 mg every 6 hours for a week."
SUMMARY = "Motrin 600 mg every 6 hours for a week."

try:
    with GroundlineClient() as client:
        result = client.evaluate_omissions(
            OmissionsRequest(source=SOURCE, summary=SUMMARY)
        )
except RateLimitError as error:
    wait = error.retry_after or 60
    print(f"Too many requests: try again in {wait} seconds")
except GroundlineError as error:
    print(error, error.status_code, error.detail)
```

Each error has `status_code` (`None` when there was no answer) and `detail`, the
server's own explanation when it sent one in the documented shape (`None`
otherwise, such as for a proxy's HTML page). `GroundlineError` here is a
different class from `groundline_core`'s, so catch the one from the package you
call.

An error's message, `repr` and `detail` are safe to log:

- The message is fixed wording plus the status, such as `The API refused the API
  key (status 401).` It never holds the source, the summary, the API key or the
  server's text.
- `detail` is cut to 250 characters, and anything shaped like an Anthropic key
  (`sk-ant-…`) or a groundline key (`gl_…`), or equal to your own API key (when it is 8 characters or more), is
  replaced with `<redacted key>`. Another kind of secret that a server echoes
  back would not be recognized.
- The `httpx2` error behind a timeout or connection failure is dropped instead
  of chained, because it carries the request, and the request holds your API
  key. The message says whether it was a timeout or a connection failure.

The parsed `ErrorBody` and `InvalidRequestBody` models are still exported, but
no method returns them anymore.

## Retries and time limits

A call retries when the API has a brief hiccup: a 408, 429, 502, 503 or 504
answer, a timeout, or a connection error. It never retries a 400, 401, 403, 413
or 422, because your request or key has to change, and it never retries a
flagged summary: that is a normal 200 result. After a failed attempt the call
waits a random time of up to 1 second, then 2, 4 and 8 seconds, and 8 from then
on. A `Retry-After` header sets the minimum wait. If the wait doesn't fit in the
time left, the call stops at once and raises the error for the last attempt.

You can change the limits when you create the client:

| Option | Default | What it limits |
| --- | --- | --- |
| `max_attempts` | 3 | Requests per call, counting the first. 1 turns retries off. |
| `max_retry_seconds` | 180 | The whole call, retries and waits included. |
| `timeout_seconds` | 150 | One attempt. |
| `connect_timeout_seconds` | 5 | Setting up the connection for one attempt. |

```python
from groundline_client import GroundlineClient

with GroundlineClient(max_attempts=1, timeout_seconds=60) as client:
    print(client.health()["status"])
```

Each attempt is also cut off at whatever is left of `max_retry_seconds`, and no
retry starts with less than `connect_timeout_seconds` left. So if
`connect_timeout_seconds` is as long as `max_retry_seconds` or longer, nothing
is retried. A value that isn't valid raises `GroundlineSetupError` when you
create the client: `max_attempts` must be a whole number of 1 or more, and the
other three a finite number above 0.

When the retries run out, the call raises the error for what the last attempt
gave (see [Errors](#errors)).

Things to know:

- **A retry can repeat paid work.** A 504 can arrive after the server spent up to
  120 seconds on model calls, and a retry runs the whole check again. A 503 is
  sometimes permanent, such as a server with no Anthropic API key, and is
  retried anyway. To turn retries off, use `max_attempts=1`.
- **Match the server's limit.** The server allows a check 120 seconds by default.
  If its operator raises `GROUNDLINE_CHECK_TIMEOUT_SECONDS`, raise
  `timeout_seconds` and `max_retry_seconds` too, or the client gives up first.
- **Sync calls are limited per step.** In a sync call, each step (waiting for a
  connection, connecting, sending, and each wait for reply data) is limited
  separately, so one attempt can take longer than `timeout_seconds`, and a server
  that keeps sending a few bytes at a time can outlast it. Async calls are cut off
  at exactly that time, and need `asyncio`: they don't work under `trio`.
- **Retries are logged.** Turn on `logging.basicConfig(level=logging.INFO)` to see
  one line per retry from the `groundline_client.retry` logger, such as
  `retry after attempt=1 status=503 wait_seconds=0.42`, or
  `retry after attempt=2 error=ReadTimeout wait_seconds=1.35` after a timeout. A
  line holds only the attempt, the status or the error's class, and the wait:
  never the URL, headers, a body or the API key.

## How it is built

Most of the code is generated from the API's OpenAPI description. It is in
`src/groundline_client/_generated/`, and isn't edited by hand. After changing
the API, run `make generate-sdk` from the repository root and commit the
result. See "The SDK" in
[DEVELOPMENT.md](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md).
