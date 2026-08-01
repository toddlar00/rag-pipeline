"""Evaluation query parsing and corpus-binding domain contracts.

This module owns query schema validation shared by the evaluator and owner
review.  It deliberately does not import either application, release policy,
runtime orchestration, model policy, or physical vector clients.
"""

import math
import re

import evaluation_contract
import retrieval_core
import table_retrieval_core


GROUNDING_CASE_SCHEMA_VERSION = 2
TABLE_FAMILY_JUDGMENT_SCHEMA_VERSION = (
    evaluation_contract.TABLE_FAMILY_JUDGMENT_SCHEMA_VERSION)
_JUDGMENT_ID_FIELDS = ("chunk_id", "source_id")
_ALLOWED_FILTER_FIELDS = ("content_type", "chapter_num")
_REVIEW_STATUSES = frozenset({"approved", "draft_requires_corpus_owner"})


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _validate_declared_corpus_snapshot(
        queries: list[dict], *, actual_hash: str, actual_count: int) -> None:
    """Validate declarations against one already-consumed corpus snapshot."""
    declarations = [
        query["corpus"] for query in queries
        if isinstance(query.get("corpus"), dict)
    ]
    if not declarations:
        return
    expected_hashes = {
        item["sha256"].lower()
        for item in declarations if item.get("sha256")
    }
    expected_counts = {
        item.get("record_count") for item in declarations
        if item.get("record_count") is not None
    }
    if len(expected_hashes) > 1 or len(expected_counts) > 1:
        raise ValueError("Evaluation queries declare inconsistent corpus snapshots")
    if expected_hashes:
        expected_hash = next(iter(expected_hashes))
        if actual_hash != expected_hash:
            raise ValueError(
                "Judged queries target a different chunks snapshot: "
                f"expected SHA-256 {expected_hash}, got {actual_hash}")
    if expected_counts:
        expected_count = next(iter(expected_counts))
        if actual_count != expected_count:
            raise ValueError(
                "Judged queries target a different record count: "
                f"expected {expected_count}, got {actual_count}")


def _validate_corpus_pin_coverage(
        queries: list[dict], *, required: bool = False) -> None:
    """Require one exact SHA/count declaration for every query in a pinned set."""
    declarations = [query.get("corpus") for query in queries]
    if not required and not any(isinstance(item, dict)
                                for item in declarations):
        return
    incomplete = [
        index for index, item in enumerate(declarations, 1)
        if not isinstance(item, dict)
        or not isinstance(item.get("sha256"), str)
        or len(item["sha256"]) != 64
        or any(character not in "0123456789abcdefABCDEF"
               for character in item["sha256"])
        or type(item.get("record_count")) is not int
        or item["record_count"] < 1
    ]
    if incomplete:
        examples = ", ".join(str(index) for index in incomplete[:3])
        raise ValueError(
            "Corpus-pinned evaluation requires every query to declare the "
            "exact corpus SHA-256 and record count; incomplete query numbers: "
            + examples)


def _judgment_satisfying_chunk_ids(judgment: dict) -> tuple[str, ...]:
    """Return the canonical chunk and explicitly reviewed child aliases."""
    if "chunk_id" not in judgment:
        return ()
    result = [judgment["chunk_id"].strip()]
    family = judgment.get("table_family")
    if isinstance(family, dict):
        result.extend(family.get("accepted_child_chunk_ids", []))
    return tuple(result)


def _has_table_family_judgments(queries: list[dict]) -> bool:
    return any(
        isinstance(judgment, dict) and "table_family" in judgment
        for query in queries for judgment in query.get("judgments", [])
    )


