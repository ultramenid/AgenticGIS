"""Mark first-party diagnostic strings without importing Qt in the backends."""


def QT_TRANSLATE_NOOP(context, source):
    """Keep an extractable Qt message for translation by the GUI."""
    return context, source


class BackendFeedback(str):
    """English diagnostic with parameters available to the presentation layer.

    Since this is a string, existing comparisons, logging and JSON encoding
    retain the English message. Plain strings from providers carry no marker.
    """

    def __new__(cls, message, **values):
        context, source = message
        feedback = super().__new__(cls, source.format(**values))
        feedback.context = context
        feedback.source = source
        feedback.values = values
        return feedback

    def __getnewargs_ex__(self):
        return ((self.context, self.source),), self.values


def exception_feedback(exc, *, include_type=False):
    """Retain a trusted diagnostic carried by an exception."""
    message = exc.args[0] if len(exc.args) == 1 else None
    if not isinstance(message, BackendFeedback):
        message = str(exc)
    if not include_type:
        return message
    if isinstance(message, BackendFeedback):
        return BackendFeedback(
            QT_TRANSLATE_NOOP("BackendFeedback", "{type}: {detail}"),
            type=type(exc).__name__,
            detail=message,
        )
    return f"{type(exc).__name__}: {message}"
