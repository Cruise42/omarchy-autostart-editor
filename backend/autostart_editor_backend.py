#!/usr/bin/env python3
"""JSON backend for the cruise42.autostart-editor Omarchy plugin.

The backend owns only explicitly marked Hyprland rules and namespaced XDG
autostart entries. It contains no hostnames, monitor names, absolute home
paths, private URLs, or application-specific behavior.
"""

from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any


PLUGIN_ID = "cruise42.autostart-editor"
CONFIG_VERSION = 1
DEFAULT_WORKSPACE_COUNT = 10
BEGIN_MARKER = f"-- >>> {PLUGIN_ID} BEGIN >>>"
END_MARKER = f"-- <<< {PLUGIN_ID} END <<<"
DESKTOP_MARKER_KEY = "X-Cruise42-AutostartEditor-Managed"
DESKTOP_MARKER = f"{DESKTOP_MARKER_KEY}=true"
DESKTOP_PREFIX = "cruise42-autostart-editor-"
# Written by 0.1.0, which used a dot in the key name. Detected, never written.
SUPERSEDED_DESKTOP_MARKER = f"X-{PLUGIN_ID}-Managed=true"
LEGACY_DESKTOP_MARKER = "# managed-by: autostart-editor"
LEGACY_DESKTOP_PREFIX = "autostart-editor-"
LEGACY_CLASS_KEY = "X-AutostartEditor-Class"
INTERNAL_CONNECTOR_PREFIXES = ("edp-", "lvds-", "dsi-")
# Reserved in an Exec value by the XDG Desktop Entry specification.
DESKTOP_EXEC_RESERVED = " \t\n\"'\\><~|&;$*?#()`"
DEFAULT_FOCUS_DELAY_MS = 8000

MANAGED_BLOCK_PATTERN = re.compile(
    r"^-- >>> (?P<manager>[^\n]+) BEGIN >>>\s*$.*?"
    r"^-- <<< (?P=manager) END <<<\s*$",
    re.MULTILINE | re.DOTALL,
)


def xdg_dir(variable: str, default: Path) -> Path:
    value = os.environ.get(variable, "").strip()
    return Path(value).expanduser() if value else default


HOME = Path.home()
CONFIG_HOME = xdg_dir("XDG_CONFIG_HOME", HOME / ".config")
STATE_HOME = xdg_dir("XDG_STATE_HOME", HOME / ".local" / "state")
PLUGIN_CONFIG_DIR = CONFIG_HOME / PLUGIN_ID
STATE_FILE = PLUGIN_CONFIG_DIR / "config.json"
HYPRLAND_FILE = CONFIG_HOME / "hypr" / "hyprland.lua"
AUTOSTART_DIR = CONFIG_HOME / "autostart"
BACKUP_DIR = STATE_HOME / PLUGIN_ID / "backups"


class BackendError(Exception):
    """A safe, user-facing backend failure."""


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        return subprocess.CompletedProcess(command, 127, "", str(error))


def as_int(value: Any, default: int) -> int:
    """Coerce a stored value without raising; malformed state falls back."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_plugin_owned(content: str) -> bool:
    """True for autostart entries this plugin wrote, including older markers."""
    return DESKTOP_MARKER in content or SUPERSEDED_DESKTOP_MARKER in content


def base_desktop_id(stem: str) -> str:
    """Strip this plugin's own file-name prefix and disambiguating suffix."""
    for prefix in (DESKTOP_PREFIX, LEGACY_DESKTOP_PREFIX):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    return re.sub(r"-[0-9a-f]{8}$", "", stem)


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def discover_monitors() -> list[dict[str, Any]]:
    """Return connected monitors, preferring live Hyprland metadata."""
    result = run(["hyprctl", "monitors", "-j"])
    if result.returncode == 0:
        try:
            monitors = json.loads(result.stdout)
            found = [
                {
                    "name": str(item["name"]),
                    "description": str(item.get("description") or item["name"]),
                    "focused": bool(item.get("focused", False)),
                    "internal": is_internal_monitor(str(item["name"])),
                }
                for item in monitors
                if isinstance(item, dict) and item.get("name")
            ]
            if found:
                return found
        except (ValueError, TypeError, KeyError):
            pass

    found = []
    for status_path in sorted(glob.glob("/sys/class/drm/card*-*/status")):
        path = Path(status_path)
        try:
            if path.read_text(encoding="utf-8").strip() != "connected":
                continue
        except OSError:
            continue
        connector = re.sub(r"^card\d+-", "", path.parent.name)
        found.append(
            {
                "name": connector,
                "description": connector,
                "focused": False,
                "internal": is_internal_monitor(connector),
            }
        )
    return found


