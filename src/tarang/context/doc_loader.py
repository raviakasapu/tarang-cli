"""
KB Documentation Loader for CLI.

Loads project-specific knowledge documentation from .tarang/docs/ directory.
These documents provide implementation details, patterns, and business logic
that help agents understand how to work with the specific project.

Directory structure:
    project/
    └── .tarang/
        └── docs/
            ├── api/
            │   └── authentication.md
            ├── database/
            │   └── schema.md
            └── patterns/
                └── error_handling.md

Document format:
    ---
    tags: [api, auth, jwt]
    priority: high
    summary: How to implement JWT authentication
    ---

    # JWT Authentication

    ## Overview
    ...
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

logger = logging.getLogger(__name__)


@dataclass
class KBDocSection:
    """A section within a KB document."""
    heading: str
    level: int  # 1 = #, 2 = ##, etc.
    content: str
    code_blocks: List[Dict[str, str]] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0


@dataclass
class KBDoc:
    """A KB documentation entry."""
    id: str
    title: str
    file_path: str
    tags: List[str]
    topics: List[str]
    summary: str
    sections: List[KBDocSection]
    full_content: str
    priority: int = 50
    properties: Dict[str, Any] = field(default_factory=dict)

    def get_section(self, heading: str) -> Optional[KBDocSection]:
        """Get a section by heading."""
        heading_lower = heading.lower()
        for section in self.sections:
            if section.heading.lower() == heading_lower:
                return section
        return None

    def get_code_examples(self, language: Optional[str] = None) -> List[Dict[str, str]]:
        """Get all code examples, optionally filtered by language."""
        examples = []
        for section in self.sections:
            for block in section.code_blocks:
                if language is None or block.get("language", "").lower() == language.lower():
                    examples.append(block)
        return examples

    def to_context_block(self, max_chars: int = 2000, include_code: bool = True) -> str:
        """Format document for context injection."""
        lines = [f"## {self.title}"]

        if self.summary:
            lines.append(f"\n{self.summary}")

        char_count = sum(len(line) for line in lines)

        for section in self.sections:
            section_text = f"\n### {section.heading}\n{section.content}"

            if include_code and section.code_blocks:
                for block in section.code_blocks[:2]:
                    lang = block.get("language", "")
                    code = block.get("code", "")
                    if len(code) < 500:
                        section_text += f"\n```{lang}\n{code}\n```"

            if char_count + len(section_text) > max_chars:
                break

            lines.append(section_text)
            char_count += len(section_text)

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "file_path": self.file_path,
            "tags": self.tags,
            "topics": self.topics,
            "summary": self.summary,
            "priority": self.priority,
        }


class KBDocLoader:
    """
    Loads KB documentation from project's docs/kb/ directory.
    """

    PRIORITY_MAP = {
        "critical": 100,
        "high": 80,
        "medium": 50,
        "low": 20,
    }

    def __init__(self, project_root: str, kb_docs_dir: str = ".tarang/docs"):
        self.project_root = Path(project_root)
        self.kb_docs_path = self.project_root / kb_docs_dir
        self._docs_cache: Dict[str, KBDoc] = {}
        self._file_hashes: Dict[str, str] = {}

    @property
    def is_available(self) -> bool:
        """Check if KB docs directory exists."""
        return self.kb_docs_path.exists()

    def load_all(self) -> List[KBDoc]:
        """Load all KB documents."""
        if not self.kb_docs_path.exists():
            return []

        docs = []
        for md_file in self.kb_docs_path.rglob("*.md"):
            doc = self._load_doc(md_file)
            if doc:
                docs.append(doc)

        logger.debug(f"[KB DOCS] Loaded {len(docs)} documents from {self.kb_docs_path}")
        return docs

    def _load_doc(self, path: Path) -> Optional[KBDoc]:
        """Load and parse a single document."""
        try:
            content = path.read_text(encoding="utf-8")
            rel_path = str(path.relative_to(self.project_root))

            # Check cache
            content_hash = hashlib.md5(content.encode()).hexdigest()[:12]
            if rel_path in self._docs_cache and self._file_hashes.get(rel_path) == content_hash:
                return self._docs_cache[rel_path]

            # Parse frontmatter
            frontmatter, body = self._parse_frontmatter(content)

            # Extract tags
            tags = list(frontmatter.get("tags", []))
            rel_to_kb = path.relative_to(self.kb_docs_path)
            if len(rel_to_kb.parts) > 1:
                tags.extend(rel_to_kb.parts[:-1])
            tags.append(path.stem.replace("_", "-"))
            tags = list(set(t.lower() for t in tags))

            # Parse sections
            sections = self._parse_sections(body)

            # Extract topics
            topics = [s.heading for s in sections if s.level <= 2]

            # Get title
            title = frontmatter.get("title", "")
            if not title:
                for section in sections:
                    if section.level == 1:
                        title = section.heading
                        break
                if not title:
                    title = path.stem.replace("_", " ").title()

            # Get summary
            summary = frontmatter.get("summary", "")
            if not summary and sections:
                first_content = sections[0].content.strip()
                if first_content:
                    summary = first_content.split("\n\n")[0][:200]

            # Get priority
            priority_str = frontmatter.get("priority", "medium")
            if isinstance(priority_str, int):
                priority = priority_str
            else:
                priority = self.PRIORITY_MAP.get(str(priority_str).lower(), 50)

            doc = KBDoc(
                id=rel_path,
                title=title,
                file_path=rel_path,
                tags=tags,
                topics=topics,
                summary=summary,
                sections=sections,
                full_content=body,
                priority=priority,
                properties=frontmatter,
            )

            self._docs_cache[rel_path] = doc
            self._file_hashes[rel_path] = content_hash
            return doc

        except Exception as e:
            logger.warning(f"Failed to load KB doc {path}: {e}")
            return None

    def _parse_frontmatter(self, content: str) -> Tuple[Dict[str, Any], str]:
        """Parse YAML frontmatter from content."""
        if not content.startswith("---"):
            return {}, content

        end_match = re.search(r"\n---\s*\n", content[3:])
        if not end_match:
            return {}, content

        frontmatter_text = content[3:end_match.start() + 3]
        body = content[end_match.end() + 3:].strip()

        if HAS_YAML:
            try:
                frontmatter = yaml.safe_load(frontmatter_text) or {}
            except yaml.YAMLError:
                frontmatter = {}
        else:
            # Simple fallback parsing without yaml
            frontmatter = {}
            for line in frontmatter_text.split("\n"):
                if ":" in line:
                    key, _, value = line.partition(":")
                    key = key.strip()
                    value = value.strip()
                    if value.startswith("[") and value.endswith("]"):
                        value = [v.strip().strip("'\"") for v in value[1:-1].split(",")]
                    frontmatter[key] = value

        return frontmatter, body

    def _parse_sections(self, content: str) -> List[KBDocSection]:
        """Parse markdown content into sections."""
        sections = []
        lines = content.split("\n")

        current_section: Optional[KBDocSection] = None
        current_content_lines: List[str] = []
        in_code_block = False
        code_block_lang = ""
        code_block_lines: List[str] = []

        for i, line in enumerate(lines):
            if line.startswith("```"):
                if in_code_block:
                    if current_section:
                        current_section.code_blocks.append({
                            "language": code_block_lang,
                            "code": "\n".join(code_block_lines),
                        })
                    code_block_lines = []
                    in_code_block = False
                else:
                    code_block_lang = line[3:].strip()
                    in_code_block = True
                continue

            if in_code_block:
                code_block_lines.append(line)
                continue

            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                if current_section:
                    current_section.content = "\n".join(current_content_lines).strip()
                    current_section.end_line = i
                    sections.append(current_section)

                level = len(heading_match.group(1))
                heading = heading_match.group(2).strip()
                current_section = KBDocSection(
                    heading=heading,
                    level=level,
                    content="",
                    start_line=i + 1,
                )
                current_content_lines = []
            else:
                current_content_lines.append(line)

        if current_section:
            current_section.content = "\n".join(current_content_lines).strip()
            current_section.end_line = len(lines)
            sections.append(current_section)

        return sections

    def search(
        self,
        query: str,
        tags: Optional[List[str]] = None,
        limit: int = 5,
    ) -> List[KBDoc]:
        """Search KB documents."""
        all_docs = self.load_all()
        query_lower = query.lower()
        query_words = set(query_lower.split())

        scored_docs: List[Tuple[int, KBDoc]] = []

        for doc in all_docs:
            if tags:
                if not any(t.lower() in doc.tags for t in tags):
                    continue

            score = 0

            if query_lower in doc.title.lower():
                score += 100
            elif any(w in doc.title.lower() for w in query_words):
                score += 50

            for topic in doc.topics:
                if query_lower in topic.lower():
                    score += 80
                elif any(w in topic.lower() for w in query_words):
                    score += 30

            for tag in doc.tags:
                if query_lower == tag:
                    score += 60
                elif any(w == tag for w in query_words):
                    score += 40

            if query_lower in doc.full_content.lower():
                score += 20

            score += doc.priority // 10

            if score > 0:
                scored_docs.append((score, doc))

        scored_docs.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored_docs[:limit]]

    def get_by_tags(self, tags: List[str], max_chars: int = 3000) -> List[KBDoc]:
        """Get documents matching tags."""
        all_docs = self.load_all()
        tags_lower = [t.lower() for t in tags]

        matching = []
        for doc in all_docs:
            doc_tags_lower = [t.lower() for t in doc.tags]
            if any(t in doc_tags_lower for t in tags_lower):
                matching.append(doc)

        matching.sort(key=lambda d: d.priority, reverse=True)

        result = []
        total_chars = 0
        for doc in matching:
            doc_chars = len(doc.full_content)
            if total_chars + doc_chars > max_chars:
                if max_chars - total_chars > 500:
                    result.append(doc)
                break
            result.append(doc)
            total_chars += doc_chars

        return result

    def format_for_context(
        self,
        docs: List[KBDoc],
        max_chars: int = 2000,
        include_code: bool = True,
    ) -> str:
        """Format docs for context injection."""
        if not docs:
            return ""

        parts = ["== PROJECT KB DOCS =="]
        char_count = len(parts[0])

        for doc in docs:
            remaining = max_chars - char_count - 50
            if remaining < 200:
                break

            doc_block = doc.to_context_block(max_chars=remaining, include_code=include_code)
            parts.append(doc_block)
            char_count += len(doc_block)

        parts.append("== END KB DOCS ==")
        return "\n\n".join(parts)

    def stats(self) -> Dict[str, Any]:
        """Get statistics."""
        docs = self.load_all()
        all_tags = set()
        for doc in docs:
            all_tags.update(doc.tags)

        return {
            "total_docs": len(docs),
            "unique_tags": len(all_tags),
            "tags": sorted(all_tags),
            "available": self.is_available,
        }
