import pytest
from fastapi import HTTPException

import server
from server import _resolve_strategy


def test_filename_md_resolves_markdown():
    assert _resolve_strategy("README.md", None, None) == ("markdown", None)


def test_filename_txt_resolves_text():
    assert _resolve_strategy("notes.txt", None, None) == ("text", None)


def test_filename_py_resolves_ast_python():
    assert _resolve_strategy("main.py", None, None) == ("ast", "python")


def test_unknown_extension_falls_back_to_text():
    assert _resolve_strategy("data.unknownext", None, None) == ("text", None)


def test_no_extension_falls_back_to_text():
    assert _resolve_strategy("Makefile", None, None) == ("text", None)


def test_language_markdown_text_auto():
    assert _resolve_strategy(None, "markdown", None) == ("markdown", None)
    assert _resolve_strategy(None, "text", None) == ("text", None)
    assert _resolve_strategy(None, "auto", None) == ("text", None)


def test_language_grammar_resolves_ast():
    assert _resolve_strategy(None, "python", None) == ("ast", "python")


def test_unsupported_language_raises_400():
    with pytest.raises(HTTPException) as ei:
        _resolve_strategy(None, "cobol", None)
    assert ei.value.status_code == 400


def test_no_hints_falls_back_to_text():
    assert _resolve_strategy(None, None, None) == ("text", None)


def test_mode_forces_strategy():
    assert _resolve_strategy("a.py", None, "text") == ("text", None)
    assert _resolve_strategy("a.txt", None, "markdown") == ("markdown", None)
    assert _resolve_strategy("a.py", None, "ast") == ("ast", "python")


def test_mode_ast_without_grammar_raises_400():
    with pytest.raises(HTTPException) as ei:
        _resolve_strategy("a.txt", None, "ast")
    assert ei.value.status_code == 400


def test_invalid_mode_raises_400():
    with pytest.raises(HTTPException) as ei:
        _resolve_strategy(None, None, "weird")
    assert ei.value.status_code == 400


def test_filename_precedence_over_language():
    # filename .md wins even if language=python
    assert _resolve_strategy("doc.md", "python", None) == ("markdown", None)


from server import _chunk_markdown, _point_at


def test_point_at_basic():
    src = "ab\ncd\nef"
    assert _point_at(src, 0) == {"row": 0, "column": 0}
    assert _point_at(src, 3) == {"row": 1, "column": 0}
    assert _point_at(src, 4) == {"row": 1, "column": 1}


def test_markdown_splits_by_heading():
    src = "# Title\nintro\n\n## A\naaa\n\n## B\nbbb\n"
    chunks = _chunk_markdown(src)
    # Title section, A section, B section
    assert len(chunks) == 3
    assert all(c["chunk_type"] == "section" for c in chunks)


def test_markdown_heading_path_hierarchy():
    src = "# Top\nx\n\n## Mid\ny\n\n### Leaf\nz\n"
    chunks = _chunk_markdown(src)
    paths = [c.get("heading_path") for c in chunks]
    assert paths == ["Top", "Top > Mid", "Top > Mid > Leaf"]


def test_markdown_preamble_has_no_heading_path():
    src = "intro text\nno heading yet\n\n# First\nbody\n"
    chunks = _chunk_markdown(src)
    assert chunks[0].get("heading_path") is None
    assert chunks[1].get("heading_path") == "First"


def test_markdown_ignores_hash_inside_code_fence():
    src = "# Real\ntext\n\n```\n# not a heading\n```\nmore\n"
    chunks = _chunk_markdown(src)
    # Only one real heading -> one section
    assert len(chunks) == 1
    assert chunks[0]["heading_path"] == "Real"


def test_markdown_no_heading_single_section():
    src = "just some\nplain paragraphs\nwithout headings\n"
    chunks = _chunk_markdown(src)
    assert len(chunks) == 1
    assert chunks[0].get("heading_path") is None