def is_internal_monitor(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.startswith(prefix) for prefix in INTERNAL_CONNECTOR_PREFIXES)


def fallback_monitor(monitors: list[dict[str, Any]]) -> str:
    for monitor in monitors:
        if monitor.get("internal"):
            return str(monitor["name"])
    for monitor in monitors:
        if monitor.get("focused"):
            return str(monitor["name"])
    return str(monitors[0]["name"]) if monitors else ""


def desktop_directories() -> list[Path]:
    data_home = xdg_dir("XDG_DATA_HOME", HOME / ".local" / "share")
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    return [data_home / "applications", *[Path(item) / "applications" for item in data_dirs.split(":") if item]]


def parse_desktop_entry(path: Path) -> dict[str, str] | None:
    """Read the first Desktop Entry section without executing its contents."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    values: dict[str, str] = {}
    in_entry = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_entry = stripped == "[Desktop Entry]"
            continue
        if not in_entry or "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in {"Name", "Exec", "StartupWMClass", "NoDisplay", "Hidden", "Type"}:
            values.setdefault(key, value.strip())
    if values.get("Type", "Application") != "Application":
        return None
    if values.get("Hidden", "false").lower() == "true" or values.get("NoDisplay", "false").lower() == "true":
        return None
    name = values.get("Name", "").strip()
    command = values.get("Exec", "").strip()
    if not name or not command:
        return None
    desktop_id = path.stem
    window_class = values.get("StartupWMClass", "").strip()
    if not window_class:
        app_id = re.search(r"(?:^|\s)--(?:app-id|class)(?:=|\s+)([^\s]+)", command)
        window_class = app_id.group(1).strip('"\'') if app_id else desktop_id
    return {
        "desktopId": desktop_id,
        "name": name,
        "command": command,
        "windowClass": window_class,
    }


def discover_applications() -> list[dict[str, str]]:
    """Discover launchable applications from the standard XDG data paths."""
    applications: dict[str, dict[str, str]] = {}
    for directory in desktop_directories():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.desktop")):
            item = parse_desktop_entry(path)
            if item:
                applications.setdefault(item["desktopId"], item)
    return sorted(applications.values(), key=lambda item: item["name"].casefold())


def parse_autostart_entries() -> list[dict[str, Any]]:
    """Read XDG autostart entries, including legacy ownership metadata."""
    entries = []
    if not AUTOSTART_DIR.is_dir():
        return entries
    for path in sorted(AUTOSTART_DIR.glob("*.desktop")):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        values: dict[str, str] = {}
        for line in content.splitlines():
            if "=" not in line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            if key in {"Name", "Exec", "Hidden", "X-GNOME-Autostart-enabled", LEGACY_CLASS_KEY}:
                values[key] = value.strip()
        command = values.get("Exec", "")
        name = values.get("Name", path.stem)
        enabled = (
            values.get("Hidden", "false").lower() != "true"
            and values.get("X-GNOME-Autostart-enabled", "true").lower() != "false"
        )
        source = "plugin" if is_plugin_owned(content) else (
            "legacy" if LEGACY_DESKTOP_MARKER in content else "external"
        )
        entries.append(
            {
                "path": path,
                "desktopId": path.stem,
                "name": name,
                "command": command,
                "windowClass": values.get(LEGACY_CLASS_KEY, ""),
                "enabled": enabled,
                "source": source,
            }
        )
    return entries


def legacy_managed_block(content: str) -> str:
    """Find an older editor block by its ownership comment, not its private ID."""
    for match in MANAGED_BLOCK_PATTERN.finditer(content):
        block = match.group(0)
        if match.group("manager") == PLUGIN_ID:
            continue
        if "Managed by AutostartEditor" in block:
            return block
    return ""


def decode_lua_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except ValueError:
        return value.replace('\\"', '"').replace("\\\\", "\\")


def extract_delay(command: str) -> tuple[str, int]:
    """Unwrap a delayed launcher back into its command and delay in seconds.

    Handles the former GTK editor's `sh -c 'sleep N; exec ...'` form and this
    plugin's own `launch --delay N --encoded ...` form, so re-importing an
    entry the plugin wrote never double-wraps it.
    """
    match = re.fullmatch(r"sh -c 'sleep (\d+); exec (.*)'", command)
    if match:
        return match.group(2), int(match.group(1))
    launch = re.search(r"launch\s+--delay\s+(\d+)\s+--encoded\s+(\S+)", command)
    if launch:
        try:
            decoded = base64.urlsafe_b64decode(launch.group(2).encode()).decode()
        except (ValueError, UnicodeError):
            return command, 0
        if decoded:
            return decoded, int(launch.group(1))
    return command, 0


def legacy_rules(block: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse workspace and window rules written by the former GTK editor."""
    workspaces = []
    applications = []
    for line in block.splitlines():
        workspace_match = re.match(r"\s*hl\.workspace_rule\(\{(.*)\}\)\s*$", line)
        if workspace_match:
            body = workspace_match.group(1)
            workspace_id = re.search(r'workspace\s*=\s*"?(\d+)"?', body)
            monitor = re.search(r'monitor\s*=\s*"((?:\\.|[^"])*)"', body)
            name = re.search(r'default_name\s*=\s*"((?:\\.|[^"])*)"', body)
            if workspace_id and monitor:
                workspaces.append(
                    {
                        "id": int(workspace_id.group(1)),
                        "name": decode_lua_string(name.group(1)) if name else "",
                        "monitor": decode_lua_string(monitor.group(1)),
                        "default": bool(re.search(r"\bdefault\s*=\s*true\b", body)),
                    }
                )
            continue

        title_match = re.match(
            r'\s*o\.window\(\{\s*title\s*=\s*"((?:\\.|[^"])*)"\s*\},\s*\{(.*)\}\)\s*$',
            line,
        )
        class_match = re.match(
            r'\s*o\.window\(\s*"((?:\\.|[^"])*)"\s*,\s*\{(.*)\}\)\s*$',
            line,
        )
        matched = title_match or class_match
        if not matched:
            continue
        workspace = re.search(r'workspace\s*=\s*"?(\d+)(?:\s+silent)?"?', matched.group(2))
        if workspace:
            rule_options: dict[str, Any] = {}
            float_option = re.search(r"\bfloat\s*=\s*(true|false)\b", matched.group(2))
            tag_option = re.search(r'\btag\s*=\s*"((?:\\.|[^"])*)"', matched.group(2))
            if float_option:
                rule_options["float"] = float_option.group(1) == "true"
            if tag_option:
                rule_options["tag"] = decode_lua_string(tag_option.group(1))
            applications.append(
                {
                    "matchType": "title" if title_match else "class",
                    "windowClass": decode_lua_string(matched.group(1)),
                    "workspace": int(workspace.group(1)),
                    "ruleOptions": rule_options,
                }
            )
    return workspaces, applications


