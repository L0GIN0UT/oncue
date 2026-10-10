from __future__ import annotations

import pytest
from bff_web_service.main import DEFINITION
from bff_web_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import assert_problem, service_client

pytestmark = pytest.mark.component


async def test_meta_health_and_page_stubs() -> None:
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)
    try:
        async with service_client(create_app(DEFINITION, infra)) as client:
            assert (await client.get("/v1/meta")).json() == {"service": "bff_web", "status": "stub"}
            assert (await client.get("/health/live")).status_code == 200
            assert (await client.get("/health/ready")).status_code == 200
            for path in (
                "/v1/session",
                "/v1/pages/home",
                "/v1/pages/solo?domain=film",
                "/v1/pages/results/0192a3b4-c5d6-7e8f-9a0b-000000000700",
            ):
                assert_problem(await client.get(path), 501, "common.not_implemented")
    finally:
        await infra.close()
