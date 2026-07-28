"""UTF-8-BOM CSV exports for Phase B."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import (
    EXCLUDED_COLUMNS,
    PHASE_B_COLUMNS,
    as_mapping,
    candidate_mapping,
    cell_value,
)


def write_candidate_csv(path: Path, candidates: Iterable[Any]) -> Path:
    """Write CandidateResult-compatible rows using the required stable schema."""

    # Keep nested values typed here; write_mapping_csv performs the single JSON
    # serialization pass for evidence, components, reasons, and query identifiers.
    rows = [candidate_mapping(candidate) for candidate in candidates]
    return write_mapping_csv(path, rows, columns=PHASE_B_COLUMNS)


def write_excluded_candidates_csv(path: Path, records: Iterable[Any]) -> Path:
    """Write exclusions with stable core columns and any additional audit fields."""

    mappings = [as_mapping(record) for record in records]
    additional = sorted(
        {
            str(field)
            for item in mappings
            for field in item
            if field not in EXCLUDED_COLUMNS
        }
    )
    columns = (*EXCLUDED_COLUMNS, *additional)
    # Keep nested exclusion data typed until write_mapping_csv performs the
    # single serialization pass. Pre-serializing here would wrap JSON arrays
    # and objects in a second JSON string.
    return write_mapping_csv(path, mappings, columns=columns)


def write_mapping_csv(
    path: Path,
    records: Iterable[Any],
    *,
    columns: Sequence[str] | None = None,
) -> Path:
    """Write arbitrary typed records with deterministic nested JSON values."""

    mappings = [
        as_mapping(record) if not isinstance(record, dict) else dict(record)
        for record in records
    ]
    if columns is None:
        columns = tuple(
            sorted({str(field) for item in mappings for field in item.keys()})
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(columns),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for item in mappings:
            writer.writerow(
                {field: cell_value(field, item.get(field)) for field in columns}
            )
    return path


__all__ = [
    "write_candidate_csv",
    "write_excluded_candidates_csv",
    "write_mapping_csv",
]