def _validate_attested_table_family_judgments(
        queries: list[dict],
        attestation: evaluation_contract.TableFamilyAttestation | None,
) -> None:
    """Require every selected child alias to belong to its declared parent."""
    if not _has_table_family_judgments(queries):
        return
    if not isinstance(attestation, evaluation_contract.TableFamilyAttestation):
        raise ValueError(
            "Table-family judgments require an attested chunks artifact")
    for query_index, query in enumerate(queries, 1):
        corpus = query.get("corpus") or {}
        if (
            corpus.get("sha256") != attestation.corpus_sha256
            or corpus.get("record_count") != attestation.corpus_record_count
            or corpus.get("id_scheme") != attestation.id_scheme
        ):
            raise ValueError(
                "Table-family attestation does not match the exact query "
                f"corpus binding: query #{query_index}")
    family_members = attestation.members
    for query_index, query in enumerate(queries, 1):
        for judgment_index, judgment in enumerate(
                query.get("judgments", []), 1):
            family = judgment.get("table_family")
            if family is None:
                continue
            parent_id = judgment["chunk_id"].strip()
            members = family_members.get(parent_id)
            if not isinstance(members, frozenset) or parent_id not in members:
                raise ValueError(
                    "Table-family judgment parent is not an attested table "
                    f"parent: query #{query_index} judgment #{judgment_index} "
                    f"{parent_id}")
            accepted = set(family["accepted_child_chunk_ids"])
            invalid = accepted - (set(members) - {parent_id})
            if invalid:
                examples = ", ".join(sorted(invalid)[:3])
                raise ValueError(
                    "Table-family judgment children do not belong to the "
                    f"declared parent: query #{query_index} judgment "
                    f"#{judgment_index}: {examples}")


def _validate_judged_ids(
        queries: list[dict], records: list[dict], chunk_id_fn, *,
        corpus_sha256: str | None = None,
        id_scheme: str = "retrieval_core._chunk_id",
) -> evaluation_contract.TableFamilyAttestation | None:
    known_chunks = {chunk_id_fn(record) for record in records}
    known_sources = {
        str(value).strip()
        for record in records
        for value in (
            record.get("metadata", {}).get("source_id"),
            record.get("metadata", {}).get("source_file"),
        )
        if value not in (None, "")
    }
    judged_chunks = {
        chunk_id
        for query in queries for judgment in query.get("judgments", [])
        for chunk_id in _judgment_satisfying_chunk_ids(judgment)
    }
    judged_sources = {
        judgment["source_id"].strip()
        for query in queries for judgment in query.get("judgments", [])
        if "source_id" in judgment
    }
    unknown = (judged_chunks - known_chunks) | (judged_sources - known_sources)
    if unknown:
        examples = ", ".join(sorted(unknown)[:3])
        raise ValueError(
            "Judged IDs are absent from the declared chunks artifact: "
            + examples)
    attestation = None
    if _has_table_family_judgments(queries):
        if corpus_sha256 is None:
            raise ValueError(
                "Table-family validation requires the exact corpus SHA-256")
        attestation = evaluation_contract.attest_table_families(
            records, stable_id_fn=chunk_id_fn,
            corpus_sha256=corpus_sha256, id_scheme=id_scheme)
    _validate_attested_table_family_judgments(queries, attestation)
    return attestation


