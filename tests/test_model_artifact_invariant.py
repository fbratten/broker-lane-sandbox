"""INVARIANT-1 tests: model artifacts can never enter git.

Each scenario runs against a throwaway git repo so behavior is deterministic and
independent of this repo's own commit state. The real tracked tree is also audited.
"""
import shutil
import subprocess
import sys
from pathlib import Path

_THIS = Path(__file__).resolve()
REPO_ROOT = _THIS.parent.parent
GUARD = REPO_ROOT / "scripts" / "check_model_artifacts.py"
GITIGNORE = REPO_ROOT / ".gitignore"

VIOLATION_EXIT = 5


def run(args, cwd=None, check=True):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=check)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(GITIGNORE, repo / ".gitignore")
    run(["git", "init", "-q"], cwd=repo)
    run(["git", "config", "user.email", "t@t"], cwd=repo)
    run(["git", "config", "user.name", "t"], cwd=repo)
    run(["git", "config", "commit.gpgsign", "false"], cwd=repo)
    return repo


def _guard(repo: Path, *extra: str):
    return subprocess.run(
        [sys.executable, str(GUARD), "--repo", str(repo), *extra],
        capture_output=True, text=True,
    )


def test_gitignore_ignores_weight_blobs(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "models").mkdir()
    blobs = ["model.gguf", "w.safetensors", "x.bin", "y.onnx", "z.pt",
             "a.pth", "b.ckpt", "c.tflite", "d.mlmodel", "models/foo.txt"]
    for name in blobs:
        (repo / name).write_text("not really weights")
    st = run(["git", "status", "--porcelain"], cwd=repo).stdout
    for name in blobs:
        assert name not in st, f"{name} should be ignored; status={st!r}"
        assert run(["git", "check-ignore", "-q", name], cwd=repo, check=False).returncode == 0


def test_guard_refuses_force_added_weight_blob(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "weights.gguf").write_text("pretend weights")
    run(["git", "add", "-f", "weights.gguf"], cwd=repo)   # force past .gitignore
    cp = _guard(repo, "--staged")
    assert cp.returncode == VIOLATION_EXIT, cp.stderr
    assert "weights.gguf" in cp.stderr
    assert "REFUSED" in cp.stderr


def test_guard_refuses_file_under_cache_dir(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "model-cache").mkdir()
    (repo / "model-cache" / "blob.dat").write_text("x")  # .dat isn't a weight ext...
    run(["git", "add", "-f", "model-cache/blob.dat"], cwd=repo)
    cp = _guard(repo, "--staged")
    assert cp.returncode == VIOLATION_EXIT, cp.stderr        # ...but the directory is forbidden
    assert "model-cache/blob.dat" in cp.stderr


def test_guard_refuses_oversize(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "big.txt").write_text("x" * 4096)
    run(["git", "add", "big.txt"], cwd=repo)
    cp = _guard(repo, "--staged", "--max-mb", "0.00001")     # ~10 byte cap
    assert cp.returncode == VIOLATION_EXIT, cp.stderr
    assert "big.txt" in cp.stderr and "oversize" in cp.stderr.lower()


def test_guard_passes_clean_tree(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "models.example.yaml").write_text("schema_version: 1\n")
    (repo / "README.md").write_text("# ok\n")
    run(["git", "add", "models.example.yaml", "README.md", ".gitignore"], cwd=repo)
    assert _guard(repo, "--staged").returncode == 0
    run(["git", "commit", "-q", "-m", "init"], cwd=repo)
    assert _guard(repo, "--tracked").returncode == 0


def test_real_repo_tracked_tree_is_clean():
    # The actual broker-lane-sandbox tracked tree must contain zero weight blobs.
    cp = subprocess.run(
        [sys.executable, str(GUARD), "--tracked", "--repo", str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    assert cp.returncode == 0, cp.stderr


def test_guard_fails_closed_when_git_unavailable(tmp_path):
    # Run from a NON-repo dir with no --repo: git resolution fails. The guard must
    # fail CLOSED (non-zero), never silently report a clean tree (INVARIANT-1).
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()
    cp = subprocess.run(
        [sys.executable, str(GUARD), "--tracked"],
        cwd=non_repo, capture_output=True, text=True,
    )
    assert cp.returncode != 0                       # NOT a false-clean exit 0
    assert cp.returncode == 2                       # GUARD_ERROR_EXIT
    assert "GUARD ERROR" in cp.stderr
