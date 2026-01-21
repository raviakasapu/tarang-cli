"""
Context Retriever - Unified interface for BM25 + KG + KB Docs retrieval.

Combines BM25 keyword search with Symbol Graph expansion and
KB documentation to provide rich, connected context for LLM queries.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .bm25 import BM25Index, SearchResult
from .chunker import Chunk
from .graph import SymbolGraph, SymbolNode
from .doc_loader import KBDocLoader, KBDoc

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Result from context retrieval."""
    chunks: List[Chunk]                         # Full code for direct matches
    signatures: List[str]                       # Signatures for connected symbols
    graph_context: Dict[str, Any]               # Relationship summary
    kb_docs: List[KBDoc] = field(default_factory=list)  # KB documentation
    kb_context: str = ""                        # Formatted KB docs context
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_context_dict(self) -> Dict:
        """Convert to dictionary for API payload."""
        return {
            "chunks": [
                {
                    "id": c.id,
                    "file": c.file,
                    "type": c.type,
                    "name": c.name,
                    "signature": c.signature,
                    "content": c.content,
                    "line_start": c.line_start,
                    "line_end": c.line_end,
                }
                for c in self.chunks
            ],
            "signatures": self.signatures,
            "graph": self.graph_context,
            "kb_docs": [d.to_dict() for d in self.kb_docs],
            "kb_context": self.kb_context,
        }

    @property
    def total_lines(self) -> int:
        """Total lines of code in chunks."""
        return sum(c.line_end - c.line_start + 1 for c in self.chunks)

    @property
    def is_empty(self) -> bool:
        """Check if result has no content."""
        return len(self.chunks) == 0


