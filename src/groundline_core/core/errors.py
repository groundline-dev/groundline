"""Errors about the environment a pipeline runs in, rather than about a stage."""


class SetupError(Exception):
    """Something about the setup is wrong, such as a missing or rejected API key.

    The runner passes these on unchanged instead of reporting them as a stage
    failure, because the fix is in the environment, not the stage.

    Every one has two texts: `message` for the CLI and the server log, which may
    name files on the server, and `api_message` for an HTTP response, which must
    not. When both are the same, pass the same text twice.
    """

    def __init__(self, message: str, api_message: str) -> None:
        super().__init__(message)
        self._api_message = api_message

    def api_message(self) -> str:
        """What an HTTP response may say about this error."""
        return self._api_message


class APIKeyRejectedError(SetupError):
    """The provider rejected the API key, or it lacks access to the model."""
