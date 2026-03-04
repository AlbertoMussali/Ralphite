from __future__ import annotations

from pathlib import Path

from ralphite_engine.task_parser import parse_task_file


def test_parse_task_file_supports_v3_and_legacy_metadata(tmp_path: Path) -> None:
    task_file = tmp_path / "RALPHEX_TASK.md"
    task_file.write_text(
        "\n".join(
            [
                "# Tasks",
                "- [ ] Plan <!-- id:t1 phase:p1 lane:seq_pre deps: tools:git,rg test:pytest -->",
                "- [ ] Build <!-- id:t2 group:p1 seq:true deps:t1 -->",
                "- [ ] A <!-- id:tA phase:p1 lane:parallel parallel_group:1 deps:t2 -->",
                "- [x] Ship <!-- id:t3 phase:p1 lane:seq_post deps:tA -->",
            ]
        ),
        encoding="utf-8",
    )

    tasks, issues = parse_task_file(task_file)
    assert issues == []
    assert len(tasks) == 4
    assert tasks[0].id == "t1"
    assert tasks[0].phase == "p1"
    assert tasks[0].lane == "seq_pre"
    assert tasks[0].tools == ["git", "rg"]
    assert tasks[1].lane == "seq_pre"
    assert tasks[1].depends_on == ["t1"]
    assert tasks[2].parallel_group == 1
    assert tasks[3].completed is True


def test_parse_task_file_requires_id_and_valid_parallel_group(tmp_path: Path) -> None:
    task_file = tmp_path / "RALPHEX_TASK.md"
    task_file.write_text(
        "\n".join(
            [
                "# Tasks",
                "- [ ] Missing id <!-- phase:p1 lane:parallel parallel_group:0 -->",
            ]
        ),
        encoding="utf-8",
    )
    tasks, issues = parse_task_file(task_file)
    assert len(tasks) == 1
    assert any("missing required id" in issue for issue in issues)
    assert any("invalid parallel_group" in issue for issue in issues)