def test_markdown_offsets_match_source():
    src = "# A\nbody\n\n## B\ntail\n"
    for c in chunks_for(src):
        assert src.encode("utf-8")[c["start_byte"]:c["end_byte"]] == c["text"].encode("utf-8")


def chunks_for(src):
    return _chunk_markdown(src)


def test_markdown_empty_returns_empty():
    assert _chunk_markdown("   \n  ") == []


from server import (
    _chunk_text,
    _split_text_recursive,
    _apply_size_limit,
    _base_spans,
    _SPLIT_SEPARATORS,
)


def test_chunk_text_single_when_small():
    chunks = _chunk_text("hello world")
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "text"
    assert chunks[0]["text"] == "hello world"


def test_chunk_text_empty():
    assert _chunk_text("   ") == []


def test_base_spans_cover_text_contiguously():
    text = "para one.\n\npara two.\n\npara three is here."
    spans = _base_spans(text, 12, _SPLIT_SEPARATORS)
    # spans cover the whole text with no gaps
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    for (s, e) in spans:
        assert e - s <= 12 or "\n\n" not in text[s:e]  # oversized only if unsplittable


def test_split_text_recursive_overlap():
    text = "abcdefghij" * 5  # 50 chars, no separators
    spans = _split_text_recursive(text, 20, 5)
    # first span no overlap, later spans start 5 earlier than their base start
    assert spans[0][0] == 0
    assert len(spans) >= 2
    # overlap: span[1] starts before span[0] ends
    assert spans[1][0] < spans[0][1]


def test_apply_size_limit_disabled_when_zero():
    chunks = [{"chunk_type": "text", "node_type": "text",
               "text": "x" * 1000, "start_byte": 0, "end_byte": 1000,
               "start_point": {"row": 0, "column": 0},
               "end_point": {"row": 0, "column": 1000}}]
    out = _apply_size_limit(chunks, 0, 0, False)
    assert len(out) == 1


def test_apply_size_limit_splits_text():
    text = "x" * 1000
    chunks = [{"chunk_type": "text", "node_type": "text",
               "text": text, "start_byte": 0, "end_byte": 1000,
               "start_point": {"row": 0, "column": 0},
               "end_point": {"row": 0, "column": 1000}}]
    out = _apply_size_limit(chunks, 500, 50, False)
    assert len(out) > 1
    assert out[0]["part"] == 1
    assert out[1]["part"] == 2


def test_apply_size_limit_protects_definitions():
    text = "def f():\n" + "    pass\n" * 200
    chunks = [{"chunk_type": "definition", "node_type": "function_definition",
               "text": text, "start_byte": 0, "end_byte": len(text.encode()),
               "start_point": {"row": 0, "column": 0},
               "end_point": {"row": 200, "column": 0}}]
    out = _apply_size_limit(chunks, 100, 10, False)
    assert len(out) == 1  # protected


def test_apply_size_limit_splits_definitions_when_enabled():
    text = "def f():\n" + "    pass\n" * 200
    chunks = [{"chunk_type": "definition", "node_type": "function_definition",
               "text": text, "start_byte": 0, "end_byte": len(text.encode()),
               "start_point": {"row": 0, "column": 0},
               "end_point": {"row": 200, "column": 0}}]
    out = _apply_size_limit(chunks, 100, 10, True)
    assert len(out) > 1


def test_subchunk_offsets_are_correct():
    src = "line0\nline1\nline2\nline3\n" * 30
    chunks = _chunk_text(src)
    out = _apply_size_limit(chunks, 100, 10, False)
    encoded = src.encode("utf-8")
    for c in out:
        assert encoded[c["start_byte"]:c["end_byte"]] == c["text"].encode("utf-8")


def test_multibyte_offsets():
    src = "あいうえお\nかきくけこ\n" * 50
    chunks = _chunk_text(src)
    out = _apply_size_limit(chunks, 60, 6, False)
    encoded = src.encode("utf-8")
    for c in out:
        assert encoded[c["start_byte"]:c["end_byte"]] == c["text"].encode("utf-8")


