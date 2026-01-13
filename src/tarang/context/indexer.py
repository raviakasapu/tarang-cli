"""
Project Indexer - Orchestrates index building and updates.

Manages the .tarang/index/ directory with:
- chunks.jsonl: Code chunks
- bm25.pkl: BM25 index
- graph.json: Symbol graph
- manifest.json: File hashes for invalidation
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from .bm25 import BM25Index
from .chunker import Chunk, Chunker
from .graph import SymbolGraph
from .retriever import ContextRetriever


@dataclass
class IndexStats:
    """Statistics from indexing operation."""
    files_scanned: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    files_updated: int = 0
    chunks_created: int = 0
    symbols_created: int = 0
    edges_created: int = 0
    duration_ms: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "files_scanned": self.files_scanned,
            "files_indexed": self.files_indexed,
            "files_skipped": self.files_skipped,
            "files_updated": self.files_updated,
            "chunks_created": self.chunks_created,
            "symbols_created": self.symbols_created,
            "edges_created": self.edges_created,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
        }


@dataclass
class FileEntry:
    """Entry in the manifest for a file."""
    hash: str
    mtime: float
    chunks: List[str]
    symbols: List[str]

    def to_dict(self) -> Dict:
        return {
            "hash": self.hash,
            "mtime": self.mtime,
            "chunks": self.chunks,
            "symbols": self.symbols,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "FileEntry":
        return cls(
            hash=data["hash"],
            mtime=data["mtime"],
            chunks=data.get("chunks", []),
            symbols=data.get("symbols", []),
        )


class ProjectIndexer:
    """
    Manages project indexing for context retrieval.

    Creates and maintains:
    - .tarang/index/bm25.pkl - BM25 search index
    - .tarang/index/graph.json - Symbol relationship graph
    - .tarang/index/manifest.json - File hashes for incremental updates
    """

    # Directories to ignore
    IGNORE_DIRS = {
        ".git", ".svn", ".hg",
        "node_modules", "venv", ".venv", "env", ".env",
        "__pycache__", ".pytest_cache", ".mypy_cache",
        "vendor", "packages",
        "dist", "build", ".next", ".nuxt", "out",
        "target", "bin", "obj",
        ".idea", ".vscode", ".vs",
        ".tarang",
    }

    # Files to ignore
    IGNORE_PATTERNS = {
        "*.pyc", "*.pyo", "*.so", "*.dylib",
        "*.egg-info", "*.egg",
        ".DS_Store", "Thumbs.db",
        "*.min.js", "*.min.css",
        "*.lock", "*.log",
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    }

    # Supported extensions for indexing
    SUPPORTED_EXTENSIONS = {
        # Python
        ".py", ".pyw",
        # JavaScript/TypeScript
        ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
        # Config files
        ".json", ".yaml", ".yml", ".toml",
        # SQL
        ".sql",
        # Other
        ".md", ".txt",
    }

    # Tarang version for manifest compatibility
    VERSION = "1.0"

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self.index_dir = self.project_root / ".tarang" / "index"

        self.chunker = Chunker()
        self.bm25 = BM25Index()
        self.graph = SymbolGraph()
        self.manifest: Dict[str, FileEntry] = {}

    @property
    def manifest_path(self) -> Path:
        return self.index_dir / "manifest.json"

    @property
    def bm25_path(self) -> Path:
        return self.index_dir / "bm25.pkl"

    @property
    def graph_path(self) -> Path:
        return self.index_dir / "graph.json"

    def exists(self) -> bool:
        """Check if index exists."""
        return self.manifest_path.exists()

    def is_stale(self) -> bool:
        """
        Check if index needs updating.

        Returns True if any indexed file has changed.
        """
        if not self.exists():
            return True

        self._load_manifest()

        for rel_path, entry in self.manifest.items():
            file_path = self.project_root / rel_path
            if not file_path.exists():
                return True  # File deleted

            current_hash = self._hash_file(file_path)
            if current_hash != entry.hash:
                return True  # File modified

        return False

    def build(self, force: bool = False) -> IndexStats:
        """
        Build complete index for project.

        Args:
            force: Rebuild even if index exists

        Returns:
            IndexStats with operation results
        """
        start_time = time.time()
        stats = IndexStats()

        # Create index directory
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Load existing index if not forcing rebuild
        if not force and self.exists():
            return self.update()

        # Scan all files
        files = self._scan_files()
        stats.files_scanned = len(files)

        # Process each file
        all_chunks: List[Chunk] = []
        new_manifest: Dict[str, FileEntry] = {}

        for file_path in files:
            try:
                rel_path = str(file_path.relative_to(self.project_root))

                # Chunk the file
                chunks, symbols = self.chunker.chunk_file(file_path, self.project_root)

                if chunks:
                    all_chunks.extend(chunks)
                    stats.chunks_created += len(chunks)
                    stats.files_indexed += 1

                    # Add symbols to graph
                    for symbol in symbols:
                        self.graph.add_symbol(symbol)
                        stats.symbols_created += 1

                    # Record in manifest
                    new_manifest[rel_path] = FileEntry(
                        hash=self._hash_file(file_path),
                        mtime=file_path.stat().st_mtime,
                        chunks=[c.id for c in chunks],
                        symbols=[s.id for s in symbols],
                    )
                else:
                    stats.files_skipped += 1

            except Exception as e:
                stats.errors.append(f"{file_path}: {str(e)}")
                stats.files_skipped += 1

        # Build BM25 index
        if all_chunks:
            self.bm25.build(all_chunks)

        # Calculate edge count
        graph_stats = self.graph.stats()
        stats.edges_created = graph_stats.get("total_edges", 0)

        # Save everything
        self._save_index(new_manifest)

        stats.duration_ms = int((time.time() - start_time) * 1000)
        return stats

    def update(self, changed_files: Optional[List[Path]] = None) -> IndexStats:
        """
        Incrementally update index for changed files.

        Args:
            changed_files: Specific files to update (None = auto-detect)

        Returns:
            IndexStats with operation results
        """
        start_time = time.time()
        stats = IndexStats()

        # Load existing index
        if not self._load_index():
            # No existing index, do full build
            return self.build()

        # Detect changed files if not provided
        if changed_files is None:
            changed_files = self._detect_changes()

        if not changed_files:
            stats.duration_ms = int((time.time() - start_time) * 1000)
            return stats

        stats.files_scanned = len(changed_files)

        # Process changed files
        for file_path in changed_files:
            try:
                if not file_path.exists():
                    # File deleted - remove from index
                    rel_path = str(file_path.relative_to(self.project_root))
                    self._remove_file_from_index(rel_path)
                    stats.files_updated += 1
                    continue

                rel_path = str(file_path.relative_to(self.project_root))

                # Remove old chunks/symbols for this file
                self._remove_file_from_index(rel_path)

                # Re-chunk the file
                chunks, symbols = self.chunker.chunk_file(file_path, self.project_root)

                if chunks:
                    # Add to BM25
                    self.bm25.add_chunks(chunks)
                    stats.chunks_created += len(chunks)

                    # Add symbols to graph
                    for symbol in symbols:
                        self.graph.add_symbol(symbol)
                        stats.symbols_created += 1

                    # Update manifest
                    self.manifest[rel_path] = FileEntry(
                        hash=self._hash_file(file_path),
                        mtime=file_path.stat().st_mtime,
                        chunks=[c.id for c in chunks],
                        symbols=[s.id for s in symbols],
                    )

                    stats.files_updated += 1
                    stats.files_indexed += 1
                else:
                    stats.files_skipped += 1

            except Exception as e:
                stats.errors.append(f"{file_path}: {str(e)}")

        # Save updated index
        self._save_index(self.manifest)

        stats.duration_ms = int((time.time() - start_time) * 1000)
        return stats

    def get_retriever(self) -> Optional[ContextRetriever]:
        """Get a retriever for this project's index."""
        if not self._load_index():
            return None

        return ContextRetriever(self.bm25, self.graph)

    def stats(self) -> Dict:
        """Get current index statistics."""
        if not self._load_index():
            return {"indexed": False}

        bm25_stats = self.bm25.stats()
        graph_stats = self.graph.stats()

        return {
            "indexed": True,
            "files": len(self.manifest),
            "chunks": bm25_stats.get("total_chunks", 0),
            "symbols": graph_stats.get("total_symbols", 0),
            "edges": graph_stats.get("total_edges", 0),
            "chunk_types": bm25_stats.get("chunk_types", {}),
            "symbol_types": graph_stats.get("symbol_types", {}),
        }

    def _scan_files(self) -> List[Path]:
        """Scan project for indexable files."""
        files = []

        for root, dirs, filenames in os.walk(self.project_root):
            # Filter directories
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]

            for filename in filenames:
                # Check ignore patterns
                if self._should_ignore(filename):
                    continue

                # Check extension
                ext = Path(filename).suffix.lower()
                if ext not in self.SUPPORTED_EXTENSIONS:
                    continue

                full_path = Path(root) / filename
                files.append(full_path)

        return files

    def _should_ignore(self, filename: str) -> bool:
        """Check if file should be ignored."""
        for pattern in self.IGNORE_PATTERNS:
            if fnmatch.fnmatch(filename, pattern):
                return True
        return False

    def _hash_file(self, file_path: Path) -> str:
        """Compute SHA256 hash of file content."""
        try:
            content = file_path.read_bytes()
            return hashlib.sha256(content).hexdigest()
        except Exception:
            return ""

    def _detect_changes(self) -> List[Path]:
        """Detect files that have changed since last index."""
        changed = []
        current_files: Set[str] = set()

        # Check for modified or new files
        for file_path in self._scan_files():
            rel_path = str(file_path.relative_to(self.project_root))
            current_files.add(rel_path)

            entry = self.manifest.get(rel_path)
            if entry is None:
                # New file
                changed.append(file_path)
            elif self._hash_file(file_path) != entry.hash:
                # Modified file
                changed.append(file_path)

        # Check for deleted files
        for rel_path in self.manifest:
            if rel_path not in current_files:
                changed.append(self.project_root / rel_path)

        return changed

    def _remove_file_from_index(self, rel_path: str) -> None:
        """Remove a file's chunks and symbols from index."""
        entry = self.manifest.get(rel_path)
        if not entry:
            return

        # Remove from BM25
        self.bm25.remove_chunks(entry.chunks)

        # Remove from graph
        self.graph.remove_file(rel_path)

        # Remove from manifest
        del self.manifest[rel_path]

    def _load_manifest(self) -> bool:
        """Load manifest from disk."""
        if not self.manifest_path.exists():
            return False

        try:
            with open(self.manifest_path, "r") as f:
                data = json.load(f)

            # Check version
            if data.get("version") != self.VERSION:
                return False

            self.manifest = {
                path: FileEntry.from_dict(entry)
                for path, entry in data.get("files", {}).items()
            }
            return True

        except Exception:
            return False

    def _load_index(self) -> bool:
        """Load full index from disk."""
        if not self._load_manifest():
            return False

        # Load BM25
        if not self.bm25.load(self.bm25_path):
            return False

        # Load graph (optional)
        self.graph.load(self.graph_path)

        return True

    def _save_index(self, manifest: Dict[str, FileEntry]) -> None:
        """Save full index to disk."""
        self.manifest = manifest

        # Save manifest
        manifest_data = {
            "version": self.VERSION,
            "indexed_at": datetime.utcnow().isoformat(),
            "tarang_version": "3.6.0",  # TODO: Get from package
            "files": {
                path: entry.to_dict()
                for path, entry in manifest.items()
            },
        }

        with open(self.manifest_path, "w") as f:
            json.dump(manifest_data, f, indent=2)

        # Save BM25
        self.bm25.save(self.bm25_path)

        # Save graph
        self.graph.save(self.graph_path)


def index_project(project_path: Path, force: bool = False) -> IndexStats:
    """
    Convenience function to index a project.

    Args:
        project_path: Path to project root
        force: Force full rebuild

    Returns:
        IndexStats with operation results
    """
    indexer = ProjectIndexer(project_path)
    return indexer.build(force=force)


def get_retriever(project_path: Path) -> Optional[ContextRetriever]:
    """
    Get a retriever for a project.

    Loads existing index or returns None if not indexed.
    """
    indexer = ProjectIndexer(project_path)
    return indexer.get_retriever()
