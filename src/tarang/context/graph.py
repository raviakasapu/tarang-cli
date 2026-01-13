"""
Symbol Graph - Lightweight Code Knowledge Graph.

A labeled property graph storing relationships between code symbols:
- Functions, classes, methods (Python/JS/TS)
- Tables, views, procedures, triggers, indexes (SQL)
- Calls, imports, inheritance, references relationships

Enables graph-augmented retrieval by expanding BM25 results
to include connected symbols.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from .chunker import SymbolInfo


@dataclass
class SymbolNode:
    """A node in the symbol graph."""
    id: str                         # "file.py:function_name" or "file.sql:table:users"
    type: str                       # "function" | "method" | "class" | "module" |
                                    # "table" | "view" | "procedure" | "trigger" | "index"
    file: str                       # File path
    name: str                       # Symbol name
    signature: str                  # Function/class/table signature
    line: int                       # Definition line

    def to_dict(self) -> Dict:
        return {
            "type": self.type,
            "file": self.file,
            "name": self.name,
            "signature": self.signature,
            "line": self.line,
        }

    @classmethod
    def from_dict(cls, id: str, data: Dict) -> "SymbolNode":
        return cls(
            id=id,
            type=data["type"],
            file=data["file"],
            name=data["name"],
            signature=data["signature"],
            line=data["line"],
        )


@dataclass
class SymbolEdges:
    """Edges for a symbol in the graph."""
    calls: List[str] = field(default_factory=list)          # Functions this calls
    called_by: List[str] = field(default_factory=list)      # Functions that call this
    imports: List[str] = field(default_factory=list)        # Modules imported
    imported_by: List[str] = field(default_factory=list)    # Files that import this
    inherits: List[str] = field(default_factory=list)       # Parent classes
    inherited_by: List[str] = field(default_factory=list)   # Child classes
    defines: List[str] = field(default_factory=list)        # Symbols defined in this scope
    defined_in: Optional[str] = None                         # Parent scope
    # SQL relationships
    references: List[str] = field(default_factory=list)     # Tables referenced by views/funcs
    referenced_by: List[str] = field(default_factory=list)  # Views/funcs that reference this

    def to_dict(self) -> Dict:
        result = {}
        if self.calls:
            result["calls"] = self.calls
        if self.called_by:
            result["called_by"] = self.called_by
        if self.imports:
            result["imports"] = self.imports
        if self.imported_by:
            result["imported_by"] = self.imported_by
        if self.inherits:
            result["inherits"] = self.inherits
        if self.inherited_by:
            result["inherited_by"] = self.inherited_by
        if self.defines:
            result["defines"] = self.defines
        if self.defined_in:
            result["defined_in"] = self.defined_in
        if self.references:
            result["references"] = self.references
        if self.referenced_by:
            result["referenced_by"] = self.referenced_by
        return result

    @classmethod
    def from_dict(cls, data: Dict) -> "SymbolEdges":
        return cls(
            calls=data.get("calls", []),
            called_by=data.get("called_by", []),
            imports=data.get("imports", []),
            imported_by=data.get("imported_by", []),
            inherits=data.get("inherits", []),
            inherited_by=data.get("inherited_by", []),
            defines=data.get("defines", []),
            defined_in=data.get("defined_in"),
            references=data.get("references", []),
            referenced_by=data.get("referenced_by", []),
        )


class SymbolGraph:
    """
    Lightweight Code Knowledge Graph.

    Stores symbols and their relationships as an adjacency list.
    Supports graph traversal for context expansion.
    """

    def __init__(self):
        self._nodes: Dict[str, SymbolNode] = {}
        self._edges: Dict[str, SymbolEdges] = {}
        # Reverse index: name -> [symbol_ids]
        self._name_index: Dict[str, List[str]] = {}

    @property
    def is_empty(self) -> bool:
        return len(self._nodes) == 0

    def add_symbol(self, info: SymbolInfo) -> None:
        """
        Add a symbol to the graph.

        Args:
            info: Symbol information from chunker
        """
        node = SymbolNode(
            id=info.id,
            type=info.type,
            file=info.file,
            name=info.name,
            signature=info.signature,
            line=info.line,
        )
        self._nodes[info.id] = node

        # Initialize edges if not exists
        if info.id not in self._edges:
            self._edges[info.id] = SymbolEdges()

        # Update name index
        if info.name not in self._name_index:
            self._name_index[info.name] = []
        if info.id not in self._name_index[info.name]:
            self._name_index[info.name].append(info.id)

        # Process calls
        for call_name in info.calls:
            # Try to resolve call to a symbol ID
            target_ids = self._resolve_call(call_name, info.file)
            for target_id in target_ids:
                self._add_edge(info.id, target_id, "calls")

        # Process imports (stored as inheritance for classes, references for SQL)
        if info.type == "class":
            for parent in info.imports:
                parent_ids = self._resolve_call(parent, info.file)
                for parent_id in parent_ids:
                    self._add_edge(info.id, parent_id, "inherits")
        elif info.type == "module":
            for module in info.imports:
                self._edges[info.id].imports.append(module)
        elif info.type in ("view", "procedure", "function", "trigger", "index"):
            # SQL: views/procedures/triggers/indexes reference tables
            for table_ref in info.imports:
                target_ids = self._resolve_call(table_ref, info.file)
                for target_id in target_ids:
                    self._add_edge(info.id, target_id, "references")

        # Process parent class relationship
        if info.parent_class:
            parent_id = f"{info.file}:{info.parent_class}"
            self._edges[info.id].defined_in = parent_id
            if parent_id in self._edges:
                if info.id not in self._edges[parent_id].defines:
                    self._edges[parent_id].defines.append(info.id)

    def _resolve_call(self, call_name: str, current_file: str) -> List[str]:
        """
        Resolve a function call name to symbol IDs.

        Strategy:
        1. Look in name index for matching symbols
        2. Prefer symbols in same file
        3. Fall back to any matching symbol
        """
        if call_name not in self._name_index:
            return []

        candidates = self._name_index[call_name]

        # Prefer same file
        same_file = [c for c in candidates if c.startswith(current_file + ":")]
        if same_file:
            return same_file

        return candidates

    def _add_edge(self, source: str, target: str, edge_type: str) -> None:
        """Add an edge between two symbols."""
        # Ensure edges exist for both
        if source not in self._edges:
            self._edges[source] = SymbolEdges()
        if target not in self._edges:
            self._edges[target] = SymbolEdges()

        # Add forward edge
        if edge_type == "calls":
            if target not in self._edges[source].calls:
                self._edges[source].calls.append(target)
            if source not in self._edges[target].called_by:
                self._edges[target].called_by.append(source)
        elif edge_type == "inherits":
            if target not in self._edges[source].inherits:
                self._edges[source].inherits.append(target)
            if source not in self._edges[target].inherited_by:
                self._edges[target].inherited_by.append(source)
        elif edge_type == "references":
            # SQL: view/procedure references table
            if target not in self._edges[source].references:
                self._edges[source].references.append(target)
            if source not in self._edges[target].referenced_by:
                self._edges[target].referenced_by.append(source)

    def remove_file(self, file_path: str) -> None:
        """Remove all symbols from a file."""
        # Find symbols to remove
        to_remove = [sid for sid in self._nodes if sid.startswith(file_path + ":")]

        for sid in to_remove:
            # Remove from nodes
            node = self._nodes.pop(sid, None)
            if node:
                # Remove from name index
                if node.name in self._name_index:
                    self._name_index[node.name] = [
                        s for s in self._name_index[node.name] if s != sid
                    ]
                    if not self._name_index[node.name]:
                        del self._name_index[node.name]

            # Remove edges
            self._edges.pop(sid, None)

            # Remove references from other edges
            for edges in self._edges.values():
                edges.calls = [c for c in edges.calls if c != sid]
                edges.called_by = [c for c in edges.called_by if c != sid]
                edges.inherits = [c for c in edges.inherits if c != sid]
                edges.inherited_by = [c for c in edges.inherited_by if c != sid]
                edges.defines = [c for c in edges.defines if c != sid]
                edges.references = [c for c in edges.references if c != sid]
                edges.referenced_by = [c for c in edges.referenced_by if c != sid]
                if edges.defined_in == sid:
                    edges.defined_in = None

    def get_node(self, symbol_id: str) -> Optional[SymbolNode]:
        """Get a symbol node by ID."""
        return self._nodes.get(symbol_id)

    def get_edges(self, symbol_id: str) -> Optional[SymbolEdges]:
        """Get edges for a symbol."""
        return self._edges.get(symbol_id)

    def get_signature(self, symbol_id: str) -> Optional[str]:
        """Get just the signature for a symbol."""
        node = self._nodes.get(symbol_id)
        return node.signature if node else None

    def get_neighbors(
        self,
        symbol_id: str,
        hops: int = 1,
        edge_types: Optional[List[str]] = None
    ) -> List[SymbolNode]:
        """
        Get symbols within N hops of a symbol.

        Args:
            symbol_id: Starting symbol
            hops: Number of hops (1 = direct connections, 2 = 2 levels)
            edge_types: Edge types to follow (None = all)

        Returns:
            List of connected SymbolNodes
        """
        if hops < 1 or symbol_id not in self._edges:
            return []

        visited: Set[str] = {symbol_id}
        current_level: Set[str] = {symbol_id}
        result: List[SymbolNode] = []

        for _ in range(hops):
            next_level: Set[str] = set()

            for sid in current_level:
                edges = self._edges.get(sid)
                if not edges:
                    continue

                # Collect neighbors based on edge types
                neighbors: List[str] = []

                if edge_types is None or "calls" in edge_types:
                    neighbors.extend(edges.calls)
                if edge_types is None or "called_by" in edge_types:
                    neighbors.extend(edges.called_by)
                if edge_types is None or "inherits" in edge_types:
                    neighbors.extend(edges.inherits)
                if edge_types is None or "inherited_by" in edge_types:
                    neighbors.extend(edges.inherited_by)
                if edge_types is None or "defines" in edge_types:
                    neighbors.extend(edges.defines)
                if edge_types is None or "defined_in" in edge_types:
                    if edges.defined_in:
                        neighbors.append(edges.defined_in)
                # SQL relationships
                if edge_types is None or "references" in edge_types:
                    neighbors.extend(edges.references)
                if edge_types is None or "referenced_by" in edge_types:
                    neighbors.extend(edges.referenced_by)

                for neighbor in neighbors:
                    if neighbor not in visited and neighbor in self._nodes:
                        visited.add(neighbor)
                        next_level.add(neighbor)
                        result.append(self._nodes[neighbor])

            current_level = next_level

        return result

    def get_callers(self, symbol_id: str) -> List[SymbolNode]:
        """Get all symbols that call this symbol."""
        edges = self._edges.get(symbol_id)
        if not edges:
            return []
        return [self._nodes[sid] for sid in edges.called_by if sid in self._nodes]

    def get_callees(self, symbol_id: str) -> List[SymbolNode]:
        """Get all symbols that this symbol calls."""
        edges = self._edges.get(symbol_id)
        if not edges:
            return []
        return [self._nodes[sid] for sid in edges.calls if sid in self._nodes]

    def save(self, path: Path) -> None:
        """Save graph to JSON file."""
        data = {
            "nodes": {sid: node.to_dict() for sid, node in self._nodes.items()},
            "edges": {sid: edges.to_dict() for sid, edges in self._edges.items()},
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self, path: Path) -> bool:
        """Load graph from JSON file."""
        if not path.exists():
            return False

        try:
            with open(path, "r") as f:
                data = json.load(f)

            self._nodes = {
                sid: SymbolNode.from_dict(sid, node_data)
                for sid, node_data in data.get("nodes", {}).items()
            }

            self._edges = {
                sid: SymbolEdges.from_dict(edge_data)
                for sid, edge_data in data.get("edges", {}).items()
            }

            # Rebuild name index
            self._name_index = {}
            for sid, node in self._nodes.items():
                if node.name not in self._name_index:
                    self._name_index[node.name] = []
                self._name_index[node.name].append(sid)

            return True

        except Exception:
            return False

    def stats(self) -> Dict:
        """Get graph statistics."""
        if not self._nodes:
            return {
                "total_symbols": 0,
                "total_edges": 0,
                "symbol_types": {},
            }

        types = {}
        for node in self._nodes.values():
            types[node.type] = types.get(node.type, 0) + 1

        total_edges = sum(
            len(e.calls) + len(e.inherits) + len(e.defines) + len(e.references)
            for e in self._edges.values()
        )

        return {
            "total_symbols": len(self._nodes),
            "total_edges": total_edges,
            "symbol_types": types,
        }

    def get_graph_context(self, symbol_ids: List[str]) -> Dict:
        """
        Get a summary of graph relationships for symbols.

        Useful for including in LLM context.
        """
        context = {}

        for sid in symbol_ids:
            edges = self._edges.get(sid)
            if not edges:
                continue

            # Get human-readable names
            calls = [
                self._nodes[c].name for c in edges.calls if c in self._nodes
            ]
            called_by = [
                self._nodes[c].name for c in edges.called_by if c in self._nodes
            ]
            inherits = [
                self._nodes[c].name for c in edges.inherits if c in self._nodes
            ]
            # SQL relationships
            references = [
                self._nodes[c].name for c in edges.references if c in self._nodes
            ]
            referenced_by = [
                self._nodes[c].name for c in edges.referenced_by if c in self._nodes
            ]

            if calls or called_by or inherits or references or referenced_by:
                context[sid] = {}
                if calls:
                    context[sid]["calls"] = calls
                if called_by:
                    context[sid]["called_by"] = called_by
                if inherits:
                    context[sid]["inherits"] = inherits
                if references:
                    context[sid]["references"] = references
                if referenced_by:
                    context[sid]["referenced_by"] = referenced_by

        return context
