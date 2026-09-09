"""Direct execution entry point for python -m darktext."""

import sys
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
