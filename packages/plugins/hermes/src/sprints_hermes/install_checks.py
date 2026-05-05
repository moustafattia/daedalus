"""Install and compatibility checks for the Sprints Hermes plugin.

Hermes' Git plugin installer clones a directory plugin and reads its manifest,
but it does not currently execute a plugin-defined install hook. Keep this
module stdlib-only so Sprints can still load far enough to install/report
missing runtime dependencies.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PythonPackageSpec:
    import_name: str
    package_name: str
    apt_package: str


REQUIRED_PYTHON_PACKAGES: tuple[PythonPackageSpec, ...] = (
    PythonPackageSpec("yaml", "PyYAML", "python3-yaml"),
    PythonPackageSpec("jsonschema", "jsonschema", "python3-jsonschema"),
    PythonPackageSpec("rich", "rich", "python3-rich"),
)

REQUIRED_CONTEXT_METHODS: tuple[str, ...] = (
    "register_command",
    "register_cli_command",
    "register_skill",
)

MIN_PYTHON = (3, 10)


@dataclass
class InstallReport:
    python_executable: str
    python_version: str
    python_ok: bool
    hermes_version: str | None
    missing_context_methods: list[str] = field(default_factory=list)
    missing_packages: list[str] = field(default_factory=list)
    installed_packages: list[str] = field(default_factory=list)
    install_error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.python_ok
            and not self.missing_context_methods
            and not self.missing_packages
            and self.install_error is None
        )


def check_install_readiness(
    ctx: Any,
    *,
    auto_install: bool = True,
    python_executable: str | None = None,
) -> InstallReport:
    executable = python_executable or sys.executable
    report = InstallReport(
        python_executable=executable,
        python_version=_python_version_label(),
        python_ok=sys.version_info >= MIN_PYTHON,
        hermes_version=_hermes_version(),
        missing_context_methods=_missing_context_methods(ctx),
        missing_packages=_missing_python_packages(),
    )

    if (
        report.python_ok
        and auto_install
        and report.missing_packages
        and not _skip_auto_install()
    ):
        attempted = list(report.missing_packages)
        ok, error = _install_python_packages(attempted, executable=executable)
        if ok:
            report.installed_packages = attempted
            report.missing_packages = _missing_python_packages()
            if report.missing_packages:
                report.install_error = (
                    "pip completed, but Python still cannot import: "
                    + ", ".join(report.missing_packages)
                )
        else:
            report.install_error = error

    return report


def format_install_report(report: InstallReport) -> str:
    lines: list[str] = [
        "Sprints install readiness",
        f"- Python: {report.python_version} ({'ok' if report.python_ok else 'unsupported'})",
        f"- Hermes: {report.hermes_version or 'unknown'}",
    ]

    if report.missing_context_methods:
        lines.append(
            "- Hermes compatibility: missing plugin APIs "
            + ", ".join(report.missing_context_methods)
        )
        lines.append("  Update Hermes with: hermes update")
    else:
        lines.append("- Hermes compatibility: ok")

    if report.installed_packages:
        lines.append("- Installed Python packages: " + ", ".join(report.installed_packages))

    if report.missing_packages:
        package_names = _package_names(report.missing_packages)
        apt_names = _apt_names(report.missing_packages)
        lines.append("- Missing Python packages: " + ", ".join(report.missing_packages))
        lines.append(
            "  Python install: "
            + report.python_executable
            + " -m pip install "
            + " ".join(package_names)
        )
        lines.append("  Shell fallback: python -m pip install " + " ".join(package_names))
        if apt_names:
            lines.append("  Debian/Ubuntu install: sudo apt install " + " ".join(apt_names))

    if report.install_error:
        lines.append("- Dependency install failed: " + report.install_error)

    if not report.python_ok:
        lines.append("  Use Python 3.10 or newer for the Hermes process that loads Sprints.")

    lines.extend(
        [
            "",
            "Next steps:",
            "1. cd /path/to/repo",
            "2. hermes sprints bootstrap",
            "3. $EDITOR WORKFLOW.md",
            "4. hermes sprints codex-app-server up",
            "5. hermes sprints validate",
            "6. hermes sprints doctor",
            "7. hermes sprints daemon up",
        ]
    )
    return "\n".join(lines)


def register_install_help(ctx: Any, report: InstallReport) -> None:
    message = format_install_report(report)

    if hasattr(ctx, "register_command"):
        ctx.register_command(
            "sprints",
            lambda raw_args="": message,
            description="Show Sprints install readiness and next steps.",
        )

    if hasattr(ctx, "register_cli_command"):

        def _setup_cli(subparser):
            subparser.set_defaults(func=_handler)

        def _handler(args):
            del args
            print(message)

        ctx.register_cli_command(
            name="sprints",
            help="Show Sprints install readiness and next steps.",
            setup_fn=_setup_cli,
            handler_fn=_handler,
            description="Sprints install readiness.",
        )


def _missing_python_packages() -> list[str]:
    missing: list[str] = []
    for spec in REQUIRED_PYTHON_PACKAGES:
        try:
            found = importlib.util.find_spec(spec.import_name)
        except ModuleNotFoundError:
            found = None
        if found is None:
            missing.append(spec.package_name)
    return missing


def _install_python_packages(
    package_names: list[str], *, executable: str
) -> tuple[bool, str | None]:
    try:
        completed = subprocess.run(
            [executable, "-m", "pip", "install", *package_names],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return False, str(exc)
    if completed.returncode == 0:
        return True, None
    detail = (completed.stderr or completed.stdout or "").strip()
    return False, detail or f"pip exited with status {completed.returncode}"


def _missing_context_methods(ctx: Any) -> list[str]:
    return [name for name in REQUIRED_CONTEXT_METHODS if not hasattr(ctx, name)]


def _package_names(missing: list[str]) -> list[str]:
    wanted = set(missing)
    return [spec.package_name for spec in REQUIRED_PYTHON_PACKAGES if spec.package_name in wanted]


def _apt_names(missing: list[str]) -> list[str]:
    wanted = set(missing)
    return [spec.apt_package for spec in REQUIRED_PYTHON_PACKAGES if spec.package_name in wanted]


def _python_version_label() -> str:
    info = sys.version_info
    return f"{info.major}.{info.minor}.{info.micro}"


def _hermes_version() -> str | None:
    for distribution in ("hermes-agent", "hermes_agent"):
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def _skip_auto_install() -> bool:
    return os.getenv("SPRINTS_SKIP_AUTO_INSTALL", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
