from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import urlparse

from ralphite_engine.headless_agent import (
    build_codex_exec_command,
    build_cursor_exec_command,
)


REPO_ROOT = Path(__file__).resolve().parents[3]

REQUIRED_DOC_PATHS = [
    "README.md",
    "AGENTS.md",
    "ARCHITECTURE.md",
    "USER_GUIDE.md",
    "docs/index.md",
    "docs/design-docs/index.md",
    "docs/design-docs/core-beliefs.md",
    "docs/design-docs/system-boundaries.md",
    "docs/design-docs/non-goals.md",
    "docs/product-specs/index.md",
    "docs/product-specs/near-term-roadmap.md",
    "docs/product-specs/operator-personas.md",
    "docs/product-specs/release-success-criteria.md",
    "docs/architecture/index.md",
    "docs/architecture/runtime-execution.md",
    "docs/architecture/orchestration-templates.md",
    "docs/architecture/persistence-and-state.md",
    "docs/architecture/failure-taxonomy.md",
    "docs/architecture/security-and-trust-model.md",
    "docs/workflows/index.md",
    "docs/workflows/first-run.md",
    "docs/workflows/recovery.md",
    "docs/workflows/release-readiness.md",
    "docs/workflows/docs-maintenance.md",
    "docs/exec-plans/active",
    "docs/exec-plans/completed",
    "docs/exec-plans/tech-debt-tracker.md",
    "docs/references/index.md",
    "docs/references/cli-contracts.md",
    "docs/references/plan-schema-reference.md",
    "docs/references/test-matrix.md",
    "docs/references/glossary.md",
    "docs/generated/index.md",
    "docs/generated/command-contracts.md",
    "docs/generated/schema-summary.md",
    "docs/decisions/index.md",
    "docs/decisions/ADR-TEMPLATE.md",
    "docs/decisions/ADR-0001-headless-backend-default.md",
    "docs/decisions/ADR-0002-strict-check-policy.md",
    "docs/decisions/ADR-0003-starter-plan-governance.md",
    "docs/QUALITY_SCORE.md",
    "docs/RELIABILITY.md",
    "docs/SECURITY.md",
    "docs/PLANS.md",
    "docs/DESIGN.md",
    "docs/PRODUCT_SENSE.md",
    "docs/FRONTEND.md",
]

MAJOR_DOCS = [
    "README.md",
    "AGENTS.md",
    "docs/index.md",
    "docs/architecture/index.md",
    "docs/workflows/index.md",
    "docs/references/index.md",
]

MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _doc_files() -> list[Path]:
    docs = sorted((REPO_ROOT / "docs").rglob("*.md"))
    top_level = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT / "ARCHITECTURE.md",
        REPO_ROOT / "USER_GUIDE.md",
    ]
    return [*top_level, *docs]


def _is_external_link(link: str) -> bool:
    parsed = urlparse(link)
    return parsed.scheme in {"http", "https", "mailto"}


def test_required_docs_exist() -> None:
    for rel in REQUIRED_DOC_PATHS:
        assert (REPO_ROOT / rel).exists(), f"missing required docs path: {rel}"


def test_major_docs_have_freshness_marker() -> None:
    for rel in MAJOR_DOCS:
        content = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "Last verified against commit:" in content, (
            f"{rel} is missing freshness marker"
        )


def test_docs_contain_current_contract_strings() -> None:
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in _doc_files())
    for required in ("gpt-5.3-codex", "--reasoning-effort", "--strict"):
        assert required in corpus, f"expected docs to contain: {required}"
    assert "gpt-4.1" not in corpus, "obsolete model reference leaked into docs"


def test_markdown_local_links_resolve() -> None:
    failures: list[str] = []
    for source in _doc_files():
        text = source.read_text(encoding="utf-8")
        for raw_link in MARKDOWN_LINK_RE.findall(text):
            link = raw_link.strip()
            if not link or link.startswith("#") or _is_external_link(link):
                continue
            target_path = link.split("#", 1)[0].strip()
            if not target_path:
                continue
            resolved = (source.parent / target_path).resolve()
            if not resolved.exists():
                failures.append(f"{source.relative_to(REPO_ROOT)} -> {link}")
    assert not failures, "broken local markdown links:\n" + "\n".join(failures)


def test_generated_command_contracts_match_builders() -> None:
    text = (REPO_ROOT / "docs/generated/command-contracts.md").read_text(
        encoding="utf-8"
    )
    expected_codex = " ".join(
        build_codex_exec_command(
            prompt="TASK_PROMPT",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            worktree=Path("/tmp/worktree"),
            sandbox="workspace-write",
        )
    )
    expected_cursor = " ".join(
        build_cursor_exec_command(
            prompt="TASK_PROMPT",
            model="gpt-5.3-codex",
            cursor_command="agent",
            force=True,
        )
    )
    assert expected_codex in text
    assert expected_cursor in text
