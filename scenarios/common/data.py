"""Synthetic resource/principal generation for the experiment.

Embeddings are real (produced by a local sentence-transformer model, no
external API calls -- reproducible, no network dependency at experiment
time), computed from short synthetic "document" text so that different
resources get genuinely different, non-degenerate vectors rather than noise,
which matters for Scenario B3's ANN-search-specific tests later.
"""
import random
from dataclasses import dataclass

import numpy as np

_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is None:
        # Imported lazily so that modules which only need resource_id/acl
        # generation (no embeddings) don't pay sentence-transformers' import
        # cost or need it installed at all.
        from sentence_transformers import SentenceTransformer
        import os
        _MODEL = SentenceTransformer(os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
    return _MODEL


@dataclass
class Resource:
    resource_id: str
    payload: dict
    embedding: list[float]
    acl: list[str]


_TOPICS = [
    "quarterly revenue forecast", "employee performance review", "customer support ticket",
    "product roadmap draft", "security incident report", "vendor contract terms",
    "marketing campaign brief", "engineering design document", "legal compliance memo",
    "internal audit findings",
]


def make_principals(n: int, seed: int) -> list[str]:
    # Deliberately deterministic (no randomness): the resulting IDs are a
    # pure function of (n, seed), which guarantees a shorter pool requested
    # with the same seed is always a prefix of a longer one -- useful when
    # different parts of the harness need consistent principal IDs for the
    # same seed without coordinating pool sizes explicitly.
    return [f"user-{seed:03d}-{i:05d}" for i in range(n)]


def make_resources(n: int, seed: int, acl_size: int = 3, embed: bool = True) -> list[Resource]:
    """n resources, each owned by acl_size principals drawn from a pool of
    principals sized proportionally to n (so ACL overlap patterns are
    realistic rather than everyone sharing one giant ACL or nobody sharing
    anything)."""
    rng = random.Random(seed)
    principal_pool = make_principals(max(n // 5, acl_size * 2), seed)

    texts, resources = [], []
    for i in range(n):
        rid = f"res-{seed:03d}-{i:06d}"
        topic = rng.choice(_TOPICS)
        text = f"{topic} #{i}: synthetic content for reproducible embedding generation, seed {seed}."
        texts.append(text)
        acl = rng.sample(principal_pool, k=min(acl_size, len(principal_pool)))
        resources.append(Resource(resource_id=rid, payload={"topic": topic, "text": text}, embedding=[], acl=acl))

    if embed:
        model = _get_model()
        # Progress bar on: at 100K this step is ~20 minutes of silence
        # otherwise, which is indistinguishable from a hung machine. It goes
        # to stderr, so piping stdout to a log keeps the log clean.
        vectors = model.encode(texts, batch_size=256, show_progress_bar=len(texts) >= 5000)
        for r, v in zip(resources, vectors):
            r.embedding = np.asarray(v, dtype=np.float32).tolist()

    return resources
