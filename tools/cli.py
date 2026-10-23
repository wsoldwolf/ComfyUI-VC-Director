import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    from _bootstrap import load_project
    load_project()

from vc_director.core.artifacts.assets import registry
from vc_director.core.compiler import compile_scene_emd
from vc_director.core.emd import parse, render_scene
from vc_director.core.errors import VCError
from vc_director.core.inference import GemmaBackend, GemmaTranslator, runtime_config
from vc_director.core.protocols import StageRunner
from vc_director.core.direction import StoryEnhancer
from vc_director.core.planner import StoryTimelinePlanner
from vc_director.core.artifacts.storage import load, save
from vc_director.core.artifacts.validation import validate_story


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vc-director")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "compile", "plan"):
        cmd = commands.add_parser(command)
        cmd.add_argument("emd", type=Path)
        if command != "validate":
            cmd.add_argument("--assets", type=Path)
            cmd.add_argument("--asset-root", type=Path, default=Path.cwd())
            cmd.add_argument("--output", type=Path, default=Path("output/vc_director"))
            cmd.add_argument("--model", type=Path, required=command == "plan")
            cmd.add_argument("--cache", type=Path)
    cmd = commands.add_parser("preflight")
    cmd.add_argument("project", type=Path)
    cmd.add_argument("--asset-root", type=Path, required=True)
    cmd.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args(argv)
    backend = None
    try:
        if args.command == "preflight":
            result = load(args.project, args.asset_root, args.runtime_root)
            print(f"Validated {len(result.plan['shots'])} scenes")
            return 0
        text = args.emd.read_text(encoding="utf-8")
        if args.command == "validate":
            document = validate_story(parse(text))
            print(document["schema"] + " " + document["project_id"])
            return 0
        entries = json.loads(args.assets.read_text(encoding="utf-8")) if args.assets else []
        shared = registry(entries, args.asset_root)
        details, scenario_text = {}, None
        if args.model:
            backend = GemmaBackend(args.model, runtime_config("planner" if args.command == "plan" else "compiler"))
        if args.command == "plan":
            runner = StageRunner(backend, args.cache)
            scenario = parse(text)
            enhanced = StoryEnhancer(runner).enhance(scenario)
            document = StoryTimelinePlanner(runner).plan(scenario, enhanced)
            scenario_text, text = text, render_scene(document)
            details = dict(planning_trace=runner.trace, enhanced_story=enhanced, planning_model=runner._identity)
            backend.close()
            backend = GemmaBackend(args.model, runtime_config("compiler"))
        result = compile_scene_emd(text, shared["assets"], GemmaTranslator(backend, args.cache / "translation" if args.cache else None) if backend else None)
        details["translation_model"] = backend.identity() if backend else None
        print(save(result, args.output, scenario_text, details))
        return 0
    except (VCError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if backend:
            backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
