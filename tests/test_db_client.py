import os
import re
from datetime import datetime, timedelta, timezone

import pytest

from bot.db.client import DBClient

_TZ_TAIPEI = timezone(timedelta(hours=8))

_UTC8_PATTERN = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$")


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    return DBClient(path)


@pytest.fixture
def sample_quote_record():
    return {
        "quote_number": "Q20260429-001",
        "customer_name": "骰吧王小明",
        "resin_label": "RPG高精度樹脂",
        "body_count": 5,
        "material_cost": 350,
        "processing_fee": 240,
        "auto_discount": "95折",
        "manual_discount": "無",
        "subtotal": 590,
        "final_total": 560,
        "order_status": "正常",
        "decision": "接受",
    }


@pytest.fixture
def sample_customer_record():
    return {
        "quote_number": "Q20260429-001",
        "customer_name": "骰吧王小明",
        "drive_folder_url": "https://drive.google.com/drive/folders/abc123",
        "final_total": 560,
        "pdf_url": "https://drive.google.com/file/d/xyz/view",
    }


# --- initialization ---

def test_creates_database_file_when_not_exists(tmp_path):
    path = str(tmp_path / "new.db")
    assert not os.path.exists(path)
    DBClient(path)
    assert os.path.exists(path)


def test_connects_to_existing_database_without_truncating(tmp_path, sample_quote_record):
    path = str(tmp_path / "existing.db")
    db1 = DBClient(path)
    db1.insert_quote_record(**sample_quote_record)

    db2 = DBClient(path)
    records = db2.get_unsynced_quote_records()
    assert len(records) == 1


# --- insert_quote_record ---

def test_insert_quote_record_sets_synced_at_null(db, sample_quote_record):
    db.insert_quote_record(**sample_quote_record)
    records = db.get_unsynced_quote_records()
    assert len(records) == 1
    assert records[0]["synced_at"] is None


def test_insert_quote_record_stores_all_fields(db, sample_quote_record):
    db.insert_quote_record(**sample_quote_record)
    records = db.get_unsynced_quote_records()
    r = records[0]
    assert r["quote_number"] == "Q20260429-001"
    assert r["customer_name"] == "骰吧王小明"
    assert r["resin_label"] == "RPG高精度樹脂"
    assert r["body_count"] == 5
    assert r["material_cost"] == 350
    assert r["processing_fee"] == 240
    assert r["auto_discount"] == "95折"
    assert r["manual_discount"] == "無"
    assert r["subtotal"] == 590
    assert r["final_total"] == 560
    assert r["order_status"] == "正常"
    assert r["decision"] == "接受"


# --- insert_customer_record ---

def test_insert_customer_record_sets_synced_at_null(db, sample_customer_record):
    db.insert_customer_record(**sample_customer_record)
    records = db.get_unsynced_customer_records()
    assert len(records) == 1
    assert records[0]["synced_at"] is None


def test_insert_customer_record_stores_all_fields(db, sample_customer_record):
    db.insert_customer_record(**sample_customer_record)
    records = db.get_unsynced_customer_records()
    r = records[0]
    assert r["quote_number"] == "Q20260429-001"
    assert r["customer_name"] == "骰吧王小明"
    assert r["drive_folder_url"] == "https://drive.google.com/drive/folders/abc123"
    assert r["final_total"] == 560
    assert r["pdf_url"] == "https://drive.google.com/file/d/xyz/view"


# --- synced_at filtering ---

def test_get_unsynced_quote_records_excludes_synced(db, sample_quote_record):
    db.insert_quote_record(**sample_quote_record)
    records = db.get_unsynced_quote_records()
    db.mark_quote_record_synced(records[0]["id"])

    unsynced = db.get_unsynced_quote_records()
    assert len(unsynced) == 0


def test_get_unsynced_customer_records_excludes_synced(db, sample_customer_record):
    db.insert_customer_record(**sample_customer_record)
    records = db.get_unsynced_customer_records()
    db.mark_customer_record_synced(records[0]["id"])

    unsynced = db.get_unsynced_customer_records()
    assert len(unsynced) == 0


