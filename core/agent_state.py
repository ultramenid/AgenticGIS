"""Structured agent-state registry that survives context compaction.

Unlike conversation history, this registry is a compact structured summary of
the QGIS workspace state — layer IDs, types, CRS, key fields, processing
history, and analysis results.  It is injected into the system prompt on every
turn so the agent never loses track of what layers exist and what was computed,
regardless of how aggressively history is compacted or elided.
"""

from typing import Any, Dict, List, Optional


class AgentState:
    """Lightweight workspace-state accumulator for the agent."""

    def __init__(self):
        self.layers: Dict[str, Dict[str, Any]] = {}
        self.processing_history: List[Dict[str, Any]] = []
        self.analysis_results: Dict[str, Dict[str, Any]] = {}
        self.errors: List[str] = []

    # ------------------------------------------------------------------ #
    # Layer tracking
    # ------------------------------------------------------------------ #
    def register_layer(self, layer_id: str, name: str, layer_type: str,
                       crs: Optional[str] = None,
                       feature_count: Optional[int] = None,
                       fields: Optional[List[str]] = None,
                       source: Optional[str] = None,
                       is_analysis: bool = False):
        """Record a layer's key properties for the agent."""
        self.layers[layer_id] = {
            "id": layer_id,
            "name": name,
            "type": layer_type,
            "crs": crs,
            "feature_count": feature_count,
            "fields": fields or [],
            "source": source,
            "is_analysis": is_analysis,
        }

    def unregister_layer(self, layer_id: str):
        """Remove a layer from the registry."""
        self.layers.pop(layer_id, None)
        self.analysis_results.pop(layer_id, None)

    def clear_layers(self):
        """Clear all layer state (e.g. when project is cleared)."""
        self.layers.clear()
        self.analysis_results.clear()

    # ------------------------------------------------------------------ #
    # Processing history
    # ------------------------------------------------------------------ #
    def record_processing(self, alg_id: str, params: Dict[str, Any],
                          output_ids: List[str], success: bool,
                          error: Optional[str] = None,
                          elapsed_ms: Optional[int] = None):
        """Log a processing algorithm run so the agent can reference it."""
        entry = {
            "alg_id": alg_id,
            "params": {k: str(v) for k, v in (params or {}).items()},
            "output_ids": output_ids,
            "success": success,
            "error": error,
            "elapsed_ms": elapsed_ms,
        }
        # Keep only the most recent 20 entries to avoid bloat.
        self.processing_history.append(entry)
        self.processing_history = self.processing_history[-20:]
        if not success and error:
            self._add_error(error)

    # ------------------------------------------------------------------ #
    # Analysis results
    # ------------------------------------------------------------------ #
    def record_analysis(self, layer_id: str, analysis_type: str,
                        field_stats: Optional[Dict[str, Any]] = None,
                        top_values: Optional[Dict[str, Any]] = None,
                        missing_values: Optional[Dict[str, Any]] = None,
                        sample: Optional[List[Any]] = None,
                        scanned_features: Optional[int] = None,
                        truncated: bool = False):
        """Cache key findings from analyze_layer so the agent doesn't re-derive them."""
        existing = self.analysis_results.setdefault(layer_id, {})
        existing["layer_id"] = layer_id
        existing["analysis_type"] = analysis_type
        existing["scanned_features"] = scanned_features
        existing["truncated"] = truncated
        if field_stats:
            existing["field_stats"] = field_stats
        if top_values:
            existing["top_values"] = top_values
        if missing_values:
            existing["missing_values"] = missing_values
        if sample:
            existing["sample"] = sample

    # ------------------------------------------------------------------ #
    # Error tracking
    # ------------------------------------------------------------------ #
    def _add_error(self, message: str):
        """Track recent errors so the agent can avoid repeating them."""
        self.errors.append(message)
        self.errors = self.errors[-10:]

    # ------------------------------------------------------------------ #
    # Summary generation — injected into system prompt
    # ------------------------------------------------------------------ #
    def to_system_prompt_section(self, max_chars: int = 4000) -> str:
        """Return a compact summary suitable for appending to the system prompt.

        The output is plain text (not JSON) so it survives any prompt-format
        conversion and is easy for the model to consume.
        """
        lines = []
        lines.append("## Workspace State")

        if self.layers:
            lines.append(f"### Layers ({len(self.layers)})")
            for layer_id, info in sorted(self.layers.items(), key=lambda kv: kv[1]["name"]):
                parts = [f"- {info['name']} (id={layer_id}, type={info['type']})"]
                if info.get("crs"):
                    parts.append(f"crs={info['crs']}")
                if info.get("feature_count") is not None:
                    parts.append(f"features={info['feature_count']}")
                if info.get("fields"):
                    fields_str = ", ".join(info["fields"][:8])
                    if len(info["fields"]) > 8:
                        fields_str += "..."
                    parts.append(f"fields=[{fields_str}]")
                if info.get("is_analysis"):
                    parts.append("is_analysis=true")
                lines.append(" ".join(parts))
        else:
            lines.append("### Layers: none loaded")

        if self.analysis_results:
            lines.append("### Cached Analyses")
            for layer_id, info in self.analysis_results.items():
                layer_name = self.layers.get(layer_id, {}).get("name", layer_id)
                lines.append(
                    f"- {layer_name}: {info.get('analysis_type')} "
                    f"(scanned={info.get('scanned_features')}, "
                    f"truncated={info.get('truncated')})"
                )
                if info.get("field_stats"):
                    for field, stats in list(info["field_stats"].items())[:3]:
                        lines.append(f"  {field}: {stats}")

        if self.processing_history:
            lines.append("### Recent Processing")
            for entry in self.processing_history[-5:]:
                status = "OK" if entry["success"] else "FAIL"
                lines.append(
                    f"- {entry['alg_id']} [{status}] outputs={entry['output_ids']}"
                )
                if entry.get("error"):
                    lines.append(f"  error: {entry['error'][:120]}")

        if self.errors:
            lines.append("### Recent Errors")
            for err in self.errors[-3:]:
                lines.append(f"- {err[:150]}")

        raw = "\n".join(lines)
        if len(raw) > max_chars:
            raw = raw[:max_chars - 3] + "..."
        return raw
