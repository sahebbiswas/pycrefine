"""Command-line interface."""
import argparse
import sys
import traceback
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

def main() -> None:
    parser = argparse.ArgumentParser(
        description="A Python .pyc decompiler that reconstructs readable source code from compiled bytecode."
    )
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
                print(output_text)
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
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # broad: last-resort guard so a decompiler bug never ends in a bare traceback  # pylint: disable=broad-exception-caught
        if args.debug:
            traceback.print_exc()
        else:
            print(f"Internal error: {type(e).__name__}: {e}\n"
                  f"(Run again with --debug for the traceback, and include it when you report this: {ISSUES_URL})",
                  file=sys.stderr)
        sys.exit(EXIT_INTERNAL_ERROR)
