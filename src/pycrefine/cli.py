"""Command-line interface."""
import argparse
import sys
import traceback
from .loader import get_decompiler


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

    args = parser.parse_args()

    try:
        decompiler = get_decompiler(args.input, args.beautification_level)
        output_text = decompiler.decompile()

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

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
