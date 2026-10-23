"""VC timing contract and reused H3 reference/camera definitions."""
from .camera import (
    ANIME_EMOTIONAL_FACE_PERFORMANCE_CUT_CAMERA,
    ANIME_EMOTIONAL_FACE_PERFORMANCE_CUT_CAMERA_H3,
    FACE_PERFORMANCE_CUT_ACTION, FACE_PERFORMANCE_CUT_ACTION_H3,
    FACE_PERFORMANCE_CUT_CAMERA, FACE_PERFORMANCE_CUT_CAMERA_H3,
)
from .context_loop import CONTEXT_LOOP_0_7_0, ContextLoopContract
from .timing import CONTRACT, quantize, lengths, event_timing, verify_runtime
