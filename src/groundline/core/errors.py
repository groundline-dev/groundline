"""Errors about the environment a pipeline runs in, rather than about a stage."""


class SetupError(Exception):
    """Something about the setup is wrong, such as a missing or rejected API key.

    The runner passes these on unchanged instead of reporting them as a stage
    failure, because the fix is in the environment, not the stage.
    """


class APIKeyRejectedError(SetupError):
    """The provider rejected the API key, or it lacks access to the model."""
