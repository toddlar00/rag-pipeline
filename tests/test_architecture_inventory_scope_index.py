"""The per-scope node index equals filtering the whole scope map.

``_ScopeMap.nodes_in`` replaces a scan of every node in the module for each
scope; it must return exactly the nodes that scan selected, in the same
order, for every kind of scope.  All source is synthetic.
"""

import ast

from tools import check_architecture_inventory as inventory


SOURCE = '''
import os as operating
from json import loads

GLOBAL = [value for value in range(3) if (seen := value)]


def outer(first, *rest, key=None, **options):
    global GLOBAL
    local = {name: len(name) for name in rest}

    def inner(second):
        nonlocal local
        try:
            return second
        except ValueError as problem:
            return problem

    class Nested:
        attribute = lambda item: item
        generator = (entry for entry in local)

    return inner, Nested


async def later(stream):
    async for chunk in stream:
        yield (total := chunk)
'''


def test_nodes_in_matches_the_full_scope_scan_for_every_scope():
    scope_map = inventory._ScopeMap(ast.parse(SOURCE))

    for scope in scope_map.scopes:
        expected = [
            node for node, owner in scope_map.node_scope.items()
            if owner is scope]
        assert scope_map.nodes_in(scope) == expected
    assert len(scope_map.scopes) >= 8
    assert scope_map.nodes_in(ast.Pass()) == []
