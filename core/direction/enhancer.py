"""Scenario interpretation and author-owned requirements."""
from ..errors import require
from ..artifacts.schema import validate
from ..artifacts.validation import unique, validate_story
from ..vision.image_facts import apply_image_facts


class StoryEnhancer:
    def __init__(self, runner):
        self.runner = runner

    def enhance(self, scenario, image_facts=()):
        require(scenario.get("schema") == "VCD_SCENARIO_V1", "INPUT_KIND", "/schema",
                "EnhancerにはVCD_SCENARIO_EMD_V1のシナリオが必要です。シーンEMDはsaved_sceneでコンパイルしてください。")
        scenario = apply_image_facts(scenario, image_facts)
        validate_story(scenario)
        actors = {a["id"] for a in scenario["actors"]}
        backgrounds = {b["id"] for b in scenario["backgrounds"]}
        for fact in image_facts:
            validate(fact, "image_fact")
            require(fact["entity_id"] in (actors if fact["entity_kind"] == "actor" else backgrounds), "IMAGE_FACT", fact["entity_id"], "Unknown entity")

        def check(result):
            require(result["project_id"] == scenario["project_id"] and result["scenario_sha256"] == scenario["source_sha256"], "ENHANCED_SOURCE", "/", "Wrong scenario identity")
            require(result["constraints"] == scenario["constraints"], "AUTHOR_CONSTRAINT", "/", "Author constraints changed")
            expected = [b["id"] for b in scenario["beats"]]
            received = [b["id"] for b in result["beats"]]
            require(received == expected, "ENHANCED_BEATS", "/beats",
                    f"Return every authored beat exactly once in this order: {expected}; received: {received}. "
                    "Do not stop after the first beat, omit beats, add beats or reorder them.")
            unique([e for b in result["beats"] for e in b["required_events"]], "required_events")
            for beat, source in zip(result["beats"], scenario["beats"]):
                for event in beat["required_events"]:
                    span = event["source"]
                    require(event["beat_id"] == source["id"] and span["document"] == "scenario" and source["source"]["first_line"] <= span["first_line"] <= span["last_line"] <= source["source"]["last_line"], "ENHANCED_EVENT_SOURCE", event["id"], "Required event must trace to its authored beat")

        return self.runner.run("enhance", scenario["project_id"], dict(scenario=scenario, image_facts=list(image_facts)), check)
