# A stage framework from the start, not a fixed pipeline

The all-Opus omission check is four model calls in a fixed order, and the simplest port would have been one function. We built it instead as steps with interchangeable implementations, assembled by a pipeline spec and run by a runner that starts each stage once the stages it reads have finished. A UI pipeline builder is planned, in which users switch stages on or off, choose an implementation for each (such as a cheaper model for a first pass), and add new steps. Several evaluators, such as omissions and commissions, should also run on one summary in parallel. Splitting a fixed pipeline later would have meant reworking every stage, its tests, and the cache, so we took the extra framework code now.

## Consequences

- Every implementation of a step must produce that step's output type, so later stages work whichever implementation ran.
- A spec is validated before any model call, and a stage may only read steps that come before it. That rules out cycles without a separate check.
