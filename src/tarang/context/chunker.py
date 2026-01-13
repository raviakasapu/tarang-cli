"""
Code Chunker - AST-based code parsing using tree-sitter.

Extracts semantic chunks (functions, classes, methods) from source files
for efficient indexing and retrieval.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Tree-sitter imports (lazy loaded)
_ts_python = None
_ts_javascript = None
_ts_sql = None


def _wrap_language(lang_ptr, name: str):
    """Wrap language pointer for tree-sitter 0.21+ compatibility."""
    try:
        from tree_sitter import Language
        # New API: wrap PyCapsule with Language
        return Language(lang_ptr)
    except TypeError:
        # Older API: Language expects (library_path, name) or already wrapped
        return lang_ptr


def _get_python_language():
    """Lazy load Python language."""
    global _ts_python
    if _ts_python is None:
        try:
            import tree_sitter_python as tspython
            _ts_python = _wrap_language(tspython.language(), "python")
        except ImportError:
            return None
    return _ts_python


def _get_javascript_language():
    """Lazy load JavaScript/TypeScript language."""
    global _ts_javascript
    if _ts_javascript is None:
        try:
            # Try typescript first (handles .ts, .tsx, .js, .jsx)
            import tree_sitter_typescript as tsts
            _ts_javascript = _wrap_language(tsts.language_tsx(), "tsx")
        except ImportError:
            try:
                import tree_sitter_javascript as tsjs
                _ts_javascript = _wrap_language(tsjs.language(), "javascript")
            except ImportError:
                return None
    return _ts_javascript


def _get_sql_language():
    """Lazy load SQL language."""
    global _ts_sql
    if _ts_sql is None:
        try:
            import tree_sitter_sql as tssql
            _ts_sql = _wrap_language(tssql.language(), "sql")
        except ImportError:
            return None
    return _ts_sql


@dataclass
class Chunk:
    """A semantic code chunk extracted from source."""
    id: str                         # Unique ID: "file.py:function_name"
    file: str                       # Relative file path
    type: str                       # "function" | "method" | "class" | "module"
    name: str                       # Symbol name
    signature: str                  # Function/class signature line
    content: str                    # Full code content
    line_start: int                 # Starting line (1-indexed)
    line_end: int                   # Ending line (1-indexed)
    tokens: List[str] = field(default_factory=list)  # Tokenized for BM25
    parent: Optional[str] = None    # Parent class for methods

    @property
    def hash(self) -> str:
        """Content hash for change detection."""
        return hashlib.sha256(self.content.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "file": self.file,
            "type": self.type,
            "name": self.name,
            "signature": self.signature,
            "content": self.content,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "tokens": self.tokens,
            "parent": self.parent,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Chunk":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            file=data["file"],
            type=data["type"],
            name=data["name"],
            signature=data["signature"],
            content=data["content"],
            line_start=data["line_start"],
            line_end=data["line_end"],
            tokens=data.get("tokens", []),
            parent=data.get("parent"),
        )


@dataclass
class SymbolInfo:
    """Information about a symbol for graph building."""
    id: str                         # "file.py:function_name"
    name: str                       # Symbol name
    type: str                       # "function" | "method" | "class"
    file: str                       # File path
    line: int                       # Definition line
    signature: str                  # Signature
    calls: List[str] = field(default_factory=list)      # Functions called
    imports: List[str] = field(default_factory=list)    # Modules imported
    parent_class: Optional[str] = None                   # For methods


class Chunker:
    """
    AST-based code chunker using tree-sitter.

    Extracts functions, classes, and methods as semantic chunks.
    """

    # Supported file extensions
    LANGUAGE_MAP = {
        # Python
        ".py": "python",
        ".pyw": "python",
        # JavaScript/TypeScript
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "javascript",  # tree-sitter-javascript handles TS basics
        ".tsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        # SQL
        ".sql": "sql",
    }

    # Max lines per chunk (split if larger)
    MAX_CHUNK_LINES = 200

    # Max file size to process (100KB)
    MAX_FILE_SIZE = 100 * 1024

    def __init__(self):
        self._parsers: Dict[str, any] = {}

    def _get_parser(self, language: str):
        """Get or create parser for language."""
        if language in self._parsers:
            return self._parsers[language]

        try:
            from tree_sitter import Parser
        except ImportError:
            return None

        lang = None
        if language == "python":
            lang = _get_python_language()
        elif language in ("javascript", "typescript", "tsx", "jsx"):
            lang = _get_javascript_language()
        elif language == "sql":
            lang = _get_sql_language()

        if lang is None:
            return None

        # Create parser - handle both old and new tree-sitter API
        parser = Parser()
        try:
            # New API (0.21+): set language via property
            parser.language = lang
        except AttributeError:
            # Old API: pass language to constructor (already created above, need to recreate)
            try:
                parser = Parser(lang)
            except TypeError:
                return None

        self._parsers[language] = parser
        return parser

    def chunk_file(self, file_path: Path, project_root: Path) -> Tuple[List[Chunk], List[SymbolInfo]]:
        """
        Parse a file and extract chunks and symbol info.

        Args:
            file_path: Absolute path to file
            project_root: Project root for relative paths

        Returns:
            Tuple of (chunks, symbols)
        """
        # Get relative path
        try:
            rel_path = str(file_path.relative_to(project_root))
        except ValueError:
            rel_path = str(file_path)

        # Check file size
        try:
            if file_path.stat().st_size > self.MAX_FILE_SIZE:
                return [], []
        except OSError:
            return [], []

        # Determine language
        ext = file_path.suffix.lower()
        language = self.LANGUAGE_MAP.get(ext)

        if language is None:
            # Return file as single module chunk for unsupported languages
            return self._chunk_as_module(file_path, rel_path)

        # Get parser
        parser = self._get_parser(language)
        if parser is None:
            return self._chunk_as_module(file_path, rel_path)

        # Read and parse
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return [], []

        tree = parser.parse(content.encode("utf-8"))

        # Extract based on language
        if language == "python":
            return self._extract_python(tree, content, rel_path)
        elif language == "javascript":
            return self._extract_javascript(tree, content, rel_path)
        elif language == "sql":
            return self._extract_sql(tree, content, rel_path)

        return [], []

    def _chunk_as_module(self, file_path: Path, rel_path: str) -> Tuple[List[Chunk], List[SymbolInfo]]:
        """Treat entire file as a single module chunk."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return [], []

        lines = content.splitlines()
        if len(lines) > self.MAX_CHUNK_LINES:
            content = "\n".join(lines[:self.MAX_CHUNK_LINES]) + "\n... (truncated)"

        chunk = Chunk(
            id=f"{rel_path}:module",
            file=rel_path,
            type="module",
            name=Path(rel_path).stem,
            signature=f"# {rel_path}",
            content=content,
            line_start=1,
            line_end=len(lines),
            tokens=self._tokenize(content),
        )

        symbol = SymbolInfo(
            id=chunk.id,
            name=chunk.name,
            type="module",
            file=rel_path,
            line=1,
            signature=chunk.signature,
        )

        return [chunk], [symbol]

    def _extract_python(self, tree, content: str, rel_path: str) -> Tuple[List[Chunk], List[SymbolInfo]]:
        """Extract chunks from Python AST."""
        chunks = []
        symbols = []
        lines = content.splitlines()

        def get_node_text(node) -> str:
            return content[node.start_byte:node.end_byte]

        def get_signature(node) -> str:
            """Extract just the signature line."""
            text = get_node_text(node)
            first_line = text.split("\n")[0]
            # For functions, include up to the colon
            if ":" in first_line:
                return first_line.rstrip()
            return first_line

        def extract_calls(node) -> List[str]:
            """Extract function calls from a node."""
            calls = []

            def walk(n):
                if n.type == "call":
                    func = n.child_by_field_name("function")
                    if func:
                        call_name = get_node_text(func)
                        # Handle method calls: obj.method -> method
                        if "." in call_name:
                            call_name = call_name.split(".")[-1]
                        calls.append(call_name)
                for child in n.children:
                    walk(child)

            walk(node)
            return calls

        def extract_imports(node) -> List[str]:
            """Extract imports from module level."""
            imports = []

            def walk(n):
                if n.type == "import_statement":
                    # import foo, bar
                    for child in n.children:
                        if child.type == "dotted_name":
                            imports.append(get_node_text(child))
                elif n.type == "import_from_statement":
                    # from foo import bar
                    module = n.child_by_field_name("module_name")
                    if module:
                        imports.append(get_node_text(module))
                for child in n.children:
                    if child.type not in ("function_definition", "class_definition"):
                        walk(child)

            walk(node)
            return imports

        # First pass: extract module-level imports
        module_imports = extract_imports(tree.root_node)

        # Process top-level nodes
        current_class = None

        def process_node(node, parent_class=None):
            nonlocal chunks, symbols

            if node.type == "function_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = get_node_text(name_node)
                    node_content = get_node_text(node)

                    chunk_type = "method" if parent_class else "function"
                    chunk_id = f"{rel_path}:{parent_class}.{name}" if parent_class else f"{rel_path}:{name}"

                    chunk = Chunk(
                        id=chunk_id,
                        file=rel_path,
                        type=chunk_type,
                        name=name,
                        signature=get_signature(node),
                        content=node_content,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        tokens=self._tokenize(node_content),
                        parent=parent_class,
                    )
                    chunks.append(chunk)

                    symbol = SymbolInfo(
                        id=chunk_id,
                        name=name,
                        type=chunk_type,
                        file=rel_path,
                        line=node.start_point[0] + 1,
                        signature=chunk.signature,
                        calls=extract_calls(node),
                        parent_class=parent_class,
                    )
                    symbols.append(symbol)

            elif node.type == "class_definition":
                name_node = node.child_by_field_name("name")
                if name_node:
                    class_name = get_node_text(name_node)
                    node_content = get_node_text(node)

                    # Extract class signature (just the class line)
                    class_sig = get_signature(node)

                    # Create class chunk (without method bodies for summary)
                    chunk_id = f"{rel_path}:{class_name}"

                    # Get just class definition without full method bodies
                    class_summary = self._get_class_summary(node, content)

                    chunk = Chunk(
                        id=chunk_id,
                        file=rel_path,
                        type="class",
                        name=class_name,
                        signature=class_sig,
                        content=class_summary,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        tokens=self._tokenize(class_summary),
                    )
                    chunks.append(chunk)

                    # Extract parent classes
                    superclasses = []
                    args = node.child_by_field_name("superclasses")
                    if args:
                        for arg in args.children:
                            if arg.type == "identifier":
                                superclasses.append(get_node_text(arg))

                    symbol = SymbolInfo(
                        id=chunk_id,
                        name=class_name,
                        type="class",
                        file=rel_path,
                        line=node.start_point[0] + 1,
                        signature=class_sig,
                        imports=superclasses,  # Reuse imports for inheritance
                    )
                    symbols.append(symbol)

                    # Process methods inside class
                    body = node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            process_node(child, parent_class=class_name)

        # Process all top-level nodes
        for child in tree.root_node.children:
            process_node(child)

        # Add module-level symbol with imports
        if module_imports:
            module_symbol = SymbolInfo(
                id=f"{rel_path}:module",
                name=Path(rel_path).stem,
                type="module",
                file=rel_path,
                line=1,
                signature=f"# {rel_path}",
                imports=module_imports,
            )
            symbols.append(module_symbol)

        return chunks, symbols

    def _get_class_summary(self, class_node, content: str) -> str:
        """Get class with method signatures only (not full bodies)."""
        lines = []

        def get_node_text(node) -> str:
            return content[node.start_byte:node.end_byte]

        # Get class definition line
        first_line = get_node_text(class_node).split("\n")[0]
        lines.append(first_line)

        # Get docstring if present
        body = class_node.child_by_field_name("body")
        if body and body.children:
            first_child = body.children[0]
            if first_child.type == "expression_statement":
                expr = first_child.children[0] if first_child.children else None
                if expr and expr.type == "string":
                    docstring = get_node_text(expr)
                    # Indent docstring
                    for doc_line in docstring.split("\n"):
                        lines.append("    " + doc_line)

        # Get method signatures
        if body:
            for child in body.children:
                if child.type == "function_definition":
                    sig = get_node_text(child).split("\n")[0]
                    lines.append("    " + sig)
                    lines.append("        ...")

        return "\n".join(lines)

    def _extract_javascript(self, tree, content: str, rel_path: str) -> Tuple[List[Chunk], List[SymbolInfo]]:
        """Extract chunks from JavaScript/TypeScript AST."""
        chunks = []
        symbols = []

        def get_node_text(node) -> str:
            return content[node.start_byte:node.end_byte]

        def get_signature(node) -> str:
            """Extract just the signature line."""
            text = get_node_text(node)
            first_line = text.split("\n")[0]
            # Truncate at opening brace
            if "{" in first_line:
                return first_line[:first_line.index("{")].strip() + " {"
            return first_line

        def extract_calls(node) -> List[str]:
            """Extract function calls."""
            calls = []

            def walk(n):
                if n.type == "call_expression":
                    func = n.child_by_field_name("function")
                    if func:
                        call_name = get_node_text(func)
                        if "." in call_name:
                            call_name = call_name.split(".")[-1]
                        calls.append(call_name)
                for child in n.children:
                    walk(child)

            walk(node)
            return calls

        def process_node(node, parent_class=None):
            nonlocal chunks, symbols

            # Function declarations
            if node.type in ("function_declaration", "function"):
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = get_node_text(name_node)
                    node_content = get_node_text(node)

                    chunk_id = f"{rel_path}:{name}"

                    chunk = Chunk(
                        id=chunk_id,
                        file=rel_path,
                        type="function",
                        name=name,
                        signature=get_signature(node),
                        content=node_content,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        tokens=self._tokenize(node_content),
                    )
                    chunks.append(chunk)

                    symbol = SymbolInfo(
                        id=chunk_id,
                        name=name,
                        type="function",
                        file=rel_path,
                        line=node.start_point[0] + 1,
                        signature=chunk.signature,
                        calls=extract_calls(node),
                    )
                    symbols.append(symbol)

            # Arrow functions assigned to variables
            elif node.type == "lexical_declaration":
                for decl in node.children:
                    if decl.type == "variable_declarator":
                        name_node = decl.child_by_field_name("name")
                        value_node = decl.child_by_field_name("value")
                        if name_node and value_node and value_node.type == "arrow_function":
                            name = get_node_text(name_node)
                            node_content = get_node_text(node)

                            chunk_id = f"{rel_path}:{name}"

                            chunk = Chunk(
                                id=chunk_id,
                                file=rel_path,
                                type="function",
                                name=name,
                                signature=get_signature(node),
                                content=node_content,
                                line_start=node.start_point[0] + 1,
                                line_end=node.end_point[0] + 1,
                                tokens=self._tokenize(node_content),
                            )
                            chunks.append(chunk)

                            symbol = SymbolInfo(
                                id=chunk_id,
                                name=name,
                                type="function",
                                file=rel_path,
                                line=node.start_point[0] + 1,
                                signature=chunk.signature,
                                calls=extract_calls(value_node),
                            )
                            symbols.append(symbol)

            # Class declarations
            elif node.type == "class_declaration":
                name_node = node.child_by_field_name("name")
                if name_node:
                    class_name = get_node_text(name_node)
                    node_content = get_node_text(node)

                    chunk_id = f"{rel_path}:{class_name}"

                    chunk = Chunk(
                        id=chunk_id,
                        file=rel_path,
                        type="class",
                        name=class_name,
                        signature=get_signature(node),
                        content=node_content,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        tokens=self._tokenize(node_content),
                    )
                    chunks.append(chunk)

                    symbol = SymbolInfo(
                        id=chunk_id,
                        name=class_name,
                        type="class",
                        file=rel_path,
                        line=node.start_point[0] + 1,
                        signature=chunk.signature,
                    )
                    symbols.append(symbol)

            # Recurse into children
            for child in node.children:
                process_node(child, parent_class)

        # Process all nodes
        for child in tree.root_node.children:
            process_node(child)

        return chunks, symbols

    def _tokenize(self, content: str) -> List[str]:
        """
        Tokenize content for BM25 indexing.

        Handles:
        - snake_case splitting
        - camelCase splitting
        - Code-specific tokens
        """
        # Split on whitespace and punctuation
        words = re.findall(r'\b\w+\b', content.lower())

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

        # Filter very short tokens and common keywords
        stop_words = {
            "def", "class", "self", "return", "if", "else", "elif", "for",
            "while", "try", "except", "finally", "with", "as", "import",
            "from", "in", "is", "not", "and", "or", "true", "false", "none",
            "function", "const", "let", "var", "this", "new", "async", "await",
        }

        return [t for t in tokens if len(t) > 2 and t not in stop_words]

    def _extract_sql(self, tree, content: str, rel_path: str) -> Tuple[List[Chunk], List[SymbolInfo]]:
        """
        Extract chunks from SQL AST.

        Handles:
        - CREATE TABLE statements
        - CREATE VIEW statements
        - CREATE FUNCTION/PROCEDURE statements
        - CREATE INDEX statements
        - CREATE TRIGGER statements
        """
        chunks = []
        symbols = []

        def get_node_text(node) -> str:
            return content[node.start_byte:node.end_byte]

        def extract_identifier(node):
            """Extract identifier name from various node structures."""
            if node is None:
                return None

            # Direct identifier
            if node.type == "identifier":
                return get_node_text(node)

            # Object reference (schema.table)
            if node.type == "object_reference":
                parts = []
                for child in node.children:
                    if child.type == "identifier":
                        parts.append(get_node_text(child))
                return ".".join(parts) if parts else None

            # Search children for identifier
            for child in node.children:
                if child.type == "identifier":
                    return get_node_text(child)
                if child.type == "object_reference":
                    return extract_identifier(child)

            return None

        def extract_table_refs(node) -> List[str]:
            """Extract table references from a statement (for views, functions)."""
            refs = []

            def walk(n):
                if n.type in ("object_reference", "table_reference"):
                    name = extract_identifier(n)
                    if name:
                        refs.append(name)
                elif n.type == "identifier" and n.parent and n.parent.type in (
                    "from_clause", "join_clause", "table_expression"
                ):
                    refs.append(get_node_text(n))
                for child in n.children:
                    walk(child)

            walk(node)
            return list(set(refs))

        def process_statement(node):
            """Process a SQL statement node."""
            node_type = node.type.lower()
            node_content = get_node_text(node)

            # CREATE TABLE
            if "create" in node_type and "table" in node_type:
                name = None
                # Find table name
                for child in node.children:
                    if child.type in ("object_reference", "identifier"):
                        name = extract_identifier(child)
                        if name:
                            break

                if name:
                    # Extract column names for signature
                    columns = []
                    for child in node.children:
                        if child.type == "column_definitions":
                            for col_def in child.children:
                                if col_def.type == "column_definition":
                                    col_name = extract_identifier(col_def)
                                    if col_name:
                                        columns.append(col_name)

                    signature = f"CREATE TABLE {name}"
                    if columns:
                        signature += f" ({', '.join(columns[:5])}{'...' if len(columns) > 5 else ''})"

                    chunk_id = f"{rel_path}:table:{name}"
                    chunk = Chunk(
                        id=chunk_id,
                        file=rel_path,
                        type="table",
                        name=name,
                        signature=signature,
                        content=node_content,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        tokens=self._tokenize(node_content),
                    )
                    chunks.append(chunk)

                    symbol = SymbolInfo(
                        id=chunk_id,
                        name=name,
                        type="table",
                        file=rel_path,
                        line=node.start_point[0] + 1,
                        signature=signature,
                    )
                    symbols.append(symbol)

            # CREATE VIEW
            elif "create" in node_type and "view" in node_type:
                name = None
                for child in node.children:
                    if child.type in ("object_reference", "identifier"):
                        name = extract_identifier(child)
                        if name:
                            break

                if name:
                    table_refs = extract_table_refs(node)
                    signature = f"CREATE VIEW {name}"

                    chunk_id = f"{rel_path}:view:{name}"
                    chunk = Chunk(
                        id=chunk_id,
                        file=rel_path,
                        type="view",
                        name=name,
                        signature=signature,
                        content=node_content,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        tokens=self._tokenize(node_content),
                    )
                    chunks.append(chunk)

                    symbol = SymbolInfo(
                        id=chunk_id,
                        name=name,
                        type="view",
                        file=rel_path,
                        line=node.start_point[0] + 1,
                        signature=signature,
                        imports=table_refs,  # Views depend on tables
                    )
                    symbols.append(symbol)

            # CREATE FUNCTION / CREATE PROCEDURE
            elif "create" in node_type and ("function" in node_type or "procedure" in node_type):
                name = None
                obj_type = "procedure" if "procedure" in node_type else "function"

                for child in node.children:
                    if child.type in ("object_reference", "identifier", "function_name"):
                        name = extract_identifier(child)
                        if name:
                            break

                if name:
                    table_refs = extract_table_refs(node)
                    signature = f"CREATE {obj_type.upper()} {name}()"

                    chunk_id = f"{rel_path}:{obj_type}:{name}"
                    chunk = Chunk(
                        id=chunk_id,
                        file=rel_path,
                        type=obj_type,
                        name=name,
                        signature=signature,
                        content=node_content,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        tokens=self._tokenize(node_content),
                    )
                    chunks.append(chunk)

                    symbol = SymbolInfo(
                        id=chunk_id,
                        name=name,
                        type=obj_type,
                        file=rel_path,
                        line=node.start_point[0] + 1,
                        signature=signature,
                        imports=table_refs,  # Functions/procedures reference tables
                    )
                    symbols.append(symbol)

            # CREATE INDEX
            elif "create" in node_type and "index" in node_type:
                index_name = None
                table_name = None

                for child in node.children:
                    if child.type in ("object_reference", "identifier"):
                        if index_name is None:
                            index_name = extract_identifier(child)
                        else:
                            table_name = extract_identifier(child)
                            break

                if index_name:
                    signature = f"CREATE INDEX {index_name}"
                    if table_name:
                        signature += f" ON {table_name}"

                    chunk_id = f"{rel_path}:index:{index_name}"
                    chunk = Chunk(
                        id=chunk_id,
                        file=rel_path,
                        type="index",
                        name=index_name,
                        signature=signature,
                        content=node_content,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        tokens=self._tokenize(node_content),
                    )
                    chunks.append(chunk)

                    symbol = SymbolInfo(
                        id=chunk_id,
                        name=index_name,
                        type="index",
                        file=rel_path,
                        line=node.start_point[0] + 1,
                        signature=signature,
                        imports=[table_name] if table_name else [],
                    )
                    symbols.append(symbol)

            # CREATE TRIGGER
            elif "create" in node_type and "trigger" in node_type:
                trigger_name = None
                table_name = None

                for child in node.children:
                    if child.type in ("object_reference", "identifier"):
                        if trigger_name is None:
                            trigger_name = extract_identifier(child)
                        else:
                            table_name = extract_identifier(child)
                            break

                if trigger_name:
                    signature = f"CREATE TRIGGER {trigger_name}"
                    if table_name:
                        signature += f" ON {table_name}"

                    chunk_id = f"{rel_path}:trigger:{trigger_name}"
                    chunk = Chunk(
                        id=chunk_id,
                        file=rel_path,
                        type="trigger",
                        name=trigger_name,
                        signature=signature,
                        content=node_content,
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        tokens=self._tokenize(node_content),
                    )
                    chunks.append(chunk)

                    symbol = SymbolInfo(
                        id=chunk_id,
                        name=trigger_name,
                        type="trigger",
                        file=rel_path,
                        line=node.start_point[0] + 1,
                        signature=signature,
                        imports=[table_name] if table_name else [],
                    )
                    symbols.append(symbol)

        # Walk the AST and process statements
        def walk(node):
            node_type = node.type.lower()

            # Check if this is a CREATE statement
            if "create" in node_type or node.type == "statement":
                process_statement(node)
            else:
                for child in node.children:
                    walk(child)

        walk(tree.root_node)

        # If no chunks extracted, fall back to module chunk
        if not chunks:
            return self._chunk_as_module(Path(rel_path), rel_path)

        return chunks, symbols
