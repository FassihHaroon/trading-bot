"""Shared test fixtures and configuration."""

import pytest
from config.settings import AgentConfig


@pytest.fixture(scope="session")
def base_config():
    return AgentConfig()
