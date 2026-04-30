from unittest.mock import MagicMock

import pytest

from bot.db.client import DBClient
from bot.sync import SyncResult, sync_records as _sync_records


@pytest.fixture
def db(tmp_path):
    return DBClient(str(tmp_path / "test.db"))


@pytest.fixture
def sheets():
    return MagicMock()


def _insert_accepted(db, quote_number="Q-001", drive_folder_url=None):
    db.insert_quote_record(
        quote_number=quote_number,
        customer_name="測試客戶",
        resin_label="RPG高精度樹脂",
        body_count=3,
        material_cost=350,
        processing_fee=240,
        auto_discount="95折",
        manual_discount="無",
        subtotal=590,
        final_total=560,
        order_status="正常",
        decision="接受",
        drive_folder_url=drive_folder_url,
    )


def _insert_rejected(db, quote_number="Q-001", file_details_text="", rejection_reason=""):
    db.insert_quote_record(
        quote_number=quote_number,
        customer_name="測試客戶",
        resin_label="RPG高精度樹脂",
        body_count=3,
        material_cost=350,
        processing_fee=240,
        auto_discount="95折",
        manual_discount="無",
        subtotal=590,
        final_total=560,
        order_status="正常",
        decision="拒絕",
        file_details_text=file_details_text,
        rejection_reason=rejection_reason,
    )


def _insert_customer(db, quote_number="Q-001"):
    db.insert_customer_record(
        quote_number=quote_number,
        customer_name="測試客戶",
        drive_folder_url="https://drive.google.com/drive/folders/abc",
        final_total=560,
        pdf_url="https://drive.google.com/file/d/xyz/view",
    )


# --- No pending records ---

def test_no_sheets_calls_when_no_pending_records(db, sheets):
    _sync_records(db, sheets)

    sheets.append_accepted_quote.assert_not_called()
    sheets.append_rejected_quote.assert_not_called()


# --- Accepted quotes routed to append_accepted_quote ---

def test_syncs_accepted_quotes_via_append_accepted_quote(db, sheets):
    _insert_accepted(db, "Q-001")
    _insert_accepted(db, "Q-002")

    _sync_records(db, sheets)

    assert sheets.append_accepted_quote.call_count == 2
    sheets.append_rejected_quote.assert_not_called()


def test_append_accepted_quote_called_with_correct_fields(db, sheets):
    _insert_accepted(db, "Q-001", drive_folder_url="https://drive.google.com/drive/folders/xyz")

    _sync_records(db, sheets)

    kwargs = sheets.append_accepted_quote.call_args[1]
    assert kwargs["quote_number"] == "Q-001"
    assert kwargs["customer_name"] == "測試客戶"
    assert kwargs["drive_folder_url"] == "https://drive.google.com/drive/folders/xyz"
    assert kwargs["final_total"] == 560


def test_marks_accepted_records_synced_after_write(db, sheets):
    _insert_accepted(db, "Q-001")

    _sync_records(db, sheets)

    assert len(db.get_unsynced_accepted_quotes()) == 0


# --- Rejected quotes routed to append_rejected_quote ---

def test_syncs_rejected_quotes_via_append_rejected_quote(db, sheets):
    _insert_rejected(db, "Q-001", file_details_text="a.stl: 2.00ml / 3件", rejection_reason="貴")
    _insert_rejected(db, "Q-002")

    _sync_records(db, sheets)

    assert sheets.append_rejected_quote.call_count == 2
    sheets.append_accepted_quote.assert_not_called()


def test_append_rejected_quote_called_with_correct_fields(db, sheets):
    _insert_rejected(db, "Q-001", file_details_text="m.stl: 3.50ml / 5件", rejection_reason="嫌貴")

    _sync_records(db, sheets)

    kwargs = sheets.append_rejected_quote.call_args[1]
    assert kwargs["customer_name"] == "測試客戶"
    assert kwargs["file_details_text"] == "m.stl: 3.50ml / 5件"
    assert kwargs["rejection_reason"] == "嫌貴"
    assert kwargs["final_total"] == 560


def test_marks_rejected_records_synced_after_write(db, sheets):
    _insert_rejected(db)

    _sync_records(db, sheets)

    assert len(db.get_unsynced_rejected_quotes()) == 0


# --- customer_records table is not synced ---

def test_customer_records_do_not_trigger_sheets_calls(db, sheets):
    _insert_customer(db, "Q-001")
    _insert_customer(db, "Q-002")

    _sync_records(db, sheets)

    sheets.append_accepted_quote.assert_not_called()
    sheets.append_rejected_quote.assert_not_called()


# --- Mixed accepted + rejected ---

def test_accepted_and_rejected_routed_separately(db, sheets):
    _insert_accepted(db, "Q-A")
    _insert_rejected(db, "Q-R")

    _sync_records(db, sheets)

    assert sheets.append_accepted_quote.call_count == 1
    assert sheets.append_rejected_quote.call_count == 1


# --- Error handling ---

def test_accepted_sheets_exception_does_not_crash_sync(db, sheets):
    _insert_accepted(db, "Q-001")
    sheets.append_accepted_quote.side_effect = Exception("API error")

    _sync_records(db, sheets)  # should not raise


def test_rejected_sheets_exception_does_not_crash_sync(db, sheets):
    _insert_rejected(db)
    sheets.append_rejected_quote.side_effect = Exception("API error")

    _sync_records(db, sheets)  # should not raise


def test_partial_accepted_failure_syncs_remaining(db, sheets):
    _insert_accepted(db, "Q-001")
    _insert_accepted(db, "Q-002")
    _insert_accepted(db, "Q-003")

    call_count = 0

    def fail_on_second(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("API error on second")

    sheets.append_accepted_quote.side_effect = fail_on_second

    _sync_records(db, sheets)

    unsynced = db.get_unsynced_accepted_quotes()
    assert len(unsynced) == 1
    assert unsynced[0]["quote_number"] == "Q-002"


# --- SyncResult return value ---

def test_returns_sync_result_type(db, sheets):
    result = _sync_records(db, sheets)
    assert isinstance(result, SyncResult)


def test_result_counts_synced_quotes_for_accepted(db, sheets):
    _insert_accepted(db, "Q-001")
    _insert_accepted(db, "Q-002")

    result = _sync_records(db, sheets)

    assert result.synced_quotes == 2
    assert result.failed_quotes == 0


def test_result_counts_failed_quotes_for_accepted(db, sheets):
    _insert_accepted(db)
    sheets.append_accepted_quote.side_effect = Exception("API error")

    result = _sync_records(db, sheets)

    assert result.synced_quotes == 0
    assert result.failed_quotes == 1


def test_result_counts_synced_customers_for_rejected(db, sheets):
    _insert_rejected(db)

    result = _sync_records(db, sheets)

    assert result.synced_customers == 1
    assert result.failed_customers == 0


def test_result_counts_failed_customers_for_rejected(db, sheets):
    _insert_rejected(db)
    sheets.append_rejected_quote.side_effect = Exception("API error")

    result = _sync_records(db, sheets)

    assert result.synced_customers == 0
    assert result.failed_customers == 1


def test_result_zero_when_nothing_to_sync(db, sheets):
    result = _sync_records(db, sheets)

    assert result == SyncResult(
        synced_quotes=0, synced_customers=0, failed_quotes=0, failed_customers=0
    )
