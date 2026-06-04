"""
Tree-sitter AST API for n8n / HTTP clients.
Parses source text and returns JSON syntax trees (or S-expression strings).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from tree_sitter import Node, Tree
from tree_sitter_languages import get_parser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ast-treesitter-api")

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8008"))
MAX_TREE_DEPTH = int(os.environ.get("MAX_TREE_DEPTH", "512"))
MAX_NODES = int(os.environ.get("MAX_NODES", "200_000"))

# Candidate names for tree-sitter-languages (subset that exists in bundled build).
_LANGUAGE_CANDIDATES: tuple[str, ...] = (
    "bash",
    "c",
    "c_sharp",
    "clojure",
    "cpp",
    "css",
    "dart",
    "dockerfile",
    "elixir",
    "go",
    "hcl",
    "html",
    "java",
    "javascript",
    "json",
    "julia",
    "kotlin",
    "lua",
    "php",
    "python",
    "r",
    "ruby",
    "rust",
    "scala",
    "sql",
    "toml",
    "tsx",
    "typescript",
    "yaml",
)


def _discover_languages() -> list[str]:
    out: list[str] = []
    for name in _LANGUAGE_CANDIDATES:
        try:
            get_parser(name)
            out.append(name)
        except Exception:
            continue
    return sorted(out)


SUPPORTED_LANGUAGES: list[str] = _discover_languages()
logger.info("Loaded grammars: %s", ", ".join(SUPPORTED_LANGUAGES) or "(none)")

# ---------------------------------------------------------------------------
# Chunk boundary node types per language
# ---------------------------------------------------------------------------
# Top-level AST node types that should each become their own chunk.
# Languages not listed (or with empty set) → entire file = one chunk.

_CHUNK_BOUNDARY_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition", "decorated_definition"},
    "javascript": {
        "function_declaration", "class_declaration", "export_statement",
        "lexical_declaration",
    },
    "typescript": {
        "function_declaration", "class_declaration", "export_statement",
        "lexical_declaration", "interface_declaration", "type_alias_declaration",
        "enum_declaration", "module",
    },
    "tsx": {
        "function_declaration", "class_declaration", "export_statement",
        "lexical_declaration", "interface_declaration", "type_alias_declaration",
        "enum_declaration", "module",
    },
    "java": {
        "class_declaration", "interface_declaration", "enum_declaration",
        "annotation_type_declaration", "record_declaration",
    },
    "c": {
        "function_definition", "struct_specifier", "enum_specifier",
        "union_specifier", "type_definition",
    },
    "cpp": {
        "function_definition", "class_specifier", "struct_specifier",
        "enum_specifier", "namespace_definition", "template_declaration",
        "type_definition",
    },
    "c_sharp": {
        "class_declaration", "struct_declaration", "interface_declaration",
        "enum_declaration", "namespace_declaration", "method_declaration",
        "record_declaration",
    },
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "rust": {
        "function_item", "struct_item", "enum_item", "impl_item",
        "trait_item", "mod_item", "type_item", "const_item", "static_item",
        "macro_definition",
    },
    "ruby": {"method", "class", "module", "singleton_method"},
    "php": {
        "function_definition", "class_declaration", "interface_declaration",
        "trait_declaration", "enum_declaration", "namespace_definition",
    },
    "kotlin": {
        "function_declaration", "class_declaration", "object_declaration",
        "interface_declaration",
    },
    "scala": {
        "function_definition", "class_definition", "object_definition",
        "trait_definition",
    },
    "dart": {
        "function_signature", "class_definition", "enum_declaration",
        "extension_declaration", "mixin_declaration",
    },
    "lua": {"function_declaration", "local_function_declaration"},
    "elixir": {"call"},
    "bash": {"function_definition"},
    "clojure": {"list_lit"},
    "sql": {
        "select_statement", "create_table_statement", "insert_statement",
        "update_statement", "delete_statement", "drop_statement",
        "create_index_statement", "alter_statement",
    },
    "r": {"left_assignment", "equals_assignment", "right_assignment"},
    "julia": {
        "function_definition", "struct_definition", "module_definition",
        "abstract_definition", "macro_definition",
    },
    # No definition boundaries – return whole file as one chunk
    "html": set(),
    "css": set(),
    "json": set(),
    "toml": set(),
    "yaml": set(),
    "hcl": set(),
    "dockerfile": set(),
}

# Node types that should attach to the *following* definition as context.
_CONTEXT_NODE_TYPES: dict[str, set[str]] = {
    "rust": {"attribute_item", "line_comment", "block_comment"},
    "java": {"marker_annotation", "annotation", "line_comment", "block_comment"},
    "kotlin": {"annotation", "line_comment", "multiline_comment"},
    "c_sharp": {"attribute_list", "comment"},
}


def _is_context_node(node: Node, language: str) -> bool:
    """Return True if *node* is a comment / decorator that belongs to the next definition."""
    if node.type == "comment":
        return True
    extra = _CONTEXT_NODE_TYPES.get(language)
    if extra and node.type in extra:
        return True
    return False


# ---------------------------------------------------------------------------
# Extension / strategy resolution
# ---------------------------------------------------------------------------
# Maps a file extension to either a tree-sitter grammar name, or the special
# plain-chunking strategies "markdown" / "text".

_EXTENSION_MAP: dict[str, str] = {
    # markdown
    ".md": "markdown", ".markdown": "markdown", ".mdx": "markdown",
    ".mkd": "markdown", ".mdown": "markdown",
    # plain text
    ".txt": "text", ".text": "text", ".log": "text",
    ".rst": "text", ".csv": "text", ".tsv": "text",
    # code -> grammar name
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".cs": "c_sharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin", ".kts": "kotlin",
    ".scala": "scala",
    ".lua": "lua",
    ".ex": "elixir", ".exs": "elixir",
    ".sh": "bash", ".bash": "bash",
    ".html": "html", ".htm": "html",
    ".css": "css",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml", ".yml": "yaml",
    # extensions missing from original map
    ".dart": "dart",
    ".clj": "clojure", ".cljs": "clojure", ".cljc": "clojure",
    # newly added grammars
    ".sql": "sql",
    ".r": "r",
    ".tf": "hcl", ".hcl": "hcl",
    ".jl": "julia",
    ".dockerfile": "dockerfile",
}

_VALID_MODES = {"auto", "ast", "markdown", "text"}


def _ext_of(filename: str) -> str:
    return os.path.splitext(filename)[1].lower()


def _resolve_grammar(language: Optional[str], filename: Optional[str]) -> Optional[str]:
    """Return a supported grammar name from language or filename, else None."""
    if language:
        lang = language.strip().lower()
        if lang in SUPPORTED_LANGUAGES:
            return lang
    if filename:
        mapped = _EXTENSION_MAP.get(_ext_of(filename))
        if mapped and mapped in SUPPORTED_LANGUAGES:
            return mapped
    return None


def _resolve_strategy(
    filename: Optional[str],
    language: Optional[str],
    mode: Optional[str],
) -> tuple[str, Optional[str]]:
    """Resolve (strategy, grammar).

    strategy in {"ast", "markdown", "text"}; grammar is the tree-sitter name
    when strategy == "ast", else None. Raises HTTPException(400) when an explicit
    request cannot be satisfied.
    """
    m = (mode or "auto").strip().lower()
    if m not in _VALID_MODES:
        raise HTTPException(400, f"invalid mode: {mode!r}. Use auto/ast/markdown/text.")

    if m == "markdown":
        return ("markdown", None)
    if m == "text":
        return ("text", None)
    if m == "ast":
        grammar = _resolve_grammar(language, filename)
        if grammar is None:
            raise HTTPException(
                400,
                "mode=ast requires a known grammar via language or filename. "
                "See GET /v1/languages.",
            )
        return ("ast", grammar)

    # m == "auto"
    if filename:
        mapped = _EXTENSION_MAP.get(_ext_of(filename))
        if mapped == "markdown":
            return ("markdown", None)
        if mapped == "text":
            return ("text", None)
        if mapped is not None and mapped in SUPPORTED_LANGUAGES:
            return ("ast", mapped)
        # unknown extension -> fall through to language / text fallback

    if language:
        lang = language.strip().lower()
        if lang == "markdown":
            return ("markdown", None)
        if lang == "text":
            return ("text", None)
        if lang and lang != "auto":
            if lang in SUPPORTED_LANGUAGES:
                return ("ast", lang)
            raise HTTPException(
                400,
                f"unsupported language: {language!r}. "
                f"Use GET /v1/languages for available grammars.",
            )

    return ("text", None)


# ---------------------------------------------------------------------------
# Offset helpers (char-index based, absolute row/column)
# ---------------------------------------------------------------------------

def _point_at(source: str, char_index: int) -> dict[str, int]:
    """Absolute {row, column} of a char index within *source*."""
    prefix = source[:char_index]
    row = prefix.count("\n")
    last_nl = prefix.rfind("\n")
    return {"row": row, "column": char_index - (last_nl + 1)}


def _span_to_chunk(
    source: str,
    cstart: int,
    cend: int,
    chunk_type: str,
    node_type: str,
    heading_path: Optional[str],
) -> dict[str, Any]:
    """Build a chunk dict for source[cstart:cend] with byte/point offsets."""
    text = source[cstart:cend]
    chunk: dict[str, Any] = {
        "chunk_type": chunk_type,
        "node_type": node_type,
        "text": text,
        "start_byte": len(source[:cstart].encode("utf-8")),
        "end_byte": len(source[:cend].encode("utf-8")),
        "start_point": _point_at(source, cstart),
        "end_point": _point_at(source, cend),
    }
    if heading_path is not None:
        chunk["heading_path"] = heading_path
    return chunk


# ---------------------------------------------------------------------------
# Markdown chunking (heading-based, no tree-sitter dependency)
# ---------------------------------------------------------------------------

_ATX_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _iter_lines(source: str):
    """Yield (line_without_newline, line_start_char_offset)."""
    start = 0
    while True:
        nl = source.find("\n", start)
        if nl == -1:
            yield source[start:], start
            break
        yield source[start:nl], start
        start = nl + 1


def _chunk_markdown(source: str) -> list[dict[str, Any]]:
    """Split markdown into sections by ATX headings, tracking heading hierarchy."""
    if not source.strip():
        return []

    sections: list[dict[str, Any]] = []
    heading_stack: list[tuple[int, str]] = []
    in_fence = False
    fence_marker = ""
    sec_start = 0
    sec_heading_path: Optional[str] = None

    def flush(end_char: int) -> None:
        if end_char > sec_start and source[sec_start:end_char].strip():
            sections.append(
                _span_to_chunk(source, sec_start, end_char, "section", "section", sec_heading_path)
            )

    for line, line_start in _iter_lines(source):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            continue

        heading = None if in_fence else _ATX_HEADING_RE.match(line)
        if heading:
            flush(line_start)
            level = len(heading.group(1))
            text = heading.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, text))
            sec_heading_path = " > ".join(t for _, t in heading_stack)
            sec_start = line_start

    flush(len(source))
    return sections


# ---------------------------------------------------------------------------
# Plain text chunking + recursive size splitting
# ---------------------------------------------------------------------------

_SPLIT_SEPARATORS: tuple[str, ...] = (
    "\n\n", "\n", "。", "．", ". ", "! ", "? ", "！", "？", " ", "",
)


def _chunk_text(source: str) -> list[dict[str, Any]]:
    """Whole text as one chunk; size splitting is applied later."""
    if not source.strip():
        return []
    return [_span_to_chunk(source, 0, len(source), "text", "text", None)]


def _split_keep_offsets(text: str, sep: str) -> list[tuple[int, int]]:
    """Split text by sep, keeping sep attached to the preceding fragment.

    Returns (start, end) char spans whose concatenation reconstructs text.
    sep == "" yields per-character fragments.
    """
    if sep == "":
        return [(i, i + 1) for i in range(len(text))]
    frags: list[tuple[int, int]] = []
    start = 0
    seplen = len(sep)
    idx = text.find(sep, start)
    while idx != -1:
        end = idx + seplen
        frags.append((start, end))
        start = end
        idx = text.find(sep, start)
    if start < len(text):
        frags.append((start, len(text)))
    return frags


def _first_usable_sep(text: str, separators: tuple[str, ...]) -> str:
    for sep in separators:
        if sep == "" or sep in text:
            return sep
    return ""


def _base_spans(text: str, max_size: int, separators: tuple[str, ...]) -> list[tuple[int, int]]:
    """Contiguous, non-overlapping spans covering text, each <= max_size where possible."""
    n = len(text)
    if n <= max_size:
        return [(0, n)]

    sep = _first_usable_sep(text, separators)
    frags = _split_keep_offsets(text, sep)
    next_seps = separators[separators.index(sep) + 1:] or ("",)

    spans: list[tuple[int, int]] = []
    cur_start: Optional[int] = None
    cur_end = 0
    for fs, fe in frags:
        if fe - fs > max_size:
            if cur_start is not None:
                spans.append((cur_start, cur_end))
                cur_start = None
            for rs, re_ in _base_spans(text[fs:fe], max_size, next_seps):
                spans.append((fs + rs, fs + re_))
            continue
        if cur_start is None:
            cur_start, cur_end = fs, fe
        elif fe - cur_start <= max_size:
            cur_end = fe
        else:
            spans.append((cur_start, cur_end))
            cur_start, cur_end = fs, fe
    if cur_start is not None:
        spans.append((cur_start, cur_end))
    return spans


def _split_text_recursive(text: str, max_size: int, overlap: int) -> list[tuple[int, int]]:
    """Spans each <= max_size with `overlap` chars of overlap between neighbors."""
    base = _base_spans(text, max_size, _SPLIT_SEPARATORS)
    if overlap <= 0 or len(base) <= 1:
        return base
    out: list[tuple[int, int]] = [base[0]]
    for s, e in base[1:]:
        out.append((max(0, s - overlap), e))
    return out


def _offset_point(base_point: dict[str, int], text: str, char_index: int) -> dict[str, int]:
    """Absolute {row,column} of char_index within a sub-slice whose start is base_point."""
    prefix = text[:char_index]
    nl = prefix.count("\n")
    if nl == 0:
        return {"row": base_point["row"], "column": base_point["column"] + char_index}
    last_nl = prefix.rfind("\n")
    return {"row": base_point["row"] + nl, "column": char_index - (last_nl + 1)}


def _subchunk(parent: dict[str, Any], cs: int, ce: int, part: int) -> dict[str, Any]:
    ptext = parent["text"]
    out: dict[str, Any] = {
        "chunk_type": parent["chunk_type"],
        "node_type": parent["node_type"],
        "text": ptext[cs:ce],
        "start_byte": parent["start_byte"] + len(ptext[:cs].encode("utf-8")),
        "end_byte": parent["start_byte"] + len(ptext[:ce].encode("utf-8")),
        "start_point": _offset_point(parent["start_point"], ptext, cs),
        "end_point": _offset_point(parent["start_point"], ptext, ce),
        "part": part,
    }
    if parent.get("heading_path") is not None:
        out["heading_path"] = parent["heading_path"]
    return out


def _apply_size_limit(
    chunks: list[dict[str, Any]],
    max_size: int,
    overlap: int,
    split_definitions: bool,
) -> list[dict[str, Any]]:
    """Split any chunk longer than max_size; definitions protected unless enabled."""
    if max_size <= 0:
        return chunks
    out: list[dict[str, Any]] = []
    for ch in chunks:
        if ch["chunk_type"] == "definition" and not split_definitions:
            out.append(ch)
            continue
        if len(ch["text"]) <= max_size:
            out.append(ch)
            continue
        spans = _split_text_recursive(ch["text"], max_size, overlap)
        if len(spans) <= 1:
            out.append(ch)
            continue
        for part, (cs, ce) in enumerate(spans, start=1):
            out.append(_subchunk(ch, cs, ce, part))
    return out


def _make_grouped_chunk(
    nodes: list[Node],
    source_bytes: bytes,
    is_preamble: bool,
) -> dict[str, Any]:
    """Merge a run of non-definition nodes into one chunk dict."""
    start_byte = nodes[0].start_byte
    end_byte = nodes[-1].end_byte
    label = "preamble" if is_preamble else "other"
    r_s, c_s = nodes[0].start_point
    r_e, c_e = nodes[-1].end_point
    return {
        "chunk_type": label,
        "node_type": label,
        "text": source_bytes[start_byte:end_byte].decode("utf-8", errors="replace"),
        "start_byte": start_byte,
        "end_byte": end_byte,
        "start_point": {"row": r_s, "column": c_s},
        "end_point": {"row": r_e, "column": c_e},
    }


def _chunk_source(
    tree: Tree,
    source_bytes: bytes,
    language: str,
    include_context: bool,
) -> list[dict[str, Any]]:
    """Split source into semantic chunks using tree-sitter AST."""
    boundary_types = _CHUNK_BOUNDARY_TYPES.get(language, set())
    root = tree.root_node

    # Languages with no boundary types → return the whole file as one chunk
    if not boundary_types:
        text = source_bytes.decode("utf-8", errors="replace")
        if not text.strip():
            return []
        r_s, c_s = root.start_point
        r_e, c_e = root.end_point
        return [{
            "chunk_type": "other",
            "node_type": root.type,
            "text": text,
            "start_byte": root.start_byte,
            "end_byte": root.end_byte,
            "start_point": {"row": r_s, "column": c_s},
            "end_point": {"row": r_e, "column": c_e},
        }]

    chunks: list[dict[str, Any]] = []
    pending: list[Node] = []

    for i in range(root.child_count):
        child = root.child(i)
        if child is None or not child.is_named:
            continue

        if child.type in boundary_types:
            # Absorb trailing context nodes (comments, annotations) into this definition
            context_nodes: list[Node] = []
            if include_context and pending:
                while pending and _is_context_node(pending[-1], language):
                    context_nodes.insert(0, pending.pop())

            # Flush remaining pending nodes as preamble / other
            if pending:
                chunks.append(
                    _make_grouped_chunk(pending, source_bytes, is_preamble=(len(chunks) == 0))
                )
                pending = []

            # Build the definition chunk (with absorbed context)
            all_nodes = context_nodes + [child]
            sb = all_nodes[0].start_byte
            eb = all_nodes[-1].end_byte
            r_s, c_s = all_nodes[0].start_point
            r_e, c_e = all_nodes[-1].end_point
            chunks.append({
                "chunk_type": "definition",
                "node_type": child.type,
                "text": source_bytes[sb:eb].decode("utf-8", errors="replace"),
                "start_byte": sb,
                "end_byte": eb,
                "start_point": {"row": r_s, "column": c_s},
                "end_point": {"row": r_e, "column": c_e},
            })
        else:
            pending.append(child)

    # Flush remaining non-definition nodes
    if pending:
        chunks.append(
            _make_grouped_chunk(pending, source_bytes, is_preamble=(len(chunks) == 0))
        )

    return chunks


def _node_to_dict(
    node: Node,
    source_bytes: bytes,
    *,
    include_text: bool,
    depth: int,
    max_depth: int,
    counter: list[int],
) -> dict[str, Any]:
    counter[0] += 1
    if counter[0] > MAX_NODES:
        raise HTTPException(
            413,
            f"AST exceeds node limit ({MAX_NODES}). Increase MAX_NODES or shorten input.",
        )
    row_start, col_start = node.start_point
    row_end, col_end = node.end_point
    base: dict[str, Any] = {
        "type": node.type,
        "start_byte": node.start_byte,
        "end_byte": node.end_byte,
        "start_point": {"row": row_start, "column": col_start},
        "end_point": {"row": row_end, "column": col_end},
    }
    if include_text:
        base["text"] = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    if depth >= max_depth:
        base["truncated"] = True
        return base
    children_out: list[dict[str, Any]] = []
    for i in range(node.child_count):
        ch = node.child(i)
        if ch is None:
            continue
        children_out.append(
            _node_to_dict(
                ch,
                source_bytes,
                include_text=include_text,
                depth=depth + 1,
                max_depth=max_depth,
                counter=counter,
            )
        )
    if children_out:
        base["children"] = children_out
    return base


def _tree_to_json(
    tree: Tree,
    source_bytes: bytes,
    *,
    include_text: bool,
    max_depth: int,
) -> dict[str, Any]:
    counter = [0]
    return {
        "root": _node_to_dict(
            tree.root_node,
            source_bytes,
            include_text=include_text,
            depth=0,
            max_depth=max_depth,
            counter=counter,
        ),
        "node_count": counter[0],
    }


app = FastAPI(title="AST Tree-sitter API", version="1.0.0")


class ParseRequest(BaseModel):
    source: str = Field(..., description="Source code to parse.")
    filename: Optional[str] = Field(
        None, description="Original file name; its extension selects the grammar (takes priority over language)."
    )
    language: Optional[str] = Field(None, description="Grammar name (e.g. python, javascript). Fallback when filename is absent or its extension is unknown.")
    include_text: bool = Field(True, description="Include source slice per node.")
    max_depth: int = Field(
        MAX_TREE_DEPTH,
        ge=1,
        le=4096,
        description="Max depth for JSON tree (deeper nodes are marked truncated).",
    )
    sexp: bool = Field(False, description="If true, include S-expression string of the tree.")


class ParseResponse(BaseModel):
    language: str
    tree: dict[str, Any]
    sexp: Optional[str] = None


# --- Chunk models -----------------------------------------------------------

class ChunkRequest(BaseModel):
    source: str = Field(..., description="Source code or text to chunk.")
    filename: Optional[str] = Field(
        None, description="Original file name; its extension selects the strategy/grammar."
    )
    language: Optional[str] = Field(
        None, description="Grammar name, or 'markdown'/'text'/'auto'. Optional."
    )
    mode: Optional[str] = Field(
        None, description="Force strategy: 'auto' (default) / 'ast' / 'markdown' / 'text'."
    )
    max_chunk_size: int = Field(
        500, ge=0, description="Max characters per chunk. 0 disables size splitting."
    )
    chunk_overlap: int = Field(
        50, ge=0, description="Overlap characters between size-split pieces."
    )
    split_definitions: bool = Field(
        False, description="Also size-split AST definition chunks when over the limit."
    )
    include_context: bool = Field(
        True, description="Attach leading comments / decorators to the following definition."
    )


class ChunkItem(BaseModel):
    chunk_type: str = Field(
        ...,
        description='Semantic label: "preamble", "definition", "other" (AST), '
        '"section" (Markdown), or "text" (plain text).',
    )
    node_type: str = Field(
        ...,
        description='Tree-sitter node type (e.g. "function_definition"), '
        '"preamble"/"other" (AST), or "section"/"text" (plain).',
    )
    text: str
    start_byte: int
    end_byte: int
    start_point: dict[str, int]
    end_point: dict[str, int]
    heading_path: Optional[str] = Field(
        None, description='Markdown heading hierarchy, e.g. "Usage > Install". Null otherwise.'
    )
    part: Optional[int] = Field(
        None, description="1-based index when a chunk was size-split; null otherwise."
    )


class ChunkResponse(BaseModel):
    language: str
    chunks: list[ChunkItem]
    chunk_count: int


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "languages_loaded": len(SUPPORTED_LANGUAGES),
        "max_tree_depth_default": MAX_TREE_DEPTH,
        "max_nodes": MAX_NODES,
    }


@app.get("/v1/languages")
@app.get("/languages")
async def list_languages() -> dict[str, Any]:
    return {"object": "list", "data": SUPPORTED_LANGUAGES}


@app.post("/v1/parse", response_model=ParseResponse)
@app.post("/parse", response_model=ParseResponse)
async def parse_code(request: ParseRequest) -> ParseResponse:
    # filename extension takes priority; language is the fallback
    lang: Optional[str] = None
    if request.filename:
        lang = _EXTENSION_MAP.get(_ext_of(request.filename))
        if lang and lang not in SUPPORTED_LANGUAGES:
            lang = None
    if lang is None and request.language:
        lang = request.language.strip().lower() or None
    if lang is None:
        raise HTTPException(
            400,
            "Either filename (with a known extension) or language is required. "
            "See GET /v1/languages.",
        )
    if lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            400,
            f"unsupported language: {lang!r}. "
            f"Use GET /v1/languages for available grammars.",
        )
    try:
        parser = get_parser(lang)
    except Exception as exc:
        logger.exception("get_parser failed")
        raise HTTPException(500, f"failed to load parser: {exc}") from exc

    source_bytes = request.source.encode("utf-8")
    tree = parser.parse(source_bytes)
    try:
        payload = _tree_to_json(
            tree,
            source_bytes,
            include_text=request.include_text,
            max_depth=request.max_depth,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("tree serialization failed")
        raise HTTPException(500, f"serialization failed: {exc}") from exc

    sexp_str: Optional[str] = None
    if request.sexp:
        sexp_str = str(tree.root_node)

    return ParseResponse(language=lang, tree=payload, sexp=sexp_str)


@app.post("/v1/chunk", response_model=ChunkResponse)
@app.post("/chunk", response_model=ChunkResponse)
async def chunk_code(request: ChunkRequest) -> ChunkResponse:
    source = request.source
    if not source.strip():
        raise HTTPException(400, "source is empty")
    if request.max_chunk_size > 0 and request.chunk_overlap >= request.max_chunk_size:
        raise HTTPException(400, "chunk_overlap must be smaller than max_chunk_size")

    strategy, grammar = _resolve_strategy(request.filename, request.language, request.mode)

    if strategy == "text":
        raw_chunks = _chunk_text(source)
        result_language = "text"
    elif strategy == "markdown":
        raw_chunks = _chunk_markdown(source)
        result_language = "markdown"
    else:  # ast
        try:
            parser = get_parser(grammar)
        except Exception as exc:
            logger.exception("get_parser failed")
            raise HTTPException(500, f"failed to load parser: {exc}") from exc
        source_bytes = source.encode("utf-8")
        tree = parser.parse(source_bytes)
        raw_chunks = _chunk_source(tree, source_bytes, grammar, request.include_context)
        result_language = grammar

    try:
        raw_chunks = _apply_size_limit(
            raw_chunks,
            request.max_chunk_size,
            request.chunk_overlap,
            request.split_definitions,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("chunking failed")
        raise HTTPException(500, f"chunking failed: {exc}") from exc

    chunks = [ChunkItem(**c) for c in raw_chunks]
    return ChunkResponse(language=result_language, chunks=chunks, chunk_count=len(chunks))


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
