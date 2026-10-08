from __future__ import annotations

import pytest
from collections_service.main import DEFINITION
from collections_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import service_client

pytestmark = pytest.mark.component


async def test_meta_and_health() -> None:
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)
    try:
        async with service_client(create_app(DEFINITION, infra)) as client:
            assert (await client.get("/v1/meta")).json() == {
                "service": "collections",
                "status": "stub",
            }
            assert (await client.get("/health/live")).status_code == 200
            assert (await client.get("/health/ready")).status_code == 200
    finally:
        await infra.close()
