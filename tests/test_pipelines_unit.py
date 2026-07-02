"""Unit tests for pipelines: validation, postprocess, retry_policy, ingestion.

Covers PRD sections:
  - 7.1 (business validation rules)
  - 6.2 (Storno/Leergut postprocessing)
  - 7.2 (retry policy)
  - 4.3 (main pipeline flow)
  - 6.3 (price ranking rules)
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from expense_tracker.pipelines.receipt_validation import (
    DEFAULT_MONEY_TOLERANCE,
    ReceiptValidationResult,
    is_cancellation_item,
    is_leergut_item,
    validate_extracted_receipt_business_rules,
)
from expense_tracker.pipelines.receipt_postprocess import (
    process_extracted_receipt_items,
    ProcessedReceiptItems,
)
from expense_tracker.pipelines.retry_policy import (
    is_retryable_ingestion_error,
    RETRYABLE_ERROR_MARKERS,
)
from expense_tracker.pipelines.receipt_ingestion import (
    ReceiptAttemptError,
    ReceiptAttemptFailure,
    ReceiptIngestionResult,
    parse_extracted_receipt,
)
from expense_tracker.schemas.extraction import ExtractedReceipt, ExtractedReceiptItem
from expense_tracker.schemas.owners import OwnersConfig


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _valid_extraction() -> ExtractedReceipt:
    return ExtractedReceipt.model_validate({
        "merchant": "REWE",
        "purchase_date": "2026-05-04",
        "currency": "EUR",
        "total_amount": 4.50,
        "payment_method": "card",
        "owner_mode": "normal",
        "default_owner_id": "me",
        "receipt_owner_marker": None,
        "items": [
            {
                "name": "Water", "normalized_name": "water",
                "category": "DRINK", "quantity": 1,
                "unit_price": 2.50, "total_price": 2.50,
                "owner_id": "me",
            },
            {
                "name": "Apple", "normalized_name": "apple",
                "category": "FRUIT", "quantity": 2,
                "unit_price": 1.00, "total_price": 2.00,
                "owner_id": "me",
            },
        ],
    })


def _valid_owners_config() -> OwnersConfig:
    return OwnersConfig.model_validate({
        "owners": [
            {"id": "me", "name": "Me", "marker": "M", "is_me": True},
            {"id": "alice", "name": "Alice", "marker": "A", "is_me": False},
        ]
    })


# ===========================================================================
# parse_extracted_receipt (PRD 7.1: JSON structure validation)
# ===========================================================================

class TestParseExtractedReceipt:
    """PRD 7.1: JSON 结构完整且字段类型合法."""

    def test_valid_json_parses(self):
        content = json.dumps(_valid_extraction().model_dump(mode="json"))
        result = parse_extracted_receipt(content)
        assert isinstance(result, ExtractedReceipt)
        assert result.merchant == "REWE"

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_extracted_receipt("not json at all")

    def test_schema_mismatch_raises(self):
        """Missing required field -> pydantic validation error."""
        bad = {"merchant": "REWE"}  # missing purchase_date etc.
        with pytest.raises(ValueError, match="ExtractedReceipt schema"):
            parse_extracted_receipt(json.dumps(bad))

    def test_extra_fields_ignored(self):
        """Pydantic by default ignores extra fields (model_config not set to forbid)."""
        content = json.dumps({**_valid_extraction().model_dump(mode="json"), "extra": "field"})
        result = parse_extracted_receipt(content)
        assert result.merchant == "REWE"


# ===========================================================================
# PRD 7.1: business rule validation
# ===========================================================================

class TestBusinessValidation:
    """PRD 7.1: business validation rules."""

    def test_valid_receipt_passes(self):
        result = validate_extracted_receipt_business_rules(
            _valid_extraction(), owners=_valid_owners_config(),
        )
        assert result.is_valid is True
        assert result.issues == []

    def test_default_owner_id_not_in_owners(self):
        extracted = _valid_extraction()
        extracted.default_owner_id = "ghost"
        result = validate_extracted_receipt_business_rules(
            extracted, owners=_valid_owners_config(),
        )
        assert not result.is_valid
        assert "default_owner_id_not_found" in result.issues

    def test_item_owner_id_not_in_owners(self):
        extracted = _valid_extraction()
        extracted.items[0].owner_id = "ghost"
        result = validate_extracted_receipt_business_rules(
            extracted, owners=_valid_owners_config(),
        )
        assert not result.is_valid
        assert any("owner_id_not_found" in issue for issue in result.issues)

    def test_item_total_price_mismatch_outside_tolerance(self):
        """quantity * unit_price != total_price."""
        extracted = _valid_extraction()
        extracted.items[0].total_price = Decimal("100.00")  # should be 2.50
        result = validate_extracted_receipt_business_rules(
            extracted, owners=_valid_owners_config(),
        )
        assert not result.is_valid
        assert any("total_price_mismatch" in issue for issue in result.issues)

    def test_item_total_price_match_within_tolerance(self):
        """0.04 difference within 0.05 tolerance."""
        extracted = _valid_extraction()
        extracted.items[0].total_price = Decimal("2.54")  # diff 0.04
        result = validate_extracted_receipt_business_rules(
            extracted, owners=_valid_owners_config(),
        )
        assert result.is_valid

    def test_receipt_total_mismatch(self):
        """Sum of item totals != receipt total_amount."""
        extracted = _valid_extraction()
        extracted.total_amount = Decimal("100.00")
        result = validate_extracted_receipt_business_rules(
            extracted, owners=_valid_owners_config(),
        )
        assert not result.is_valid
        assert "receipt_total_mismatch" in result.issues

    def test_negative_item_total_price_validation(self):
        """PRD 6.2: negative items validated - quantity*unit_price abs matches total_price abs."""
        payload = _valid_extraction().model_dump(mode="json")
        payload["items"].append({
            "name": "Sofortstorno", "normalized_name": "sofortstorno",
            "category": "OTHER", "quantity": 1,
            "unit_price": 2.50, "total_price": -2.50,
            "owner_id": "me",
        })
        payload["total_amount"] = 2.00  # 4.50 - 2.50
        extracted = ExtractedReceipt.model_validate(payload)
        result = validate_extracted_receipt_business_rules(
            extracted, owners=_valid_owners_config(),
        )
        assert result.is_valid


# ===========================================================================
# PRD 6.3: cancellation / leergut item detection
# ===========================================================================

class TestCancellationLeergutDetection:
    """Helper functions used by reports to exclude items from price ranking."""

    def test_is_cancellation_item_detects_storno(self):
        assert is_cancellation_item("Storno", "storno") is True
        assert is_cancellation_item("Sofortstorno Wurst", "sofortstorno_wurst") is True

    def test_is_cancellation_item_rejects_normal(self):
        assert is_cancellation_item("Water", "water") is False

    def test_is_leergut_item_detects_pfand(self):
        assert is_leergut_item("Pfand", "pfand") is True
        assert is_leergut_item("Leergut", "leergut") is True
        assert is_leergut_item("Flaschenpfand", "flaschenpfand") is True

    def test_is_leergut_item_rejects_normal(self):
        assert is_leergut_item("Water", "water") is False


# ===========================================================================
# PRD 6.2: postprocessing (keep all items)
# ===========================================================================

class TestPostprocess:
    """PRD 6.2: all items kept in formal list, no auto-removal."""

    def test_normal_items_all_kept(self):
        extracted = _valid_extraction()
        result = process_extracted_receipt_items(extracted)
        assert len(result.formal_items) == 2
        assert result.removed_items == []

    def test_cancellation_items_kept(self):
        payload = _valid_extraction().model_dump(mode="json")
        payload["items"].append({
            "name": "Storno", "normalized_name": "storno",
            "category": "OTHER", "quantity": 1,
            "unit_price": 2.50, "total_price": -2.50,
            "owner_id": "me",
        })
        extracted = ExtractedReceipt.model_validate(payload)
        result = process_extracted_receipt_items(extracted)
        assert len(result.formal_items) == 3
        assert result.removed_items == []

    def test_leergut_items_kept(self):
        payload = _valid_extraction().model_dump(mode="json")
        payload["items"].append({
            "name": "Pfand", "normalized_name": "pfand",
            "category": "OTHER", "quantity": 1,
            "unit_price": 0.25, "total_price": -0.25,
            "owner_id": "me",
        })
        extracted = ExtractedReceipt.model_validate(payload)
        result = process_extracted_receipt_items(extracted)
        assert len(result.formal_items) == 3


# ===========================================================================
# PRD 7.2: retry policy
# ===========================================================================

class TestRetryPolicy:
    """PRD 7.2: retryable error markers."""

    def test_invalid_json_is_retryable(self):
        assert is_retryable_ingestion_error("Model output is not valid JSON: example") is True

    def test_schema_mismatch_is_retryable(self):
        assert is_retryable_ingestion_error(
            "Model output does not match ExtractedReceipt schema: field required"
        ) is True

    def test_default_owner_id_not_found_is_retryable(self):
        assert is_retryable_ingestion_error(
            "Business validation failed: default_owner_id_not_found, receipt_total_mismatch"
        ) is True

    def test_owner_id_not_found_is_retryable(self):
        assert is_retryable_ingestion_error(
            "Business validation failed: items[0].owner_id_not_found"
        ) is True

    def test_total_price_mismatch_is_retryable(self):
        assert is_retryable_ingestion_error(
            "Business validation failed: items[2].total_price_mismatch"
        ) is True

    def test_receipt_total_mismatch_is_retryable(self):
        assert is_retryable_ingestion_error(
            "Business validation failed: receipt_total_mismatch"
        ) is True

    def test_non_retryable_error(self):
        """FileNotFound is not retryable."""
        assert is_retryable_ingestion_error("FileNotFound: image.jpg") is False

    def test_all_retryable_markers_are_defined(self):
        assert len(RETRYABLE_ERROR_MARKERS) == 6
        assert "Model output is not valid JSON" in RETRYABLE_ERROR_MARKERS
        assert "Model output does not match ExtractedReceipt schema" in RETRYABLE_ERROR_MARKERS


# ===========================================================================
# PRD 4.3: ReceiptAttemptError / ReceiptAttemptFailure data structures
# ===========================================================================

class TestReceiptIngestionDataStructures:
    """Verify the pipeline data model carries all required fields."""

    def test_receipt_attempt_failure_stores_all_fields(self):
        f = ReceiptAttemptFailure(
            attempt_number=2,
            failure_reason="receipt_total_mismatch",
            content="{}",
        )
        assert f.attempt_number == 2
        assert f.failure_reason == "receipt_total_mismatch"
        assert f.content == "{}"

    def test_receipt_attempt_error_carries_content(self):
        err = ReceiptAttemptError("bad json", content="not-json")
        assert err.content == "not-json"
        assert str(err) == "bad json"