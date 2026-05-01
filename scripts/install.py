#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PLUGIN_NAME = "daedalus"
# All plugin payload lives under ``daedalus/`` in the repo. The install
# script copies the *contents* of that directory into the destination
# plugin root — keeping the source layout cleanly separated from
# operator scripts and dev material at the repo root.
PAYLOAD_ROOT = "daedalus"
PAYLOAD_ITEMS = [
    "__init__.py",
    "alerts.py",
    "code_hosts",
    "formatters.py",
    "migration.py",
    "plugin.yaml",
    "runtime.py",
    "engine",
    "runtimes",
    "schemas.py",
    "trackers",
    "daedalus_cli.py",
    "watch.py",
    "watch_sources.py",
    "workflows",
    "skills",
]


def _check_runtime_deps() -> None:
    """Fail early if the supported host python/runtime deps are missing."""
    if sys.version_info < (3, 10):
        raise RuntimeError(
            "daedalus plugin requires python3 >= 3.10 on the host "
            "(see docs/operator/installation.md)"
        )
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
            + " (see docs/operator/installation.md)"
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

    If ``target`` is a symlink, treat it as a retired legacy install layout:
    remove the symlink and replace it with a real directory at the canonical
    global plugin path.

    If ``target`` is a regular directory, wipe and recreate it. If it doesn't
    exist yet, create it (and its parents).
    """
    if target.is_symlink():
        target.unlink()
        target.mkdir(parents=True, exist_ok=True)
        return target
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def install_plugin(*, repo_root: Path, hermes_home: Path | None = None, destination: Path | None = None) -> Path:
    _check_runtime_deps()
    repo_root = repo_root.expanduser().resolve()
    payload_root = repo_root / PAYLOAD_ROOT
    if not payload_root.is_dir():
        raise FileNotFoundError(f"missing payload root: {payload_root}")
    target = resolve_destination(hermes_home=hermes_home, destination=destination)
    install_dir = _prepare_install_target(target)

    for item in PAYLOAD_ITEMS:
        source = payload_root / item
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
