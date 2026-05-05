"""Standalone Sprints CLI entrypoint."""

from .commands import *  # noqa: F401,F403
from .commands import SprintsCommandError, build_parser


def main(argv: list[str] | None = None) -> int:
    import sys

    parser = build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
        args.func(args)
        return 0
    except SprintsCommandError as exc:
        print(f"sprints error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
