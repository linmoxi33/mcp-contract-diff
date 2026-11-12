"""Behaviour tests for mcp-contract-diff (stdlib unittest, no network)."""

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mcpdiff  # noqa: E402

CLI = os.path.join(ROOT, "bin", "mcp-contract-diff")


def tool(name, props=None, required=None, description="d", extra=None):
    schema = {"type": "object", "properties": props or {}}
    if required:
        schema["required"] = required
    if extra:
        schema.update(extra)
    return {"name": name, "description": description, "inputSchema": schema}


def run_cli(old, new, *flags):
    proc = subprocess.run([sys.executable, CLI, old, new, *flags],
                          capture_output=True, text=True, timeout=60)
    return proc


def write(tmpdir, name, payload):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


class LoadingTests(unittest.TestCase):
    def test_accepts_jsonrpc_envelope(self):
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [tool("a")]}}
        self.assertIn("a", mcpdiff.load_tools(payload))

    def test_accepts_bare_list(self):
        self.assertIn("a", mcpdiff.load_tools([tool("a")]))

    def test_rejects_duplicate_names(self):
        with self.assertRaises(mcpdiff.InputError):
            mcpdiff.load_tools([tool("a"), tool("a")])

    def test_rejects_missing_name(self):
        with self.assertRaises(mcpdiff.InputError):
            mcpdiff.load_tools([{"description": "x", "inputSchema": {}}])


