# Thresholds belong to the pipeline they were calibrated on

The alarm thresholds (strict 0.90, moderate 0.425) were fixed in advance on calibration data for the all-Opus pipeline, and only mean something for that pipeline. A numeric `--threshold 0.9` option would have been the obvious design. Instead, thresholds are named levels saved in the pipeline spec, and the CLI picks a level by name. A run counts as the validated preset only if its pipeline and profile match the preset exactly, apart from the chosen level. If a modified pipeline reuses the preset's thresholds, the run warns that they weren't calibrated for it. A different pipeline needs its own calibrated levels before its flags mean anything.

Update: the CLI and API no longer let callers choose a level. They run the preset's strict level until thresholds become configurable.
