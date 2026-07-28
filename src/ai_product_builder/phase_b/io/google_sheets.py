"""Mock-friendly Google Sheets upsert adapter.

The adapter performs no credential discovery and imports no Google SDK. A configured
client is injected by the application boundary, which keeps credentials in ``.env``
and allows deterministic contract tests without network access.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import PHASE_B_COLUMNS, candidate_mapping, candidate_row, normalized_identity


DEFAULT_WORKSHEET_NAME = "Новые блоггеры"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def build_google_sheets_client(service_account_json: str) -> Any:
    """Build an official Sheets client from JSON content or a JSON file path.

    The caller is responsible for sourcing ``service_account_json`` from
    ``.env``. Imports stay optional so the credential-free demo has no Google
    dependency.
    """

    raw = service_account_json.strip()
    if not raw:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is empty")
    if raw.startswith("{"):
        try:
            service_account_info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_JSON contains invalid JSON"
            ) from exc
    else:
        credential_path = Path(raw).expanduser()
        try:
            service_account_info = json.loads(
                credential_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError as exc:
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_JSON path does not exist"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_JSON file contains invalid JSON"
            ) from exc
    if not isinstance(service_account_info, Mapping):
        raise ValueError("Google service-account credentials must be a JSON object")
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google Sheets dependencies are not installed; install "
            "ai-product-builder[sheets]"
        ) from exc
    credentials = Credentials.from_service_account_info(
        dict(service_account_info),
        scopes=[SHEETS_SCOPE],
    )
    return build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False,
    )


@dataclass(frozen=True, slots=True)
class GoogleSheetsWriteReceipt:
    spreadsheet_id: str
    worksheet_name: str
    affected_ranges: tuple[str, ...]
    inserted_rows: int
    updated_rows: int
    preserved_manual_rows: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "spreadsheet_id": self.spreadsheet_id,
            "worksheet_name": self.worksheet_name,
            "affected_ranges": list(self.affected_ranges),
            "inserted_rows": self.inserted_rows,
            "updated_rows": self.updated_rows,
            "preserved_manual_rows": self.preserved_manual_rows,
        }


class GoogleSheetsAdapter:
    """Upsert candidates without clearing unrelated sheets or ranges.

    Supported injected client contracts:

    - ``get_values(spreadsheet_id, range_name)`` and
      ``batch_update_values(spreadsheet_id, updates)``;
    - an official Google API service exposing
      ``spreadsheets().values().get(...).execute()`` and
      ``spreadsheets().values().batchUpdate(...).execute()``.
    """

    def __init__(
        self,
        client: Any | None,
        spreadsheet_id: str,
        *,
        worksheet_name: str = DEFAULT_WORKSHEET_NAME,
    ) -> None:
        self.client = client
        self.spreadsheet_id = spreadsheet_id
        self.worksheet_name = worksheet_name

    @property
    def configured(self) -> bool:
        return self.client is not None and bool(self.spreadsheet_id)

    def _sheet_range(self, cells: str) -> str:
        escaped = self.worksheet_name.replace("'", "''")
        return f"'{escaped}'!{cells}"

    def _read_values(self) -> list[list[Any]]:
        if not self.configured:
            raise RuntimeError(
                "Google Sheets is not configured; provide an authenticated client "
                "and spreadsheet_id"
            )
        range_name = self._sheet_range("A:AB")
        if hasattr(self.client, "get_values"):
            response = self.client.get_values(self.spreadsheet_id, range_name)
            if isinstance(response, Mapping):
                return [list(row) for row in response.get("values", [])]
            return [list(row) for row in (response or [])]

        values_api = self.client.spreadsheets().values()
        response = values_api.get(
            spreadsheetId=self.spreadsheet_id,
            range=range_name,
        ).execute()
        return [list(row) for row in response.get("values", [])]

    def _batch_update(self, updates: list[dict[str, Any]]) -> Any:
        if hasattr(self.client, "batch_update_values"):
            return self.client.batch_update_values(self.spreadsheet_id, updates)

        values_api = self.client.spreadsheets().values()
        return values_api.batchUpdate(
            spreadsheetId=self.spreadsheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()

    def upsert(self, candidates: Iterable[Any]) -> GoogleSheetsWriteReceipt:
        """Batch-upsert by normalized username and preserve manual review fields."""

        existing = self._read_values()
        header = list(existing[0]) if existing else []
        header_index = {str(value): index for index, value in enumerate(header)}
        username_index = header_index.get("username")
        profile_index = header_index.get("profile_url")
        status_index = header_index.get("manual_verification_status")
        notes_index = header_index.get("verification_notes")

        existing_rows: dict[str, tuple[int, list[Any]]] = {}
        for row_number, row in enumerate(existing[1:], start=2):
            identity = ""
            if username_index is not None and username_index < len(row):
                identity = str(row[username_index] or "")
            if (
                not identity
                and profile_index is not None
                and profile_index < len(row)
            ):
                identity = str(row[profile_index] or "")
            key = normalized_identity(identity)
            if key:
                existing_rows[key] = (row_number, row)

        next_row = max(2, len(existing) + 1)
        updates: list[dict[str, Any]] = [
            {
                "range": self._sheet_range(
                    f"A1:{_column_letter(len(PHASE_B_COLUMNS))}1"
                ),
                "values": [list(PHASE_B_COLUMNS)],
            }
        ]
        inserted = 0
        updated = 0
        preserved = 0

        for candidate in candidates:
            item = candidate_mapping(candidate)
            key = normalized_identity(item)
            existing_row = existing_rows.get(key)
            if existing_row is not None:
                row_number, old_row = existing_row
                if status_index is not None and status_index < len(old_row):
                    old_status = old_row[status_index]
                    if old_status not in (None, ""):
                        item["manual_verification_status"] = old_status
                if notes_index is not None and notes_index < len(old_row):
                    old_notes = old_row[notes_index]
                    if old_notes not in (None, ""):
                        item["verification_notes"] = old_notes
                preserved += 1
                updated += 1
            else:
                row_number = next_row
                next_row += 1
                inserted += 1
            item.setdefault("manual_verification_status", "pending")
            item.setdefault("verification_notes", "")
            row = candidate_row(item)
            updates.append(
                {
                    "range": self._sheet_range(
                        f"A{row_number}:{_column_letter(len(PHASE_B_COLUMNS))}"
                        f"{row_number}"
                    ),
                    "values": [[row[field] for field in PHASE_B_COLUMNS]],
                }
            )

        self._batch_update(updates)
        return GoogleSheetsWriteReceipt(
            spreadsheet_id=self.spreadsheet_id,
            worksheet_name=self.worksheet_name,
            affected_ranges=tuple(update["range"] for update in updates),
            inserted_rows=inserted,
            updated_rows=updated,
            preserved_manual_rows=preserved,
        )


def _column_letter(index: int) -> str:
    if index < 1:
        raise ValueError("Column index must be positive")
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


__all__ = [
    "DEFAULT_WORKSHEET_NAME",
    "GoogleSheetsAdapter",
    "GoogleSheetsWriteReceipt",
    "SHEETS_SCOPE",
    "build_google_sheets_client",
]
