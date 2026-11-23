"""Resolve the QGIS interface locale and load this plugin's Qt catalog."""

import re
from pathlib import Path

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QLocale, QTranslator


def _normalize_locale(value):
    if not isinstance(value, str):
        return None
    name = value.strip().split(".", 1)[0].replace("-", "_")
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:_[A-Za-z]{4})?(?:_(?:[A-Za-z]{2}|[0-9]{3}))?", name):
        return None
    locale = QLocale(name)
    if locale.language() == QLocale.Language.C:
        return None
    language, *parts = name.split("_")
    if parts and len(parts[-1]) != 4 and locale.name().partition("_")[2] != parts[-1].upper():
        return None
    return "_".join([language.lower(), *(p.title() if len(p) == 4 else p.upper() for p in parts)])


def resolve_qgis_locale(settings=None, system_locale=None):
    """Honor an explicit QGIS language override, then the system locale."""
    settings = QgsSettings() if settings is None else settings
    if settings.value("locale/overrideFlag", False, bool):
        override = _normalize_locale(settings.value("locale/userLocale", ""))
        if override:
            return override
    if system_locale is None:
        system_locale = QLocale.system().name()
    return _normalize_locale(system_locale) or "en"


def load_plugin_translator(locale, directory=None):
    """Load an exact or language-only catalog if one is available."""
    directory = Path(__file__).parent if directory is None else Path(directory)
    locale = _normalize_locale(locale)
    if locale is None:
        return None
    for candidate in dict.fromkeys((locale, locale.partition("_")[0])):
        catalog = directory / f"AgenticGIS_{candidate}.qm"
        if catalog.is_file():
            translator = QTranslator()
            if translator.load(str(catalog), searchDelimiters="\x1f") and not translator.isEmpty():
                return translator
    return None
