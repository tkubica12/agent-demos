from pathlib import Path
from typing import BinaryIO, Iterator, Protocol
from uuid import UUID


class FileStorage(Protocol):
    def begin(self, file_id: str) -> BinaryIO: ...
    def complete(self, file_id: str) -> None: ...
    def discard(self, file_id: str) -> None: ...
    def exists(self, file_id: str) -> bool: ...
    def read_chunks(self, file_id: str) -> Iterator[bytes]: ...


class LocalFileStorage:
    """Opaque server IDs, never client filenames, determine storage paths."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, file_id: str):
        if str(UUID(file_id)) != file_id:
            raise ValueError("Invalid storage ID")
        return self.root / file_id

    def begin(self, file_id: str):
        return self.path(file_id).with_suffix(".partial").open("xb")

    def complete(self, file_id: str):
        self.path(file_id).with_suffix(".partial").replace(self.path(file_id))

    def discard(self, file_id: str):
        self.path(file_id).with_suffix(".partial").unlink(missing_ok=True)
        self.path(file_id).unlink(missing_ok=True)

    def exists(self, file_id: str):
        return self.path(file_id).is_file()

    def read_chunks(self, file_id: str):
        with self.path(file_id).open("rb") as source:
            while chunk := source.read(256 * 1024):
                yield chunk
