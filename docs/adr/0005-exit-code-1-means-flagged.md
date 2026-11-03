# Exit code 1 means flagged, and nothing else

Following ruff and pytest, the `groundline omissions` exit code reports the outcome: 0 means the check finished and the summary wasn't flagged, 1 means it finished and the summary was flagged, and 2 means the check couldn't finish. Scripts and CI jobs can act on the result without parsing output, so this is a public interface that is hard to change later. Python's own exit code for an uncaught error is 1, so a crash would read as a flag. The command therefore turns every error, including unexpected ones, into 2.
