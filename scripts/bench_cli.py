#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
import time
from typing import Any

TIME_BIN = "/usr/bin/time"
RSS_PATTERN = re.compile(r"^\s*(\d+)\s+maximum resident set size")


def _parse_time_output(stderr: str) -> int | None:
    for line in stderr.splitlines():
        match = RSS_PATTERN.match(line.strip())
        if match:
            return int(match.group(1))
    return None


def _run_timed(command: list[str], env: dict[str, str]) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(
        [TIME_BIN, "-lp", *command],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    elapsed = time.perf_counter() - started
    return {
        "command": " ".join(command),
        "exit_code": proc.returncode,
        "seconds": elapsed,
        "max_rss": _parse_time_output(proc.stderr or ""),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    times = [s["seconds"] for s in samples]
    rss_values = [s["max_rss"] for s in samples if isinstance(s.get("max_rss"), int)]
    return {
        "median_seconds": statistics.median(times),
        "p95_seconds": statistics.quantiles(times, n=20)[18] if len(times) >= 2 else times[0],
        "max_rss": max(rss_values) if rss_values else None,
    }


def _build_commands(workspace: str) -> dict[str, list[str]]:
    return {
        "quickstart": [
            "uv",
            "run",
            "ralphite",
            "quickstart",
            "--workspace",
            workspace,
            "--bootstrap",
            "--yes",
            "--output",
            "json",
        ],
        "check_strict": [
            "uv",
            "run",
            "ralphite",
            "check",
            "--workspace",
            workspace,
            "--strict",
            "--output",
            "json",
        ],
        "run": [
            "uv",
            "run",
            "ralphite",
            "run",
            "--workspace",
            workspace,
            "--yes",
            "--output",
            "json",
        ],
        "cli_tests": [
            "uv",
            "run",
            "--with",
            "pytest",
            "pytest",
            "apps/cli/tests/test_cli_output_contract.py",
            "apps/cli/tests/test_cli_ux_commands.py",
            "apps/cli/tests/test_cli_recover.py",
            "-q",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Ralphite CLI commands")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/current_cli_perf.json"))
    args = parser.parse_args()

    env = os.environ.copy()
    env.setdefault("RALPHITE_DEV_SIMULATED_EXECUTION", "1")
    env.setdefault("RALPHITE_SKIP_BACKEND_CMD_CHECKS", "1")
    env["RALPHITE_PERF"] = "0"

    with tempfile.TemporaryDirectory(prefix="ralphite-bench-") as tmpdir:
        commands = _build_commands(tmpdir)
        report: dict[str, Any] = {"repeats": args.repeats, "results": {}}
        for name, command in commands.items():
            samples: list[dict[str, Any]] = []
            for _ in range(args.repeats):
                sample = _run_timed(command, env=env)
                if sample["exit_code"] != 0:
                    raise SystemExit(
                        f"benchmark command failed for {name}: {sample['command']}\n{sample['stderr']}"
                    )
                samples.append(sample)
            report["results"][name] = {
                "summary": _summary(samples),
                "samples": [
                    {
                        "seconds": s["seconds"],
                        "max_rss": s["max_rss"],
                        "exit_code": s["exit_code"],
                    }
                    for s in samples
                ],
            }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
