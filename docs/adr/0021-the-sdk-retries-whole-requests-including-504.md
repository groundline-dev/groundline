# The SDK retries whole requests, including 502, 503 and 504

A brief hiccup in the API shouldn't make every caller write retry code, so the SDK retries a 408, 429, 502, 503 or 504 answer, a timeout and a connection error, up to 3 attempts within 180 seconds. However, a retry sends the whole request again: a 504 can arrive after the server has already spent up to 120 seconds on paid model calls, and the repeat pays for them again, because nothing is stored to resume from (ADR 0009). We chose to retry anyway because a 503 is sometimes permanent but often not, and a caller that needs no repeats can set `max_attempts=1`. Each retry is logged on `groundline_client.retry`, so a repeat can be seen.

## Considered Options

- Retry only 429 and 503, and leave 502 and 504 to the caller: no repeated paid work after a gateway timeout, but every caller then has to write the retry code the SDK is meant to save them.
- Retry only a request that never reached the server (connection errors): the safest, but it misses the brief outages the ticket asked about.

## Consequences

- The SDK README warns that a retry can repeat paid work, and how to turn retries off.
- Reversing this later (retrying less) changes what callers see, so it needs a new decision.
