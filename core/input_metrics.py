"""Pure math for the chat input field's auto-grow behavior.

QGIS-free so it is unit-testable everywhere; gui/chat_dock.py feeds it the
live QTextDocument/font metrics and applies the returned heights.
"""


def input_box_metrics(doc_height, line_height, *, min_h, max_h,
                      frame_min_h, frame_max_h, frame_pad=10):
    """Return ``(input_h, frame_h, top_inset)`` for the chat input field.

    ``doc_height`` is the QTextDocument's layouted pixel height and
    ``line_height`` the font's line spacing. The top inset vertically
    centers the *content block* inside the field — never a single line:
    centering by one line's height makes the inset grow with the field,
    clipping every line past the first.
    """
    doc_h = int(doc_height)
    line_h = int(line_height)
    input_h = max(min_h, min(max_h, doc_h + 2))
    frame_h = max(frame_min_h, min(frame_max_h, input_h + frame_pad))
    content_h = max(line_h, doc_h)
    top_inset = max(0, (input_h - content_h) // 2 - 1)
    return input_h, frame_h, top_inset
