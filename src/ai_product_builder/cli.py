from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .analysis import analyze_dataset
from .reporting import generate_outputs, ranking


def parse_as_of(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--as-of must be an ISO-8601 date/time, e.g. 2026-07-27T00:00:00Z"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-product-builder",
        description=(
            "Analyze the verified Instagram reference dataset and build a transparent "
            "ideal-creator profile."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")
    for command, help_text in (
        ("demo", "Run the complete offline demo using data/raw and output."),
        ("analyze", "Run analysis with configurable paths."),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument(
            "--input-dir",
            type=Path,
            default=Path("data/raw"),
            help="Directory containing the three source files (default: data/raw).",
        )
        command_parser.add_argument(
            "--output-dir",
            type=Path,
            default=Path("output"),
            help="Destination for generated artifacts (default: output).",
        )
        command_parser.add_argument(
            "--as-of",
            type=parse_as_of,
            default=None,
            help=(
                "Optional ISO-8601 analysis reference time. Default: newest valid "
                "sampled-post timestamp for reproducibility."
            ),
        )
    return parser


def run(command_args: argparse.Namespace) -> int:
    input_dir: Path = command_args.input_dir
    expected = {
        "profiles": input_dir / "instagram_profiles.json",
        "audit": input_dir / "manual_verification_audit.json",
        "workbook": input_dir / "Блогеры.xlsx",
    }
    missing = [str(path) for path in expected.values() if not path.is_file()]
    if missing:
        print("Missing required input file(s):", file=sys.stderr)
        for path in missing:
            print(f"  - {path}", file=sys.stderr)
        return 2
    try:
        result = analyze_dataset(
            expected["profiles"],
            expected["audit"],
            expected["workbook"],
            as_of=command_args.as_of,
        )
        generated = generate_outputs(command_args.output_dir, result)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Analysis failed: {exc}", file=sys.stderr)
        return 1

    ranked = ranking(result)
    summary = result.dataset_summary
    print("Analysis complete.")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Top scores:")
    for item in ranked[:10]:
        print(f"  {item['username']}: {item['total']:.2f}/100")
    print("Generated files:")
    for path in generated:
        print(f"  {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["demo", *(argv or [])])
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