def _model_visible_string_contains(value, marker: str) -> bool:
    """Search payload string values without joining keys or delimiters."""
    if isinstance(value, str):
        return marker in value
    if isinstance(value, dict):
        return any(
            _model_visible_string_contains(item, marker)
            for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(
            _model_visible_string_contains(item, marker) for item in value)
    return False


def _validate_grounding_evidence_ids(
        queries: list[dict], records: list[dict], chunk_id_fn) -> None:
    """Bind v2 human grounding labels to exact model-visible corpus text."""
    records_by_id = {chunk_id_fn(record): record for record in records}

    def validate_declaration(
            declaration: dict, *, query_index: int, query_text: str,
            allow_metadata: bool) -> None:
        source_id = declaration["source_id"]
        record = records_by_id.get(source_id)
        if record is None:
            raise ValueError(
                "Grounding source IDs are absent from the declared chunks "
                f"artifact: query #{query_index} {source_id}")
        excerpt = retrieval_core._query_centered_excerpt(
            record["text"], query_text)
        marker = declaration["excerpt_contains"]
        marker_visible = marker in excerpt
        if allow_metadata and not marker_visible:
            visible_metadata = retrieval_core._prompt_source_metadata(
                retrieval_core._useful_source_metadata(
                    record.get("metadata") or {}))
            marker_visible = _model_visible_string_contains(
                visible_metadata, marker)
        if not marker_visible:
            kind = "marker" if allow_metadata else "evidence anchor"
            raise ValueError(
                f"Grounding {kind} is absent from the model-visible "
                f"excerpt: query #{query_index} {source_id}")

    for query_index, query in enumerate(queries, 1):
        case = query.get("grounding_case") or {}
        if case.get("schema_version") != GROUNDING_CASE_SCHEMA_VERSION:
            continue
        query_text = query["query"]
        for claim in case.get("claim_judgments", []):
            for support in claim["entailed_by"]:
                validate_declaration(
                    support, query_index=query_index,
                    query_text=query_text, allow_metadata=False)
        prompt_fixture = case.get("prompt_injection")
        if prompt_fixture:
            validate_declaration(
                {
                    "source_id": prompt_fixture["source_id"],
                    "excerpt_contains": prompt_fixture["marker"],
                },
                query_index=query_index, query_text=query_text,
                allow_metadata=True)


def _slice_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", value.casefold()).strip("_")


def _answer_units(answer: str) -> list[str]:
    """Return authored one-line claim units without semantic segmentation."""
    return [line.strip() for line in answer.splitlines() if line.strip()]


def _validate_grounding_case(case: dict, *, label: str) -> None:
    if not isinstance(case, dict):
        raise ValueError(f"{label} 'grounding_case' must be an object")
    if not isinstance(case.get("answer"), str) or not case["answer"].strip():
        raise ValueError(
            f"{label} grounding case must contain a non-empty 'answer'")
    if not isinstance(case.get("expected_abstained"), bool):
        raise ValueError(
            f"{label} grounding case 'expected_abstained' must be a boolean")
    schema_version = case.get("schema_version")
    if schema_version is not None and (
            type(schema_version) is not int
            or schema_version != GROUNDING_CASE_SCHEMA_VERSION):
        raise ValueError(
            f"{label} grounding case 'schema_version' must be "
            f"{GROUNDING_CASE_SCHEMA_VERSION}")
    case_type = case.get("case_type")
    if case_type is not None and (
            not isinstance(case_type, str) or not _slice_slug(case_type)):
        raise ValueError(
            f"{label} grounding case 'case_type' must be a non-empty label")
    citations = case.get("expected_citations")
    if citations is not None and (
            not isinstance(citations, list)
            or not all(isinstance(value, str)
                       and re.fullmatch(r"S[1-9]\d*", value)
                       for value in citations)
            or len(set(citations)) != len(citations)):
        raise ValueError(
            f"{label} grounding case citations must use S-number strings")
    for field in ("warning_contains", "excerpt_contains"):
        values = case.get(field)
        if values is not None and (
                not isinstance(values, list)
                or not all(isinstance(value, str) and value
                           for value in values)):
            raise ValueError(
                f"{label} grounding case '{field}' must be a list of strings")

    claims = case.get("claim_judgments")
    prompt_fixture = case.get("prompt_injection")
    if schema_version != GROUNDING_CASE_SCHEMA_VERSION:
        if claims is not None or prompt_fixture is not None:
            raise ValueError(
                f"{label} claim and prompt fixtures require grounding case "
                f"schema version {GROUNDING_CASE_SCHEMA_VERSION}")
        return

    if not isinstance(claims, list):
        raise ValueError(
            f"{label} v2 grounding case 'claim_judgments' must be a list")
    if retrieval_core._THINK_TAG_RE.search(case["answer"]):
        raise ValueError(
            f"{label} v2 grounding answers cannot contain removable "
            "'<think>' blocks")
    answer_units = _answer_units(case["answer"])
    expected_units = []
    claim_ids = set()
    has_supported_claim = False
    has_unsupported_claim = False
    for index, claim in enumerate(claims, 1):
        claim_label = f"{label} grounding claim #{index}"
        if not isinstance(claim, dict) or set(claim) != {
                "claim_id", "answer_unit", "entailed_by"}:
            raise ValueError(
                f"{claim_label} must contain exactly 'claim_id', "
                "'answer_unit', and 'entailed_by'")
        claim_id = claim["claim_id"]
        if (not isinstance(claim_id, str) or not claim_id.strip()
                or claim_id != claim_id.strip() or not _slice_slug(claim_id)):
            raise ValueError(f"{claim_label} 'claim_id' must be a label")
        if claim_id in claim_ids:
            raise ValueError(f"{claim_label} duplicates a claim ID")
        claim_ids.add(claim_id)
        answer_unit = claim["answer_unit"]
        if not isinstance(answer_unit, str) or not answer_unit.strip():
            raise ValueError(
                f"{claim_label} 'answer_unit' must be a non-empty string")
        if answer_unit != answer_unit.strip() or "\n" in answer_unit:
            raise ValueError(
                f"{claim_label} 'answer_unit' must be one stripped line")
        without_citations = re.sub(r"\[[^\]]*\]", " ", answer_unit)
        without_citations = re.sub(
            r"\bS[1-9]\d*\b", " ", without_citations,
            flags=re.IGNORECASE)
        if not any(character.isalnum() for character in without_citations):
            raise ValueError(
                f"{claim_label} 'answer_unit' must contain meaningful claim "
                "text, not only citations")
        if answer_unit in expected_units:
            raise ValueError(f"{claim_label} duplicates an answer unit")
        expected_units.append(answer_unit)
        supports = claim["entailed_by"]
        if not isinstance(supports, list):
            raise ValueError(
                f"{claim_label} 'entailed_by' must be a list")
        support_ids = set()
        for support_index, support in enumerate(supports, 1):
            support_label = f"{claim_label} support #{support_index}"
            if not isinstance(support, dict) or set(support) != {
                    "source_id", "excerpt_contains"}:
                raise ValueError(
                    f"{support_label} must contain exactly 'source_id' and "
                    "'excerpt_contains'")
            source_id = support["source_id"]
            anchor = support["excerpt_contains"]
            if not isinstance(source_id, str) or not source_id.strip():
                raise ValueError(
                    f"{support_label} 'source_id' must be a non-empty string")
            if source_id != source_id.strip():
                raise ValueError(
                    f"{support_label} 'source_id' must be stripped")
            if source_id in support_ids:
                raise ValueError(f"{support_label} duplicates a source ID")
            support_ids.add(source_id)
            if not isinstance(anchor, str) or not anchor.strip():
                raise ValueError(
                    f"{support_label} 'excerpt_contains' must be non-empty")
            if anchor != anchor.strip():
                raise ValueError(
                    f"{support_label} 'excerpt_contains' must be stripped")
        has_supported_claim = has_supported_claim or bool(supports)
        has_unsupported_claim = has_unsupported_claim or not supports

    sentinel = case["answer"].strip().rstrip(". ").upper() == (
        "INSUFFICIENT_EVIDENCE")
    if sentinel and claims:
        raise ValueError(
            f"{label} INSUFFICIENT_EVIDENCE answers must have no claims")
    if expected_units != answer_units:
        if not (sentinel and not expected_units):
            raise ValueError(
                f"{label} grounding claims must cover every non-empty answer "
                "line exactly once and in order")
    if not claims and not sentinel:
        raise ValueError(
            f"{label} v2 grounding cases require claims unless the answer is "
            "INSUFFICIENT_EVIDENCE")
    if not case["expected_abstained"] and (
            not has_supported_claim or has_unsupported_claim):
        raise ValueError(
            f"{label} a non-abstaining v2 answer must contain only supported "
            "claims")

    if prompt_fixture is not None:
        if not isinstance(prompt_fixture, dict) or set(prompt_fixture) != {
                "source_id", "marker"}:
            raise ValueError(
                f"{label} 'prompt_injection' must contain exactly "
                "'source_id' and 'marker'")
        for field in ("source_id", "marker"):
            value = prompt_fixture[field]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"{label} prompt-injection '{field}' must be non-empty")


