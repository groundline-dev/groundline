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

A method returns the parsed response model. When the API answers with an error
status, it returns the parsed `ErrorBody` instead of raising, and an answer the
API doesn't document comes back as `None`. Typed exceptions are planned. There
are no retries or time limits yet, so a call to an unresponsive server can wait
forever.

## How it is built

Most of the code is generated from the API's OpenAPI description. It is in
`src/groundline_client/_generated/`, and isn't edited by hand. After changing
the API, run `make generate-sdk` from the repository root and commit the
result. See "The SDK" in
[DEVELOPMENT.md](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md).
