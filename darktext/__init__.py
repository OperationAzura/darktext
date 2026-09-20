"""Darklands text reader and accessibility package."""

__version__ = "2.0.0"

def main():
    """Load the OCR CLI only when requested."""
    from .cli import main as cli_main
    return cli_main()

__all__ = ["main", "__version__"]
