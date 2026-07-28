from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .analysis import analyze_dataset
from .phase_b.errors import PhaseBError
from .phase_b.pipeline import run_phase_b, validate_phase_b_config
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

    phase_b_parser = subparsers.add_parser(
        "phase-b",
        help="Discover, evaluate, and export new Instagram creator candidates.",
    )
    phase_b_commands = phase_b_parser.add_subparsers(dest="phase_b_command")

    validate_parser = phase_b_commands.add_parser(
        "validate-config",
        help="Validate Phase B configuration and local inputs without a live request.",
    )
    validate_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to a Phase B JSON configuration.",
    )

    phase_b_demo = phase_b_commands.add_parser(
        "demo",
        help="Run deterministic, credential-free Phase B fixtures.",
    )
    phase_b_demo.add_argument(
        "--config",
        type=Path,
        default=Path("config/phase_b.demo.json"),
        help="Demo configuration (default: config/phase_b.demo.json).",
    )
    phase_b_demo.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/phase_b"),
        help="Base directory for Phase B run folders.",
    )

    phase_b_run = phase_b_commands.add_parser(
        "run",
        help="Run configured live discovery. This never sends outreach.",
    )
    phase_b_run.add_argument(
        "--mode",
        choices=("live",),
        required=True,
        help="Explicit live-mode safety acknowledgement.",
    )
    phase_b_run.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to a live Phase B JSON configuration.",
    )
    phase_b_run.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/phase_b"),
        help="Base directory for Phase B run folders.",
    )
    return parser


def run_phase_a(command_args: argparse.Namespace) -> int:
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


def run_phase_b_command(command_args: argparse.Namespace) -> int:
    try:
        if command_args.phase_b_command == "validate-config":
            config, warnings = validate_phase_b_config(command_args.config)
            print(
                f"Phase B configuration is valid: mode={config.mode}, "
                f"provider={config.provider.type}"
            )
            for warning in warnings:
                print(f"Warning: {warning}")
            return 0
        if command_args.phase_b_command is None:
            print(
                "A Phase B command is required: validate-config, demo, or run.",
                file=sys.stderr,
            )
            return 2

        expected_mode = (
            "demo"
            if command_args.phase_b_command == "demo"
            else command_args.mode
        )
        result = run_phase_b(
            command_args.config,
            command_args.output_dir,
            expected_mode=expected_mode,
        )
    except PhaseBError as exc:
        detail = (
            f" ({json.dumps(exc.details, ensure_ascii=False, sort_keys=True)})"
            if exc.details
            else ""
        )
        print(
            f"Phase B failed [{exc.category}]: {exc}{detail}",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, KeyError) as exc:
        print(f"Phase B failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"Phase B complete: run_id={result.manifest.run_id}, "
        f"mode={result.manifest.mode}"
    )
    print(json.dumps(result.manifest.counts, ensure_ascii=False, indent=2))
    print("Selected creators:")
    for rank, candidate in enumerate(result.selected_candidates, start=1):
        print(
            f"  {rank}. {candidate.username}: {candidate.score:.2f}/100 "
            f"(confidence {candidate.discovery_confidence:.3f})"
        )
    print("Generated files:")
    for path in result.generated_paths:
        print(f"  {path}")
    if result.manifest.warnings:
        print("Warnings:")
        for warning in result.manifest.warnings:
            print(f"  - {warning}")
    return 0


def run(command_args: argparse.Namespace) -> int:
    if command_args.command == "phase-b":
        return run_phase_b_command(command_args)
    return run_phase_a(command_args)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["demo", *(argv or [])])
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
