"""Read-only, cursor-paged export adapter for an authorized L5 migration.

This module is intentionally separate from ordinary recall and write tools.
It exposes the same source-of-truth Markdown/evidence set as the existing
backup exporter, without producing a ZIP and without mutating the vault.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from errors import ToolInputError
from ombrebrain.storage.backup_archive import (
    BackupArchiveError,
    READONLY_EXPORT_DEFAULT_PAGE_BYTES,
    build_readonly_text_export_page,
)

from . import _runtime as rt


async def page(
    cursor: Optional[str] = "",
    max_bytes: Optional[int] = READONLY_EXPORT_DEFAULT_PAGE_BYTES,
) -> str:
    """Return one exact page and a cursor for the next page."""

    buckets_dir = str((rt.config or {}).get("buckets_dir") or "").strip()
    if not buckets_dir:
        raise ToolInputError("OB 记忆目录未配置；本次没有读取或修改任何记忆")
    try:
        payload = await asyncio.to_thread(
            build_readonly_text_export_page,
            buckets_dir,
            cursor=str(cursor or ""),
            max_bytes=(
                READONLY_EXPORT_DEFAULT_PAGE_BYTES
                if max_bytes is None
                else max_bytes
            ),
        )
    except BackupArchiveError as exc:
        raise ToolInputError(f"只读导出未完成：{exc}；本次没有修改任何记忆") from exc
    except OSError as exc:
        raise ToolInputError(
            "只读导出未完成：读取记忆文件失败；本次没有修改任何记忆"
        ) from exc
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
