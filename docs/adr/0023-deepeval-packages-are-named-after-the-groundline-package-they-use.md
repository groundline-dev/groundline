# DeepEval packages are named after the groundline package they use

The DeepEval integration that calls the API through the SDK is published as `groundline-deepeval-client`, after `groundline-client`, and an adapter built on the library would be `groundline-deepeval-core`, after `groundline-core` (ADR 0016). Each name then tells users whether it installs only the SDK or the whole pipeline, and leaves room for both, at the cost of a longer name than `groundline-deepeval`. A published name can't change without breaking every install, so it is settled before the first release.
