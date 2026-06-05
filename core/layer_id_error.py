"""Actionable 'no layer with id' error builder.

Pure-Python helper (no QGIS imports) so the format is unit-testable in
isolation. ``QgisToolkit._layer_id_error`` wraps this with a live project
lookup; the module-level ``_require_vector_layer`` helper calls it
directly.
"""


_MAX_AVAILABLE_IDS_IN_MESSAGE = 10


def build_layer_id_error(layer_id, known_layer_ids):
    """Return a structured error for a missing layer id.

    The error string embeds the currently available layer ids (capped) so
    the agent can recover in a single tool call by picking a valid id,
    without needing a follow-up ``list_layers`` round-trip. The full list
    is also returned under ``available_layer_ids`` for programmatic access.

    Args:
        layer_id: the missing layer id (string).
        known_layer_ids: iterable of currently loaded layer ids.

    Returns:
        dict with keys ``ok``, ``error``, ``missing_layer_id``,
        ``available_layer_ids``.
    """
    known = sorted(known_layer_ids or [])
    shown = known[:_MAX_AVAILABLE_IDS_IN_MESSAGE]
    more = len(known) - len(shown)

    if known:
        ids_text = ", ".join(repr(i) for i in shown)
        if more > 0:
            ids_text += f", … (+{more} more)"
        available_text = f" Currently loaded layer ids: [{ids_text}]."
    else:
        available_text = " No layers are currently loaded."

    return {
        "ok": False,
        "error": (
            f"No layer with id {layer_id!r}.{available_text} "
            "Call list_layers or get_project_state to refresh."
        ),
        "missing_layer_id": layer_id,
        "available_layer_ids": known,
    }
