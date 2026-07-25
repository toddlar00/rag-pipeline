"""Dependency-free classification of remote embedding model names.

The surrounding configuration layer is responsible for validating that a
model name is usable.  This module only answers whether a validated name is in
one of the API-backed model families understood by the pipeline.
"""


SUPPORTED_API_EMBEDDING_MODEL_PREFIXES = (
    "voyage-",
    "text-embedding-",
    "embed-",
    "cohere-",
)
UNSUPPORTED_API_EMBEDDING_MODEL_PREFIXES = (
    "embo-",
    "minimax-emb",
)
API_EMBEDDING_MODEL_PREFIXES = (
    *SUPPORTED_API_EMBEDDING_MODEL_PREFIXES,
    *UNSUPPORTED_API_EMBEDDING_MODEL_PREFIXES,
)

__all__ = [
    "API_EMBEDDING_MODEL_PREFIXES",
    "SUPPORTED_API_EMBEDDING_MODEL_PREFIXES",
    "UNSUPPORTED_API_EMBEDDING_MODEL_PREFIXES",
    "is_api_embedding_model",
]


def is_api_embedding_model(model_name: str) -> bool:
    """Return whether ``model_name`` has an exact API-family prefix.

    Matching is case-sensitive and starts at the first character.  No name is
    normalized or inferred from a prefix found later in the string.  An empty
    string therefore classifies as local; configuration validation, including
    rejecting unusable empty names, belongs to the caller.
    """
    if not isinstance(model_name, str):
        raise TypeError("embedding model name must be text")
    return model_name.startswith(API_EMBEDDING_MODEL_PREFIXES)
