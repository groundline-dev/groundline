"""Errors about the environment a pipeline runs in, rather than about a stage."""


class SetupError(Exception):
    """Something about the setup is wrong, such as a missing or rejected API key.

    The runner passes these on unchanged instead of reporting them as a stage
    failure, because the fix is in the environment, not the stage.
    """

    def api_message(self) -> str:
        """What an HTTP response may say about this error. By default, the whole
        message; an error that names something on the server, such as a file
        path, overrides it to leave that out."""
        return str(self)


class APIKeyRejectedError(SetupError):
    """The provider rejected the API key, or it lacks access to the model."""

    def __init__(self, message: str, api_message: str | None = None) -> None:
        super().__init__(message)
        self._api_message = api_message

    def api_message(self) -> str:
        return self._api_message or str(self)
