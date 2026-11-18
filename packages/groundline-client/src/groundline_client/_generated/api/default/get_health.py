from http import HTTPStatus
from typing import Any

import httpx2

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_body import ErrorBody
from ...models.get_health_response_get_health import GetHealthResponseGetHealth
from ...types import Response


def _get_kwargs() -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/health",
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx2.Response
) -> ErrorBody | GetHealthResponseGetHealth | None:
    if response.status_code == 200:
        response_200 = GetHealthResponseGetHealth.from_dict(response.json())

        return response_200

    if response.status_code == 413:
        response_413 = ErrorBody.from_dict(response.json())

        return response_413

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx2.Response
) -> Response[ErrorBody | GetHealthResponseGetHealth]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorBody | GetHealthResponseGetHealth]:
    """Health

     Whether the app is running. It never touches the database, so an outage
    doesn't mark a working app unhealthy in Compose.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorBody | GetHealthResponseGetHealth]
    """

    kwargs = _get_kwargs()

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
) -> ErrorBody | GetHealthResponseGetHealth | None:
    """Health

     Whether the app is running. It never touches the database, so an outage
    doesn't mark a working app unhealthy in Compose.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorBody | GetHealthResponseGetHealth
    """

    return sync_detailed(
        client=client,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
) -> Response[ErrorBody | GetHealthResponseGetHealth]:
    """Health

     Whether the app is running. It never touches the database, so an outage
    doesn't mark a working app unhealthy in Compose.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorBody | GetHealthResponseGetHealth]
    """

    kwargs = _get_kwargs()

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
) -> ErrorBody | GetHealthResponseGetHealth | None:
    """Health

     Whether the app is running. It never touches the database, so an outage
    doesn't mark a working app unhealthy in Compose.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorBody | GetHealthResponseGetHealth
    """

    return (
        await asyncio_detailed(
            client=client,
        )
    ).parsed
