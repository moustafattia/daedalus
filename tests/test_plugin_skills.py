"""Structural tests for the plugin-owned ``skills/`` payload.

Skills live under ``~/WS/daedalus/skills/<skill-name>/SKILL.md``.
The installer copies this tree to ``~/.hermes/plugins/daedalus/skills/``
so Hermes can discover the skills when ``HERMES_ENABLE_PROJECT_PLUGINS=true``
is set.

These tests pin:

- every skill dir contains a ``SKILL.md`` file
- every ``SKILL.md`` starts with a YAML frontmatter block declaring at
  least ``name`` and ``description``
- the frontmatter ``name`` matches the directory name
- the set of skills includes the expected YoYoPod + Daedalus surface
"""
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1] / "daedalus"
SKILLS_DIR = REPO_ROOT / "skills"


EXPECTED_SKILLS = {
    "operator",
    "yoyopod-lane-automation",
    "yoyopod-workflow-watchdog-tick",
    "yoyopod-closeout-notifier",
    "yoyopod-daedalus-alerts-monitoring",
    "yoyopod-daedalus-outage-alerts",
    "daedalus-architecture",
    "daedalus-model1-project-layout",
    "hermes-plugin-cli-wiring",
    "daedalus-hardening-slices",
    "daedalus-retire-watchdog-and-migrate-control-schema",
}


def _discover_skill_dirs() -> list[Path]:
    return sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())


def _parse_frontmatter(skill_md: Path) -> dict[str, str]:
    text = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{skill_md} must start with a YAML frontmatter block"
    fm_text = match.group(1)
    fields: dict[str, str] = {}
    for line in fm_text.splitlines():
        if not line or line.startswith(" "):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_skills_dir_contains_expected_set():
    dirs = {p.name for p in _discover_skill_dirs()}
    missing = EXPECTED_SKILLS - dirs
    unexpected = dirs - EXPECTED_SKILLS - {"README.md"}
    assert not missing, f"skill directories missing from skills/: {sorted(missing)}"
    # Document any unexpected skill directories so we notice additions.
    assert not unexpected, (
        f"unexpected skill directories (update EXPECTED_SKILLS if intentional): {sorted(unexpected)}"
    )


def test_every_skill_has_skill_md_with_valid_frontmatter():
    for skill_dir in _discover_skill_dirs():
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.exists(), f"{skill_dir.name} is missing SKILL.md"
        fields = _parse_frontmatter(skill_md)
        assert "name" in fields, f"{skill_dir.name}/SKILL.md missing `name` in frontmatter"
        assert "description" in fields, f"{skill_dir.name}/SKILL.md missing `description` in frontmatter"


def test_skill_name_matches_directory_name():
    mismatches = []
    for skill_dir in _discover_skill_dirs():
        fields = _parse_frontmatter(skill_dir / "SKILL.md")
        if fields.get("name") != skill_dir.name:
            mismatches.append((skill_dir.name, fields.get("name")))
    assert not mismatches, (
        f"skills where frontmatter `name` ≠ directory name: {mismatches}. "
        "Rename either the directory or the frontmatter to match."
    )


def test_skills_readme_exists_and_lists_each_skill():
    readme = SKILLS_DIR / "README.md"
    assert readme.exists(), "skills/README.md is missing"
    text = readme.read_text(encoding="utf-8")
    for skill_name in EXPECTED_SKILLS:
        assert skill_name in text, f"skills/README.md does not mention `{skill_name}`"


def test_no_skills_reference_retired_wrapper_script():
    """The workspace-side `scripts/yoyopod_workflow.py` wrapper has been retired.

    Skill commands should invoke the plugin entrypoint, not the wrapper.
    """
    offenders: list[tuple[str, int]] = []
    for skill_dir in _discover_skill_dirs():
        skill_md = skill_dir / "SKILL.md"
        for i, line in enumerate(skill_md.read_text(encoding="utf-8").splitlines(), start=1):
            # Only flag actionable command lines, not narrative history.
            stripped = line.strip().lstrip("`").lstrip()
            if stripped.startswith("python3 ") and "scripts/yoyopod_workflow.py" in stripped:
                offenders.append((f"{skill_dir.name}/SKILL.md:{i}", line.strip()))
    assert not offenders, (
        "skills still contain actionable `python3 .../scripts/yoyopod_workflow.py` commands. "
        "These must use the plugin entrypoint "
        "`python3 .../adapters/yoyopod_core/__main__.py` instead. Offenders: "
        f"{offenders}"
    )
