"""Root conftest: ensure the repository root is importable so that both the
``skillops`` package and the ``tests`` package resolve when pytest runs."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
