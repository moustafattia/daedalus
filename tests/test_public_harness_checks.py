from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_SPECIFIC_TOKENS = ("yoyo" + "pod",)
REMOVED_PUBLIC_ARCHIVE = "super" + "powers"
PUBLIC_TEXT_EXTENSIONS = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".svg",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
SKIPPED_DIRS = {
    ".claude",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
}


def _is_skipped_path(path: Path) -> bool:
    rel_parts = path.relative_to(REPO_ROOT).parts
    return bool(set(rel_parts) & SKIPPED_DIRS)


def test_project_specific_terms_do_not_leak_into_public_repo():
    leaks: list[str] = []

    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or _is_skipped_path(path):
            continue
        if path.suffix.lower() not in PUBLIC_TEXT_EXTENSIONS:
            continue

        rel_path = path.relative_to(REPO_ROOT).as_posix().casefold()
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        if any(token in rel_path or token in text for token in PROJECT_SPECIFIC_TOKENS):
            leaks.append(path.relative_to(REPO_ROOT).as_posix())

    assert leaks == []


def test_projects_tree_is_placeholder_only():
    projects_root = REPO_ROOT / "daedalus" / "projects"
    files = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in projects_root.rglob("*")
        if path.is_file()
    )

    assert files == [
        "daedalus/projects/PLACE_HOLDER.md",
        "daedalus/projects/README.md",
    ]


def test_public_docs_present_github_first_path():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    install = (REPO_ROOT / "docs" / "operator" / "installation.md").read_text(encoding="utf-8")
    issue_runner = (REPO_ROOT / "docs" / "workflows" / "issue-runner.md").read_text(encoding="utf-8")
    conformance = (REPO_ROOT / "docs" / "symphony-conformance.md").read_text(encoding="utf-8")

    assert "GitHub-first SDLC automation engine" in readme
    assert "First-class tracker" in readme
    assert "docs/harness-engineering.md" in readme
    assert "tracker.kind: github" in install
    assert "Linear exists as an experimental adapter" in install
    assert "`github` — first-class public tracker path" in issue_runner
    assert "`local-json` — local development and test fixture path" in issue_runner
    assert "`linear` — experimental adapter" in issue_runner
    assert "skipped-by-default live smoke" in conformance
    assert ("Linear integration" + " smoke tests") not in conformance


def test_docs_index_links_harness_and_omits_removed_planning_archive():
    docs_index = (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    public_contract = (REPO_ROOT / "docs" / "public-contract.md").read_text(encoding="utf-8")

    assert "harness-engineering.md" in docs_index
    assert REMOVED_PUBLIC_ARCHIVE not in docs_index.casefold()
    assert REMOVED_PUBLIC_ARCHIVE not in public_contract.casefold()
