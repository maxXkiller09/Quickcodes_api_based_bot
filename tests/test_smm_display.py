"""Pure display-helper tests for SMM order statuses."""

import os

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_NOT_FOR_REAL_USE")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("DATABASE_URL", "mongodb://localhost:27017")
os.environ.setdefault("QC_API_BASE", "https://example.invalid")
os.environ.setdefault("BOT_TITLE", "Nexora Test")

from smm import status_label


def test_status_label_formats_known_statuses():
    assert status_label("completed") == "✅ Completed"
    assert status_label("in progress") == "🔄 In Progress"
    assert status_label("cancelled") == "❌ Cancelled"


def test_status_label_handles_missing_and_unknown_statuses():
    assert status_label(None) == "⏳ Pending"
    assert status_label("") == "⏳ Pending"
    assert status_label("awaiting review") == "⏳ Awaiting Review"


def test_status_label_is_case_insensitive():
    assert status_label("COMPLETED") == "✅ Completed"
