"""User workspace: one clean, image-only directory for finished images.

The workflow workers write rich *packages* (the deliverable image mixed with
prompts, request JSON, status.json, super-res scratch, per-candidate subdirs)
into a per-user engineering cache the user never sees. When the user configures
a workspace directory, this module MOVES only the final image(s) out of that
package into ``<workspace>/<中文分类>/`` — so the workspace stays image-only with
folders an ordinary user understands (文生图 / 整图海报 / 图片修改 / 去二维码 /
批量印刷).

Discovery is keyed by the worker name that produced the package (the token the
Qt layer passes after ``--worker``). Only ``load/save`` touch QSettings; the
discovery/move logic is pure and unit-tested.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QSettings

_WORKSPACE_KEY = "workspace/root"

# Worker token (after --worker) → human-friendly Chinese category folder.
CATEGORY_BY_WORKER: dict[str, str] = {
    "text-image": "文生图",
    "full-poster": "整图海报",
    "gpt": "图片修改",
    "qr": "去二维码",
    "batch-style": "批量印刷",
    "batch-pil": "批量印刷",
}

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


# -- persistence -------------------------------------------------------------
def load_workspace_root() -> str:
    return str(QSettings().value(_WORKSPACE_KEY, "") or "").strip()


def save_workspace_root(path: str) -> None:
    QSettings().setValue(_WORKSPACE_KEY, path.strip())


def is_active() -> bool:
    """True when a workspace directory is configured (feature opt-in)."""
    return bool(load_workspace_root())


def apply_output_field(output_field: object, note_label: object) -> None:
    """Toggle a page's manual output row for the current workspace state.

    When a workspace is set the per-workflow output directory is auto-managed:
    hide the manual field and show the note. The field's *text* is never mutated
    — the effective output dir is decided at command-build time via
    :func:`effective_output_dir` — so clearing the workspace restores the user's
    own value untouched.
    """
    active = is_active()
    output_field.setVisible(not active)  # type: ignore[attr-defined]
    note_label.setVisible(active)  # type: ignore[attr-defined]


def effective_output_dir(engineering_default: str, field_value: str) -> str:
    """Where a run should write its package.

    With a workspace set, packages go to the hidden engineering default (only
    finished images are later moved into the workspace); otherwise the user's own
    output field value is used unchanged.
    """
    return engineering_default if is_active() else field_value


# -- deliverable discovery (pure) -------------------------------------------
def _is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES


def _images_in(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if _is_image(p))


def discover_deliverables(worker: str, search_root: Path) -> list[Path]:
    """Locate the FINAL image(s) a worker produced under ``search_root``.

    ``search_root`` is the per-run package dir for the package workers
    (text-image / full-poster / gpt) and the flat output dir for the print-prep
    workers (qr / batch). Intermediates (master before print, super-res scratch,
    source previews, review sheets, request/prompt/status files) are excluded.
    """
    root = Path(search_root)
    if not root.is_dir():
        return []

    if worker == "text-image":
        print_ready = _images_in(root / "print_ready")
        if print_ready:
            return print_ready
        # No print-ready pass requested: the composited/raw master is the result.
        return [p for p in _images_in(root) if p.stem in {"master", "poster_master"}]

    if worker == "full-poster":
        found: list[Path] = []
        for candidate in sorted(root.glob("candidate_*")):
            if not candidate.is_dir():
                continue
            print_ready = _images_in(candidate / "print_ready")
            if print_ready:
                found.extend(print_ready)
            else:
                found.extend(
                    p for p in _images_in(candidate) if p.stem == "full_poster_master"
                )
        return found

    if worker == "gpt":
        # Only the generated master; never the source_preview.jpg or the copy of
        # the original source that the worker also drops in the package.
        return [
            p for p in _images_in(root)
            if p.stem in {"gpt_image_edit_master", "gpt_image_master"}
        ]

    if worker == "qr":
        return [p for p in _images_in(root) if p.stem.endswith("_no_qr")]

    if worker in ("batch-style", "batch-pil"):
        # Flat print files at the top level; review/ and _masters/ are subdirs.
        return _images_in(root)

    return []


def normalize_search_root(done_label: str, fallback: Path) -> Path:
    """Resolve the directory to search for deliverables after a run.

    Workers report their result via the progress ``done`` event: package workers
    report the package directory, but the qr worker reports the single output
    *file*. Normalize a file to its parent directory; fall back to ``fallback``
    (the command's output dir) when nothing was reported.
    """
    root = Path(done_label) if done_label else Path(fallback)
    return root.parent if root.is_file() else root


# -- export (move) -----------------------------------------------------------
def _dest_name(worker: str, src: Path) -> str:
    if worker == "gpt":
        pkg = src.parent.name  # e.g. "13131_edit" / "13131_generate"
        base = pkg.rsplit("_", 1)[0] if pkg.rsplit("_", 1)[-1] in {"edit", "generate"} else pkg
        return f"{base}_已修改{src.suffix}"
    return src.name


def _unique_destination(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem = Path(name).stem
    suffix = Path(name).suffix
    index = 2
    while (directory / f"{stem} ({index}){suffix}").exists():
        index += 1
    return directory / f"{stem} ({index}){suffix}"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def export_run(
    worker: str,
    search_root: Path,
    workspace_root: str | Path,
    *,
    min_mtime: float = 0.0,
) -> list[Path]:
    """Move a run's deliverable image(s) into ``<workspace>/<category>/``.

    Returns the destinations actually moved (empty if none). ``min_mtime`` scopes
    the export to the current run — the flat qr/batch output dirs accumulate
    across runs, so only files written at/after the run started are taken. Moves
    are per-file resilient: a failing move leaves that image in the package and
    the rest still export (partial success is reported, never a hard failure). A
    name collision gets a `` (2)`` suffix rather than overwriting.
    """
    category = CATEGORY_BY_WORKER.get(worker)
    if category is None:
        return []
    deliverables = [
        path for path in discover_deliverables(worker, search_root)
        if _mtime(path) >= min_mtime
    ]
    if not deliverables:
        return []
    dest_dir = Path(workspace_root) / category
    dest_dir.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for src in deliverables:
        dest = _unique_destination(dest_dir, _dest_name(worker, src))
        try:
            shutil.move(str(src), str(dest))
        except OSError:
            continue  # leave this one in the package; keep organizing the rest
        moved.append(dest)
    return moved
