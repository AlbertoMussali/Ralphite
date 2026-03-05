from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.perf
@pytest.mark.skipif(
    os.getenv("RALPHITE_PERF") != "1",
    reason="Set RALPHITE_PERF=1 to run performance baseline checks.",
)
def test_cli_perf_within_10_percent_baseline(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    baseline_path = root / "benchmarks" / "baseline" / "pre_unification_v1.json"
    output_path = tmp_path / "current.json"

    completed = subprocess.run(
        [
            "python3",
            "scripts/bench_cli.py",
            "--repeats",
            "1",
            "--output",
            str(output_path),
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = json.loads(output_path.read_text(encoding="utf-8"))

    for key in ("quickstart", "check_strict", "run", "cli_tests"):
        base_seconds = float(baseline["results"][key]["summary"]["median_seconds"])
        cur_seconds = float(current["results"][key]["summary"]["median_seconds"])
        assert cur_seconds <= base_seconds * 1.10, (
            f"{key} runtime regression: {cur_seconds} > {base_seconds * 1.10}"
        )

        base_rss = baseline["results"][key]["summary"].get("max_rss")
        cur_rss = current["results"][key]["summary"].get("max_rss")
        if isinstance(base_rss, (int, float)) and isinstance(cur_rss, (int, float)):
            assert cur_rss <= base_rss * 1.10, (
                f"{key} RSS regression: {cur_rss} > {base_rss * 1.10}"
            )
