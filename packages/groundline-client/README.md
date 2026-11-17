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

If the URL or API key is missing, creating the client fails at once with a message
naming the setting. The client doesn't print the API key in its `repr` or its
own error messages, and its tests check that the key stays out of tracebacks
and of the logs of the libraries it uses.

Each method has an `_async` twin (`await client.health_async()`; use
`async with` to close the connections). A method returns the parsed response
model. When the API answers with an error status, it returns the parsed
`ErrorBody` instead of raising, and an answer the API doesn't document comes
back as `None`. Typed exceptions are planned (ALL-158). There are no retries or
time limits yet (ALL-157), so a call to an unresponsive server can wait
forever.

## How it is built

Most of the code is generated from the API's OpenAPI description. It is in
`src/groundline_client/_generated/`, and isn't edited by hand. After changing
the API, run `make generate-sdk` from the repository root and commit the
result. See "The SDK" in
[DEVELOPMENT.md](https://github.com/groundline-dev/groundline/blob/main/DEVELOPMENT.md).
