"""
BM25 Index - Fast keyword-based search over code chunks.

Uses the Okapi BM25 algorithm for ranking code chunks by relevance.
"""
from __future__ import annotations

import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .chunker import Chunk


@dataclass
class SearchResult:
    """A search result with chunk and score."""
    chunk: Chunk
    score: float

    def __repr__(self) -> str:
        return f"SearchResult({self.chunk.id}, score={self.score:.3f})"


class BM25Index:
    """
    BM25 index for code chunk search.

    Uses rank-bm25 library for efficient BM25 ranking.
    """

    def __init__(self):
        self._bm25 = None
        self._chunks: List[Chunk] = []
        self._chunk_map: Dict[str, Chunk] = {}  # id -> chunk

    @property
    def is_empty(self) -> bool:
        """Check if index is empty."""
        return len(self._chunks) == 0

    def build(self, chunks: List[Chunk]) -> None:
        """
        Build BM25 index from chunks.

        Args:
            chunks: List of code chunks with tokens
        """
        from rank_bm25 import BM25Okapi

        self._chunks = chunks
        self._chunk_map = {c.id: c for c in chunks}

        # Build BM25 index from tokens
        tokenized_corpus = [c.tokens for c in chunks]
        self._bm25 = BM25Okapi(tokenized_corpus)

    def add_chunks(self, new_chunks: List[Chunk]) -> None:
        """
        Add chunks to existing index (requires rebuild).

        For now, this rebuilds the entire index.
        Future optimization: incremental BM25.
        """
        # Merge chunks, replacing existing by ID
        for chunk in new_chunks:
            self._chunk_map[chunk.id] = chunk

        self._chunks = list(self._chunk_map.values())
        self.build(self._chunks)

    def remove_chunks(self, chunk_ids: List[str]) -> None:
        """
        Remove chunks from index (requires rebuild).
        """
        for chunk_id in chunk_ids:
            self._chunk_map.pop(chunk_id, None)

        self._chunks = list(self._chunk_map.values())
        if self._chunks:
            self.build(self._chunks)
        else:
            self._bm25 = None

    def search(self, query: str, k: int = 10) -> List[SearchResult]:
        """
        Search for chunks matching query.

        Args:
            query: Search query (natural language)
            k: Maximum results to return

        Returns:
            List of SearchResult sorted by score (descending)
        """
        if self._bm25 is None or not self._chunks:
            return []

        # Tokenize query
        query_tokens = self._tokenize_query(query)
        if not query_tokens:
            return []

        # Get BM25 scores
        scores = self._bm25.get_scores(query_tokens)

        # Pair with chunks and sort
        results = [
            SearchResult(chunk=self._chunks[i], score=score)
            for i, score in enumerate(scores)
            if score > 0
        ]

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:k]

    def get_chunk(self, chunk_id: str) -> Optional[Chunk]:
        """Get a chunk by ID."""
        return self._chunk_map.get(chunk_id)

    def get_chunks_for_file(self, file_path: str) -> List[Chunk]:
        """Get all chunks for a file."""
        return [c for c in self._chunks if c.file == file_path]

    def _tokenize_query(self, query: str) -> List[str]:
        """Tokenize search query."""
        # Split on whitespace and punctuation
        words = re.findall(r'\b\w+\b', query.lower())

        tokens = []
        for word in words:
            # Split snake_case
            if "_" in word:
                tokens.extend(word.split("_"))
            # Split camelCase
            elif any(c.isupper() for c in word[1:]):
                parts = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', word)
                tokens.extend(p.lower() for p in parts)
            else:
                tokens.append(word)

        # Filter stop words
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "must", "can", "need",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after",
            "and", "but", "if", "or", "because", "until", "while",
            "this", "that", "these", "those", "i", "me", "my", "we",
            "you", "your", "it", "its", "they", "them", "their",
            "what", "which", "who", "how", "where", "when", "why",
            "add", "create", "make", "build", "implement", "write",
            "fix", "update", "change", "modify", "remove", "delete",
        }

        return [t for t in tokens if len(t) > 2 and t not in stop_words]

    def save(self, path: Path) -> None:
        """
        Save index to disk.

        Args:
            path: Path to save file (pickle format)
        """
        data = {
            "chunks": [c.to_dict() for c in self._chunks],
            "bm25": self._bm25,
        }
        with open(path, "wb") as f:
            pickle.dump(data, f)

    def load(self, path: Path) -> bool:
        """
        Load index from disk.

        Args:
            path: Path to saved file

        Returns:
            True if loaded successfully
        """
        if not path.exists():
            return False

        try:
            with open(path, "rb") as f:
                data = pickle.load(f)

            self._chunks = [Chunk.from_dict(d) for d in data["chunks"]]
            self._chunk_map = {c.id: c for c in self._chunks}
            self._bm25 = data["bm25"]
            return True

        except Exception:
            return False

    def stats(self) -> Dict:
        """Get index statistics."""
        if not self._chunks:
            return {
                "total_chunks": 0,
                "total_files": 0,
                "chunk_types": {},
            }

        files = set(c.file for c in self._chunks)
        types = {}
        for c in self._chunks:
            types[c.type] = types.get(c.type, 0) + 1

        return {
            "total_chunks": len(self._chunks),
            "total_files": len(files),
            "chunk_types": types,
        }
