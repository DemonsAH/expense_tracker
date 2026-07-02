"""Unit tests for prompt builder module.

Covers PRD section 5.4: built-in refined prompt with dynamic owner configuration.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from expense_tracker.prompts.receipt_prompt import (
    CATEGORY_VALUES,
    build_receipt_prompt,
    load_owners,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_owners_file(data: dict) -> Path:
    """Write owners data to a temp file and return path."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(data, f)
        return Path(f.name)


# ===========================================================================
# PRD 5.4: prompt builder tests
# ===========================================================================

class TestLoadOwners:
    """load_owners reads and validates owners.json."""

    def test_loads_valid_owners(self):
        data = {
            "owners": [
                {"id": "me", "name": "Me", "marker": "M", "is_me": True},
                {"id": "alice", "name": "Alice", "marker": "A", "is_me": False},
            ]
        }
        path = _write_owners_file(data)
        try:
            owners = load_owners(path)
            assert len(owners) == 2
            assert owners[0]["id"] == "me"
            assert owners[0]["marker"] == "M"
        finally:
            path.unlink(missing_ok=True)

    def test_raises_on_empty_owners(self):
        path = _write_owners_file({"owners": []})
        try:
            with pytest.raises(ValueError, match="non-empty"):
                load_owners(path)
        finally:
            path.unlink(missing_ok=True)

    def test_raises_on_missing_owners_key(self):
        path = _write_owners_file({"something": "else"})
        try:
            with pytest.raises(ValueError, match="non-empty"):
                load_owners(path)
        finally:
            path.unlink(missing_ok=True)


class TestBuildReceiptPrompt:
    """build_receipt_prompt produces the full system prompt with owner context."""

    def test_prompt_contains_category_list(self):
        """PRD 5.3: all 9 categories present in prompt."""
        data = {
            "owners": [
                {"id": "me", "name": "Me", "marker": "M", "is_me": True},
            ]
        }
        path = _write_owners_file(data)
        try:
            prompt = build_receipt_prompt(owners_path=path)
            for cat in CATEGORY_VALUES:
                assert cat in prompt, f"Missing category {cat} in prompt"
        finally:
            path.unlink(missing_ok=True)

    def test_prompt_contains_owner_ids(self):
        """PRD 6.4: prompt includes owner IDs for LLM to reference."""
        data = {
            "owners": [
                {"id": "me", "name": "Me", "marker": "M", "is_me": True},
                {"id": "alice", "name": "Alice", "marker": "A", "is_me": False},
            ]
        }
        path = _write_owners_file(data)
        try:
            prompt = build_receipt_prompt(owners_path=path)
            assert "me" in prompt
            assert "alice" in prompt
            assert 'id="me"' in prompt
            assert 'marker="A"' in prompt
        finally:
            path.unlink(missing_ok=True)

    def test_prompt_structure_requirements(self):
        """PRD 5.4: prompt instructs JSON-only, no markdown, fixed schema."""
        data = {
            "owners": [
                {"id": "me", "name": "Me", "marker": "M", "is_me": True},
            ]
        }
        path = _write_owners_file(data)
        try:
            prompt = build_receipt_prompt(owners_path=path)
            assert "只输出 JSON" in prompt
            assert "YYYY-MM-DD" in prompt
            assert "EUR" in prompt
            assert "normal, receipt_owner, item_owner" in prompt
            assert "owner_mode" in prompt
            assert "default_owner_id" in prompt
            assert "items" in prompt
            # Keep Storno/negative items
            assert "Storno" in prompt or "Sofortstorno" in prompt
        finally:
            path.unlink(missing_ok=True)

    def test_prompt_is_not_empty(self):
        data = {
            "owners": [
                {"id": "me", "name": "Me", "marker": "M", "is_me": True},
            ]
        }
        path = _write_owners_file(data)
        try:
            prompt = build_receipt_prompt(owners_path=path)
            assert len(prompt) > 500  # non-trivial prompt
        finally:
            path.unlink(missing_ok=True)