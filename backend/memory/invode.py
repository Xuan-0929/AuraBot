"""Compatibility façade for the backend memory API.

The filename is kept for existing imports. New implementation lives in focused
modules under backend.memory.
"""

from .api import (
    ForgetRequest,
    ImproveRequest,
    RecallRequest,
    RememberRequest,
    create_default_memory_service,
    create_memory_router,
)
from .consolidation.strategies import ConflictingPreferenceStrategy, DuplicateMemoryStrategy
from .embedding import HashEmbeddingProvider, create_embedding_provider
from .extraction.providers import StaticExtractionModel
from .extractor import StructuredMemoryExtractor
from .retrieval.fusion import fuse_candidates
from .retrieval.context import pack_context
from .retrieval.query import QueryAnalyzer
from .retrieval.ranking import filter_relations_for_ranked_memories, hydrate_and_rank_memories
from .retrieval.rerank import MemoryReranker, RuleBasedMemoryReranker
from .retrieval.relations import expand_relation_candidates
from .retrieval.seeds import EntitySeedRetriever, FtsSeedRetriever, VectorSeedRetriever
from .retrieval.trace import build_recall_trace
from .service import MemoryService
from .short_term import (
    SegmentState,
    ShortTermMemoryBuffer,
    ShortTermPolicy,
    ShortTermStateError,
    SummaryRequest,
)
from .stores.qdrant import QdrantLocalVectorStore
from .stores.short_term_sqlite import SQLiteShortTermStore
from .stores.sqlite import SQLiteMemoryStore
from .types import DEFAULT_COLLECTION_NAME, DEFAULT_VECTOR_DIMENSION, EmbeddingProvider, VectorStore
from .validators import validate_memory_candidate

__all__ = [
    "DEFAULT_COLLECTION_NAME",
    "DEFAULT_VECTOR_DIMENSION",
    "ConflictingPreferenceStrategy",
    "DuplicateMemoryStrategy",
    "EmbeddingProvider",
    "EntitySeedRetriever",
    "ForgetRequest",
    "FtsSeedRetriever",
    "HashEmbeddingProvider",
    "ImproveRequest",
    "MemoryReranker",
    "MemoryService",
    "QdrantLocalVectorStore",
    "QueryAnalyzer",
    "RecallRequest",
    "RememberRequest",
    "RuleBasedMemoryReranker",
    "SegmentState",
    "SQLiteMemoryStore",
    "SQLiteShortTermStore",
    "StaticExtractionModel",
    "StructuredMemoryExtractor",
    "ShortTermMemoryBuffer",
    "ShortTermPolicy",
    "ShortTermStateError",
    "SummaryRequest",
    "VectorStore",
    "VectorSeedRetriever",
    "build_recall_trace",
    "create_default_memory_service",
    "create_embedding_provider",
    "create_memory_router",
    "expand_relation_candidates",
    "filter_relations_for_ranked_memories",
    "fuse_candidates",
    "hydrate_and_rank_memories",
    "pack_context",
    "validate_memory_candidate",
]