class SeverityTests(unittest.TestCase):
    def diff(self, old_tools, new_tools):
        return mcpdiff.diff(mcpdiff.load_tools(old_tools), mcpdiff.load_tools(new_tools))

    def kinds(self, changes):
        return [c["kind"] for c in changes]

    def test_removed_tool_is_breaking(self):
        changes = self.diff([tool("a")], [])
        self.assertEqual(self.kinds(changes), ["TOOL_REMOVED"])
        self.assertEqual(changes[0]["severity"], mcpdiff.BREAKING)

    def test_added_optional_param_is_compatible(self):
        changes = self.diff([tool("a")], [tool("a", {"x": {"type": "string"}})])
        self.assertEqual(changes[0]["kind"], "PARAM_ADDED")
        self.assertEqual(changes[0]["severity"], mcpdiff.COMPATIBLE)

    def test_added_required_param_is_breaking_twice_named(self):
        changes = self.diff([tool("a")], [tool("a", {"x": {"type": "string"}}, ["x"])])
        kinds = set(self.kinds(changes))
        self.assertIn("REQUIRED_PARAM_ADDED", kinds)
        self.assertIn("PARAM_ADDED", kinds)
        self.assertTrue(all(c["severity"] == mcpdiff.BREAKING for c in changes))

    def test_required_relaxed_is_compatible(self):
        changes = self.diff([tool("a", {"x": {"type": "string"}}, ["x"])],
                            [tool("a", {"x": {"type": "string"}})])
        self.assertEqual(changes[0]["kind"], "REQUIRED_RELAXED")
        self.assertEqual(changes[0]["severity"], mcpdiff.COMPATIBLE)

    def test_enum_narrowed_is_breaking_and_names_the_value(self):
        old = [tool("a", {"m": {"type": "string", "enum": ["merge", "replace"]}})]
        new = [tool("a", {"m": {"type": "string", "enum": ["merge"]}})]
        changes = self.diff(old, new)
        self.assertEqual(changes[0]["kind"], "ENUM_NARROWED")
        self.assertIn("replace", changes[0]["detail"])
        self.assertTrue(changes[0]["path"].endswith("properties.m.enum"))

    def test_enum_widened_is_compatible(self):
        old = [tool("a", {"m": {"type": "string", "enum": ["merge"]}})]
        new = [tool("a", {"m": {"type": "string", "enum": ["merge", "append"]}})]
        changes = self.diff(old, new)
        self.assertEqual(changes[0]["kind"], "ENUM_WIDENED")
        self.assertEqual(changes[0]["severity"], mcpdiff.COMPATIBLE)

    def test_free_form_string_restricted_to_enum_is_breaking(self):
        old = [tool("a", {"m": {"type": "string"}})]
        new = [tool("a", {"m": {"type": "string", "enum": ["merge"]}})]
        self.assertEqual(self.diff(old, new)[0]["kind"], "ENUM_INTRODUCED")

    def test_type_widened_is_compatible_and_narrowed_is_breaking(self):
        wide = self.diff([tool("a", {"x": {"type": "string"}})],
                         [tool("a", {"x": {"type": ["string", "array"]}})])
        self.assertEqual(wide[0]["kind"], "TYPE_WIDENED")
        narrow = self.diff([tool("a", {"x": {"type": ["string", "array"]}})],
                           [tool("a", {"x": {"type": "string"}})])
        self.assertEqual(narrow[0]["kind"], "TYPE_NARROWED")
        self.assertEqual(narrow[0]["severity"], mcpdiff.BREAKING)
        self.assertIn("array", narrow[0]["detail"])

    def test_additional_properties_forbidden_is_breaking(self):
        old = [tool("a", {"x": {"type": "string"}})]
        new = [tool("a", {"x": {"type": "string"}}, extra={"additionalProperties": False})]

        changes = self.diff(old, new)
        self.assertEqual(changes[0]["kind"], "ADDITIONAL_PROPERTIES_FORBIDDEN")

    def test_description_only_change_is_info(self):
        changes = self.diff([tool("a", description="old")], [tool("a", description="new")])
        self.assertEqual(changes[0]["kind"], "DESCRIPTION_CHANGED")
        self.assertEqual(changes[0]["severity"], mcpdiff.INFO)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(ROOT, "tests", "_tmp")
        os.makedirs(self.tmp, exist_ok=True)

    def tearDown(self):
        for entry in os.listdir(self.tmp):
            os.remove(os.path.join(self.tmp, entry))
        os.rmdir(self.tmp)

    def test_example_pair_exits_1_with_valid_json(self):
        proc = run_cli(os.path.join(ROOT, "examples/in/home-assistant-7.2.tools.json"),
                       os.path.join(ROOT, "examples/in/home-assistant-7.3.tools.json"),
                       "--json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        report = json.loads(proc.stdout)  # must be parseable by a plain json.load
        self.assertEqual(report["verdict"], "breaking")
        self.assertEqual(report["counts"]["breaking"], 3)
        self.assertEqual(report["old"]["tool_count"], 3)

    def test_expected_output_matches_committed_example(self):
        proc = run_cli(os.path.join(ROOT, "examples/in/home-assistant-7.2.tools.json"),
                       os.path.join(ROOT, "examples/in/home-assistant-7.3.tools.json"),
                       "--json")
        with open(os.path.join(ROOT, "examples/out/home-assistant-7.3.diff.json"), encoding="utf-8") as fh:
            expected = json.load(fh)
        self.assertEqual(json.loads(proc.stdout), expected)

    def test_identical_snapshots_exit_0(self):
        a = write(self.tmp, "a.json", {"tools": [tool("a", {"x": {"type": "string"}})]})
        b = write(self.tmp, "b.json", {"tools": [tool("a", {"x": {"type": "string"}})]})
        proc = run_cli(a, b, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["verdict"], "identical")

    def test_compatible_only_exits_0_but_fail_on_compatible_exits_1(self):
        a = write(self.tmp, "a.json", {"tools": [tool("a")]})
        b = write(self.tmp, "b.json", {"tools": [tool("a", {"x": {"type": "string"}})]})
        self.assertEqual(run_cli(a, b).returncode, 0)
        self.assertEqual(run_cli(a, b, "--fail-on", "compatible").returncode, 1)
        self.assertEqual(run_cli(a, b, "--fail-on", "info").returncode, 1)
        self.assertEqual(run_cli(a, b, "--exit-zero").returncode, 0)

    def test_malformed_json_exits_2(self):
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        proc = run_cli(bad, bad)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not valid JSON", proc.stderr)

    def test_missing_file_exits_2(self):
        proc = run_cli(os.path.join(self.tmp, "nope.json"), os.path.join(self.tmp, "nope.json"))
        self.assertEqual(proc.returncode, 2)

    def test_duplicate_tool_names_exit_2_with_json_error_object(self):
        dup = write(self.tmp, "dup.json", {"tools": [tool("a"), tool("a")]})
        proc = run_cli(dup, dup, "--json")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("error", json.loads(proc.stdout))

    def test_missing_argument_exits_2(self):
        proc = subprocess.run([sys.executable, CLI], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
