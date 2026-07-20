"""Storage abstraction for durable generated resume exports."""

import os
import tempfile
from pathlib import Path, PurePosixPath

from app.core.config import settings


class ExportStorageError(RuntimeError):
    """A resume export could not be safely stored or retrieved."""

    retryable = True


class LocalExportStorage:
    """Atomic local-disk storage; replaceable by an object-storage adapter."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or settings.RESUME_EXPORT_STORAGE_PATH).resolve()

    def _path(self, key: str) -> Path:
        relative = PurePosixPath(key)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ExportStorageError("Invalid export storage key.")
        candidate = self.root.joinpath(*relative.parts).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ExportStorageError("Export storage key escapes its configured root.")
        return candidate

    def write(self, key: str, content: bytes) -> Path:
        if not content:
            raise ExportStorageError("Cannot store an empty export.")
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", delete=False, dir=target.parent, prefix=".export-"
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            temporary_path.replace(target)
        except Exception as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise ExportStorageError("Could not persist resume export.") from exc
        return target

    def path(self, key: str) -> Path:
        path = self._path(key)
        if not path.is_file():
            raise ExportStorageError("Stored resume export is unavailable.")
        return path

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:
            raise ExportStorageError("Could not delete resume export.") from exc


def get_export_storage() -> LocalExportStorage:
    return LocalExportStorage()
