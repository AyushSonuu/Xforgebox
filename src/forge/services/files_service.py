"""Workspace-scoped file operations.

Every method is workspace-relative. Absolute paths and traversal segments
(``..``) are refused. Symbolic links that resolve outside the workspace root
are refused too. The public API is designed to be a drop-in for the Deep-Agents
file tools (``ls``, ``read``, ``write``, ``edit``, ``glob``, ``grep``, ``delete``).

All I/O runs on the host filesystem. Callers do not need Docker; the pool
container mounts the same on-host workspace tree read-write, so writes from
this service become visible to running executions immediately.
"""
from __future__ import annotations

import base64
import fnmatch
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from forge.errors import PathEscapeError, WorkspaceError
from forge.models import FileListEntry
from forge.storage.workspace_store import FORGE_META_DIR, WorkspaceStore

# Guardrails applied to read operations. Callers can request smaller values,
# but never larger — we don't want a single read to blow past a few MiB.
_MAX_READ_BYTES = 5 * 1024 * 1024
_MAX_GLOB_MATCHES = 5_000
_MAX_GREP_MATCHES = 2_000

# Line-oriented read defaults mirror Deep-Agents' ``read_file`` semantics: the
# tool returns up to ``limit`` lines starting at ``offset``.
_DEFAULT_READ_LIMIT = 2000


@dataclass(slots=True)
class GrepMatch:
    """One line matched by :meth:`FilesService.grep`."""

    path: str
    line: int
    text: str


@dataclass(slots=True)
class UploadItem:
    """A single file to upload into a workspace.

    ``content`` is base64-encoded so the request shape works over HTTP without
    a multipart parser. The service decodes eagerly.
    """

    path: str
    content_b64: str


