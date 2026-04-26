#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

PLUGIN_NAME = "daedalus"
PAYLOAD_ITEMS = [
    "__init__.py",
    "alerts.py",
    "formatters.py",
    "migration.py",
    "observability_overrides.py",
    "plugin.yaml",
    "runtime.py",
    "schemas.py",
    "tools.py",
    "watch.py",
    "watch_sources.py",
    "workflows",
    "projects",
    "skills",
]


def _check_runtime_deps() -> None:
    """Fail early if PyYAML or jsonschema are missing on the host python."""
    missing = []
    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append("pyyaml (apt: python3-yaml)")
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        missing.append("jsonschema (apt: python3-jsonschema)")
    if missing:
        raise RuntimeError(
            "daedalus plugin requires the following python modules on the host: "
            + ", ".join(missing)
        )


def resolve_destination(*, hermes_home: Path | None = None, destination: Path | None = None) -> Path:
    # Intentionally avoid ``Path.resolve()`` on the final path because that
    # follows symlinks — callers passing a symlink destination expect the
    # symlink itself to be returned (and preserved across reinstall).
    if destination is not None:
        return destination.expanduser().absolute()
    hermes_root = (hermes_home or Path.home() / ".hermes").expanduser().absolute()
    return hermes_root / "plugins" / PLUGIN_NAME


def _prepare_install_target(target: Path) -> Path:
    """Return the concrete directory to install into.

    If ``target`` is a symlink (common setup: ``~/.hermes/plugins/daedalus``
    pointing at a workflow-scoped plugin tree under
    ``~/.hermes/workflows/<project>/.hermes/plugins/daedalus``), follow
    the symlink and install into the real directory. The symlink itself is
    preserved so callers that hard-code the symlink path keep working.

    If ``target`` is a regular directory, wipe and recreate it. If it doesn't
    exist yet, create it (and its parents).
    """
    if target.is_symlink():
        real_target = target.resolve()
        if real_target.exists():
            shutil.rmtree(real_target)
        real_target.mkdir(parents=True, exist_ok=True)
        return real_target
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def install_plugin(*, repo_root: Path, hermes_home: Path | None = None, destination: Path | None = None) -> Path:
    _check_runtime_deps()
    repo_root = repo_root.expanduser().resolve()
    target = resolve_destination(hermes_home=hermes_home, destination=destination)
    install_dir = _prepare_install_target(target)

    for item in PAYLOAD_ITEMS:
        source = repo_root / item
        if not source.exists():
            raise FileNotFoundError(f"missing payload item: {source}")
        dest = install_dir / item
        if source.is_dir():
            shutil.copytree(source, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install the daedalus plugin into a Hermes plugins directory.")
    parser.add_argument("--hermes-home", help="Hermes home directory. Default: ~/.hermes")
    parser.add_argument("--destination", help="Explicit plugin destination directory. Overrides --hermes-home.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]), help="Source repository root. Default: this repository root.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    target = install_plugin(
        repo_root=Path(args.repo_root),
        hermes_home=Path(args.hermes_home) if args.hermes_home else None,
        destination=Path(args.destination) if args.destination else None,
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
