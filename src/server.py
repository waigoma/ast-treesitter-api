"""
Tree-sitter AST API for n8n / HTTP clients.
Parses source text and returns JSON syntax trees (or S-expression strings).
"""

from __future__ import annotations

import logging
import os
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
    "elixir",
    "go",
    "html",
    "java",
    "javascript",
    "json",
    "kotlin",
    "lua",
    "php",
    "python",
    "ruby",
    "rust",
    "scala",
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
    language: str = Field(..., description="Grammar name (e.g. python, javascript).")
    source: str = Field(..., description="Source code to parse.")
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
    lang = request.language.strip().lower()
    if not lang:
        raise HTTPException(400, "language is empty")
    if lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            400,
            f"unsupported language: {request.language!r}. "
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


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
