import argparse
import importlib.util
import re
import shlex
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DAEDALUS_ROOT = REPO_ROOT / "daedalus"
DOCS_WITH_OPERATOR_COMMANDS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "operator" / "installation.md",
    REPO_ROOT / "docs" / "operator" / "codex-app-server.md",
    REPO_ROOT / "docs" / "operator" / "slash-commands.md",
]
DOCS_WITH_WORKFLOW_COMMANDS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "operator" / "installation.md",
    REPO_ROOT / "docs" / "operator" / "slash-commands.md",
    REPO_ROOT / "docs" / "workflows" / "change-delivery.md",
    REPO_ROOT / "docs" / "workflows" / "issue-runner.md",
]
DOCS_WITH_DIRECT_WORKFLOW_COMMANDS = [
    REPO_ROOT / "docs" / "operator" / "cheat-sheet.md",
    REPO_ROOT / "docs" / "operator" / "http-status.md",
    REPO_ROOT / "docs" / "concepts" / "migration.md",
]


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _subparser_choices(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _daedalus_parser() -> argparse.ArgumentParser:
    return _load_module("daedalus_cli_docs_drift", DAEDALUS_ROOT / "daedalus_cli.py").build_parser()


def _workflow_parser(workflow_name: str) -> argparse.ArgumentParser:
    slug = workflow_name.replace("-", "_")
    return _load_module(
        f"{slug}_cli_docs_drift",
        DAEDALUS_ROOT / "workflows" / slug / "cli.py",
    ).build_parser()


def _operator_command_mentions(prefix: str, paths: list[Path]) -> list[tuple[Path, str]]:
    pattern = re.compile(rf"`({re.escape(prefix)}(?:\s+[^`]+)?)`")
    mentions: list[tuple[Path, str]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            command = " ".join(match.group(1).split())
            if "<" in command or "[" in command:
                continue
            mentions.append((path, command))
    return mentions


def _shell_commands_from_fences(paths: list[Path], *, prefix: str) -> list[tuple[Path, str]]:
    commands: list[tuple[Path, str]] = []
    fence_re = re.compile(r"```(?:bash|shell|sh|text)?\n(.*?)```", re.DOTALL)
    for path in paths:
        for fence in fence_re.findall(path.read_text(encoding="utf-8")):
            pending = ""
            for raw_line in fence.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or line.startswith("export "):
                    continue
                line = line.split("#", 1)[0].rstrip()
                if pending:
                    line = f"{pending} {line}"
                if line.endswith("\\"):
                    pending = line[:-1].strip()
                    continue
                pending = ""
                if line.startswith(prefix):
                    commands.append((path, " ".join(line.split())))
    return commands


def _without_workflow_root(argv: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--workflow-root":
            index += 2
            continue
        if arg.startswith("--workflow-root="):
            index += 1
            continue
        out.append(arg)
        index += 1
    return out


def test_documented_daedalus_commands_exist_on_operator_parser():
    parser = _daedalus_parser()
    daedalus_choices = _subparser_choices(parser)
    mentions = _operator_command_mentions("/daedalus", [REPO_ROOT / "docs" / "operator" / "slash-commands.md"])

    missing: list[str] = []
    for path, command in mentions:
        argv = shlex.split(command)
        if len(argv) < 2:
            continue
        subcommand = argv[1]
        if subcommand not in daedalus_choices:
            missing.append(f"{path.relative_to(REPO_ROOT)}: {command}")
            continue
        if subcommand == "codex-app-server" and len(argv) >= 3:
            codex_choices = _subparser_choices(daedalus_choices[subcommand])
            if argv[2] not in codex_choices:
                missing.append(f"{path.relative_to(REPO_ROOT)}: {command}")

    assert missing == []


def test_documented_hermes_daedalus_snippets_parse_with_operator_parser():
    parser = _daedalus_parser()
    commands = _shell_commands_from_fences(DOCS_WITH_OPERATOR_COMMANDS, prefix="hermes daedalus")
    inline = [
        (path, command.replace("`", ""))
        for path, command in _operator_command_mentions("hermes daedalus", DOCS_WITH_OPERATOR_COMMANDS)
    ]
    commands.extend(inline)

    failures: list[str] = []
    for path, command in sorted(set(commands), key=lambda item: (str(item[0]), item[1])):
        argv = shlex.split(command)[2:]
        try:
            parser.parse_args(argv)
        except SystemExit:
            failures.append(f"{path.relative_to(REPO_ROOT)}: {command}")

    assert failures == []


def test_documented_workflow_commands_exist_on_workflow_parsers():
    parsers = {
        "change-delivery": _workflow_parser("change-delivery"),
        "issue-runner": _workflow_parser("issue-runner"),
    }
    choices = {name: _subparser_choices(parser) for name, parser in parsers.items()}
    mentions = _operator_command_mentions("/workflow", DOCS_WITH_WORKFLOW_COMMANDS)

    failures: list[str] = []
    for path, command in mentions:
        argv = shlex.split(command)
        if len(argv) < 3 or argv[1].startswith("<"):
            continue
        workflow_name = argv[1]
        if workflow_name not in parsers:
            failures.append(f"{path.relative_to(REPO_ROOT)}: unknown workflow in {command}")
            continue
        workflow_args = argv[2:]
        if workflow_args and workflow_args[0] not in choices[workflow_name]:
            failures.append(f"{path.relative_to(REPO_ROOT)}: {command}")
            continue
        try:
            parsers[workflow_name].parse_args(workflow_args or ["--help"])
        except SystemExit as exc:
            if exc.code != 0:
                failures.append(f"{path.relative_to(REPO_ROOT)}: {command}")

    assert failures == []


def test_documented_direct_workflow_python_commands_map_to_workflow_parsers():
    parsers = {
        "change-delivery": _workflow_parser("change-delivery"),
        "issue-runner": _workflow_parser("issue-runner"),
    }
    choices = {name: _subparser_choices(parser) for name, parser in parsers.items()}
    commands = []
    commands.extend(
        _shell_commands_from_fences(DOCS_WITH_DIRECT_WORKFLOW_COMMANDS, prefix="python3 -m workflows")
    )
    commands.extend(
        _shell_commands_from_fences(
            DOCS_WITH_DIRECT_WORKFLOW_COMMANDS,
            prefix="python3 ~/.hermes/plugins/daedalus/workflows/__main__.py",
        )
    )

    failures: list[str] = []
    for path, command in sorted(set(commands), key=lambda item: (str(item[0]), item[1])):
        argv = shlex.split(command)
        if argv[:3] == ["python3", "-m", "workflows.change_delivery"]:
            parser = parsers["change-delivery"]
            workflow_args = _without_workflow_root(argv[3:])
        elif argv[:3] == ["python3", "-m", "workflows.issue_runner"]:
            parser = parsers["issue-runner"]
            workflow_args = _without_workflow_root(argv[3:])
        elif argv[:3] == ["python3", "-m", "workflows"] or argv[:2] == [
            "python3",
            "~/.hermes/plugins/daedalus/workflows/__main__.py",
        ]:
            start = 3 if argv[:3] == ["python3", "-m", "workflows"] else 2
            workflow_args = _without_workflow_root(argv[start:])
            if not workflow_args:
                failures.append(f"{path.relative_to(REPO_ROOT)}: {command}")
                continue
            if not any(workflow_args[0] in workflow_choices for workflow_choices in choices.values()):
                failures.append(f"{path.relative_to(REPO_ROOT)}: {command}")
                continue
            parser = next(
                parser
                for workflow_name, parser in parsers.items()
                if workflow_args[0] in choices[workflow_name]
            )
        else:
            continue
        try:
            parser.parse_args(workflow_args)
        except SystemExit as exc:
            if exc.code != 0:
                failures.append(f"{path.relative_to(REPO_ROOT)}: {command}")

    assert failures == []


def test_readme_quickstart_keeps_issue_runner_default_path():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    quickstart_match = re.search(r"## Quick Start\n\n```bash\n(.*?)```", readme, re.DOTALL)
    assert quickstart_match is not None
    quickstart = quickstart_match.group(1)

    assert "hermes daedalus bootstrap\n" in quickstart
    assert "hermes daedalus bootstrap --workflow" not in quickstart
    assert "hermes daedalus validate" in quickstart
    assert "hermes daedalus service-up" in quickstart
    assert readme.index("hermes daedalus bootstrap") < readme.index("hermes daedalus validate")
    assert "`issue-runner` is the default public bootstrap path." in readme