from fastapi.testclient import TestClient
from server import app

client = TestClient(app)


def test_endpoint_markdown_via_filename():
    src = "# Title\nintro\n\n## A\n" + ("word " * 5) + "\n"
    r = client.post("/v1/chunk", json={"filename": "README.md", "source": src})
    assert r.status_code == 200
    body = r.json()
    assert body["language"] == "markdown"
    assert body["chunk_count"] >= 2


def test_endpoint_text_via_filename():
    r = client.post("/v1/chunk", json={"filename": "a.txt", "source": "x" * 1200})
    assert r.status_code == 200
    body = r.json()
    assert body["language"] == "text"
    assert body["chunk_count"] > 1
    assert body["chunks"][0]["part"] == 1


def test_endpoint_unknown_filename_falls_back_to_text():
    r = client.post("/v1/chunk", json={"filename": "x.weird", "source": "hello"})
    assert r.status_code == 200
    assert r.json()["language"] == "text"


def test_endpoint_empty_source_400():
    r = client.post("/v1/chunk", json={"source": "   "})
    assert r.status_code == 400


def test_endpoint_overlap_ge_max_400():
    r = client.post("/v1/chunk", json={"source": "abc", "max_chunk_size": 10, "chunk_overlap": 10})
    assert r.status_code == 400


def test_backward_compatible_python_definitions_unsplit():
    src = "import os\n\n" + "def f():\n" + "    x = 1\n" * 80 + "\n"
    # old-style call: language only, no new params
    r = client.post("/v1/chunk", json={"language": "python", "source": src, "include_context": True})
    assert r.status_code == 200
    body = r.json()
    assert body["language"] == "python"
    defs = [c for c in body["chunks"] if c["chunk_type"] == "definition"]
    assert len(defs) == 1
    # definition not split: no "part"
    assert defs[0].get("part") in (None,)
    # text matches one whole function
    assert "def f()" in defs[0]["text"]


def test_chunkitem_has_legacy_fields():
    r = client.post("/v1/chunk", json={"language": "python", "source": "x = 1\n"})
    assert r.status_code == 200
    c = r.json()["chunks"][0]
    for key in ("chunk_type", "node_type", "text", "start_byte", "end_byte", "start_point", "end_point"):
        assert key in c


def test_split_definitions_true_splits_python():
    src = "def f():\n" + "    x = 1\n" * 200 + "\n"
    r = client.post("/v1/chunk", json={
        "language": "python", "source": src,
        "max_chunk_size": 200, "chunk_overlap": 20, "split_definitions": True,
    })
    assert r.status_code == 200
    parts = [c for c in r.json()["chunks"] if c.get("part")]
    assert len(parts) >= 2


def test_mode_text_overrides_python_filename():
    r = client.post("/v1/chunk", json={"filename": "a.py", "source": "def f(): pass\n", "mode": "text"})
    assert r.status_code == 200
    assert r.json()["language"] == "text"


def test_markdown_multibyte_offsets():
    # Spec §9: byte offsets must stay correct for multibyte (Japanese) markdown.
    src = "# 日本語見出し\nあいうえお かきくけこ\n\n## 次の節\nテスト本文\n"
    encoded = src.encode("utf-8")
    for c in _chunk_markdown(src):
        assert encoded[c["start_byte"]:c["end_byte"]] == c["text"].encode("utf-8")


def test_markdown_multibyte_offsets_after_size_split():
    # Long multibyte section forced through size rescue keeps byte offsets correct.
    src = "# 章\n" + ("あいうえおかきくけこ\n" * 40)
    out = _apply_size_limit(_chunk_markdown(src), 80, 8, False)
    encoded = src.encode("utf-8")
    assert any(c.get("part") for c in out)  # confirm splitting happened
    for c in out:
        assert encoded[c["start_byte"]:c["end_byte"]] == c["text"].encode("utf-8")
