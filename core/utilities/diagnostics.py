"""Atomic, readable diagnostics shared by planning and translation."""
import os
import tempfile

from . import pretty_json


def save_diagnostic(path, diagnostic, logger):
    if path is None:
        return None
    temporary_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".diagnostic-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(pretty_json(diagnostic))
        os.replace(temporary_name, path)
        return path
    except OSError:
        logger.exception("Could not save diagnostic: %s", path)
        return None
    finally:
        if temporary_name is not None and os.path.exists(temporary_name):
            os.unlink(temporary_name)