class ContextRetriever:
    """
    Unified context retrieval using BM25 + Knowledge Graph + KB Docs.

    Workflow:
    1. BM25 search finds relevant code chunks
    2. KG expansion adds connected symbols (signatures only)
    3. KB docs search adds relevant documentation
    4. Returns combined context for LLM
    """

    def __init__(
        self,
        bm25_index: BM25Index,
        symbol_graph: SymbolGraph,
        doc_loader: Optional[KBDocLoader] = None,
    ):
        self.bm25 = bm25_index
        self.graph = symbol_graph
        self.doc_loader = doc_loader

    @property
    def is_ready(self) -> bool:
        """Check if retriever has indexed data."""
        return not self.bm25.is_empty

    @property
    def has_kb_docs(self) -> bool:
        """Check if KB docs are available."""
        return self.doc_loader is not None and self.doc_loader.is_available

    def retrieve(
        self,
        query: str,
        hops: int = 1,
        max_chunks: int = 10,
        max_signatures: int = 20,
        include_kb_docs: bool = True,
        max_kb_docs: int = 3,
    ) -> RetrievalResult:
        """
        Retrieve relevant context for a query.

        Args:
            query: User instruction or search query
            hops: KG expansion hops (0=none, 1=direct, 2=2-level)
            max_chunks: Maximum code chunks to return
            max_signatures: Maximum connected signatures
            include_kb_docs: Whether to include KB documentation
            max_kb_docs: Maximum KB docs to include

        Returns:
            RetrievalResult with chunks, signatures, graph context, and KB docs
        """
        # Step 1: BM25 search
        search_results = self.bm25.search(query, k=max_chunks)

        chunks = []
        symbol_ids = []

        if search_results:
            chunks = [r.chunk for r in search_results]
            symbol_ids = [c.id for c in chunks]

        # Step 2: KG expansion
        signatures: List[str] = []
        expanded_ids: set = set()

        if hops > 0 and not self.graph.is_empty:
            for sid in symbol_ids:
                neighbors = self.graph.get_neighbors(sid, hops=hops)
                for neighbor in neighbors:
                    if neighbor.id not in symbol_ids and neighbor.id not in expanded_ids:
                        expanded_ids.add(neighbor.id)
                        signatures.append(neighbor.signature)

                        if len(signatures) >= max_signatures:
                            break

                if len(signatures) >= max_signatures:
                    break

        # Step 3: Get graph context
        all_ids = symbol_ids + list(expanded_ids)
        graph_context = self.graph.get_graph_context(all_ids) if all_ids else {}

        # Step 4: KB Docs search
        kb_docs: List[KBDoc] = []
        kb_context = ""

        if include_kb_docs and self.doc_loader and self.doc_loader.is_available:
            try:
                kb_docs = self.doc_loader.search(query, limit=max_kb_docs)
                if kb_docs:
                    kb_context = self.doc_loader.format_for_context(kb_docs, max_chars=2000)
                    logger.debug(f"[KB] Found {len(kb_docs)} relevant docs for query")
            except Exception as e:
                logger.warning(f"[KB] Error searching docs: {e}")

        return RetrievalResult(
            chunks=chunks,
            signatures=signatures[:max_signatures],
            graph_context=graph_context,
            kb_docs=kb_docs,
            kb_context=kb_context,
            stats={
                "bm25_hits": len(search_results) if search_results else 0,
                "expanded_symbols": len(expanded_ids),
                "total_chunks": len(chunks),
                "total_signatures": len(signatures),
                "kb_docs_found": len(kb_docs),
            },
        )

    def retrieve_for_file(
        self,
        file_path: str,
        hops: int = 1,
    ) -> RetrievalResult:
        """
        Retrieve all context for a specific file.

        Useful when user mentions a file explicitly.
        """
        chunks = self.bm25.get_chunks_for_file(file_path)

        if not chunks:
            return RetrievalResult(
                chunks=[],
                signatures=[],
                graph_context={},
            )

        symbol_ids = [c.id for c in chunks]

        # KG expansion
        signatures: List[str] = []
        expanded_ids: set = set()

        if hops > 0 and not self.graph.is_empty:
            for sid in symbol_ids:
                neighbors = self.graph.get_neighbors(sid, hops=hops)
                for neighbor in neighbors:
                    if neighbor.id not in symbol_ids and neighbor.id not in expanded_ids:
                        expanded_ids.add(neighbor.id)
                        signatures.append(neighbor.signature)

        graph_context = self.graph.get_graph_context(symbol_ids + list(expanded_ids))

        return RetrievalResult(
            chunks=chunks,
            signatures=signatures,
            graph_context=graph_context,
        )

    def retrieve_symbol(
        self,
        symbol_name: str,
        hops: int = 1,
    ) -> RetrievalResult:
        """
        Retrieve context for a specific symbol by name.

        Searches for chunks matching the symbol name exactly.
        """
        # Search for the symbol
        results = self.bm25.search(symbol_name, k=5)

        # Filter to exact name matches
        exact_matches = [
            r for r in results
            if r.chunk.name.lower() == symbol_name.lower()
        ]

        if not exact_matches:
            # Fall back to partial matches
            exact_matches = results[:3]

        if not exact_matches:
            return RetrievalResult(chunks=[], signatures=[], graph_context={})

        chunks = [r.chunk for r in exact_matches]
        symbol_ids = [c.id for c in chunks]

        # KG expansion
        signatures: List[str] = []
        expanded_ids: set = set()

        if hops > 0 and not self.graph.is_empty:
            for sid in symbol_ids:
                neighbors = self.graph.get_neighbors(sid, hops=hops)
                for neighbor in neighbors:
                    if neighbor.id not in symbol_ids and neighbor.id not in expanded_ids:
                        expanded_ids.add(neighbor.id)
                        signatures.append(neighbor.signature)

        graph_context = self.graph.get_graph_context(symbol_ids + list(expanded_ids))

        return RetrievalResult(
            chunks=chunks,
            signatures=signatures,
            graph_context=graph_context,
        )

    def get_callers(self, symbol_id: str) -> List[SymbolNode]:
        """Get all symbols that call this symbol."""
        return self.graph.get_callers(symbol_id)

    def get_callees(self, symbol_id: str) -> List[SymbolNode]:
        """Get all symbols that this symbol calls."""
        return self.graph.get_callees(symbol_id)


def create_retriever(
    index_path: Path,
    project_root: Optional[Path] = None,
) -> Optional[ContextRetriever]:
    """
    Create a retriever from saved index files.

    Args:
        index_path: Path to .tarang/index/ directory
        project_root: Project root for KB docs (defaults to index_path parent)

    Returns:
        ContextRetriever if index exists, None otherwise
    """
    bm25_path = index_path / "bm25.pkl"
    graph_path = index_path / "graph.json"

    bm25 = BM25Index()
    graph = SymbolGraph()

    # Load BM25 index
    if not bm25.load(bm25_path):
        return None

    # Load graph (optional, retriever works without it)
    graph.load(graph_path)

    # Create KB doc loader
    if project_root is None:
        # Assume index_path is .tarang/index, so project root is 2 levels up
        project_root = index_path.parent.parent

    doc_loader = KBDocLoader(str(project_root))  # Uses .tarang/docs/ by default
    if doc_loader.is_available:
        logger.debug(f"[KB] Found KB docs at {project_root / '.tarang/docs'}")
    else:
        doc_loader = None

    return ContextRetriever(bm25, graph, doc_loader)