def match_autostart_entry(rule: dict[str, Any], entries: list[dict[str, Any]], used: set[Path]) -> dict[str, Any] | None:
    value = rule["windowClass"]
    value_slug = slug(value)
    candidates = [entry for entry in entries if entry["path"] not in used]

    for entry in candidates:
        if entry["windowClass"] == value:
            return entry
    for entry in candidates:
        if slug(base_desktop_id(entry["desktopId"])) == value_slug:
            return entry
        if slug(entry["name"]) == value_slug:
            return entry
    if rule["matchType"] == "title":
        needles = unique_strings(
            [value.replace("_", "").casefold(), value.split("_", 1)[0].casefold()]
        )
        for entry in candidates:
            command = entry["command"].casefold()
            if any(len(needle) >= 4 and needle in command for needle in needles):
                return entry
    return None


def import_legacy_state(block: str, installed: list[dict[str, str]]) -> dict[str, Any]:
    workspaces, rules = legacy_rules(block)
    entries = parse_autostart_entries()
    installed_by_class = {item["windowClass"]: item for item in installed}
    used: set[Path] = set()
    applications = []
    for rule in rules:
        entry = match_autostart_entry(rule, entries, used)
        discovered = installed_by_class.get(rule["windowClass"])
        legacy_path = ""
        if entry:
            used.add(entry["path"])
            command, delay = extract_delay(entry["command"])
            name = entry["name"]
            desktop_id = base_desktop_id(entry["desktopId"])
            enabled = entry["enabled"]
            source = entry["source"]
            if source == "legacy":
                # Keep the real file name; the cleanup in apply() unlinks it.
                legacy_path = str(entry["path"])
        elif discovered:
            command, delay = extract_delay(discovered["command"])
            name = discovered["name"]
            desktop_id = discovered["desktopId"]
            enabled = False
            source = "plugin"
        else:
            command, delay = "", 0
            name = rule["windowClass"]
            desktop_id = ""
            enabled = False
            source = "plugin"
        applications.append(
            {
                "desktopId": desktop_id,
                "legacyPath": legacy_path,
                "name": name,
                "windowClass": rule["windowClass"],
                "matchType": rule["matchType"],
                "ruleOptions": rule.get("ruleOptions", {}),
                "command": command,
                "workspace": rule["workspace"],
                "delay": delay,
                "enabled": enabled,
                "autostartSource": source,
            }
        )
    applications.sort(key=lambda item: (item["workspace"], item["name"].casefold()))
    return {
        "version": CONFIG_VERSION,
        "legacyImport": True,
        "refocusDefaults": "hl.timer(" in block,
        "focusDelayMs": int(timeout.group(1)) if (
            timeout := re.search(r"timeout\s*=\s*(\d+)", block)
        ) else DEFAULT_FOCUS_DELAY_MS,
        "workspaces": workspaces,
        "applications": applications,
    }


