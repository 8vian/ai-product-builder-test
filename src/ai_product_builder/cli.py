from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .analysis import analyze_dataset
from .phase_b.errors import PhaseBError
from .phase_b.expansion_review import run_expansion_review
from .phase_b.final_review import run_final_campaign_review
from .phase_b.manual_finalization import finalize_saved_run
from .phase_b.offline_reselection import reselect_saved_run
from .phase_b.pipeline import run_phase_b, validate_phase_b_config
from .phase_b.queries import generate_queries
from .phase_b.recovery import recover_completed_apify_runs
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

    phase_b_recover = phase_b_commands.add_parser(
        "recover",
        help=(
            "Read two existing completed Apify runs and rebuild audit "
            "artifacts offline. This command cannot start an Actor."
        ),
    )
    phase_b_recover.add_argument(
        "--config",
        type=Path,
        required=True,
        help="The live configuration used by the completed run.",
    )
    phase_b_recover.add_argument(
        "--source-failed-run",
        type=Path,
        required=True,
        help="Immutable failed Phase B run directory.",
    )
    phase_b_recover.add_argument(
        "--search-run-id",
        required=True,
        help="Existing completed Search Actor run ID.",
    )
    phase_b_recover.add_argument(
        "--profile-run-id",
        required=True,
        help="Existing completed Profile Actor run ID.",
    )
    phase_b_recover.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New recovery output directory; existing artifacts are not overwritten.",
    )

    phase_b_reselect = phase_b_commands.add_parser(
        "reselect",
        help=(
            "Re-evaluate a saved live run offline without provider discovery "
            "or enrichment."
        ),
    )
    phase_b_reselect.add_argument(
        "--source-run",
        type=Path,
        required=True,
        help="Existing completed Phase B run directory.",
    )
    phase_b_reselect.add_argument(
        "--config",
        type=Path,
        required=True,
        help="The live configuration used for Phase A references and campaign.",
    )
    phase_b_reselect.add_argument(
        "--review-file",
        type=Path,
        required=True,
        help="Structured manual-review decisions for the saved run.",
    )
    phase_b_reselect.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "New reviewed run directory. Default: <source-run>-reviewed. "
            "The source run is never overwritten."
        ),
    )

    phase_b_final_review = phase_b_commands.add_parser(
        "final-review",
        help=(
            "Apply campaign-specific barter and compatibility rules to two "
            "saved runs without provider calls."
        ),
    )
    phase_b_final_review.add_argument(
        "--source-run",
        type=Path,
        required=True,
        help="Original completed live run directory.",
    )
    phase_b_final_review.add_argument(
        "--reviewed-run",
        type=Path,
        required=True,
        help="Completed offline reviewed run directory.",
    )
    phase_b_final_review.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Saved live campaign configuration.",
    )
    phase_b_final_review.add_argument(
        "--review-file",
        type=Path,
        required=True,
        help="Structured manual-review decisions.",
    )
    phase_b_final_review.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New, empty final-review output directory.",
    )

    phase_b_manual_finalize = phase_b_commands.add_parser(
        "manual-finalize",
        help=(
            "Apply a complete structured human review to one immutable saved "
            "run without provider calls."
        ),
    )
    phase_b_manual_finalize.add_argument(
        "--source-run",
        type=Path,
        required=True,
        help="Existing completed Phase B run directory.",
    )
    phase_b_manual_finalize.add_argument(
        "--review-file",
        type=Path,
        required=True,
        help="Structured decisions covering the complete saved eligible pool.",
    )
    phase_b_manual_finalize.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New, empty manual-finalization output directory.",
    )

    phase_b_expansion_review = phase_b_commands.add_parser(
        "expansion-review",
        help=(
            "Review saved eligible and noneligible profiles offline without "
            "provider calls, offers, or shortlist changes."
        ),
    )
    phase_b_expansion_review.add_argument(
        "--source-run",
        type=Path,
        required=True,
        help="Immutable original completed Phase B run directory.",
    )
    phase_b_expansion_review.add_argument(
        "--manual-final-run",
        type=Path,
        required=True,
        help="Immutable manual-final run whose shortlist must remain unchanged.",
    )
    phase_b_expansion_review.add_argument(
        "--review-file",
        type=Path,
        required=True,
        help="Immutable structured review used by the manual-final run.",
    )
    phase_b_expansion_review.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New, empty expansion-review output directory.",
    )
    phase_b_expansion_review.add_argument(
        "--focus-candidate",
        default="stylistelenaialena",
        help="Saved eligible candidate to diagnose (default: stylistelenaialena).",
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
            ideal_document = json.loads(
                config.inputs.ideal_creator_profile.read_text(
                    encoding="utf-8"
                )
            )
            queries = generate_queries(
                ideal_document,
                config.campaign,
                query_texts=config.discovery.query_texts,
            )
            print(
                f"Phase B configuration is valid: mode={config.mode}, "
                f"provider={config.provider.type}"
            )
            print(
                "Campaign geography: "
                f"{config.campaign.geography or 'not configured'}"
            )
            print(
                "Target content languages: "
                + ", ".join(
                    config.campaign.target_content_languages
                    or (config.campaign.language,)
                )
            )
            print(
                "Delivery markets: "
                + (
                    ", ".join(config.campaign.delivery_markets)
                    or "not configured"
                )
            )
            print(
                "Run-level exclusions: "
                f"{len(config.run_level_exclusions)}"
            )
            print("Generated query_text values:")
            for query in queries:
                print(f"  - {query.query_text}")
            for warning in warnings:
                print(f"Warning: {warning}")
            return 0
        if command_args.phase_b_command is None:
            print(
                "A Phase B command is required: validate-config, demo, run, "
                "recover, reselect, final-review, manual-finalize, or "
                "expansion-review.",
                file=sys.stderr,
            )
            return 2
        if command_args.phase_b_command == "recover":
            recovery = recover_completed_apify_runs(
                config_path=command_args.config,
                source_failed_run=command_args.source_failed_run,
                search_run_id=command_args.search_run_id,
                profile_run_id=command_args.profile_run_id,
                output_dir=command_args.output_dir,
            )
            print(
                "Phase B read-only recovery complete: "
                f"source={recovery.manifest['source_failed_run']}"
            )
            print(
                json.dumps(
                    recovery.manifest["counts"],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            print("Near-miss candidates (manual review only):")
            for rank, candidate in enumerate(
                recovery.near_miss_candidates[:5], start=1
            ):
                print(
                    f"  {rank}. {candidate['username']}: "
                    f"{candidate.get('score', 'n/a')} "
                    f"({', '.join(candidate['reviewable_reasons'])})"
                )
            print("Generated files:")
            for path in recovery.generated_paths:
                print(f"  {path}")
            return 0
        if command_args.phase_b_command == "expansion-review":
            manifest, generated_paths, candidates = run_expansion_review(
                command_args.source_run,
                command_args.manual_final_run,
                command_args.output_dir,
                review_path=command_args.review_file,
                focus_username=command_args.focus_candidate,
            )
            print(
                "Phase B offline expansion review complete: "
                f"run_id={manifest['run_id']}, mode={manifest['mode']}"
            )
            print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
            print("Expansion candidates (manual review only):")
            for candidate in candidates:
                print(
                    f"  {candidate['proposed_order']}. "
                    f"{candidate['username']}: "
                    f"{candidate['proposed_decision']} "
                    f"({candidate['priority']})"
                )
            print("Generated files:")
            for path in generated_paths:
                print(f"  {path}")
            return 0
        if command_args.phase_b_command == "manual-finalize":
            manifest, generated_paths, selected = finalize_saved_run(
                command_args.source_run,
                command_args.output_dir,
                review_path=command_args.review_file,
            )
            print(
                "Phase B manual finalization complete: "
                f"run_id={manifest['run_id']}, mode={manifest['mode']}"
            )
            print(
                json.dumps(
                    manifest["counts"],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            print("Final shortlist:")
            for rank, candidate in enumerate(selected, start=1):
                print(
                    f"  {rank}. {candidate['username']}: "
                    f"{float(candidate['score']):.2f}/100 "
                    f"({candidate['manual_verification_status']}, not_sent)"
                )
            print("Generated files:")
            for path in generated_paths:
                print(f"  {path}")
            return 0
        if command_args.phase_b_command == "final-review":
            manifest, generated_paths, top_candidates = (
                run_final_campaign_review(
                    command_args.source_run,
                    command_args.reviewed_run,
                    command_args.output_dir,
                    config_path=command_args.config,
                    review_path=command_args.review_file,
                )
            )
            print(
                f"Phase B final review complete: run_id={manifest.run_id}, "
                f"mode={manifest.mode}"
            )
            print(json.dumps(manifest.counts, ensure_ascii=False, indent=2))
            print("Top personal candidates:")
            for rank, candidate in enumerate(top_candidates, start=1):
                print(
                    f"  {rank}. {candidate.username}: "
                    f"{candidate.score:.2f}/100 "
                    f"({candidate.campaign_bucket})"
                )
            print("Generated files:")
            for path in generated_paths:
                print(f"  {path}")
            return 0
        if command_args.phase_b_command == "reselect":
            output_run_dir = command_args.output_dir or Path(
                f"{command_args.source_run}-reviewed"
            )
            result = reselect_saved_run(
                command_args.source_run,
                output_run_dir,
                config_path=command_args.config,
                review_path=command_args.review_file,
            )
        else:
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
