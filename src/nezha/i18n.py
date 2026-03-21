"""Internationalization support via python-i18n.

Usage:
    from nezha.i18n import setup_locale, t

    setup_locale("zh_CN")   # call once at startup
    print(t('executor.info.agent', name='my-agent'))
"""

from pathlib import Path

import i18n as _i18n

_LOCALES_DIR = Path(__file__).parent / "locales"
_initialized = False


def setup_locale(locale: str = "en") -> None:
    """Initialize i18n with the given locale. Call once at startup before any t() usage."""
    global _initialized
    _i18n.set("locale", locale)
    _i18n.set("fallback", "en")
    _i18n.set("file_format", "yaml")
    _i18n.set("filename_format", "{locale}.{format}")
    _i18n.set("error_on_missing_translation", False)
    _i18n.set("error_on_missing_placeholder", False)
    if not _initialized:
        _i18n.load_path.append(str(_LOCALES_DIR))
        _initialized = True


def get_locale() -> str:
    """Return the currently active locale."""
    return _i18n.get("locale")


# Short alias for use throughout the codebase.
# Call setup_locale() before using t() to ensure the correct locale is loaded.
t = _i18n.t


# Auto-initialize with English so t() is safe to call even without explicit setup.
setup_locale("en")
