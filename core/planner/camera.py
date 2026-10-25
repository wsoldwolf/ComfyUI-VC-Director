"""Camera coverage required for newly planned scenes, independent of legacy EMD."""
from ..errors import require


def validate_camera_plan(scene):
    for shot in scene["shots"]:
        path = f"{scene['id']}/shots/{shot['id']}/camera"
        start, end = shot["start_ms"], shot["end_ms"]
        cameras = sorted((event for event in shot["events"] if event["kind"] == "camera"),
                         key=lambda event: (event["start_ms"], event["end_ms"], event["id"]))
        require(bool(cameras), "CAMERA_MISSING", path,
                f"Add explicit CAMERA events covering this Shot from {start} to {end}ms. "
                "A static camera must also be explicit; preserve ACTION, dialogue and audio permissions.")
        endpoint, previous = start, None
        for camera in cameras:
            event_start, event_end = camera["start_ms"], camera["end_ms"]
            require(start <= event_start < event_end <= end, "CAMERA_TIME", path,
                    f"CAMERA {camera['id']} must have positive length inside {start}..{end}ms; "
                    f"received {event_start}..{event_end}ms.")
            require(event_start >= endpoint, "CAMERA_OVERLAP", path,
                    f"CAMERA {camera['id']} starts at {event_start}ms before {previous or 'Shot start'} "
                    f"ends at {endpoint}ms. Split camera coverage into non-overlapping intervals.")
            require(event_start == endpoint, "CAMERA_COVERAGE", path,
                    f"Missing CAMERA coverage at {endpoint}..{event_start}ms. "
                    f"Camera intervals must cover this Shot continuously from {start} to {end}ms.")
            endpoint, previous = event_end, camera["id"]
        require(endpoint == end, "CAMERA_COVERAGE", path,
                f"Missing CAMERA coverage at {endpoint}..{end}ms. Extend or add a CAMERA event "
                "without changing the approved Shot duration, ACTION or dialogue.")
    return scene
