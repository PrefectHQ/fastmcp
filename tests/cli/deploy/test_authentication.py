from __future__ import annotations

import httpx2
import pytest

from fastmcp.cli.deploy.authentication import (
    DeviceAuthorizationDeniedError,
    DeviceAuthorizationExpiredError,
    authorize_device,
    poll_device_authorization,
)
from fastmcp.cli.deploy.horizon_client import DeviceAuthorization, HorizonClient


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay


def authorization(*, expires_in: int = 600, interval: int = 5) -> DeviceAuthorization:
    return DeviceAuthorization(
        device_code="device-secret",
        user_code="BCDF-GHJK",
        verification_uri="https://horizon.prefect.io/oauth/device",
        verification_uri_complete=(
            "https://horizon.prefect.io/oauth/device?user_code=BCDF-GHJK"
        ),
        expires_in=expires_in,
        interval=interval,
    )


def sequenced_transport(
    responses: list[httpx2.Response],
) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return responses.pop(0)

    return httpx2.MockTransport(handler)


async def test_polling_handles_pending_slow_down_and_approval() -> None:
    clock = Clock()
    async with HorizonClient(
        transport=sequenced_transport(
            [
                httpx2.Response(400, json={"error": "authorization_pending"}),
                httpx2.Response(400, json={"error": "slow_down"}),
                httpx2.Response(
                    200,
                    json={"access_token": "fmcp_secret", "token_type": "Bearer"},
                ),
            ]
        )
    ) as client:
        api_key = await poll_device_authorization(
            client,
            authorization(),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

    assert api_key.get_secret_value() == "fmcp_secret"
    assert clock.sleeps == [5, 5, 10]


@pytest.mark.parametrize(
    ("error", "exception"),
    [
        ("access_denied", DeviceAuthorizationDeniedError),
        ("expired_token", DeviceAuthorizationExpiredError),
    ],
)
async def test_polling_handles_terminal_errors(
    error: str,
    exception: type[Exception],
) -> None:
    clock = Clock()
    async with HorizonClient(
        transport=sequenced_transport([httpx2.Response(400, json={"error": error})])
    ) as client:
        with pytest.raises(exception):
            await poll_device_authorization(
                client,
                authorization(),
                sleep=clock.sleep,
                monotonic=clock.monotonic,
            )


async def test_polling_stops_at_the_local_expiry_deadline() -> None:
    clock = Clock()
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(400, json={"error": "authorization_pending"})

    async with HorizonClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(DeviceAuthorizationExpiredError):
            await poll_device_authorization(
                client,
                authorization(expires_in=5, interval=5),
                sleep=clock.sleep,
                monotonic=clock.monotonic,
            )

    assert requests == []


async def test_authorize_device_presents_challenge_before_opening_browser() -> None:
    events: list[str] = []
    clock = Clock()

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/authorization"):
            return httpx2.Response(200, json=authorization().model_dump())
        return httpx2.Response(
            200,
            json={"access_token": "fmcp_secret", "token_type": "Bearer"},
        )

    def present(challenge: DeviceAuthorization) -> None:
        events.append(f"present:{challenge.user_code}")

    def open_browser(url: str) -> None:
        events.append(f"browser:{url}")

    async with HorizonClient(transport=httpx2.MockTransport(handler)) as client:
        await authorize_device(
            client,
            on_challenge=present,
            open_browser=True,
            browser_opener=open_browser,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

    assert events == [
        "present:BCDF-GHJK",
        "browser:https://horizon.prefect.io/oauth/device?user_code=BCDF-GHJK",
    ]


async def test_browser_failure_does_not_stop_remote_login() -> None:
    clock = Clock()

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/authorization"):
            return httpx2.Response(200, json=authorization().model_dump())
        return httpx2.Response(
            200,
            json={"access_token": "fmcp_secret", "token_type": "Bearer"},
        )

    def fail_to_open(url: str) -> None:
        raise OSError("no browser")

    async with HorizonClient(transport=httpx2.MockTransport(handler)) as client:
        api_key = await authorize_device(
            client,
            open_browser=True,
            browser_opener=fail_to_open,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )

    assert api_key.get_secret_value() == "fmcp_secret"