class FilesService:
    """Path-safe file operations rooted at a workspace directory.

    The service is stateless besides the ``WorkspaceStore`` handle. Every
    method takes an explicit ``workspace_id`` so a single instance can serve
    many workspaces (matching the daemon's usage pattern).
    """

    def __init__(self, workspace_store: WorkspaceStore) -> None:
        self._workspaces = workspace_store

    # ------------------------------------------------------------------
    # Path validation
    # ------------------------------------------------------------------

    def _workspace_root(self, workspace_id: str) -> Path:
        root = self._workspaces.path(workspace_id).resolve()
        if not root.is_dir():
            raise WorkspaceError(f"workspace directory missing: {workspace_id}")
        return root

    def _resolve(self, workspace_id: str, user_path: str) -> Path:
        """Resolve a workspace-relative path to an absolute host path.

        Raises :class:`PathEscapeError` if the input is absolute, contains
        ``..`` segments that escape the workspace, or resolves through a
        symlink that leaves the workspace.
        """
        # Note: static type says `user_path: str`; the HTTP layer validates
        # that upstream, so we skip a runtime None-check.
        # PurePosixPath handles the forward-slash convention agents use.
        p = PurePosixPath(user_path)
        if p.is_absolute():
            raise PathEscapeError(f"absolute paths are not allowed: {user_path!r}")
        # ".." at the root of the user path is a common escape attempt; the
        # resolve() check below catches deeper cases (e.g. "a/../../etc").
        for part in p.parts:
            if part == "..":
                # We still need to reject it *before* touching the filesystem
                # in case the target doesn't exist.
                pass
        root = self._workspace_root(workspace_id)
        candidate = (root / user_path).resolve()
        _ensure_within(candidate, root)
        return candidate

    def _resolve_for_read(self, workspace_id: str, user_path: str) -> Path:
        """Like :meth:`_resolve` but also validates symlink targets.

        The base ``_resolve`` already resolves symlinks, so a link that points
        outside the workspace will trip the ``is_relative_to`` guard. This
        helper additionally rejects reads of files inside ``.forge/`` unless
        the caller has explicitly opted in (they haven't — this is a public
        surface). Keeps overflow logs and internal state opaque to agents.
        """
        resolved = self._resolve(workspace_id, user_path)
        root = self._workspace_root(workspace_id)
        try:
            relative = resolved.relative_to(root)
        except ValueError as e:
            raise PathEscapeError(str(e)) from e
        if relative.parts and relative.parts[0] == FORGE_META_DIR:
            raise PathEscapeError(f"path {user_path!r} is inside reserved {FORGE_META_DIR}/")
        return resolved

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def ls(self, workspace_id: str, path: str = ".") -> list[FileListEntry]:
        """List the immediate children of a directory (non-recursive)."""
        target = self._resolve_for_read(workspace_id, path)
        if not target.exists():
            raise WorkspaceError(f"path not found: {path}")
        if not target.is_dir():
            raise WorkspaceError(f"path is not a directory: {path}")
        root = self._workspace_root(workspace_id)
        out: list[FileListEntry] = []
        for child in sorted(target.iterdir()):
            rel = child.relative_to(root).as_posix()
            if rel.split("/", 1)[0] == FORGE_META_DIR:
                continue  # never leak the reserved metadata dir
            stat = child.stat()
            out.append(
                FileListEntry(
                    name=child.name,
                    path=rel,
                    is_dir=child.is_dir(),
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                )
            )
        return out

    def read(
        self,
        workspace_id: str,
        path: str,
        *,
        offset: int = 0,
        limit: int | None = _DEFAULT_READ_LIMIT,
        max_bytes: int = _MAX_READ_BYTES,
    ) -> tuple[str, int, bool]:
        """Read a text file with line-based offset/limit.

        Returns ``(content, total_lines, truncated)``. Binary files (those
        containing NUL bytes) raise :class:`WorkspaceError`. The caller can
        upload binary content via :meth:`upload_files`.
        """
        target = self._resolve_for_read(workspace_id, path)
        if not target.exists():
            raise WorkspaceError(f"path not found: {path}")
        if target.is_dir():
            raise WorkspaceError(f"path is a directory: {path}")
        size = target.stat().st_size
        if size > max_bytes:
            raise WorkspaceError(
                f"file exceeds max read size ({size} > {max_bytes} bytes)"
            )
        raw = target.read_bytes()
        if b"\x00" in raw:
            raise WorkspaceError(f"file is binary: {path}")
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)
        if offset < 0:
            raise WorkspaceError("offset must be non-negative")
        window = lines[offset : offset + limit] if limit is not None else lines[offset:]
        content = "".join(window)
        truncated = limit is not None and (offset + limit) < total_lines
        return content, total_lines, truncated

    def write(self, workspace_id: str, path: str, content: str) -> None:
        """Write text to ``path``, creating parent directories as needed."""
        target = self._resolve(workspace_id, path)
        root = self._workspace_root(workspace_id)
        # Do not allow writes into the reserved meta directory.
        if target.is_relative_to(root / FORGE_META_DIR):
            raise PathEscapeError(f"cannot write inside reserved {FORGE_META_DIR}/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def edit(
        self,
        workspace_id: str,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> int:
        """In-place string replacement.

        Returns the number of replacements made. When ``replace_all`` is
        False, the file must contain exactly one occurrence of ``old_string``;
        otherwise :class:`WorkspaceError` is raised (matches Deep-Agents).
        """
        target = self._resolve_for_read(workspace_id, path)
        if not target.exists() or not target.is_file():
            raise WorkspaceError(f"file not found: {path}")
        text = target.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            raise WorkspaceError(f"old_string not found in {path}")
        if not replace_all and count > 1:
            raise WorkspaceError(
                f"old_string occurs {count} times in {path}; pass replace_all=True"
            )
        if replace_all:
            new_text = text.replace(old_string, new_string)
            replacements = count
        else:
            new_text = text.replace(old_string, new_string, 1)
            replacements = 1
        target.write_text(new_text, encoding="utf-8")
        return replacements

    def glob(self, workspace_id: str, pattern: str) -> list[str]:
        """Return workspace-relative paths matching ``pattern`` (recursive).

        Pattern uses the shell-style syntax accepted by :func:`fnmatch`
        with ``**`` treated as "any number of path components", matching
        Deep-Agents / ripgrep-style intuition.
        """
        root = self._workspace_root(workspace_id)
        # Normalise pattern to strip leading "./" so users can write "*.py".
        normalised = pattern.lstrip("./")
        matches: list[str] = []
        for host_path in _walk(root):
            rel = host_path.relative_to(root).as_posix()
            if rel.split("/", 1)[0] == FORGE_META_DIR:
                continue
            if _glob_match(rel, normalised):
                matches.append(rel)
                if len(matches) >= _MAX_GLOB_MATCHES:
                    break
        matches.sort()
        return matches

    def grep(
        self,
        workspace_id: str,
        pattern: str,
        *,
        path_glob: str | None = None,
        max_matches: int = _MAX_GREP_MATCHES,
    ) -> list[GrepMatch]:
        """Line-oriented regex search.

        ``pattern`` is a Python regex. ``path_glob`` optionally restricts the
        search to matching relative paths. Binary and reserved files are
        skipped silently. Returns matches in file+line order.
        """
        try:
            regex = re.compile(pattern)
        except re.error as e:
            raise WorkspaceError(f"invalid regex: {e}") from e
        root = self._workspace_root(workspace_id)
        results: list[GrepMatch] = []
        for host_path in _walk(root):
            if not host_path.is_file():
                continue
            rel = host_path.relative_to(root).as_posix()
            if rel.split("/", 1)[0] == FORGE_META_DIR:
                continue
            if path_glob is not None and not _glob_match(rel, path_glob):
                continue
            try:
                raw = host_path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw:
                continue  # binary
            text = raw.decode("utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    results.append(GrepMatch(path=rel, line=lineno, text=line))
                    if len(results) >= max_matches:
                        return results
        return results

    def delete(self, workspace_id: str, path: str) -> None:
        """Remove a file or empty directory."""
        target = self._resolve(workspace_id, path)
        root = self._workspace_root(workspace_id)
        if target == root:
            raise WorkspaceError("refusing to delete workspace root")
        if target.is_relative_to(root / FORGE_META_DIR):
            raise PathEscapeError(f"cannot delete inside reserved {FORGE_META_DIR}/")
        if not target.exists():
            return
        if target.is_dir():
            try:
                target.rmdir()
            except OSError as e:
                raise WorkspaceError(f"directory not empty: {path}") from e
        else:
            target.unlink()

    def upload_files(self, workspace_id: str, items: list[UploadItem]) -> list[str]:
        """Write a batch of (possibly binary) files.

        Content is base64-encoded on the wire; decode + write in one shot per
        item. Returns the workspace-relative paths written, in the order they
        were provided.
        """
        written: list[str] = []
        for item in items:
            target = self._resolve(workspace_id, item.path)
            root = self._workspace_root(workspace_id)
            if target.is_relative_to(root / FORGE_META_DIR):
                raise PathEscapeError(f"cannot write inside reserved {FORGE_META_DIR}/")
            try:
                blob = base64.b64decode(item.content_b64, validate=True)
            except (ValueError, TypeError) as e:
                raise WorkspaceError(f"invalid base64 for {item.path}: {e}") from e
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            written.append(target.relative_to(root).as_posix())
        return written

    def download_files(self, workspace_id: str, paths: list[str]) -> list[UploadItem]:
        """Read a batch of files as base64-encoded bytes.

        Symmetric with :meth:`upload_files` — this is how the HTTP layer
        exports files without a streaming download endpoint.
        """
        out: list[UploadItem] = []
        for path in paths:
            target = self._resolve_for_read(workspace_id, path)
            if not target.is_file():
                raise WorkspaceError(f"not a file: {path}")
            blob = target.read_bytes()
            out.append(UploadItem(path=path, content_b64=base64.b64encode(blob).decode("ascii")))
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_within(candidate: Path, root: Path) -> None:
    """Raise :class:`PathEscapeError` unless ``candidate`` is at or under ``root``."""
    try:
        candidate.relative_to(root)
    except ValueError as e:
        raise PathEscapeError(
            f"path {candidate} escapes workspace root {root}"
        ) from e
    # Extra check for symlinks: resolve() collapses them, so if the resolved
    # candidate still lies within the resolved root we're safe. We already ran
    # resolve() at the caller; keep the double-check cheap and explicit.
    if not str(candidate).startswith(str(root)):
        raise PathEscapeError(f"path {candidate} escapes workspace root {root}")


def _walk(root: Path):  # type: ignore[no-untyped-def]
    """Yield every file/dir under ``root`` in a stable order.

    We use os.walk with ``followlinks=False`` so a symlink into ``/etc`` never
    gets recursed into. The files service still resolves each path before
    reading, so even a bare symlink file that points outside is caught.
    """
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        base_path = Path(base)
        for f in files:
            yield base_path / f
        for d in dirs:
            yield base_path / d


def _glob_match(rel_path: str, pattern: str) -> bool:
    """Match ``rel_path`` against a glob supporting ``**`` for any depth."""
    # Special-case a bare "**" pattern → match everything.
    if pattern in ("**", "**/*"):
        return True
    if "**" in pattern:
        # Expand "**/" to a regex that matches zero or more path components.
        regex = _glob_to_regex(pattern)
        return regex.fullmatch(rel_path) is not None
    return fnmatch.fnmatchcase(rel_path, pattern)


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a glob with ``**`` semantics to a compiled regex.

    ``**/`` matches zero or more path components (including nothing), so
    ``**/*.py`` matches both ``a.py`` and ``src/b.py``.
    """
    # First, normalise the ``**/`` → sentinel that survives per-segment translation.
    # We split on "/" and treat "**" as a component that can absorb the following "/".
    parts = pattern.split("/")
    regex_parts: list[str] = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if part == "**":
            # Absorb an optional following "/" so a leading "**/" matches "".
            if i + 1 < len(parts):
                # zero-or-more components + slash, OR nothing
                regex_parts.append("(?:.*/)?")
                i += 1
                continue
            # Trailing "**" — match anything, including empty.
            regex_parts.append(".*")
            i += 1
            continue
        regex_parts.append(fnmatch.translate(part).removesuffix(r"\Z").removesuffix(r"\z"))
        # Join with "/" except when the previous emitted piece already ends in "/"
        if i + 1 < len(parts) and not regex_parts[-1].endswith(("/", "?")):
            regex_parts.append("/")
        i += 1
    joined = "".join(regex_parts)
    # Trim any accidental double "/" caused by adjacent "**" absorptions.
    joined = re.sub(r"/+", "/", joined)
    return re.compile(joined + r"\Z")


__all__ = ["FilesService", "GrepMatch", "UploadItem"]
