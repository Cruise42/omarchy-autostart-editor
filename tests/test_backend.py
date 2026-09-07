#!/usr/bin/env python3

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


BACKEND_PATH = Path(__file__).parents[1] / "backend" / "autostart_editor_backend.py"
SPEC = importlib.util.spec_from_file_location("autostart_editor_backend", BACKEND_PATH)
backend = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(backend)


class MonitorTests(unittest.TestCase):
    def test_internal_panel_is_preferred(self):
        monitors = [
            {"name": "Dock-Output", "internal": False, "focused": True},
            {"name": "BuiltIn-Panel", "internal": True, "focused": False},
        ]
        self.assertEqual(backend.fallback_monitor(monitors), "BuiltIn-Panel")

    def test_focused_external_is_second_choice(self):
        monitors = [
            {"name": "Dock-One", "internal": False, "focused": False},
            {"name": "Dock-Two", "internal": False, "focused": True},
        ]
        self.assertEqual(backend.fallback_monitor(monitors), "Dock-Two")

    def test_connector_names_are_not_hard_coded(self):
        self.assertTrue(backend.is_internal_monitor("LVDS-9"))
        self.assertTrue(backend.is_internal_monitor("DSI-4"))
        self.assertFalse(backend.is_internal_monitor("DisplayPort-7"))

    def test_missing_command_is_reported_without_crashing(self):
        result = backend.run(["a-command-that-does-not-exist-for-this-test"])
        self.assertEqual(result.returncode, 127)