def test_get_unsynced_returns_null_rows_only(db, sample_quote_record):
    db.insert_quote_record(**sample_quote_record)
    db.insert_quote_record(**{**sample_quote_record, "quote_number": "Q20260429-002"})

    records = db.get_unsynced_quote_records()
    db.mark_quote_record_synced(records[0]["id"])

    unsynced = db.get_unsynced_quote_records()
    assert len(unsynced) == 1
    assert unsynced[0]["quote_number"] == "Q20260429-002"


# --- mark_*_synced ---

def test_mark_quote_record_synced_sets_timestamp(db, sample_quote_record):
    db.insert_quote_record(**sample_quote_record)
    record_id = db.get_unsynced_quote_records()[0]["id"]

    db.mark_quote_record_synced(record_id)

    all_records = db.get_unsynced_quote_records()
    assert len(all_records) == 0


def test_mark_customer_record_synced_sets_timestamp(db, sample_customer_record):
    db.insert_customer_record(**sample_customer_record)
    record_id = db.get_unsynced_customer_records()[0]["id"]

    db.mark_customer_record_synced(record_id)

    all_records = db.get_unsynced_customer_records()
    assert len(all_records) == 0


def test_partial_sync_leaves_unsynced_rows(db, sample_quote_record):
    for i in range(3):
        db.insert_quote_record(**{**sample_quote_record, "quote_number": f"Q-{i}"})

    records = db.get_unsynced_quote_records()
    db.mark_quote_record_synced(records[0]["id"])

    unsynced = db.get_unsynced_quote_records()
    assert len(unsynced) == 2


# --- new optional columns ---

def test_insert_quote_record_with_drive_folder_url(db, sample_quote_record):
    db.insert_quote_record(**sample_quote_record, drive_folder_url="https://drive.google.com/drive/folders/xyz")
    records = db.get_unsynced_quote_records()
    assert records[0]["drive_folder_url"] == "https://drive.google.com/drive/folders/xyz"


def test_insert_quote_record_without_drive_folder_url_is_null(db, sample_quote_record):
    db.insert_quote_record(**sample_quote_record)
    records = db.get_unsynced_quote_records()
    assert records[0]["drive_folder_url"] is None


def test_insert_quote_record_with_file_details_and_rejection_reason(db, sample_quote_record):
    db.insert_quote_record(
        **{**sample_quote_record, "decision": "拒絕"},
        file_details_text="model.stl: 3.50ml / 5件",
        rejection_reason="價格太高",
    )
    records = db.get_unsynced_quote_records()
    r = records[0]
    assert r["file_details_text"] == "model.stl: 3.50ml / 5件"
    assert r["rejection_reason"] == "價格太高"


def test_insert_quote_record_rejected_without_optional_fields_is_null(db, sample_quote_record):
    db.insert_quote_record(**{**sample_quote_record, "decision": "拒絕"})
    records = db.get_unsynced_quote_records()
    r = records[0]
    assert r["file_details_text"] is None
    assert r["rejection_reason"] is None


# --- get_unsynced_accepted_quotes / get_unsynced_rejected_quotes ---

def test_get_unsynced_accepted_quotes_returns_only_accepted(db, sample_quote_record):
    db.insert_quote_record(**{**sample_quote_record, "decision": "接受", "quote_number": "Q-A"})
    db.insert_quote_record(**{**sample_quote_record, "decision": "拒絕", "quote_number": "Q-R"})

    accepted = db.get_unsynced_accepted_quotes()
    assert len(accepted) == 1
    assert accepted[0]["decision"] == "接受"
    assert accepted[0]["quote_number"] == "Q-A"


def test_get_unsynced_rejected_quotes_returns_only_rejected(db, sample_quote_record):
    db.insert_quote_record(**{**sample_quote_record, "decision": "接受", "quote_number": "Q-A"})
    db.insert_quote_record(**{**sample_quote_record, "decision": "拒絕", "quote_number": "Q-R"})

    rejected = db.get_unsynced_rejected_quotes()
    assert len(rejected) == 1
    assert rejected[0]["decision"] == "拒絕"
    assert rejected[0]["quote_number"] == "Q-R"


