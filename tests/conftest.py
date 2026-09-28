"""Suite-wide fixtures."""

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repository_inventory():
    """This repository's architecture inventory, built once per test session.

    A build analyzes every tracked Python source and takes tens of seconds.
    Tests that read the repository-wide inventory share this value and must
    not mutate it.
    """
    from tools import check_architecture_inventory

    return check_architecture_inventory.build_inventory(PROJECT_ROOT)
