"""Validation helpers for exported GT JSON."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .docling_validator import validate
from .io_docling import export_docling
from .models import TableDocument


def validate_record(record: dict[str, Any], png_path: str | Path | None = None, check_png: bool = True) -> tuple[bool, list[str]]:
    return validate(record, str(png_path) if png_path is not None else None, check_png=check_png)


def validate_document(doc: TableDocument, *, check_png: bool = True) -> tuple[bool, list[str]]:
    return validate_record(export_docling(doc), doc.image_path, check_png=check_png and doc.image_path is not None)