def test_get_unsynced_accepted_quotes_excludes_synced(db, sample_quote_record):
    db.insert_quote_record(**{**sample_quote_record, "decision": "接受"})
    records = db.get_unsynced_accepted_quotes()
    db.mark_quote_record_synced(records[0]["id"])

    assert len(db.get_unsynced_accepted_quotes()) == 0


def test_get_unsynced_rejected_quotes_excludes_synced(db, sample_quote_record):
    db.insert_quote_record(**{**sample_quote_record, "decision": "拒絕"})
    records = db.get_unsynced_rejected_quotes()
    db.mark_quote_record_synced(records[0]["id"])

    assert len(db.get_unsynced_rejected_quotes()) == 0


# --- time format ---

def test_created_at_is_utc8_format(db, sample_quote_record):
    db.insert_quote_record(**sample_quote_record)
    r = db.get_unsynced_quote_records()[0]
    assert _UTC8_PATTERN.match(r["created_at"]), f"Expected YYYY/MM/DD HH:mm, got {r['created_at']!r}"


def test_customer_record_created_at_is_utc8_format(db, sample_customer_record):
    db.insert_customer_record(**sample_customer_record)
    r = db.get_unsynced_customer_records()[0]
    assert _UTC8_PATTERN.match(r["created_at"]), f"Expected YYYY/MM/DD HH:mm, got {r['created_at']!r}"


# --- drive_folder_url uniqueness ---

def test_insert_customer_record_returns_true_on_first_insert(db, sample_customer_record):
    result = db.insert_customer_record(**sample_customer_record)
    assert result is True


def test_insert_customer_record_returns_false_on_duplicate_url(db, sample_customer_record):
    db.insert_customer_record(**sample_customer_record)
    result = db.insert_customer_record(**{**sample_customer_record, "quote_number": "Q-DUPE"})
    assert result is False


def test_duplicate_drive_url_does_not_create_second_record(db, sample_customer_record):
    db.insert_customer_record(**sample_customer_record)
    db.insert_customer_record(**{**sample_customer_record, "quote_number": "Q-DUPE"})
    records = db.get_unsynced_customer_records()
    assert len(records) == 1


# --- count_accepted_quotes_today ---

def test_count_accepted_quotes_today_empty(db):
    assert db.count_accepted_quotes_today("260430") == 0


def test_count_accepted_quotes_today_counts_matching_prefix(db, sample_quote_record):
    db.insert_quote_record(**{**sample_quote_record, "quote_number": "trb26043001", "decision": "接受"})
    db.insert_quote_record(**{**sample_quote_record, "quote_number": "trb26043002", "decision": "接受"})
    assert db.count_accepted_quotes_today("260430") == 2


def test_count_accepted_quotes_today_excludes_other_dates(db, sample_quote_record):
    db.insert_quote_record(**{**sample_quote_record, "quote_number": "trb26043001", "decision": "接受"})
    db.insert_quote_record(**{**sample_quote_record, "quote_number": "trb26050101", "decision": "接受"})
    assert db.count_accepted_quotes_today("260430") == 1


def test_count_accepted_quotes_today_excludes_rejected(db, sample_quote_record):
    db.insert_quote_record(**{**sample_quote_record, "quote_number": "trb26043001", "decision": "接受"})
    db.insert_quote_record(**{**sample_quote_record, "quote_number": "trb26043002", "decision": "拒絕"})
    assert db.count_accepted_quotes_today("260430") == 1


# --- migration idempotency ---

def test_migration_is_idempotent(tmp_path, sample_quote_record):
    path = str(tmp_path / "migrate.db")
    db1 = DBClient(path)
    db1.insert_quote_record(**sample_quote_record)

    db2 = DBClient(path)  # second init on same DB should not raise
    records = db2.get_unsynced_quote_records()
    assert len(records) == 1


def test_migration_adds_shipping_columns_to_new_db(tmp_path):
    import sqlite3
    path = str(tmp_path / "new.db")
    DBClient(path)
    conn = sqlite3.connect(path)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(quote_records)").fetchall()]
    conn.close()
    assert "shipping_fee" in cols
    assert "shipping_address" in cols