def load_state() -> dict[str, Any] | None:
    if not STATE_FILE.is_file():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BackendError(f"Could not read {STATE_FILE}: {error}") from error
    if not isinstance(data, dict):
        raise BackendError(f"{STATE_FILE} does not contain a JSON object")
    return data


def file_digest(path: Path) -> str:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        content = b""
    except OSError as error:
        raise BackendError(f"Could not read {path}: {error}") from error
    return hashlib.sha256(content).hexdigest()


def revision() -> str:
    digest = hashlib.sha256()
    for path in (STATE_FILE, HYPRLAND_FILE):
        digest.update(str(path).encode())
        digest.update(file_digest(path).encode())
    return digest.hexdigest()


def default_workspaces(monitor: str) -> list[dict[str, Any]]:
    return [
        {
            "id": workspace,
            "name": "",
            "monitor": monitor,
            "default": workspace == 1 and bool(monitor),
        }
        for workspace in range(1, DEFAULT_WORKSPACE_COUNT + 1)
    ]


def normalize_state(data: dict[str, Any] | None, monitors: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    fallback = fallback_monitor(monitors)
    connected = {str(item["name"]) for item in monitors}
    warnings: list[str] = []
    if data is None:
        return {
            "version": CONFIG_VERSION,
            "legacyImport": False,
            "refocusDefaults": True,
            "focusDelayMs": DEFAULT_FOCUS_DELAY_MS,
            "workspaces": default_workspaces(fallback),
            "applications": [],
        }, warnings

    workspaces = []
    claimed_defaults: set[str] = set()
    raw_workspaces = data.get("workspaces", [])
    by_id = {
        int(item["id"]): item
        for item in raw_workspaces
        if isinstance(item, dict) and str(item.get("id", "")).isdigit()
    }
    count = max([DEFAULT_WORKSPACE_COUNT, *by_id.keys()])
    for workspace_id in range(1, count + 1):
        item = by_id.get(workspace_id, {})
        configured = str(item.get("monitor", ""))
        monitor = configured if configured in connected else fallback
        if configured and configured != monitor:
            warnings.append(f"Workspace {workspace_id} used disconnected output {configured}; using {monitor or 'no output'}")
        is_default = bool(item.get("default", False)) and bool(monitor)
        if is_default and monitor in claimed_defaults:
            # Relocating a workspace must not produce two defaults on one
            # output; validate_payload rejects that and would block every Apply.
            is_default = False
            warnings.append(
                f"Workspace {workspace_id} is no longer the default because {monitor} already has one"
            )
        elif is_default:
            claimed_defaults.add(monitor)
        workspaces.append(
            {
                "id": workspace_id,
                "name": str(item.get("name", "")),
                "monitor": monitor,
                "default": is_default,
            }
        )

    applications = []
    for raw in data.get("applications", []):
        if not isinstance(raw, dict):
            continue
        applications.append(
            {
                "desktopId": str(raw.get("desktopId", "")),
                "legacyPath": str(raw.get("legacyPath", "")),
                "name": str(raw.get("name", "")),
                "windowClass": str(raw.get("windowClass", "")),
                "matchType": "title" if raw.get("matchType") == "title" else "class",
                "ruleOptions": raw.get("ruleOptions", {}) if isinstance(raw.get("ruleOptions", {}), dict) else {},
                "command": str(raw.get("command", "")),
                "workspace": as_int(raw.get("workspace", 1), 1),
                "delay": as_int(raw.get("delay", 0), 0),
                "enabled": bool(raw.get("enabled", True)),
                "autostartSource": str(raw.get("autostartSource", "plugin")),
            }
        )
    return {
        "version": CONFIG_VERSION,
        "legacyImport": bool(data.get("legacyImport", False)),
        "refocusDefaults": bool(data.get("refocusDefaults", True)),
        # A stored 0 is a valid delay and must survive the round trip.
        "focusDelayMs": as_int(data.get("focusDelayMs", DEFAULT_FOCUS_DELAY_MS), DEFAULT_FOCUS_DELAY_MS),
        "workspaces": workspaces,
        "applications": applications,
    }, warnings


def inspect() -> dict[str, Any]:
    monitors = discover_monitors()
    installed = discover_applications()
    data = load_state()
    imported = False
    if data is None and HYPRLAND_FILE.is_file():
        try:
            block = legacy_managed_block(HYPRLAND_FILE.read_text(encoding="utf-8"))
        except OSError as error:
            raise BackendError(f"Could not read {HYPRLAND_FILE}: {error}") from error
        if block:
            data = import_legacy_state(block, installed)
            imported = True
    state, warnings = normalize_state(data, monitors)
    if imported:
        warnings.insert(0, "Imported the existing Autostart Editor configuration as a read-only preview")
    return {
        "ok": True,
        "pluginId": PLUGIN_ID,
        "revision": revision(),
        "monitors": monitors,
        "fallbackMonitor": fallback_monitor(monitors),
        "legacyImport": state["legacyImport"],
        "refocusDefaults": state["refocusDefaults"],
        "focusDelayMs": state["focusDelayMs"],
        "workspaces": state["workspaces"],
        "applications": state["applications"],
        "installedApplications": installed,
        "warnings": warnings,
        "paths": {
            "state": str(STATE_FILE),
            "hyprland": str(HYPRLAND_FILE),
            "autostart": str(AUTOSTART_DIR),
        },
    }


def safe_text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    text = str(value).strip()
    if not allow_empty and not text:
        raise BackendError(f"{field} cannot be empty")
    if any(char in text for char in "\r\n\0"):
        raise BackendError(f"{field} cannot contain line breaks")
    return text


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BackendError("Apply input must be a JSON object")
    expected_revision = safe_text(payload.get("revision", ""), "revision")
    if expected_revision != revision():
        raise BackendError("Configuration changed on disk. Reload before applying changes.")

    live_monitors = discover_monitors()
    connected = {str(item["name"]) for item in live_monitors}
    if not connected:
        raise BackendError("No connected monitor could be discovered")

    workspaces = []
    seen_ids: set[int] = set()
    defaults: set[str] = set()
    for raw in payload.get("workspaces", []):
        if not isinstance(raw, dict):
            raise BackendError("Every workspace must be a JSON object")
        try:
            workspace_id = int(raw.get("id"))
        except (TypeError, ValueError) as error:
            raise BackendError("Workspace IDs must be positive integers") from error
        if workspace_id < 1 or workspace_id in seen_ids:
            raise BackendError(f"Workspace ID {workspace_id} is invalid or duplicated")
        seen_ids.add(workspace_id)
        monitor = safe_text(raw.get("monitor", ""), f"Workspace {workspace_id} monitor")
        if monitor not in connected:
            raise BackendError(f"Workspace {workspace_id} uses disconnected output {monitor}")
        is_default = bool(raw.get("default", False))
        if is_default and monitor in defaults:
            raise BackendError(f"More than one default workspace is assigned to {monitor}")
        if is_default:
            defaults.add(monitor)
        workspaces.append(
            {
                "id": workspace_id,
                "name": safe_text(raw.get("name", ""), f"Workspace {workspace_id} name", allow_empty=True),
                "monitor": monitor,
                "default": is_default,
            }
        )
    if not workspaces:
        raise BackendError("At least one workspace is required")

    applications = []
    seen_classes: set[str] = set()
    valid_workspaces = seen_ids
    for raw in payload.get("applications", []):
        if not isinstance(raw, dict):
            raise BackendError("Every application must be a JSON object")
        name = safe_text(raw.get("name", ""), "Application name")
        window_class = safe_text(raw.get("windowClass", ""), f"{name} window class")
        if window_class in seen_classes:
            raise BackendError(f"Window class {window_class} is assigned more than once")
        seen_classes.add(window_class)
        enabled = bool(raw.get("enabled", True))
        command = safe_text(raw.get("command", ""), f"{name} command", allow_empty=not enabled)
        match_type = "title" if raw.get("matchType") == "title" else "class"
        raw_options = raw.get("ruleOptions", {})
        rule_options: dict[str, Any] = {}
        if isinstance(raw_options, dict):
            if isinstance(raw_options.get("float"), bool):
                rule_options["float"] = raw_options["float"]
            if raw_options.get("tag") is not None:
                rule_options["tag"] = safe_text(raw_options["tag"], f"{name} rule tag")
        source = str(raw.get("autostartSource", "plugin"))
        if source not in {"plugin", "legacy", "external"}:
            source = "plugin"
        if source == "external" and not enabled:
            raise BackendError(
                f"{name} is started by an application-managed autostart entry and cannot be disabled here"
            )
        try:
            workspace = int(raw.get("workspace"))
            delay = int(raw.get("delay", 0))
        except (TypeError, ValueError) as error:
            raise BackendError(f"{name} has a non-numeric workspace or delay") from error
        if workspace not in valid_workspaces:
            raise BackendError(f"{name} uses unknown workspace {workspace}")
        if delay < 0 or delay > 300:
            raise BackendError(f"{name} delay must be between 0 and 300 seconds")
        legacy_path = safe_text(raw.get("legacyPath", ""), f"{name} legacy path", allow_empty=True)
        if legacy_path:
            # apply() unlinks this path, so confine it to the autostart directory.
            candidate = Path(legacy_path)
            if candidate.parent != AUTOSTART_DIR or candidate.suffix != ".desktop":
                raise BackendError(f"{name} has an invalid legacy autostart path")
        applications.append(
            {
                "desktopId": safe_text(raw.get("desktopId", ""), f"{name} desktop ID", allow_empty=True),
                "legacyPath": legacy_path,
                "name": name,
                "windowClass": window_class,
                "matchType": match_type,
                "ruleOptions": rule_options,
                "command": command,
                "workspace": workspace,
                "delay": delay,
                "enabled": enabled,
                "autostartSource": source,
            }
        )
    try:
        focus_delay = int(payload.get("focusDelayMs", DEFAULT_FOCUS_DELAY_MS))
    except (TypeError, ValueError) as error:
        raise BackendError("Default-workspace focus delay must be numeric") from error
    if focus_delay < 0 or focus_delay > 60000:
        raise BackendError("Default-workspace focus delay must be between 0 and 60000 milliseconds")
    return {
        "version": CONFIG_VERSION,
        "legacyImport": bool(payload.get("legacyImport", False)),
        "refocusDefaults": bool(payload.get("refocusDefaults", True)),
        "focusDelayMs": focus_delay,
        "workspaces": workspaces,
        "applications": applications,
    }


def lua_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_hyprland(state: dict[str, Any]) -> str:
    lines = [BEGIN_MARKER, "-- Generated by Autostart Editor. Do not edit this block by hand."]
    for workspace in state["workspaces"]:
        parts = [
            f"workspace = {lua_string(str(workspace['id']))}",
            f"monitor = {lua_string(workspace['monitor'])}",
        ]
        if workspace["default"]:
            parts.append("default = true")
        if workspace["name"]:
            parts.append(f"default_name = {lua_string(workspace['name'])}")
            parts.append("persistent = true")
        lines.append(f"hl.workspace_rule({{ {', '.join(parts)} }})")
    for application in state["applications"]:
        workspace = lua_string(f"{application['workspace']} silent")
        if application.get("matchType") == "title":
            matcher = f"{{ title = {lua_string(application['windowClass'])} }}"
        else:
            matcher = lua_string(application["windowClass"])
        parts = [f"workspace = {workspace}"]
        options = application.get("ruleOptions", {})
        if isinstance(options.get("float"), bool):
            parts.append(f"float = {'true' if options['float'] else 'false'}")
        if options.get("tag"):
            parts.append(f"tag = {lua_string(options['tag'])}")
        lines.append(f"o.window({matcher}, {{ {', '.join(parts)} }})")
    defaults = [workspace for workspace in state["workspaces"] if workspace["default"]]
    if state.get("refocusDefaults") and defaults:
        lines.append("-- Re-focus each monitor's default workspace after login applications settle.")
        lines.append("hl.timer(function()")
        for workspace in defaults:
            lines.append(f"  hl.dispatch(hl.dsp.focus({{ monitor = {lua_string(workspace['monitor'])} }}))")
            lines.append(f"  hl.dispatch(hl.dsp.focus({{ workspace = {lua_string(str(workspace['id']))} }}))")
        lines.append(
            f'end, {{ timeout = {int(state.get("focusDelayMs", 8000))}, type = "oneshot" }})'
        )
    lines.append(END_MARKER)
    return "\n".join(lines)


def replace_managed_block(content: str, block: str, migrate_legacy: bool = False) -> str:
    pattern = re.compile(re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL)
    if pattern.search(content):
        return pattern.sub(lambda _match: block, content)
    if migrate_legacy:
        legacy = legacy_managed_block(content)
        if legacy:
            return content.replace(legacy, block, 1)
    prefix = content.rstrip()
    return f"{prefix}\n\n{block}\n" if prefix else f"{block}\n"


def atomic_write(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def rotate_backup(path: Path) -> None:
    if not path.is_file():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stem = path.name
    for generation in range(3, 1, -1):
        older = BACKUP_DIR / f"{stem}.bak.{generation - 1}"
        newer = BACKUP_DIR / f"{stem}.bak.{generation}"
        if older.exists():
            os.replace(older, newer)
    shutil.copy2(path, BACKUP_DIR / f"{stem}.bak.1")


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "application"


def managed_desktop_path(application: dict[str, Any]) -> Path:
    """Build a collision-free file name for an application's autostart entry.

    `slug` is lossy — `Foo_Bar` and `foo-bar` collapse to the same text — so a
    digest of the untouched key is appended to keep distinct applications in
    distinct files while staying stable across runs.
    """
    key = str(application.get("desktopId") or application["windowClass"])
    digest = hashlib.sha256(key.encode()).hexdigest()[:8]
    return AUTOSTART_DIR / f"{DESKTOP_PREFIX}{slug(key)}-{digest}.desktop"


def desktop_exec_argument(value: str) -> str:
    """Quote one Exec argument the way the Desktop Entry specification requires.

    Quoting uses double quotes, not the shell's single quotes; `shlex.quote`
    produces a string the spec reads as reserved characters outside a quote.
    """
    if not value:
        return '""'
    if not any(character in value for character in DESKTOP_EXEC_RESERVED):
        return value
    escaped = value
    for character in ("\\", '"', "`", "$"):
        escaped = escaped.replace(character, f"\\{character}")
    # The enclosing quotes and their escapes are themselves part of a desktop
    # entry string value, where a literal backslash is written doubled.
    return '"' + escaped.replace("\\", "\\\\") + '"'


def render_desktop(application: dict[str, Any]) -> str:
    backend = Path(__file__).resolve()
    command = base64.urlsafe_b64encode(application["command"].encode()).decode()
    name = application["name"].replace("\\", "\\\\")
    quoted_backend = desktop_exec_argument(str(backend))
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={name}",
            f"Exec={quoted_backend} launch --delay {application['delay']} --encoded {command}",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            DESKTOP_MARKER,
            f"X-AutostartEditor-WindowClass={application['windowClass']}",
            "",
        ]
    )


