"""Workspace file operations — the Deep-Agents-compatible surface."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from forge.models import (
    FileEditRequest,
    FileListEntry,
    FileReadResult,
    FileWriteRequest,
)
from forge.server.deps import ForgeState, get_state
from forge.services.files_service import UploadItem

router = APIRouter(prefix="/workspaces/{workspace_id}/files", tags=["files"])


class GrepMatchDTO(BaseModel):
    path: str
    line: int
    text: str


class GrepResult(BaseModel):
    matches: list[GrepMatchDTO]


class GlobResult(BaseModel):
    paths: list[str]


class UploadRequest(BaseModel):
    items: list[UploadItem] = Field(default_factory=list)


class UploadResult(BaseModel):
    written: list[str]


class DownloadRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)


class DownloadResult(BaseModel):
    items: list[UploadItem]


@router.get("", response_model=list[FileListEntry])
async def ls(
    workspace_id: str,
    path: str = Query(default="."),
    state: ForgeState = Depends(get_state),
) -> list[FileListEntry]:
    return state.files.ls(workspace_id, path)


@router.get("/read", response_model=FileReadResult)
async def read_file(
    workspace_id: str,
    path: str = Query(...),
    offset: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1),
    state: ForgeState = Depends(get_state),
) -> FileReadResult:
    content, total, truncated = state.files.read(
        workspace_id, path, offset=offset, limit=limit if limit is not None else 2000
    )
    return FileReadResult(
        path=path, content=content, offset=offset, limit=limit,
        total_lines=total, truncated=truncated,
    )


@router.put("/write", status_code=204)
async def write_file(
    workspace_id: str,
    req: FileWriteRequest,
    state: ForgeState = Depends(get_state),
) -> None:
    state.files.write(workspace_id, req.path, req.content)


@router.post("/edit", response_model=dict[str, int])
async def edit_file(
    workspace_id: str,
    req: FileEditRequest,
    state: ForgeState = Depends(get_state),
) -> dict[str, int]:
    n = state.files.edit(
        workspace_id, req.path, req.old_string, req.new_string,
        replace_all=req.replace_all,
    )
    return {"replacements": n}


@router.get("/glob", response_model=GlobResult)
async def glob_files(
    workspace_id: str,
    pattern: str = Query(...),
    state: ForgeState = Depends(get_state),
) -> GlobResult:
    return GlobResult(paths=state.files.glob(workspace_id, pattern))


@router.get("/grep", response_model=GrepResult)
async def grep_files(
    workspace_id: str,
    pattern: str = Query(...),
    path_glob: str | None = Query(default=None),
    state: ForgeState = Depends(get_state),
) -> GrepResult:
    hits = state.files.grep(workspace_id, pattern, path_glob=path_glob)
    return GrepResult(
        matches=[GrepMatchDTO(path=h.path, line=h.line, text=h.text) for h in hits]
    )


@router.delete("", status_code=204)
async def delete_file(
    workspace_id: str,
    path: str = Query(...),
    state: ForgeState = Depends(get_state),
) -> None:
    state.files.delete(workspace_id, path)


@router.post("/upload", response_model=UploadResult)
async def upload(
    workspace_id: str,
    req: UploadRequest,
    state: ForgeState = Depends(get_state),
) -> UploadResult:
    written = state.files.upload_files(workspace_id, req.items)
    return UploadResult(written=written)


@router.post("/download", response_model=DownloadResult)
async def download(
    workspace_id: str,
    req: DownloadRequest,
    state: ForgeState = Depends(get_state),
) -> DownloadResult:
    items = state.files.download_files(workspace_id, req.paths)
    return DownloadResult(items=items)


# Ergonomic single-file download for LangChain's "upload_files" symmetry
# (frequently one file at a time).
@router.post("/download-one", response_model=UploadItem)
async def download_one(
    workspace_id: str,
    body: dict[str, Any] = Body(...),
    state: ForgeState = Depends(get_state),
) -> UploadItem:
    path = body.get("path")
    if not isinstance(path, str):
        raise HTTPException(status_code=422, detail="path is required")
    result = state.files.download_files(workspace_id, [path])
    return result[0]