def test_migration_shipping_columns_idempotent(tmp_path, sample_quote_record):
    path = str(tmp_path / "existing.db")
    db1 = DBClient(path)
    db1.insert_quote_record(**sample_quote_record)
    db2 = DBClient(path)  # should not raise even though columns already exist
    assert len(db2.get_unsynced_quote_records()) == 1


def test_insert_quote_record_with_shipping_fee_and_address(db, sample_quote_record):
    db.insert_quote_record(**sample_quote_record, shipping_fee=60, shipping_address="台北市大安區")
    r = db.get_unsynced_quote_records()[0]
    assert r["shipping_fee"] == 60
    assert r["shipping_address"] == "台北市大安區"


def test_insert_quote_record_default_shipping_is_zero_empty(db, sample_quote_record):
    db.insert_quote_record(**sample_quote_record)
    r = db.get_unsynced_quote_records()[0]
    assert r["shipping_fee"] == 0
    assert r["shipping_address"] == ""


# --- count_quick_quotes_today ---

def test_count_quick_quotes_today_zero(db):
    today = datetime.now(_TZ_TAIPEI).strftime("%Y-%m-%d")
    assert db.count_quick_quotes_today(today) == 0


def test_count_quick_quotes_today_counts_only_quick(db, sample_quote_record):
    today = datetime.now(_TZ_TAIPEI).strftime("%Y-%m-%d")
    db.insert_quote_record(**{**sample_quote_record, "decision": "快速"})
    db.insert_quote_record(**{**sample_quote_record, "decision": "快速"})
    db.insert_quote_record(**{**sample_quote_record, "decision": "接受"})
    assert db.count_quick_quotes_today(today) == 2


def test_count_quick_quotes_today_date_filter(db):
    today = datetime.now(_TZ_TAIPEI)
    today_str = today.strftime("%Y-%m-%d")
    today_slash = today.strftime("%Y/%m/%d")
    yesterday_slash = (today - timedelta(days=1)).strftime("%Y/%m/%d")

    for _ in range(3):
        db._conn.execute(
            """INSERT INTO quote_records
            (created_at, quote_number, customer_name, resin_label, body_count,
             material_cost, processing_fee, auto_discount, manual_discount,
             subtotal, final_total, order_status, decision, shipping_fee, shipping_address)
            VALUES (?, '', 'test', 'RPG', 1, 100, 90, '無', '無', 190, 190, '正常', '快速', 0, '')""",
            (f"{today_slash} 10:00",),
        )
    for _ in range(2):
        db._conn.execute(
            """INSERT INTO quote_records
            (created_at, quote_number, customer_name, resin_label, body_count,
             material_cost, processing_fee, auto_discount, manual_discount,
             subtotal, final_total, order_status, decision, shipping_fee, shipping_address)
            VALUES (?, '', 'test', 'RPG', 1, 100, 90, '無', '無', 190, 190, '正常', '快速', 0, '')""",
            (f"{yesterday_slash} 10:00",),
        )
    db._conn.commit()

    assert db.count_quick_quotes_today(today_str) == 3


# --- insert_customer_record empty drive_folder_url ---

def test_insert_customer_record_empty_drive_url_skips_uniqueness_check(db):
    result1 = db.insert_customer_record(
        quote_number="", customer_name="test1", drive_folder_url="",
        final_total=500, pdf_url="url1",
    )
    result2 = db.insert_customer_record(
        quote_number="", customer_name="test2", drive_folder_url="",
        final_total=600, pdf_url="url2",
    )
    assert result1 is True
    assert result2 is True
    assert len(db.get_unsynced_customer_records()) == 2


def test_insert_customer_record_nonempty_url_retains_uniqueness_check(db, sample_customer_record):
    result1 = db.insert_customer_record(**sample_customer_record)
    result2 = db.insert_customer_record(**{**sample_customer_record, "quote_number": "Q-DUP"})
    assert result1 is True
    assert result2 is False
    assert len(db.get_unsynced_customer_records()) == 1
