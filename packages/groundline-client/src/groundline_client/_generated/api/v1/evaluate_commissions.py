from http import HTTPStatus
from typing import Any

import httpx2

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.commissions_request import CommissionsRequest
from ...models.commissions_response import CommissionsResponse
from ...models.error_body import ErrorBody
from ...models.invalid_request_body import InvalidRequestBody
from ...types import Response


def _get_kwargs(
    *,
    body: CommissionsRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/v1/commissions",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx2.Response
) -> CommissionsResponse | ErrorBody | InvalidRequestBody | None:
    if response.status_code == 200:
        response_200 = CommissionsResponse.from_dict(response.json())

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
) -> Response[CommissionsResponse | ErrorBody | InvalidRequestBody]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: CommissionsRequest,
) -> Response[CommissionsResponse | ErrorBody | InvalidRequestBody]:
    """Commissions

     Find the claims a summary makes that its source doesn't support, and
    whether to flag it.

    Runs a validated commission check (often called hallucination detection),
    chosen with `preset`: `commission_quick` by default, or
    `commission_reverse_lean`. The `preset` field says what each runs, how it was
    measured, what it costs and whether it is confirmed to its pre-registered
    standard.
    Each preset flags at its own threshold, and both answer in the same shape. A
    claim is a commission if the source contradicts it or never mentions it.
    Answers 200 for every finished check, flagged or not.

    Args:
        body (CommissionsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CommissionsResponse | ErrorBody | InvalidRequestBody]
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
    body: CommissionsRequest,
) -> CommissionsResponse | ErrorBody | InvalidRequestBody | None:
    """Commissions

     Find the claims a summary makes that its source doesn't support, and
    whether to flag it.

    Runs a validated commission check (often called hallucination detection),
    chosen with `preset`: `commission_quick` by default, or
    `commission_reverse_lean`. The `preset` field says what each runs, how it was
    measured, what it costs and whether it is confirmed to its pre-registered
    standard.
    Each preset flags at its own threshold, and both answer in the same shape. A
    claim is a commission if the source contradicts it or never mentions it.
    Answers 200 for every finished check, flagged or not.

    Args:
        body (CommissionsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CommissionsResponse | ErrorBody | InvalidRequestBody
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: CommissionsRequest,
) -> Response[CommissionsResponse | ErrorBody | InvalidRequestBody]:
    """Commissions

     Find the claims a summary makes that its source doesn't support, and
    whether to flag it.

    Runs a validated commission check (often called hallucination detection),
    chosen with `preset`: `commission_quick` by default, or
    `commission_reverse_lean`. The `preset` field says what each runs, how it was
    measured, what it costs and whether it is confirmed to its pre-registered
    standard.
    Each preset flags at its own threshold, and both answer in the same shape. A
    claim is a commission if the source contradicts it or never mentions it.
    Answers 200 for every finished check, flagged or not.

    Args:
        body (CommissionsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[CommissionsResponse | ErrorBody | InvalidRequestBody]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: CommissionsRequest,
) -> CommissionsResponse | ErrorBody | InvalidRequestBody | None:
    """Commissions

     Find the claims a summary makes that its source doesn't support, and
    whether to flag it.

    Runs a validated commission check (often called hallucination detection),
    chosen with `preset`: `commission_quick` by default, or
    `commission_reverse_lean`. The `preset` field says what each runs, how it was
    measured, what it costs and whether it is confirmed to its pre-registered
    standard.
    Each preset flags at its own threshold, and both answer in the same shape. A
    claim is a commission if the source contradicts it or never mentions it.
    Answers 200 for every finished check, flagged or not.

    Args:
        body (CommissionsRequest):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx2.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        CommissionsResponse | ErrorBody | InvalidRequestBody
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
