"""
Tarang Context - Code indexing and retrieval system.

Provides:
- AST-based code chunking (tree-sitter)
- BM25 keyword search
- Symbol Graph (Code Knowledge Graph)
- Graph-augmented retrieval
"""

from tarang.context.skeleton import SkeletonGenerator, ProjectSkeleton
from tarang.context.chunker import Chunk, Chunker, SymbolInfo
from tarang.context.bm25 import BM25Index, SearchResult
from tarang.context.graph import SymbolGraph, SymbolNode
from tarang.context.retriever import ContextRetriever, RetrievalResult, create_retriever
from tarang.context.indexer import ProjectIndexer, IndexStats, index_project, get_retriever

__all__ = [
    # Skeleton (existing)
    "SkeletonGenerator",
    "ProjectSkeleton",
    # Chunker
    "Chunk",
    "Chunker",
    "SymbolInfo",
    # BM25
    "BM25Index",
    "SearchResult",
    # Graph
    "SymbolGraph",
    "SymbolNode",
    # Retriever
    "ContextRetriever",
    "RetrievalResult",
    "create_retriever",
    # Indexer
    "ProjectIndexer",
    "IndexStats",
    "index_project",
    "get_retriever",
]
