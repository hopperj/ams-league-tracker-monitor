"""Open the desktop UI by default; retain explicit portable CLI commands."""
import sys


def main():
    if len(sys.argv) == 1:
        from .gui import main as launch
    else:
        from .__main__ import main as launch
    return launch()