def remove_orphaned_desktops(keep: set[Path]) -> None:
    if not AUTOSTART_DIR.is_dir():
        return
    for path in AUTOSTART_DIR.glob(f"{DESKTOP_PREFIX}*.desktop"):
        if path in keep:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if is_plugin_owned(content):
            path.unlink()


def apply(payload: dict[str, Any]) -> dict[str, Any]:
    state = validate_payload(payload)
    try:
        current_hyprland = HYPRLAND_FILE.read_text(encoding="utf-8") if HYPRLAND_FILE.exists() else ""
    except OSError as error:
        raise BackendError(f"Could not read {HYPRLAND_FILE}: {error}") from error

    rotate_backup(HYPRLAND_FILE)
    rotate_backup(STATE_FILE)
    rendered = replace_managed_block(
        current_hyprland, render_hyprland(state), migrate_legacy=state["legacyImport"]
    )
    atomic_write(HYPRLAND_FILE, rendered)

    keep: set[Path] = set()
    migrated_legacy_paths: set[Path] = set()
    for application in state["applications"]:
        source = application.get("autostartSource", "plugin")
        if source == "legacy" and application.get("legacyPath"):
            migrated_legacy_paths.add(Path(application["legacyPath"]))
        if source == "external":
            continue
        path = managed_desktop_path(application)
        if application["enabled"]:
            atomic_write(path, render_desktop(application))
            keep.add(path)
    remove_orphaned_desktops(keep)

    for path in migrated_legacy_paths:
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if LEGACY_DESKTOP_MARKER in content and not is_plugin_owned(content):
            path.unlink()

    state["legacyImport"] = False
    for application in state["applications"]:
        if application.get("autostartSource") == "legacy":
            application["autostartSource"] = "plugin"
            application["legacyPath"] = ""
    atomic_write(STATE_FILE, json.dumps(state, indent=2, ensure_ascii=False) + "\n")

    # Every write above has already landed, so a Hyprland complaint is reported
    # as a warning alongside the new revision. Failing here would strand the
    # panel on a stale revision, with the user's edits recoverable only by
    # discarding them.
    warnings: list[str] = []
    reload_result = run(["hyprctl", "reload"])
    error_result = run(["hyprctl", "configerrors"])
    errors = error_result.stdout.strip() or error_result.stderr.strip()
    if reload_result.returncode != 0 or error_result.returncode != 0 or errors:
        detail = (
            errors
            or reload_result.stderr.strip()
            or reload_result.stdout.strip()
            or "reload failed"
        )
        warnings.append(f"Changes were saved, but Hyprland reported an error: {detail}")
    return {
        "ok": True,
        "message": "Changes applied" if not warnings else "Changes saved with warnings",
        "revision": revision(),
        "warnings": warnings,
    }


