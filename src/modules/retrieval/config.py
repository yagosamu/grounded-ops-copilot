"""Validated public production retrieval configuration."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RetrievalConfig:
    version: int
    production_strategy: str
    fallback_strategy: str
    chunking_strategy: str
    dataset_hash: str
    reports: dict[str, str]


def load_retrieval_config(path: Path) -> RetrievalConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = RetrievalConfig(
        int(raw["version"]),
        str(raw["production_strategy"]),
        str(raw["fallback_strategy"]),
        str(raw["chunking_strategy"]),
        str(raw["dataset_hash"]),
        {str(key): str(value) for key, value in raw["reports"].items()},
    )
    if config.version != 1:
        raise ValueError("unsupported retrieval config version")
    if config.production_strategy not in {"bm25", "dense", "hybrid", "reranker"}:
        raise ValueError("invalid production retrieval strategy")
    if config.fallback_strategy != "bm25":
        raise ValueError("invalid retrieval fallback")
    if config.chunking_strategy not in {"fixed", "structural", "parent-child"}:
        raise ValueError("invalid chunking strategy")
    if len(config.dataset_hash) != 64 or any(
        len(digest) != 64 for digest in config.reports.values()
    ):
        raise ValueError("invalid retrieval evidence hash")
    return config
