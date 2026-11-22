"""Lets the acceptance tests import _helpers and pawpal_system."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
