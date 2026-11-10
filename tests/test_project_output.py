import json
import unittest
from unittest.mock import patch

from test_core import english_document, compile_fixture, temporary_directory
from vc_director.core.artifacts.storage import save
from vc_director.core.artifacts.assets import registry
from vc_director.core.utilities import canonical, digest
from vc_director.nodes import VCDirectorLoad


class ProjectOutputTests(unittest.TestCase):
    def test_load_exposes_saved_project_identity_without_changing_plan_or_existing_sockets(self):
        self.assertEqual(VCDirectorLoad.RETURN_NAMES[:3], ("plan_json", "compiled", "generation_fingerprint"))
        self.assertEqual(VCDirectorLoad.RETURN_NAMES[3], "project_name")
        for project in ("station_demo_0001", "second_project"):
            with self.subTest(project=project), temporary_directory() as directory:
                document = english_document()
                document["project_id"] = project
                compiled = compile_fixture(document)
                revision = save(compiled, directory)
                shared = registry([], directory)
                # This exercises saved revision hashes and replay; no GPU/runtime lookup.
                result = VCDirectorLoad().read(str(revision.parents[1]), shared, None)
                plan_json, loaded, fingerprint, project_name = result["result"]
                self.assertEqual(project_name, project)
                self.assertEqual(result["ui"], dict(project_name=[project]))
                self.assertEqual(json.loads(plan_json), compiled.plan)
                self.assertEqual(loaded.execution, compiled.execution)
                self.assertEqual(fingerprint, digest(canonical(dict(plan=compiled.plan, execution=compiled.execution))))

    def test_directory_alias_does_not_override_authored_project_name(self):
        compiled = compile_fixture()
        with patch("vc_director.nodes.node_plan_load.node.load", return_value=compiled):
            result = VCDirectorLoad().read("X:/user_chosen_folder/alias", {"assets": [], "root": "."}, None)
        self.assertEqual(result["result"][3], compiled.document["project_id"])
        self.assertNotEqual(result["result"][3], "alias")


if __name__ == "__main__":
    unittest.main()
