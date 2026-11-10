"""Keep immutable source in diagnostics; scope native detail inference to its beat."""
import copy


def detail_request(request):
    result = copy.deepcopy(request)
    payload = result["payload"]
    beat_id = payload["outline"]["beat_id"]
    # Planning has already approved the global timeline. All current-beat speech,
    # actor profiles, global constraints/directions and dependency Scenes remain.
    # Other beats' source and requirements cannot be authored into this Scene.
    for key in ("scenario", "enhanced"):
        payload[key]["beats"] = [beat for beat in payload[key]["beats"] if beat["id"] == beat_id]
    return result


def compact_schema(value):
    """A readable field/type contract alongside the authoritative native grammar."""
    def shape(node):
        if "$ref" in node:
            return node["$ref"].rsplit("/", 1)[-1]
        if "const" in node:
            return dict(const=node["const"])
        if "enum" in node:
            return node["enum"]
        if "anyOf" in node or "oneOf" in node:
            return dict(one_of=[shape(item) for item in node.get("anyOf", node.get("oneOf"))])
        if "properties" in node:
            return {key: shape(item) for key, item in node["properties"].items()}
        if node.get("type") == "array":
            return [shape(node["items"])]
        return node.get("type", "value")
    return dict(root=shape(value), types={key: shape(item) for key, item in value["$defs"].items()})
