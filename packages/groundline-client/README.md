# groundline-client

The Python SDK for the groundline API: one `GroundlineClient` with a method per
endpoint, typed requests and responses, sync and async.

## Connect

Give the client your deployment's URL and an API key. Either set the two
environment variables:

```sh
export GROUNDLINE_API_URL="https://groundline.example.com"
export GROUNDLINE_API_KEY="..."
```

```python
from groundline_client import GroundlineClient, OmissionsRequest

with GroundlineClient() as client:
    print(client.health())
    result = client.evaluate_omissions(OmissionsRequest(source="...", summary="..."))
```

or pass them as arguments, which win over the environment variables:

```python
client = GroundlineClient(base_url="https://groundline.example.com", api_key=key)
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
| `AuthenticationError` | 401, 403 | Fix the API key. |
| `RateLimitError` | 429 | Wait `error.retry_after` seconds (`None` if the answer gave none). |
| `InvalidRequestError` | 400, 422 | Fix the request; `error.detail` says where. |
| `RequestTooLargeError` | 413 | Send less text. |
| `ServerUnavailableError` | 500, 502, 503, or the server couldn't be reached | Try again later. |
| `GroundlineTimeoutError` | 504, 408, or the call ran out of its time limits | Try again later, or raise the limits. |
| `GroundlineError` itself | Anything else: a redirect, a 404, a status the API doesn't document, or an answer the SDK can't read | Check the URL. |

`GroundlineConfigError` (a bad URL, key or option when you create the client) is
a `GroundlineError` too, and still a `ValueError`. An error that doesn't come
from the HTTP layer, such as one in your own code, passes through as it is.

```python
from groundline_client import (
    GroundlineClient,
    GroundlineError,
    OmissionsRequest,
    RateLimitError,
)

try:
    with GroundlineClient() as client:
        result = client.evaluate_omissions(
            OmissionsRequest(source="...", summary="...")
        )
except RateLimitError as error:
    print(f"Too many requests: wait {error.retry_after} seconds")
except GroundlineError as error:
    print(error, error.status_code, error.detail)
```

Each error has `status_code` (`None` when there was no answer) and `detail`, the
server's own explanation when it sent one in the documented shape (`None`
otherwise, such as for a proxy's HTML page). `GroundlineError` here is a
different class from `groundline_core`'s, so catch the one from the package you
call.

An error is safe to log or show to anyone:

- The message is fixed wording plus the status, such as `The API refused the API
  key (status 401).` It never holds the source, the summary, the API key or the
  server's text.
- `detail` is cut to 250 characters, and anything shaped like an Anthropic key
  (`sk-ant-…`) or a groundline key (`gl_…`), or equal to your own API key (when it is 8 characters or more), is
  replaced with `<redacted key>`. Another kind of secret that a server echoes
  back would not be recognised.
- The `httpx2` error behind a timeout or connection failure is dropped instead
  of chained, because it carries the request, and the request holds your API
  key. The message says whether it was a timeout or a connection failure.

The parsed `ErrorBody` and `InvalidRequestBody` models are still exported, but
no method returns them any more.

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
client = GroundlineClient(max_attempts=1, timeout_seconds=60)
```

Each attempt is also cut off at whatever is left of `max_retry_seconds`, and no
retry starts with less than `connect_timeout_seconds` left. So if
`connect_timeout_seconds` is as long as `max_retry_seconds` or longer, nothing
is retried. A value that isn't valid raises `GroundlineConfigError` when you
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
- **Sync calls are limited per step.** A sync attempt is limited by `httpx2`'s
  connect, send and wait-for-reply timeouts, each set to the time left, so it can
  run up to that time plus `connect_timeout_seconds`. A server that keeps sending
  a few bytes at a time can outlast `timeout_seconds` in a sync call. Async calls
  are cut off at exactly that time, and need `asyncio`: they don't work under
  `trio`.
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
