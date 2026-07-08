"""Shared pytest fixtures for docker-touching integration tests.

The ``docker_available`` fixture pings the daemon once per session; tests that
need docker should depend on it, and any test module can add
``pytestmark = pytest.mark.integration`` to gate itself behind the
``-m integration`` selector.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import aiodocker
import aiodocker.exceptions
import pytest
import pytest_asyncio

from forge.config import ForgeConfig, make_config
from forge.drivers.docker_driver import DockerDriver

FORGE_TEST_IMAGE = os.environ.get("FORGE_TEST_IMAGE", "python:3.14-slim")


@pytest_asyncio.fixture(scope="session")
async def _docker_probe() -> AsyncIterator[bool]:
    """Ping the docker daemon; yield True if reachable, False otherwise."""
    client = aiodocker.Docker()
    try:
        try:
            await client.version()
            yield True
        except aiodocker.exceptions.DockerError:
            yield False
        except Exception:
            yield False
    finally:
        await client.close()


@pytest_asyncio.fixture
async def docker_available(_docker_probe: bool) -> bool:
    if not _docker_probe:
        pytest.skip("docker daemon not reachable")
    return True


@pytest_asyncio.fixture
async def docker_client(docker_available: bool) -> AsyncIterator[aiodocker.Docker]:
    client = aiodocker.Docker()
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def _pull_test_image(docker_client: aiodocker.Docker) -> None:
    """Ensure the test image is present before per-test drivers start."""
    try:
        await docker_client.images.inspect(FORGE_TEST_IMAGE)
    except aiodocker.exceptions.DockerError:
        await docker_client.images.pull(FORGE_TEST_IMAGE)


@pytest.fixture
def forge_config(tmp_path: Path) -> ForgeConfig:
    return make_config(tmp_path)


@pytest_asyncio.fixture
async def docker_driver(
    _pull_test_image: None,
) -> AsyncIterator[DockerDriver]:
    driver = DockerDriver()
    try:
        yield driver
    finally:
        await driver.close()
