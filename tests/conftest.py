"""Suite-wide fixtures and the ``--shard`` selection option."""

from pathlib import Path
import re

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SHARD = re.compile(r"([1-9][0-9]*)/([1-9][0-9]*)")


def pytest_addoption(parser):
    parser.addoption(
        "--shard", default=None, metavar="INDEX/COUNT",
        help=("run only shard INDEX of COUNT (1-based): every COUNT-th test "
              "in collection order, after all other deselection"),
    )


def shard_items(items, value):
    """Split *items* into ``(selected, deselected)`` for ``INDEX/COUNT``.

    Round-robin over the collection order balances time better than whole
    files: one test module alone can take a fifth of the Windows lane.
    Every shard of one collection is disjoint, and together they cover it.
    """
    match = _SHARD.fullmatch(value)
    if match is None or int(match[1]) > int(match[2]):
        raise pytest.UsageError(
            "--shard must be INDEX/COUNT with 1 <= INDEX <= COUNT")
    index, count = int(match[1]) - 1, int(match[2])
    selected = [item for position, item in enumerate(items)
                if position % count == index]
    deselected = [item for position, item in enumerate(items)
                  if position % count != index]
    return selected, deselected


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    value = config.getoption("--shard")
    if value is None:
        return
    selected, deselected = shard_items(items, value)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


@pytest.fixture(scope="session")
def repository_inventory():
    """This repository's architecture inventory, built once per test session.

    A build analyzes every tracked Python source and takes tens of seconds.
    Tests that read the repository-wide inventory share this value and must
    not mutate it.
    """
    from tools import check_architecture_inventory

    return check_architecture_inventory.build_inventory(PROJECT_ROOT)
