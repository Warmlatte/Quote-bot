import logging
from dataclasses import dataclass

from bot.db.client import DBClient
from bot.sheets.client import SheetsClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncResult:
    synced_quotes: int
    synced_customers: int
    failed_quotes: int
    failed_customers: int


def sync_records(db: DBClient, sheets: SheetsClient) -> SyncResult:
    # synced_quotes = accepted rows synced; synced_customers = rejected rows synced
    synced_quotes = 0
    failed_quotes = 0
    synced_customers = 0
    failed_customers = 0

    for record in db.get_unsynced_accepted_quotes():
        try:
            sheets.append_accepted_quote(
                created_at=record["created_at"],
                quote_number=record["quote_number"],
                customer_name=record["customer_name"],
                drive_folder_url=record["drive_folder_url"],
                final_total=record["final_total"],
            )
            db.mark_quote_record_synced(record["id"])
            synced_quotes += 1
        except Exception:
            logger.exception("Failed to sync accepted quote id=%s", record["id"])
            failed_quotes += 1

    for record in db.get_unsynced_rejected_quotes():
        try:
            sheets.append_rejected_quote(
                created_at=record["created_at"],
                customer_name=record["customer_name"],
                final_total=record["final_total"],
                file_details_text=record["file_details_text"] or "",
                rejection_reason=record["rejection_reason"] or "",
            )
            db.mark_quote_record_synced(record["id"])
            synced_customers += 1
        except Exception:
            logger.exception("Failed to sync rejected quote id=%s", record["id"])
            failed_customers += 1

    return SyncResult(
        synced_quotes=synced_quotes,
        synced_customers=synced_customers,
        failed_quotes=failed_quotes,
        failed_customers=failed_customers,
    )
