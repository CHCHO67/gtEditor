"""Ground-truth table editor core package.

Worker-1 owns the model and Docling IO primitives. GUI, commands, text
assignment, and validation slices can build on these stable contracts.
"""

from .io_docling import (
    TablePair,
    discover_pairs,
    document_to_project_state,
    export_docling,
    load_document,
    load_project_state,
    save_docling_json,
    save_project_state,
    validate_docling_record,
)
from .models import (
    AxisName,
    GridAxis,
    GridWarning,
    Rect,
    TableCell,
    TableDocument,
    TextSpan,
    detect_crossing_warnings,
)

__all__ = [
    "AxisName",
    "GridAxis",
    "GridWarning",
    "Rect",
    "TableCell",
    "TableDocument",
    "TextSpan",
    "TablePair",
    "detect_crossing_warnings",
    "discover_pairs",
    "document_to_project_state",
    "export_docling",
    "load_document",
    "load_project_state",
    "save_docling_json",
    "save_project_state",
    "validate_docling_record",
]
