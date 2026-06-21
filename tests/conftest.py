import os
import sys

# Make the src/ package importable without an install step.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

REPO_ROOT = _ROOT
GUARD = os.path.join(_ROOT, "scripts", "check_model_artifacts.py")
GITIGNORE = os.path.join(_ROOT, ".gitignore")
