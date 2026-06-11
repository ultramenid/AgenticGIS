"""Pure helper to validate Processing parameter names before running.

QGIS Processing silently ignores unknown parameter keys, so a mistyped
name (e.g. GROUP_FIELD instead of GROUP_BY on native:aggregate) silently
falls back to the parameter's default — the algorithm runs for minutes
and produces a wrong result instead of failing fast. This module builds
the error message that lets the agent self-correct immediately.

Kept QGIS-free so it is unit-testable everywhere; callers supply the
valid parameter names from ``algorithm.parameterDefinitions()``.
"""

import difflib


def build_unknown_params_error(alg_id, provided_keys, valid_names):
    """Return an error string for unknown parameter keys, or ``None``.

    Keys starting with ``_`` are internal plumbing (e.g.
    ``_evicted_tool_layer_ids``) and are never flagged. An empty
    ``valid_names`` means the algorithm's definitions could not be read —
    validation must never block in that case.
    """
    valid = [str(n) for n in (valid_names or [])]
    if not valid:
        return None
    valid_set = set(valid)
    unknown = [
        str(k) for k in (provided_keys or [])
        if not str(k).startswith("_") and str(k) not in valid_set
    ]
    if not unknown:
        return None

    upper_to_valid = {n.upper(): n for n in valid}
    parts = []
    for key in unknown:
        suggestion = upper_to_valid.get(key.upper())
        if suggestion is None:
            matches = difflib.get_close_matches(key.upper(), list(upper_to_valid), n=1, cutoff=0.6)
            if matches:
                suggestion = upper_to_valid[matches[0]]
        if suggestion:
            parts.append(f"'{key}' (did you mean '{suggestion}'?)")
        else:
            parts.append(f"'{key}'")
    return (
        f"Unknown parameter(s) for {alg_id}: {', '.join(parts)}. "
        f"Valid parameters: {', '.join(valid)}. "
        "QGIS would silently ignore unknown keys and run with defaults, "
        "so fix the parameter name(s) and retry."
    )
