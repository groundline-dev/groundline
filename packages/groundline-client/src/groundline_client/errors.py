"""The errors the SDK raises itself."""


class GroundlineConfigError(ValueError):
    """The URL or API key needed to create a client is missing or unusable.

    The message says which setting is wrong and how to set it. It never
    contains the value, because the value may be the API key.
    """
