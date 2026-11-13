"""Where the groundline-core command starts.

It loads the command line in `cli` only here, so that an install without the
cli extra gets one line saying what to install instead of a traceback. Any
other failure to load still shows its traceback. Either way it exits 2, as
every error from these commands does, so a script never reads a failure as a
flagged summary (ADR 0005).
"""

import sys
import traceback

from groundline_core.extras import CLI_MESSAGE, CLI_PACKAGES, is_missing_one_of

EXIT_ERROR = 2
"""The same as `cli.EXIT_ERROR`, which can't be imported here: loading `cli`
is what may fail."""


def main() -> None:
    try:
        from groundline_core.cli import app
    except Exception as error:
        if isinstance(error, ModuleNotFoundError) and is_missing_one_of(
            error, CLI_PACKAGES
        ):
            print(CLI_MESSAGE, file=sys.stderr)
        else:
            traceback.print_exc()
        sys.exit(EXIT_ERROR)
    app()
