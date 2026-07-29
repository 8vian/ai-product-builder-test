"""Non-destructive local XLSX export for Phase B candidates."""

from __future__ import annotations

from copy import copy
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable

from openpyxl import load_workbook
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation, DataValidationList

from . import (
    MANUAL_VERIFICATION_STATUSES,
    PHASE_B_COLUMNS,
    candidate_mapping,
    candidate_row,
    normalized_identity,
)


DEFAULT_SHEET_NAME = "Новые блоггеры"

_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
_LINK_FONT = Font(name="Calibri", size=11, color="0563C1", underline="single")
_BODY_FONT = Font(name="Calibri", size=11, color="1F1F1F")
_PENDING_FILL = PatternFill("solid", fgColor="FFF2CC")
_APPROVED_FILL = PatternFill("solid", fgColor="E2F0D9")
_REJECTED_FILL = PatternFill("solid", fgColor="FCE4D6")
_REVIEW_FILL = PatternFill("solid", fgColor="DDEBF7")

_COLUMN_WIDTHS = {
    "platform": 12,
    "username": 24,
    "profile_url": 36,
    "account_type": 25,
    "account_type_explanation": 55,
    "followers": 14,
    "median_likes": 14,
    "median_comments": 16,
    "engagement_rate": 16,
    "usable_posts": 14,
    "sampled_posts": 14,
    "data_completeness": 17,
    "short_video_share": 17,
    "last_post_date": 16,
    "score": 11,
    "score_components": 52,
    "selection_explanation": 55,
    "evidence": 60,
    "recent_post_url": 38,
    "barter_offer": 70,
    "manual_verification_status": 25,
    "verification_notes": 45,
    "outreach_status": 18,
    "barter_feasibility_review_required": 23,
    "barter_feasibility_explanation": 55,
    "content_themes": 24,
    "known_format_posts": 18,
    "detected_content_language": 24,
    "campaign_language_compatible": 26,
    "detected_geography": 24,
    "delivery_market_review_required": 27,
    "compatibility_explanation": 60,
    "barter_evidence": 50,
    "no_barter_evidence": 50,
    "campaign_bucket": 26,
    "campaign_status_reasons": 55,
    "alternative_campaign_note": 55,
    "discovery_confidence": 19,
    "eligibility_status": 18,
    "eligibility_reasons": 45,
    "query_ids": 28,
    "provider": 18,
    "collected_at": 24,
    "offer_generation_mode": 23,
    "source_exclusion_check": 22,
}


def _existing_manual_values(worksheet: Any) -> dict[str, tuple[Any, Any]]:
    headers = {
        str(cell.value): cell.column
        for cell in worksheet[1]
        if cell.value not in (None, "")
    }
    status_col = headers.get("manual_verification_status")
    notes_col = headers.get("verification_notes")
    username_col = headers.get("username")
    profile_col = headers.get("profile_url") or headers.get("Ссылка")
    if not (status_col or notes_col) or not (username_col or profile_col):
        return {}

    preserved: dict[str, tuple[Any, Any]] = {}
    for row_index in range(2, worksheet.max_row + 1):
        identity_value = (
            worksheet.cell(row_index, username_col).value if username_col else None
        )
        if identity_value in (None, "") and profile_col:
            identity_value = worksheet.cell(row_index, profile_col).value
        key = normalized_identity(str(identity_value or ""))
        if not key:
            continue
        status = worksheet.cell(row_index, status_col).value if status_col else None
        notes = worksheet.cell(row_index, notes_col).value if notes_col else None
        preserved[key] = (status, notes)
    return preserved


def _clear_candidate_area(worksheet: Any) -> None:
    """Clear values and links but retain the worksheet and its non-cell properties."""

    max_column = max(worksheet.max_column, len(PHASE_B_COLUMNS))
    for row in worksheet.iter_rows(
        min_row=1,
        max_row=1,
        min_col=1,
        max_col=max_column,
    ):
        for cell in row:
            cell.value = None
            cell.hyperlink = None
            cell.comment = None
    if worksheet.max_row > 1:
        worksheet.delete_rows(2, worksheet.max_row - 1)

    worksheet.data_validations = DataValidationList()
    worksheet.conditional_formatting = ConditionalFormattingList()


def _apply_manual_values(
    candidate: Any,
    preserved: dict[str, tuple[Any, Any]],
) -> dict[str, Any]:
    item = candidate_mapping(candidate)
    existing = preserved.get(normalized_identity(item))
    if existing is not None:
        status, notes = existing
        if status not in (None, ""):
            item["manual_verification_status"] = status
        if notes not in (None, ""):
            item["verification_notes"] = notes
    item.setdefault("manual_verification_status", "pending")
    item.setdefault("verification_notes", "")
    return item


