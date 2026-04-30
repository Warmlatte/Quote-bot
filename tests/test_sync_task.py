from unittest.mock import MagicMock, call

import pytest

from bot.db.client import DBClient
from bot.sync import SyncResult, sync_records as _sync_records


@pytest.fixture
def db(tmp_path):
    return DBClient(str(tmp_path / "test.db"))


@pytest.fixture
def sheets():
    return MagicMock()


def _insert_quote(db, quote_number="Q-001", decision="接受"):
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
        decision=decision,
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

    sheets.append_quote_record.assert_not_called()
    sheets.append_customer_record.assert_not_called()


# --- Batch write quote records ---

def test_syncs_n_quote_records(db, sheets):
    _insert_quote(db, "Q-001")
    _insert_quote(db, "Q-002")

    _sync_records(db, sheets)

    assert sheets.append_quote_record.call_count == 2


def test_marks_quote_records_synced_after_write(db, sheets):
    _insert_quote(db, "Q-001")

    _sync_records(db, sheets)

    assert len(db.get_unsynced_quote_records()) == 0


def test_syncs_customer_records(db, sheets):
    _insert_customer(db, "Q-001")
    _insert_customer(db, "Q-002")

    _sync_records(db, sheets)

    assert sheets.append_customer_record.call_count == 2


def test_marks_customer_records_synced_after_write(db, sheets):
    _insert_customer(db)

    _sync_records(db, sheets)

    assert len(db.get_unsynced_customer_records()) == 0


# --- Error handling ---

def test_sheets_exception_does_not_crash_sync(db, sheets):
    _insert_quote(db, "Q-001")
    sheets.append_quote_record.side_effect = Exception("API error")

    _sync_records(db, sheets)  # should not raise


def test_partial_failure_syncs_remaining_records(db, sheets):
    _insert_quote(db, "Q-001")
    _insert_quote(db, "Q-002")
    _insert_quote(db, "Q-003")

    call_count = 0

    def fail_on_second(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise Exception("API error on second record")

    sheets.append_quote_record.side_effect = fail_on_second

    _sync_records(db, sheets)

    unsynced = db.get_unsynced_quote_records()
    assert len(unsynced) == 1
    assert unsynced[0]["quote_number"] == "Q-002"


def test_customer_record_exception_does_not_crash_sync(db, sheets):
    _insert_customer(db)
    sheets.append_customer_record.side_effect = Exception("API error")

    _sync_records(db, sheets)  # should not raise


# --- SyncResult return value ---

def test_returns_sync_result_type(db, sheets):
    result = _sync_records(db, sheets)
    assert isinstance(result, SyncResult)


def test_result_counts_synced_quotes(db, sheets):
    _insert_quote(db, "Q-001")
    _insert_quote(db, "Q-002")

    result = _sync_records(db, sheets)

    assert result.synced_quotes == 2
    assert result.failed_quotes == 0


def test_result_counts_failed_quotes(db, sheets):
    _insert_quote(db, "Q-001")
    sheets.append_quote_record.side_effect = Exception("API error")

    result = _sync_records(db, sheets)

    assert result.synced_quotes == 0
    assert result.failed_quotes == 1


def test_result_counts_synced_customers(db, sheets):
    _insert_customer(db, "Q-001")

    result = _sync_records(db, sheets)

    assert result.synced_customers == 1
    assert result.failed_customers == 0


def test_result_counts_failed_customers(db, sheets):
    _insert_customer(db)
    sheets.append_customer_record.side_effect = Exception("API error")

    result = _sync_records(db, sheets)

    assert result.synced_customers == 0
    assert result.failed_customers == 1


def test_result_zero_when_nothing_to_sync(db, sheets):
    result = _sync_records(db, sheets)

    assert result == SyncResult(
        synced_quotes=0, synced_customers=0, failed_quotes=0, failed_customers=0
    )
