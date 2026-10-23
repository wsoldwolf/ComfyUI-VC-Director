"""Load the source checkout as its single project package for local tools."""
import importlib.util
import sys
from pathlib import Path


def load_project():
    root = Path(__file__).resolve().parents[1]
    name = "vc_director"
    existing = sys.modules.get(name)
    if existing is not None:
        if Path(existing.__file__).resolve() != root / "__init__.py":
            raise ImportError("A different vc_director package is already loaded")
        return existing
    spec = importlib.util.spec_from_file_location(name, root / "__init__.py", submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module
