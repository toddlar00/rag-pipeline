"""Additive evidence adapter; reuses the existing app's auth and lifecycle.

The outer composition root injects the base adapter's bounded body and reader
dependency.  This module never imports the concrete host, base HTTP adapter,
pipeline facade or composition root.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Request

import service_contracts
import service_evidence_contracts


@dataclass(frozen=True, slots=True)
class EvidenceHttpBinding:
    require_reader: Callable
    bounded_json_body: Callable

    def __post_init__(self) -> None:
        if not callable(self.require_reader) or not callable(self.bounded_json_body):
            raise TypeError("invalid evidence HTTP binding")


def install_evidence_routes(app: FastAPI, runtime: object, *,
                            binding: EvidenceHttpBinding) -> None:
    """Mount only on an explicitly opted-in, already constructed service app."""
    if (not isinstance(binding, EvidenceHttpBinding)
            or not callable(getattr(runtime, "search_evidence", None))
            or not callable(getattr(runtime, "mark_unhealthy", None))):
        raise TypeError("invalid evidence HTTP capability")
    route = "/evidence/v1/corpora/{corpus_id}/search"
    if any(getattr(item, "path", None) == route for item in app.routes):
        raise ValueError("evidence routes already installed")

    @app.post(route, dependencies=[Depends(binding.require_reader)])
    async def search_evidence(corpus_id: str, request: Request):
        if request.url.query:
            raise service_contracts.ServiceContractError()
        body = await binding.bounded_json_body(request)
        search_request = service_contracts.parse_search_request(body)
        result = await asyncio.to_thread(
            runtime.search_evidence, corpus_id, search_request, request.state.request_id)
        try:
            return service_evidence_contracts.validate_public_evidence_search_response(
                result, expected_corpus_id=corpus_id,
                expected_request_id=request.state.request_id, expected_request=search_request)
        except service_contracts.ServiceContractError:
            runtime.mark_unhealthy()
            raise service_contracts.ServiceContractError("service_unavailable") from None