def _validate_query(query: dict, *, label: str = "query") -> None:
    """Validate relevance, abstention, filter, and slice ground truth."""
    if not isinstance(query, dict):
        raise ValueError(f"{label} must be a JSON object")
    if not isinstance(query.get("query"), str) or not query["query"].strip():
        raise ValueError(f"{label} must contain a non-empty 'query' string")

    expected_abstain = query.get("expected_abstain", False)
    if not isinstance(expected_abstain, bool):
        raise ValueError(f"{label} 'expected_abstain' must be a boolean")

    keywords = query.get("expected_keywords")
    if keywords is not None and (
            not isinstance(keywords, list) or not keywords
            or not all(isinstance(item, str) and item.strip()
                       for item in keywords)):
        raise ValueError(
            f"{label} must contain non-empty string 'expected_keywords'")

    judgments = query.get("judgments")
    has_table_family = False
    if judgments is not None:
        if not isinstance(judgments, list) or not judgments:
            raise ValueError(f"{label} 'judgments' must be a non-empty list")
        seen_ids = set()
        judgment_id_types = set()
        has_positive = False
        for index, judgment in enumerate(judgments, 1):
            judgment_label = f"{label} judgment #{index}"
            if not isinstance(judgment, dict):
                raise ValueError(f"{judgment_label} must be a JSON object")
            present_ids = [
                field for field in _JUDGMENT_ID_FIELDS
                if isinstance(judgment.get(field), str)
                and judgment[field].strip()
            ]
            if len(present_ids) != 1:
                raise ValueError(
                    f"{judgment_label} must contain exactly one non-empty "
                    "'chunk_id' or 'source_id'")
            id_field = present_ids[0]
            allowed_fields = {id_field, "relevance"}
            satisfying_ids = [judgment[id_field].strip()]
            family = judgment.get("table_family")
            if family is not None:
                has_table_family = True
                allowed_fields.add("table_family")
                if id_field != "chunk_id":
                    raise ValueError(
                        f"{judgment_label} 'table_family' requires a chunk_id")
                if (not isinstance(family, dict)
                        or set(family) != {
                            "schema_version", "accepted_child_chunk_ids"}):
                    raise ValueError(
                        f"{judgment_label} 'table_family' fields do not match "
                        "schema v1")
                schema_version = family.get("schema_version")
                if (isinstance(schema_version, bool)
                        or not isinstance(schema_version, int)
                        or schema_version
                        != TABLE_FAMILY_JUDGMENT_SCHEMA_VERSION):
                    raise ValueError(
                        f"{judgment_label} table-family schema_version must be "
                        f"{TABLE_FAMILY_JUDGMENT_SCHEMA_VERSION}")
                accepted = family.get("accepted_child_chunk_ids")
                if (not isinstance(accepted, list) or not accepted
                        or len(accepted)
                        > table_retrieval_core.MAX_TABLE_CHILDREN_PER_PARENT
                        or not all(
                            isinstance(value, str) and value
                            and value == value.strip()
                            for value in accepted)):
                    raise ValueError(
                        f"{judgment_label} accepted child IDs must be a "
                        "non-empty bounded list of canonical strings")
                if accepted != sorted(set(accepted)):
                    raise ValueError(
                        f"{judgment_label} accepted child IDs must be sorted "
                        "and unique")
                if satisfying_ids[0] in accepted:
                    raise ValueError(
                        f"{judgment_label} table parent cannot also be an "
                        "accepted child")
                satisfying_ids.extend(accepted)
            if set(judgment) != allowed_fields:
                raise ValueError(
                    f"{judgment_label} fields do not match the judgment schema")
            relevance = judgment.get("relevance")
            numeric_relevance = _finite_float(relevance)
            if (numeric_relevance is None
                    or not 0 <= numeric_relevance <= 100):
                raise ValueError(
                    f"{judgment_label} 'relevance' must be a finite number "
                    "from 0 to 100")
            if family is not None and numeric_relevance <= 0:
                raise ValueError(
                    f"{judgment_label} table-family aliases require positive "
                    "relevance")
            has_positive = has_positive or numeric_relevance > 0
            judgment_id_types.add(id_field)
            for satisfying_id in satisfying_ids:
                judgment_id = (id_field, satisfying_id)
                if judgment_id in seen_ids:
                    raise ValueError(
                        f"{judgment_label} duplicates satisfying ID "
                        f"{judgment_id!r}")
                seen_ids.add(judgment_id)
        if not has_positive:
            raise ValueError(f"{label} must have at least one relevant judgment")
        if len(judgment_id_types) > 1:
            raise ValueError(
                f"{label} judgments must use one ID type consistently; do not "
                "mix 'chunk_id' and 'source_id' in the same query")

    if keywords is not None and judgments is not None:
        raise ValueError(
            f"{label} must not mix 'expected_keywords' and graded 'judgments'")
    if expected_abstain and (keywords is not None or judgments is not None):
        raise ValueError(
            f"{label} expected-abstention cases cannot contain relevance ground "
            "truth")
    if not expected_abstain and keywords is None and judgments is None:
        raise ValueError(
            f"{label} must contain 'expected_keywords', graded 'judgments', or "
            "set 'expected_abstain' to true")

    expected_type = query.get("expected_type", "")
    if not isinstance(expected_type, str):
        raise ValueError(f"{label} 'expected_type' must be a string")
    if expected_abstain and expected_type:
        raise ValueError(
            f"{label} expected-abstention cases cannot require an expected type")

    filters = query.get("filters")
    if filters is not None:
        if not isinstance(filters, dict) or not filters:
            raise ValueError(f"{label} 'filters' must be a non-empty object")
        unknown = set(filters) - set(_ALLOWED_FILTER_FIELDS)
        if unknown:
            raise ValueError(
                f"{label} has unsupported filters: {', '.join(sorted(unknown))}")
        content_type = filters.get("content_type")
        if ("content_type" in filters
                and (not isinstance(content_type, str)
                     or not content_type.strip())):
            raise ValueError(
                f"{label} filter 'content_type' must be a non-empty string")
        chapter_num = filters.get("chapter_num")
        if ("chapter_num" in filters
                and (isinstance(chapter_num, bool)
                     or not isinstance(chapter_num, int)
                     or chapter_num < 0)):
            raise ValueError(
                f"{label} filter 'chapter_num' must be an integer >= 0")

    tags = query.get("tags")
    if tags is not None:
        if (not isinstance(tags, list) or not tags
                or not all(isinstance(tag, str) and tag.strip()
                           for tag in tags)):
            raise ValueError(
                f"{label} 'tags' must be a non-empty list of strings")
        normalized_tags = [tag.strip() for tag in tags]
        if len(set(normalized_tags)) != len(normalized_tags):
            raise ValueError(f"{label} 'tags' must not contain duplicates")
        tag_slugs = [_slice_slug(tag) for tag in normalized_tags]
        if not all(tag_slugs) or len(set(tag_slugs)) != len(tag_slugs):
            raise ValueError(
                f"{label} 'tags' must have unique ASCII metric names")

    for field in ("subject", "book", "difficulty"):
        value = query.get(field)
        if value is not None and (
                not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{label} '{field}' must be a non-empty string")
        if value is not None and not _slice_slug(value):
            raise ValueError(
                f"{label} '{field}' must contain an ASCII letter or number")

    review_status = query.get("review_status")
    if review_status is not None and (
            not isinstance(review_status, str)
            or review_status not in _REVIEW_STATUSES):
        allowed = ", ".join(sorted(_REVIEW_STATUSES))
        raise ValueError(
            f"{label} 'review_status' must be one of: {allowed}")
    if has_table_family and review_status is None:
        raise ValueError(
            f"{label} table-family judgments must declare 'review_status'")

    approval = query.get("approval")
    if approval is not None:
        approval_schema = (
            approval.get("schema_version")
            if isinstance(approval, dict) else None)
        if (not isinstance(approval, dict)
                or set(approval) != {"schema_version", "review_batch_id"}
                or isinstance(approval_schema, bool)
                or not isinstance(approval_schema, int)
                or approval_schema != 1
                or not isinstance(approval.get("review_batch_id"), str)
                or re.fullmatch(
                    r"[0-9a-f]{64}", approval["review_batch_id"]) is None):
            raise ValueError(
                f"{label} 'approval' must be a schema-v1 review batch binding")
        if review_status != "approved":
            raise ValueError(
                f"{label} receipt-bound approval requires review_status "
                "'approved'")

    corpus = query.get("corpus")
    if corpus is not None:
        if not isinstance(corpus, dict):
            raise ValueError(f"{label} 'corpus' must be an object")
        digest = corpus.get("sha256")
        if digest is not None and (
                not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdefABCDEF" for char in digest)):
            raise ValueError(
                f"{label} corpus 'sha256' must be a 64-character hex digest")
        count = corpus.get("record_count")
        if count is not None and (
                isinstance(count, bool) or not isinstance(count, int)
                or count < 1):
            raise ValueError(
                f"{label} corpus 'record_count' must be a positive integer")
        if digest is None and count is None:
            raise ValueError(
                f"{label} corpus must declare 'sha256' or 'record_count'")
    if has_table_family and (
            not isinstance(corpus, dict)
            or corpus.get("sha256") is None
            or corpus.get("record_count") is None
            or not isinstance(corpus.get("id_scheme"), str)
            or not corpus["id_scheme"].strip()):
        raise ValueError(
            f"{label} table-family judgments require an exact corpus SHA-256 "
            "record count, and ID scheme")

    grounding_case = query.get("grounding_case")
    if grounding_case is not None:
        _validate_grounding_case(grounding_case, label=label)
        if (grounding_case.get("schema_version")
                == GROUNDING_CASE_SCHEMA_VERSION
                and review_status is None):
            raise ValueError(
                f"{label} v2 grounding cases must declare 'review_status'")
        if (grounding_case.get("prompt_injection") is not None
                and "prompt_injection" not in (tags or [])):
            raise ValueError(
                f"{label} prompt-injection fixtures must include the "
                "'prompt_injection' tag")
