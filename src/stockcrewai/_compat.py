"""Small runtime compatibility shims for third-party package boundaries."""

from __future__ import annotations

import sys
import warnings
from typing import Any


def _is_crewai_warning_wrapper(warn: object) -> bool:
    module_name = getattr(warn, "__module__", "")
    return isinstance(module_name, str) and module_name.startswith("crewai")


def _supports_skip_file_prefixes(warn: Any) -> bool:
    """Probe support without emitting a warning to the test or application."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            warn(
                "StockCrewAI warning-wrapper compatibility probe",
                UserWarning,
                skip_file_prefixes=(),
            )
        except TypeError as error:
            return "skip_file_prefixes" not in str(error)
    return True


def _patch_crewai_warning_wrapper() -> None:
    current_warn = warnings.warn
    if not _is_crewai_warning_wrapper(current_warn):
        return
    if _supports_skip_file_prefixes(current_warn):
        return

    def compatible_warn(
        message: Any,
        category: Any = None,
        stacklevel: int = 1,
        source: Any = None,
        *,
        skip_file_prefixes: tuple[str, ...] = (),
    ) -> Any:
        del skip_file_prefixes
        return current_warn(message, category, stacklevel + 1, source)

    warnings.warn = compatible_warn  # type: ignore[assignment]


def install_crewai_warning_compatibility() -> None:
    """Make CrewAI 1.15.11 compatible with Python 3.12+ warning callers.

    The shim is deliberately behavior-gated. It only wraps a warning function
    owned by CrewAI after that function rejects ``skip_file_prefixes``. Once a
    CrewAI release accepts the keyword, this function leaves it untouched.
    """
    if sys.version_info < (3, 12):
        return

    _patch_crewai_warning_wrapper()
