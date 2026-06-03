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
