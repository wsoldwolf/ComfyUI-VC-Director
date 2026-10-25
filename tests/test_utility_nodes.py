import base64
import math
import unittest
from unittest.mock import patch

from tools._bootstrap import load_project
load_project()
from vc_director.nodes.node_seed32 import VCDirectorSeed32
from vc_director.nodes.node_load_text_file import VCDirectorLoadTextFile


class UtilityNodeTests(unittest.TestCase):
    def test_seed_modes_and_bounds(self):
        node = VCDirectorSeed32()
        self.assertEqual(node.make_seed("fixed", 42), (42,))
        for invalid in (0, -2, 2147483648, True):
            with self.assertRaises(ValueError):
                node.make_seed("fixed", invalid)
        with patch("vc_director.nodes.node_seed32.node.secrets.randbelow", return_value=2147483646):
            self.assertEqual(node.make_seed("random", 1), (2147483647,))
            self.assertEqual(node.make_seed("fixed", -1), (2147483647,))
        self.assertTrue(math.isnan(node.IS_CHANGED("random", 1)))

    def test_embedded_text_roundtrip_and_cache(self):
        node = VCDirectorLoadTextFile()
        encoded = base64.b64encode("\ufeff日本語\r\nScene\r".encode()).decode()
        self.assertEqual(node.load_text(encoded, "scene.txt", "{}"), ("日本語\nScene\n",))
        self.assertNotEqual(node.IS_CHANGED(encoded, "scene.txt", "{}"),
                            node.IS_CHANGED(encoded, "scene.txt", '{"lastModified":1}'))
        for data, name in (("!", "scene.txt"), (encoded, "../scene.txt"),
                           (encoded, "scene.emd"), (base64.b64encode(b"\x00").decode(), "scene.txt")):
            with self.assertRaises(ValueError):
                node.load_text(data, name, "{}")


if __name__ == "__main__":
    unittest.main()
