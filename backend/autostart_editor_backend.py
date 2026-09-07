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
DESKTOP_MARKER = f"X-{PLUGIN_ID}-Managed=true"
DESKTOP_PREFIX = "cruise42-autostart-editor-"
INTERNAL_CONNECTOR_PREFIXES = ("edp-", "lvds-", "dsi-")


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
    window_class = values.get("StartupWMClass", "").strip() or desktop_id
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
            "workspaces": default_workspaces(fallback),
            "applications": [],
        }, warnings

    workspaces = []
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
        workspaces.append(
            {
                "id": workspace_id,
                "name": str(item.get("name", "")),
                "monitor": monitor,
                "default": bool(item.get("default", False)) and bool(monitor),
            }
        )

    applications = []
    for raw in data.get("applications", []):
        if not isinstance(raw, dict):
            continue
        applications.append(
            {
                "desktopId": str(raw.get("desktopId", "")),
                "name": str(raw.get("name", "")),
                "windowClass": str(raw.get("windowClass", "")),
                "command": str(raw.get("command", "")),
                "workspace": int(raw.get("workspace", 1)),
                "delay": int(raw.get("delay", 0)),
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    return {"version": CONFIG_VERSION, "workspaces": workspaces, "applications": applications}, warnings


def inspect() -> dict[str, Any]:
    monitors = discover_monitors()
    state, warnings = normalize_state(load_state(), monitors)
    return {
        "ok": True,
        "pluginId": PLUGIN_ID,
        "revision": revision(),
        "monitors": monitors,
        "fallbackMonitor": fallback_monitor(monitors),
        "workspaces": state["workspaces"],
        "applications": state["applications"],
        "installedApplications": discover_applications(),
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
        command = safe_text(raw.get("command", ""), f"{name} command")
        try:
            workspace = int(raw.get("workspace"))
            delay = int(raw.get("delay", 0))
        except (TypeError, ValueError) as error:
            raise BackendError(f"{name} has a non-numeric workspace or delay") from error
        if workspace not in valid_workspaces:
            raise BackendError(f"{name} uses unknown workspace {workspace}")
        if delay < 0 or delay > 300:
            raise BackendError(f"{name} delay must be between 0 and 300 seconds")
        applications.append(
            {
                "desktopId": safe_text(raw.get("desktopId", ""), f"{name} desktop ID", allow_empty=True),
                "name": name,
                "windowClass": window_class,
                "command": command,
                "workspace": workspace,
                "delay": delay,
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    return {"version": CONFIG_VERSION, "workspaces": workspaces, "applications": applications}


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
        lines.append(f"o.window({lua_string(application['windowClass'])}, {{ workspace = {workspace} }})")
    lines.append(END_MARKER)
    return "\n".join(lines)


def replace_managed_block(content: str, block: str) -> str:
    pattern = re.compile(re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL)
    if pattern.search(content):
        return pattern.sub(lambda _match: block, content)
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
    key = application.get("desktopId") or application["windowClass"]
    return AUTOSTART_DIR / f"{DESKTOP_PREFIX}{slug(str(key))}.desktop"


def render_desktop(application: dict[str, Any]) -> str:
    backend = Path(__file__).resolve()
    command = base64.urlsafe_b64encode(application["command"].encode()).decode()
    name = application["name"].replace("\\", "\\\\")
    encoded_backend = shlex.quote(str(backend))
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={name}",
            f"Exec={encoded_backend} launch --delay {application['delay']} --encoded {command}",
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
        if DESKTOP_MARKER in content:
            path.unlink()


def apply(payload: dict[str, Any]) -> dict[str, Any]:
    state = validate_payload(payload)
    try:
        current_hyprland = HYPRLAND_FILE.read_text(encoding="utf-8") if HYPRLAND_FILE.exists() else ""
    except OSError as error:
        raise BackendError(f"Could not read {HYPRLAND_FILE}: {error}") from error

    rotate_backup(HYPRLAND_FILE)
    rotate_backup(STATE_FILE)
    rendered = replace_managed_block(current_hyprland, render_hyprland(state))
    atomic_write(HYPRLAND_FILE, rendered)
    atomic_write(STATE_FILE, json.dumps(state, indent=2, ensure_ascii=False) + "\n")

    keep: set[Path] = set()
    for application in state["applications"]:
        path = managed_desktop_path(application)
        if application["enabled"]:
            atomic_write(path, render_desktop(application))
            keep.add(path)
    remove_orphaned_desktops(keep)

    reload_result = run(["hyprctl", "reload"])
    error_result = run(["hyprctl", "configerrors"])
    errors = error_result.stdout.strip() or error_result.stderr.strip()
    if reload_result.returncode != 0 or error_result.returncode != 0 or errors:
        detail = errors or reload_result.stderr.strip() or reload_result.stdout.strip()
        raise BackendError(f"Changes were saved, but Hyprland reported an error: {detail or 'reload failed'}")
    return {"ok": True, "message": "Changes applied", "revision": revision()}


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


if __name__ == "__main__":
    raise SystemExit(main())
