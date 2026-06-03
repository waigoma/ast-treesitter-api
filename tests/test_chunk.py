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
