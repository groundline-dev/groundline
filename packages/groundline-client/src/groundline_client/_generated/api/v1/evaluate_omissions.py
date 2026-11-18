from http import HTTPStatus
from typing import Any

import httpx2

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.error_body import ErrorBody
from ...models.invalid_request_body import InvalidRequestBody
from ...models.omissions_request import OmissionsRequest
from ...models.omissions_response import OmissionsResponse
from ...types import Response


def _get_kwargs(
    *,
    body: OmissionsRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/v1/omissions",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx2.Response
) -> ErrorBody | InvalidRequestBody | OmissionsResponse | None:
    if response.status_code == 200:
        response_200 = OmissionsResponse.from_dict(response.json())

        return response_200

    if response.status_code == 400:
        response_400 = ErrorBody.from_dict(response.json())

        return response_400

    if response.status_code == 401:
        response_401 = ErrorBody.from_dict(response.json())

        return response_401

    if response.status_code == 413:
        response_413 = ErrorBody.from_dict(response.json())

        return response_413

    if response.status_code == 422:
        response_422 = InvalidRequestBody.from_dict(response.json())

        return response_422

    if response.status_code == 500:
        response_500 = ErrorBody.from_dict(response.json())

        return response_500

    if response.status_code == 502:
        response_502 = ErrorBody.from_dict(response.json())

        return response_502

    if response.status_code == 503:
        response_503 = ErrorBody.from_dict(response.json())

        return response_503

    if response.status_code == 504:
        response_504 = ErrorBody.from_dict(response.json())

        return response_504

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx2.Response
) -> Response[ErrorBody | InvalidRequestBody | OmissionsResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: OmissionsRequest,
) -> Response[ErrorBody | InvalidRequestBody | OmissionsResponse]:
    """Omissions

     Find the facts a summary leaves out, and whether to flag it.

    Runs the validated all-Opus omission check, which costs about $0.21 for a
    typical clinical note. Set the client timeout above the configured
    whole-check limit, which defaults to 120 seconds, to allow time for request
    and response transfer. Answers 200 for every finished check, flagged or
    not.

    Args:
        body (OmissionsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorBody | InvalidRequestBody | OmissionsResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    body: OmissionsRequest,
) -> ErrorBody | InvalidRequestBody | OmissionsResponse | None:
    """Omissions

     Find the facts a summary leaves out, and whether to flag it.

    Runs the validated all-Opus omission check, which costs about $0.21 for a
    typical clinical note. Set the client timeout above the configured
    whole-check limit, which defaults to 120 seconds, to allow time for request
    and response transfer. Answers 200 for every finished check, flagged or
    not.

    Args:
        body (OmissionsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorBody | InvalidRequestBody | OmissionsResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: OmissionsRequest,
) -> Response[ErrorBody | InvalidRequestBody | OmissionsResponse]:
    """Omissions

     Find the facts a summary leaves out, and whether to flag it.

    Runs the validated all-Opus omission check, which costs about $0.21 for a
    typical clinical note. Set the client timeout above the configured
    whole-check limit, which defaults to 120 seconds, to allow time for request
    and response transfer. Answers 200 for every finished check, flagged or
    not.

    Args:
        body (OmissionsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ErrorBody | InvalidRequestBody | OmissionsResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: OmissionsRequest,
) -> ErrorBody | InvalidRequestBody | OmissionsResponse | None:
    """Omissions

     Find the facts a summary leaves out, and whether to flag it.

    Runs the validated all-Opus omission check, which costs about $0.21 for a
    typical clinical note. Set the client timeout above the configured
    whole-check limit, which defaults to 120 seconds, to allow time for request
    and response transfer. Answers 200 for every finished check, flagged or
    not.

    Args:
        body (OmissionsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ErrorBody | InvalidRequestBody | OmissionsResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
