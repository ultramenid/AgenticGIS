"""Presentation boundary for diagnostics from backends and CLI adapters."""

from qgis.PyQt.QtCore import QCoreApplication

from ..backends.feedback import BackendFeedback


def display_backend_feedback(value):
    if isinstance(value, BackendFeedback):
        # The originating QT_TRANSLATE_NOOP call supplies literal extraction.
        template = QCoreApplication.translate(value.context, value.source)
        return template.format(**{
            name: display_backend_feedback(item) for name, item in value.values.items()
        })
    return value
