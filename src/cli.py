"""Unified Command-Line Interface for the One User, One Agent platform.
"""

import argparse
import logging
from pathlib import Path
import subprocess
import sys

from src.config import DEFAULT_PACK_DIR, DEFAULT_SAMPLE_RUN_DIR
from src.impact_reporter import ImpactReporter
from src.outcome_judge import OutcomeJudge
from src.replay_harness import ReplayHarness

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cli")


def cmd_replay(args: argparse.Namespace) -> None:
    """Execute the full end-to-end replay, outcome simulation, judging, and reporting pipeline."""
    pack_dir = Path(args.pack)
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("==================================================")
    logger.info("STAGE: ONE USER, ONE AGENT - REPLAY PIPELINE")
    logger.info("Pack Directory:   %s", pack_dir)
    logger.info("Output Directory: %s", output_dir)
    logger.info("==================================================")

    # 1. Run Replay Harness
    t_start = Path(sys.executable)
    harness = ReplayHarness(pack_dir=pack_dir, output_dir=output_dir)
    harness.initialize()
    latency_report = harness.run_replay()

    # 2. Run Simulator
    sim_dir = output_dir / "sim"
    actions_file = output_dir / "actions.jsonl"
    sim_script = pack_dir / "simulator" / "simulate_outcomes.py"

    logger.info("Executing simulator via %s...", sim_script)
    sim_cmd = [
        sys.executable,
        str(sim_script),
        "--pack", str(pack_dir),
        "--actions", str(actions_file),
        "--out", str(sim_dir),
        "--seed", args.seed,
    ]
    res = subprocess.run(sim_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error("Simulator failed:\n%s", res.stderr)
        sys.exit(res.returncode)
    logger.info("Simulator completed successfully.")

    # 3. Judge Outcomes from Telemetry
    logger.info("Judging goal outcomes from attributable simulator events...")
    judge = OutcomeJudge(pack_dir=pack_dir, run_dir=output_dir)
    judged_records = judge.judge_all()

    # 4. Generate Causal Impact & Statistical Analysis Report
    logger.info("Generating statistical ITT impact report...")
    reporter = ImpactReporter(pack_dir=pack_dir, run_dir=output_dir)
    reporter.generate_markdown_report()

    logger.info("==================================================")
    logger.info("PIPELINE COMPLETED SUCCESSFULLY")
    logger.info("Decisions: %s", output_dir / "decisions.jsonl")
    logger.info("Actions:   %s", output_dir / "actions.jsonl")
    logger.info("Outcomes:  %s", output_dir / "outcomes.jsonl")
    logger.info("Latency:   %s", output_dir / "latency_report.json")
    logger.info("Impact:    %s", output_dir / "impact_report.md")
    logger.info("States:    %s", output_dir / "viewer_state")
    logger.info("==================================================")


def cmd_pilot(args: argparse.Namespace) -> None:
    """Recompute the earlier pilot study and expose selection bias."""
    pack_dir = Path(args.pack)
    run_dir = Path(args.out)
    reporter = ImpactReporter(pack_dir=pack_dir, run_dir=run_dir)
    res = reporter.analyze_pilot()
    print("\n--- PILOT RECOMPUTATION ANALYSIS ---")
    print(f"Naive As-Treated Comparison (Served vs Unserved):")
    print(f"  Served Rate:   {res['biased']['served_rate']*100:.2f}% (N={res['biased']['served_count']})")
    print(f"  Unserved Rate: {res['biased']['unserved_rate']*100:.2f}% (N={res['biased']['unserved_count']})")
    print(f"  Uplift:        +{res['biased']['diff']*100:.2f}% points (95% CI: [{res['biased']['ci_low']*100:.2f}%, {res['biased']['ci_high']*100:.2f}%])")
    print("\nTrue Randomized Intention-To-Treat (ITT) Comparison:")
    print(f"  Treatment Arm: {res['itt']['treatment_rate']*100:.2f}% (N={res['itt']['treatment_count']})")
    print(f"  Holdout Arm:   {res['itt']['holdout_rate']*100:.2f}% (N={res['itt']['holdout_count']})")
    print(f"  True Uplift:   {res['itt']['diff']*100:+.2f}% points (95% CI: [{res['itt']['ci_low']*100:.2f}%, {res['itt']['ci_high']*100:.2f}%])")
    print("------------------------------------\n")


def cmd_judge(args: argparse.Namespace) -> None:
    """Judge goal outcomes from simulator telemetry."""
    pack_dir = Path(args.pack)
    run_dir = Path(args.out)
    judge = OutcomeJudge(pack_dir=pack_dir, run_dir=run_dir)
    judge.judge_all()


def cmd_report(args: argparse.Namespace) -> None:
    """Generate impact markdown report."""
    pack_dir = Path(args.pack)
    run_dir = Path(args.out)
    reporter = ImpactReporter(pack_dir=pack_dir, run_dir=run_dir)
    reporter.generate_markdown_report()


def main() -> None:
    parser = argparse.ArgumentParser(description="One User, One Agent - Production Replay & Evaluation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Replay subcommand
    p_replay = subparsers.add_parser("replay", help="Run full replay day pipeline end-to-end")
    p_replay.add_argument("--pack", default=str(DEFAULT_PACK_DIR), help="Path to data pack directory")
    p_replay.add_argument("--out", default=str(DEFAULT_SAMPLE_RUN_DIR), help="Output run directory")
    p_replay.add_argument("--seed", default="oua-practice", help="Simulator random seed")
    p_replay.set_defaults(func=cmd_replay)

    # Pilot subcommand
    p_pilot = subparsers.add_parser("pilot", help="Recompute pilot report and examine selection bias")
    p_pilot.add_argument("--pack", default=str(DEFAULT_PACK_DIR), help="Path to data pack directory")
    p_pilot.add_argument("--out", default=str(DEFAULT_SAMPLE_RUN_DIR), help="Output run directory")
    p_pilot.set_defaults(func=cmd_pilot)

    # Judge subcommand
    p_judge = subparsers.add_parser("judge", help="Judge outcomes from existing simulator telemetry")
    p_judge.add_argument("--pack", default=str(DEFAULT_PACK_DIR), help="Path to data pack directory")
    p_judge.add_argument("--out", default=str(DEFAULT_SAMPLE_RUN_DIR), help="Output run directory")
    p_judge.set_defaults(func=cmd_judge)

    # Report subcommand
    p_report = subparsers.add_parser("report", help="Generate impact markdown report from existing outcomes")
    p_report.add_argument("--pack", default=str(DEFAULT_PACK_DIR), help="Path to data pack directory")
    p_report.add_argument("--out", default=str(DEFAULT_SAMPLE_RUN_DIR), help="Output run directory")
    p_report.set_defaults(func=cmd_report)

    parsed = parser.parse_args()
    parsed.func(parsed)


if __name__ == "__main__":
    main()
