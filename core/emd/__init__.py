"""VC scene grammar plus structural helpers for image observation."""
from .parser import ID, parse, time_ms, escape, unescape
from .renderer import render_scene
from .ast import SceneSetting
from .scene_fragment import render_scene_emd_fragment