class DesktopDiscoveryTests(unittest.TestCase):
    def test_desktop_entry_uses_startup_window_class(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "example.desktop"
            path.write_text(
                "[Desktop Entry]\nType=Application\nName=Example\n"
                "Exec=example --open\nStartupWMClass=example-window\n",
                encoding="utf-8",
            )
            parsed = backend.parse_desktop_entry(path)
        self.assertEqual(parsed["windowClass"], "example-window")
        self.assertEqual(parsed["desktopId"], "example")

    def test_desktop_entry_infers_terminal_app_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "monitor.desktop"
            path.write_text(
                "[Desktop Entry]\nType=Application\nName=Monitor\n"
                "Exec=terminal --app-id=monitor-window -e monitor\n",
                encoding="utf-8",
            )
            parsed = backend.parse_desktop_entry(path)
        self.assertEqual(parsed["windowClass"], "monitor-window")

    def test_hidden_entries_are_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hidden.desktop"
            path.write_text(
                "[Desktop Entry]\nType=Application\nName=Hidden\n"
                "Exec=hidden\nNoDisplay=true\n",
                encoding="utf-8",
            )
            self.assertIsNone(backend.parse_desktop_entry(path))


class StateTests(unittest.TestCase):
    def test_disconnected_workspace_moves_to_internal_fallback(self):
        state = {
            "workspaces": [
                {"id": 1, "monitor": "Old-Output", "name": "Main", "default": True}
            ],
            "applications": [],
        }
        monitors = [
            {"name": "HDMI-A-7", "internal": False, "focused": True},
            {"name": "eDP-9", "internal": True, "focused": False},
        ]
        normalized, warnings = backend.normalize_state(state, monitors)
        self.assertEqual(normalized["workspaces"][0]["monitor"], "eDP-9")
        self.assertTrue(warnings)

    def test_replace_block_preserves_unmanaged_configuration(self):
        original = "local o = require('omarchy')\ncustom_setting()\n"
        first = backend.replace_managed_block(original, "BEGIN\nmanaged\nEND")
        self.assertIn("custom_setting()", first)
        second = backend.replace_managed_block(
            original + "\n" + backend.BEGIN_MARKER + "\nold\n" + backend.END_MARKER,
            backend.BEGIN_MARKER + "\nnew\n" + backend.END_MARKER,
        )
        self.assertIn("custom_setting()", second)
        self.assertIn("\nnew\n", second)
        self.assertNotIn("\nold\n", second)

    def test_render_contains_supplied_values(self):
        state = {
            "workspaces": [
                {"id": 1, "name": "Work", "monitor": "Panel-42", "default": True}
            ],
            "applications": [
                {
                    "name": "Editor", "windowClass": "editor-window",
                    "workspace": 1, "command": "editor", "delay": 0,
                    "enabled": True, "desktopId": "editor",
                }
            ],
        }
        rendered = backend.render_hyprland(state)
        self.assertIn(backend.BEGIN_MARKER, rendered)
        self.assertIn('monitor = "Panel-42"', rendered)
        self.assertIn('o.window("editor-window"', rendered)

    def test_legacy_block_is_discovered_without_a_hard_coded_marker_name(self):
        content = """before()
-- >>> OLD-AUTOSTART-EDITOR BEGIN >>>
-- Managed by AutostartEditor. Do not edit by hand.
hl.workspace_rule({ workspace = "1", monitor = "Panel-42" })
-- <<< OLD-AUTOSTART-EDITOR END <<<
after()
"""
        block = backend.legacy_managed_block(content)
        self.assertIn("Panel-42", block)
        self.assertNotIn("before()", block)

    def test_legacy_rules_preserve_class_and_title_match_types(self):
        block = """-- >>> OLD-AUTOSTART-EDITOR BEGIN >>>
-- Managed by AutostartEditor. Do not edit by hand.
hl.workspace_rule({ workspace = "1", monitor = "Panel-42", default = true, default_name = "Main" })
o.window("editor-window", { workspace = "1 silent" })
o.window({ title = "example.test_/app" }, { workspace = "1 silent", float = false })
-- <<< OLD-AUTOSTART-EDITOR END <<<"""
        workspaces, applications = backend.legacy_rules(block)
        self.assertEqual(workspaces[0]["name"], "Main")
        self.assertEqual(applications[0]["matchType"], "class")
        self.assertEqual(applications[1]["matchType"], "title")

    def test_title_rule_matches_legacy_autostart_command_without_private_mapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "web-tool.desktop").write_text(
                "[Desktop Entry]\nName=Web Tool\n"
                "Exec=launcher https://example.test/app\n"
                "X-GNOME-Autostart-enabled=true\n"
                "# managed-by: autostart-editor\n",
                encoding="utf-8",
            )
            block = """-- >>> OLD-AUTOSTART-EDITOR BEGIN >>>
-- Managed by AutostartEditor. Do not edit by hand.
hl.workspace_rule({ workspace = "1", monitor = "Panel-42" })
o.window({ title = "example.test_/app" }, { workspace = "1 silent" })
-- <<< OLD-AUTOSTART-EDITOR END <<<"""
            with mock.patch.object(backend, "AUTOSTART_DIR", directory):
                imported = backend.import_legacy_state(block, [])
        application = imported["applications"][0]
        self.assertEqual(application["name"], "Web Tool")
        self.assertEqual(application["autostartSource"], "legacy")
        self.assertEqual(application["matchType"], "title")

    def test_title_rule_matches_url_with_a_port(self):
        rule = {"matchType": "title", "windowClass": "example.test_/"}
        entry = {
            "path": Path("web.desktop"), "desktopId": "web", "name": "Web",
            "command": "launcher http://example.test:8080", "windowClass": "",
            "enabled": True, "source": "legacy",
        }
        self.assertIs(backend.match_autostart_entry(rule, [entry], set()), entry)

    def test_legacy_migration_replaces_instead_of_duplicating_block(self):
        content = """keep()
-- >>> OLD-AUTOSTART-EDITOR BEGIN >>>
-- Managed by AutostartEditor. Do not edit by hand.
old()
-- <<< OLD-AUTOSTART-EDITOR END <<<
"""
        replacement = backend.BEGIN_MARKER + "\nnew()\n" + backend.END_MARKER
        migrated = backend.replace_managed_block(content, replacement, migrate_legacy=True)
        self.assertIn("keep()", migrated)
        self.assertIn("new()", migrated)
        self.assertNotIn("old()", migrated)
        self.assertEqual(migrated.count(" BEGIN >>>"), 1)


class ValidationTests(unittest.TestCase):
    def valid_payload(self):
        return {
            "revision": "expected",
            "workspaces": [
                {"id": 1, "name": "Main", "monitor": "Panel-1", "default": True}
            ],
            "applications": [],
        }

    @mock.patch.object(backend, "revision", return_value="expected")
    @mock.patch.object(
        backend, "discover_monitors",
        return_value=[{"name": "Panel-1", "internal": True, "focused": True}],
    )
    def test_valid_payload(self, _monitors, _revision):
        state = backend.validate_payload(self.valid_payload())
        self.assertEqual(state["workspaces"][0]["monitor"], "Panel-1")

    @mock.patch.object(backend, "revision", return_value="new")
    def test_stale_revision_is_rejected(self, _revision):
        with self.assertRaisesRegex(backend.BackendError, "changed on disk"):
            backend.validate_payload(self.valid_payload())


if __name__ == "__main__":
    unittest.main()