def _xlsx_value(field: str, value: Any) -> Any:
    if field not in {"last_post_date", "collected_at"} or value in (None, ""):
        return value
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        return value
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _format_sheet(worksheet: Any, row_count: int) -> None:
    final_column = len(PHASE_B_COLUMNS)
    final_row = max(2, row_count + 1)

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{worksheet.cell(1, final_column).coordinate}"
    worksheet.sheet_view.showGridLines = False
    worksheet.row_dimensions[1].height = 32

    for column_index, field in enumerate(PHASE_B_COLUMNS, start=1):
        cell = worksheet.cell(1, column_index)
        cell.fill = copy(_HEADER_FILL)
        cell.font = copy(_HEADER_FONT)
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )
        worksheet.column_dimensions[cell.column_letter].width = _COLUMN_WIDTHS[field]

    for row_index in range(2, row_count + 2):
        # Five final candidates make generous wrapped rows practical and keep
        # the grounded offer/reference text reviewable without manual resizing.
        worksheet.row_dimensions[row_index].height = 90
        for column_index in range(1, final_column + 1):
            cell = worksheet.cell(row_index, column_index)
            if cell.hyperlink is None:
                cell.font = copy(_BODY_FONT)
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    field_columns = {
        field: index for index, field in enumerate(PHASE_B_COLUMNS, start=1)
    }
    for field in ("followers", "usable_posts", "sampled_posts"):
        for cells in worksheet.iter_cols(
            min_col=field_columns[field],
            max_col=field_columns[field],
            min_row=2,
            max_row=final_row,
        ):
            for cell in cells:
                cell.number_format = "#,##0"
    for field in ("median_likes", "median_comments", "engagement_rate", "score"):
        for cells in worksheet.iter_cols(
            min_col=field_columns[field],
            max_col=field_columns[field],
            min_row=2,
            max_row=final_row,
        ):
            for cell in cells:
                cell.number_format = "0.00"
    for field in (
        "data_completeness",
        "short_video_share",
        "discovery_confidence",
    ):
        for cells in worksheet.iter_cols(
            min_col=field_columns[field],
            max_col=field_columns[field],
            min_row=2,
            max_row=final_row,
        ):
            for cell in cells:
                cell.number_format = "0.00%"
    for field in ("last_post_date", "collected_at"):
        for cells in worksheet.iter_cols(
            min_col=field_columns[field],
            max_col=field_columns[field],
            min_row=2,
            max_row=final_row,
        ):
            for cell in cells:
                cell.number_format = "yyyy-mm-dd"

    status_letter = worksheet.cell(
        1, field_columns["manual_verification_status"]
    ).column_letter
    validation = DataValidation(
        type="list",
        formula1='"pending,approved,rejected,needs_review"',
        allow_blank=False,
    )
    validation.error = "Use pending, approved, rejected, or needs_review."
    validation.errorTitle = "Invalid manual verification status"
    worksheet.add_data_validation(validation)
    validation.add(f"{status_letter}2:{status_letter}{max(final_row, 1001)}")

    status_range = f"{status_letter}2:{status_letter}{final_row}"
    for status, fill in (
        ("pending", _PENDING_FILL),
        ("approved", _APPROVED_FILL),
        ("rejected", _REJECTED_FILL),
        ("needs_review", _REVIEW_FILL),
    ):
        worksheet.conditional_formatting.add(
            status_range,
            FormulaRule(
                formula=[f'${status_letter}2="{status}"'],
                fill=copy(fill),
            ),
        )


def write_candidates_workbook(
    source_workbook: Path,
    output_path: Path,
    candidates: Iterable[Any],
    *,
    sheet_name: str = DEFAULT_SHEET_NAME,
    preserve_existing_manual_values: bool = True,
) -> Path:
    """Copy a workbook and replace only its dedicated Phase B candidate table.

    If ``output_path`` already exists, it is used as the rerun base so manually
    edited status and notes survive. The source workbook is never overwritten.
    """

    source_workbook = Path(source_workbook)
    output_path = Path(output_path)
    if not source_workbook.is_file():
        raise FileNotFoundError(f"Source workbook not found: {source_workbook}")
    if source_workbook.resolve() == output_path.resolve():
        raise ValueError("Phase B workbook output must not overwrite the raw workbook")

    base_path = output_path if output_path.is_file() else source_workbook
    workbook = load_workbook(base_path, read_only=False, data_only=False)
    worksheet = (
        workbook[sheet_name]
        if sheet_name in workbook.sheetnames
        else workbook.create_sheet(sheet_name)
    )
    preserved = (
        _existing_manual_values(worksheet)
        if preserve_existing_manual_values
        else {}
    )
    items = [_apply_manual_values(candidate, preserved) for candidate in candidates]

    _clear_candidate_area(worksheet)
    for column_index, field in enumerate(PHASE_B_COLUMNS, start=1):
        worksheet.cell(1, column_index, field)
    for row_index, item in enumerate(items, start=2):
        row = candidate_row(item)
        for column_index, field in enumerate(PHASE_B_COLUMNS, start=1):
            cell = worksheet.cell(
                row_index,
                column_index,
                _xlsx_value(field, row[field]),
            )
            if field in {"profile_url", "recent_post_url"} and row[field]:
                cell.hyperlink = str(row[field])
                cell.font = copy(_LINK_FONT)

    _format_sheet(worksheet, len(items))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            prefix=f".{output_path.stem}-",
            suffix=".xlsx",
            dir=output_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        workbook.save(temporary_path)
        temporary_path.replace(output_path)
    finally:
        workbook.close()
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return output_path


__all__ = ["DEFAULT_SHEET_NAME", "write_candidates_workbook"]
