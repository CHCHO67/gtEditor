from gt_editor.commands import AddLineCommand, CommandStack, DeleteLineCommand, MergeCellsCommand, UnmergeCellCommand
from gt_editor.io_docling import discover_pairs, load_document
from gt_editor.models import TableCell
from gt_editor.text_assign import assign_text_to_document


def load_doc():
    pair = discover_pairs("gt_editor_samples/image", "gt_editor_samples/json")[0]
    return assign_text_to_document(load_document(pair.image_path, pair.json_path))


def test_add_delete_line_roundtrip_changes_grid():
    doc = load_doc()
    old_cols = doc.num_cols
    coord = (doc.x_edges[0] + doc.x_edges[1]) / 2.0
    doc2 = AddLineCommand(axis="x", coordinate=coord).apply(doc)
    assert doc2.num_cols == old_cols + 1
    doc3 = DeleteLineCommand(axis="x", line_index=1).apply(doc2)
    assert doc3.num_cols == old_cols


def test_merge_unmerge_cells():
    doc = load_doc()
    # The first selected sample has no cell at col=0 in the first rows, so
    # merge a real complete 2x1 rectangle in col=1.
    selection = [c for c in doc.cells if 0 <= c.row < 2 and 1 <= c.col < 2 and c.end_row <= 2 and c.end_col <= 2]
    doc2 = MergeCellsCommand(selection=selection).apply(doc)
    merged_idx = next(i for i, c in enumerate(doc2.cells) if c.row == 0 and c.col == 1 and c.end_row == 2 and c.end_col == 2)
    doc3 = UnmergeCellCommand(target=merged_idx).apply(doc2)
    assert any(c.row == 0 and c.col == 1 and c.end_row == 1 and c.end_col == 2 for c in doc3.cells)


def test_merge_can_create_cell_from_implicit_empty_grid_slots():
    doc = load_doc()
    assert not any(c.row == 0 and c.col == 0 for c in doc.cells)
    selection = [
        TableCell(row=0, col=0, end_row=1, end_col=1),
        TableCell(row=1, col=0, end_row=2, end_col=1),
    ]

    doc2 = MergeCellsCommand(selection=selection).apply(doc)

    assert any(c.row == 0 and c.col == 0 and c.end_row == 2 and c.end_col == 1 for c in doc2.cells)


def test_command_stack_undo():
    doc = load_doc()
    old_cols = doc.num_cols
    coord = (doc.x_edges[0] + doc.x_edges[1]) / 2.0
    stack = CommandStack(doc)
    doc2 = stack.do(AddLineCommand(axis="x", coordinate=coord))
    assert doc2.num_cols == old_cols + 1
    doc3 = stack.undo()
    assert doc3.num_cols == old_cols
