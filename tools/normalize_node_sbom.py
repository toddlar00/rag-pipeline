"""Normalize an npm-generated CycloneDX SBOM into deterministic bytes.

npm sbom output embeds a random serial number and a generation timestamp,
so raw output is never byte-stable. This tool strips exactly those two
volatile fields, orders every collection deterministically, and emits
canonical JSON so identical inputs, tool, and build epoch always produce
identical bytes. The npm tool identity inside metadata.tools is retained:
it is part of the binding, not volatility.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _load_strict_json(path: Path, context: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read {context}: {exc}") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON number {token}")
            ),
        )
    except ValueError as exc:
        raise ValueError(f"{context} is not strict JSON: {exc}") from exc


def normalize(document: Any) -> dict[str, Any]:
    """Return the normalized SBOM document, failing closed on surprises."""
    if not isinstance(document, dict):
        raise ValueError("SBOM must be a JSON object")
    if document.get("bomFormat") != "CycloneDX":
        raise ValueError("SBOM must use bomFormat CycloneDX")
    spec_version = document.get("specVersion")
    if not isinstance(spec_version, str) or not spec_version:
        raise ValueError("SBOM must carry a non-empty specVersion")
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("SBOM must carry a metadata object")
    tools = metadata.get("tools")
    if (
        not isinstance(tools, list)
        or not tools
        or any(
            not isinstance(tool, dict)
            or not isinstance(tool.get("name"), str)
            or not isinstance(tool.get("version"), str)
            for tool in tools
        )
    ):
        raise ValueError("SBOM metadata must identify the generating tool")
    components = document.get("components")
    dependencies = document.get("dependencies")
    if not isinstance(components, list) or not isinstance(dependencies, list):
        raise ValueError("SBOM must carry components and dependencies lists")

    normalized = {
        key: value
        for key, value in document.items()
        if key != "serialNumber"
    }
    normalized["metadata"] = {
        key: value for key, value in metadata.items() if key != "timestamp"
    }

    refs: set[str] = set()
    for component in components:
        if not isinstance(component, dict) or not isinstance(
            component.get("bom-ref"), str
        ):
            raise ValueError("every SBOM component must carry a bom-ref")
        if component["bom-ref"] in refs:
            raise ValueError(
                f"duplicate component bom-ref {component['bom-ref']!r}"
            )
        refs.add(component["bom-ref"])
    normalized["components"] = sorted(
        components, key=lambda item: item["bom-ref"]
    )

    seen_dependency_refs: set[str] = set()
    ordered_dependencies = []
    for dependency in dependencies:
        if not isinstance(dependency, dict) or not isinstance(
            dependency.get("ref"), str
        ):
            raise ValueError("every SBOM dependency must carry a ref")
        if dependency["ref"] in seen_dependency_refs:
            raise ValueError(
                f"duplicate dependency ref {dependency['ref']!r}"
            )
        seen_dependency_refs.add(dependency["ref"])
        entry = dict(dependency)
        depends_on = entry.get("dependsOn")
        if depends_on is not None:
            if not isinstance(depends_on, list) or any(
                not isinstance(item, str) for item in depends_on
            ):
                raise ValueError(
                    f"dependency {entry['ref']!r} has an invalid dependsOn"
                )
            entry["dependsOn"] = sorted(depends_on)
        ordered_dependencies.append(entry)
    normalized["dependencies"] = sorted(
        ordered_dependencies, key=lambda item: item["ref"]
    )
    return normalized


def serialized_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sbom", type=Path, help="raw npm sbom JSON output")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = _load_strict_json(args.sbom, "npm SBOM")
        normalized = normalize(document)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    payload = serialized_bytes(normalized)
    args.output.write_bytes(payload)
    print(
        "Normalized Node SBOM: "
        f"{len(normalized['components'])} components, {len(payload)} bytes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