def launch(delay: int, encoded: str) -> dict[str, Any]:
    if delay < 0 or delay > 300:
        raise BackendError("Delay must be between 0 and 300 seconds")
    try:
        command = base64.urlsafe_b64decode(encoded.encode()).decode()
        arguments = [argument for argument in shlex.split(command) if not re.fullmatch(r"%[A-Za-z]", argument)]
    except (ValueError, UnicodeError) as error:
        raise BackendError("The stored launch command is invalid") from error
    if not arguments:
        raise BackendError("The stored launch command is empty")
    if delay:
        time.sleep(delay)
    subprocess.Popen(arguments, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}


def read_stdin_json() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.readline())
    except ValueError as error:
        raise BackendError(f"Invalid JSON input: {error}") from error
    if not isinstance(value, dict):
        raise BackendError("Input must be a JSON object")
    return value


def emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Autostart Editor JSON backend")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("inspect", help="Print current state as JSON")
    subparsers.add_parser("apply", help="Read state from stdin and apply it")
    launch_parser = subparsers.add_parser("launch", help=argparse.SUPPRESS)
    launch_parser.add_argument("--delay", type=int, default=0)
    launch_parser.add_argument("--encoded", required=True)
    args = parser.parse_args()

    try:
        if args.action == "inspect":
            emit(inspect())
        elif args.action == "apply":
            emit(apply(read_stdin_json()))
        else:
            emit(launch(args.delay, args.encoded))
        return 0
    except BackendError as error:
        emit({"ok": False, "error": str(error)})
        return 1
    except OSError as error:
        emit({"ok": False, "error": f"Operating-system error: {error}"})
        return 1
    except (ValueError, TypeError) as error:
        # A malformed config.json must still answer in the JSON protocol
        # rather than printing a traceback the panel cannot read.
        emit({"ok": False, "error": f"Stored configuration is not valid: {error}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
