"""Thin Python wrapper that delegates to the bash arv script."""

import os
import sys


def main() -> None:
    script = os.path.join(os.path.dirname(__file__), "bin", "arv")
    os.execvp("bash", ["bash", script, *sys.argv[1:]])
