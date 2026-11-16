"""Reviewed MinerU 4 readiness markers and the empty runtime-lock mount point."""
from pathlib import Path, PurePosixPath
import stat


# Lead evidence E1/E2, 2026-11-15: production MinerU 4.0.7 and its registry
# require exactly these downloader-created markers, outside upstream snapshots.
COMPLETION_MARKERS = (
    'models/MinerU2.5-Pro-2605-1.2B/.mineru_complete',
    'models/MinerU-4_models_torch/Layout/PP-DocLayoutV2/.mineru_complete',
    'models/MinerU-4_models_torch/OCR/paddleocr/.mineru_complete',
)
LOCKS_DIRECTORY = 'models/.locks'
MINERU_ROLES = frozenset({'mineru_small', 'mineru_vlm'})


def refuse_upstream_markers(paths):
    if any('.mineru_complete' in PurePosixPath(path).parts for path in paths):
        raise ValueError('MinerU upstream snapshot contains a reserved .mineru_complete path')


def markers_for_repository(repository):
    markers = tuple(path for path in COMPLETION_MARKERS
                    if PurePosixPath(path).parts[1] == repository)
    if not markers:
        raise ValueError('MinerU repository has no reviewed completion-marker layout')
    return markers


def finish_snapshot(home, repository):
    """Call only after all upstream snapshot files and blob hashes pass."""
    home = Path(home)
    markers = [home / path for path in markers_for_repository(repository)]
    for marker in markers:
        if marker.exists() or marker.is_symlink():
            raise ValueError('MinerU completion marker already exists; never overwrite it')
        if marker.parent.is_symlink() or not marker.parent.is_dir():
            raise ValueError('MinerU completion marker directory is missing from verified snapshot')
    locks = home / LOCKS_DIRECTORY
    if locks.is_symlink() or (locks.exists() and not locks.is_dir()):
        raise ValueError('MinerU .locks must be an empty directory')
    locks.mkdir(parents=True, exist_ok=True)
    if any(locks.iterdir()):
        raise ValueError('MinerU .locks must be an empty directory')
    for marker in markers:
        with marker.open('xb'):
            pass


def validate_home(home):
    home = Path(home)
    markers = set()
    for path in home.rglob('*'):
        if path.is_symlink():
            raise ValueError('MinerU home contains a symbolic link')
        if path.name == '.mineru_complete':
            markers.add(path.relative_to(home).as_posix())
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size:
                raise ValueError('MinerU completion markers must be 0-byte regular files')
    if markers != set(COMPLETION_MARKERS):
        raise ValueError('MinerU completion markers differ: missing or unexpected marker')
    locks = home / LOCKS_DIRECTORY
    if locks.is_symlink() or not locks.is_dir() or any(locks.iterdir()):
        raise ValueError('MinerU .locks must be an empty directory')
