import os
import tempfile

import pytest

from bot.db.client import DBClient


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
