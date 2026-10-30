"""Command-line interface."""
import argparse
import importlib
import os
import sys
import traceback
from typing import Any, Optional, Sequence
from .diagnostics import DecompilationError, comment_block
from .loader import get_decompiler

# Exit status when --strict stops at the first place the decompiler would have had to guess.
EXIT_STRICT_FAILURE = 3
# Exit status for a failure inside pycrefine itself, as opposed to a file it cannot read.
EXIT_INTERNAL_ERROR = 4
ISSUES_URL = "https://github.com/sahebbiswas/pycrefine/issues"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _version() -> str:
    """The package version. Looked up on demand: the package imports this module, so a top-level import would be circular."""
    return str(importlib.import_module(__package__ or "pycrefine").__version__)


def _silence_stdout() -> None:
    """Point stdout at the null device, so the flush at interpreter exit cannot fail again."""
    try:
        target = sys.stdout.fileno()
    except (OSError, ValueError, AttributeError):  # stdout is not a real file, so there is nothing to flush
        return
    try:
        null = os.open(os.devnull, os.O_WRONLY)
    except OSError:  # no null device to use (descriptors exhausted, say): the exit status still tells the story
        return
    try:
        os.dup2(null, target)
    except OSError:
        pass
    finally:
        os.close(null)


def _write_stdout(text: str, what: str = "the decompiled source") -> None:
    """Print *text*; a failure to write (a closed pipe, say) exits with status 1 and its own message."""
    try:
        print(text)
        sys.stdout.flush()
    except OSError as e:  # the input was read fine, so this is not a read error
        print(f"Error: could not write {what} to standard output: {e.strerror or e}", file=sys.stderr)
        _silence_stdout()
        sys.exit(1)


class _VersionAction(argparse.Action):
    """``--version``, written through ``_write_stdout`` so a closed pipe gives the same one-line error."""

    def __init__(self, option_strings: Sequence[str], dest: str = argparse.SUPPRESS,
                 default: str = argparse.SUPPRESS, help: Optional[str] = None) -> None:  # pylint: disable=redefined-builtin
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace, values: Any,
                 option_string: Optional[str] = None) -> None:
        _write_stdout(f"pycrefine {_version()}", "the version")
        parser.exit()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pycrefine",
        description="A Python .pyc decompiler that reconstructs readable source code from compiled bytecode."
    )
    parser.add_argument("--version", action=_VersionAction, help="Print the version and exit.")
    parser.add_argument("input", help="The .pyc file to decompile")
    parser.add_argument(
        "-o", "--output",
        help="Optional filename to save the decompiled output to. If not supplied, prints to screen."
    )
    parser.add_argument(
        "--beautification-level",
        choices=['none', 'core', 'aggressive'], default='core',
        help="Level of code beautification to apply. 'none' does no beautification. 'core' collapses else-if to elif. 'aggressive' is a stub for future use."
    )

    parser.add_argument(
        "--strict", action="store_true",
        help="Stop with exit status 3 at the first place the decompiler cannot reconstruct reliably, instead of guessing."
    )

    parser.add_argument(
        "--debug", action="store_true",
        help="Print the full Python traceback when pycrefine fails internally (exit status 4)."
    )

    args = parser.parse_args()

    try:
        decompiler = get_decompiler(args.input, args.beautification_level, strict=args.strict)
        output_text = decompiler.decompile()
        diagnostics = list(getattr(decompiler, "diagnostics", ()))
        if diagnostics and output_text and output_text.strip():
            for d in diagnostics:
                print(f"Warning: {d}", file=sys.stderr)
            output_text = comment_block(diagnostics) + output_text

        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(output_text)
                print(f"Decompiled output saved to {args.output}", file=sys.stderr)
            except OSError as e:
                print(f"Error: Failed to save decompiled output to {args.output}: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            if output_text and output_text.strip():
                _write_stdout(output_text)
            else:
                print(f"Warning: Decompiler returned no source code for {args.input}", file=sys.stderr)
                sys.exit(1)

    except DecompilationError as e:
        print(f"Error: strict mode: {e}", file=sys.stderr)
        sys.exit(EXIT_STRICT_FAILURE)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"Error: cannot read {e.filename or args.input}: {e.strerror or e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # broad: last-resort guard so a decompiler bug never ends in a bare traceback  # pylint: disable=broad-exception-caught
        if args.debug:
            traceback.print_exc()
        else:
            print(f"Internal error: {type(e).__name__}: {e}\n"
                  f"(Run again with --debug for the traceback, and include it when you report this: {ISSUES_URL})",
                  file=sys.stderr)
        sys.exit(EXIT_INTERNAL_ERROR)
