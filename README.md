# Omarchy Autostart Editor

A native Omarchy Shell panel for managing login applications, workspace names,
workspace-to-monitor assignments, and startup placement without hand-editing
Hyprland Lua or XDG autostart files.

Login startup and workspace placement are independent. An application can run
in the background at login without receiving a Hyprland window rule, which is
useful for system-tray applications started with a silent/minimized flag.

> This project is an early development version. Review generated changes and
> keep backups before using it on an important session.

![The Autostart Editor panel: workspace names and monitor assignments above,
login applications with their command, workspace, delay and launch
state below](docs/panel.jpg)

## Design

- `AutostartEditor.qml` provides an adaptive, theme-aware Omarchy panel.
- `BarWidget.qml` provides a bar launcher that toggles the panel.
- `backend/autostart_editor_backend.py` discovers system state, validates edits,
  and performs narrow configuration writes through a JSON interface.

The backend contains no monitor names, private hosts, private URLs, fixed home
directories, or application-specific rules. It discovers monitors through
Hyprland with Linux DRM as a fallback, and discovers applications through the
standard XDG application directories.

## Safety boundaries

The plugin owns only:

- `~/.config/cruise42.autostart-editor/config.json`
- `.desktop` files beginning with
  `~/.config/autostart/cruise42-autostart-editor-`
- The block between `cruise42.autostart-editor` markers in
  `~/.config/hypr/hyprland.lua`
- Rotating backups under
  `~/.local/state/cruise42.autostart-editor/backups/`

Generated autostart entries carry an `X-Cruise42-AutostartEditor-Managed` key,
and that key — not the file name alone — is what marks an entry as owned. Entries
written by 0.1.0 used a key containing a dot, which the Desktop Entry
specification does not allow; they are still recognised as owned, and are
rewritten under the valid key on the next Apply. Each file name also ends in a
short digest of the application it belongs to, so two applications whose names
differ only in punctuation cannot collide on one file. Entries left by an earlier
naming scheme are removed once they are no longer referenced.

It refuses to apply if `hyprland.lua` or its state changed after the panel was
loaded. Files are replaced atomically, and unrelated Hyprland configuration and
autostart entries are preserved.

On first run, the plugin can read a configuration created by an earlier
Autostart Editor. Imported entries are editable like any others, and nothing is
written until Apply. That Apply replaces the legacy managed block instead of
creating duplicate workspace or window rules. Legacy editor-owned autostart
entries are migrated to the new namespace; application-owned entries remain
external and read-only.

## Requirements

- Omarchy with the Quattro Shell plugin system
- Python 3.10 or newer
- Hyprland and `hyprctl`

No Python packages outside the standard library are required.

## Development validation

```bash
omarchy plugin validate .
python3 -m unittest discover -s tests -v
python3 backend/autostart_editor_backend.py inspect | python3 -m json.tool
```

The `inspect` command is read-only. The backend does not write anything unless
the panel explicitly invokes `apply`.

## Installation

Once published, install and enable the plugin with:

```bash
omarchy plugin add https://github.com/Cruise42/omarchy-autostart-editor.git --enable
```

Open it with:

```bash
omarchy-shell shell summon cruise42.autostart-editor '{}'
```

## Bar widget

The plugin also ships a bar launcher, so the panel does not need a keybinding
or a terminal. Left-clicking it toggles the panel; other buttons do nothing,
because every action this plugin performs belongs behind an explicit Apply.

Enabling the plugin places the widget in the bar's right section:

```bash
omarchy plugin enable cruise42.autostart-editor
```

An installation that was already enabled before the widget existed is recorded
in `shell.json` as a `plugins[]` entry. `omarchy plugin enable` and `omarchy bar
put` both report success there but place nothing, because the plugin already
counts as enabled and the placement falls through to a move with no entry to
move. Add the layout entry by hand instead — `shell.json` hot-reloads on save:

```jsonc
// ~/.config/omarchy/shell.json, in bar.layout.right
{ "id": "cruise42.autostart-editor" }
```

Keeping the `plugins[]` entry alongside it is what lets the launcher be removed
on its own later. Once placed, the widget can be dragged along the bar or moved
from a script:

```bash
omarchy bar move cruise42.autostart-editor --section left --index 0
```

Adding the widget to a shell that is already running needs a restart, not just
a rescan:

```bash
omarchy restart shell
```

Editing a plugin file the shell has already loaded hot-reloads, but a *newly
added* file does not: Qt caches the plugin directory's listing, so the widget
fails to load with `File name case mismatch` until the process restarts.

Take the launcher off the bar:

```bash
omarchy plugin disable cruise42.autostart-editor
```

For a bar widget the layout entry *is* the enablement record, so this also
disables the plugin — panel included — unless a `plugins` entry in `shell.json`
still lists it. An installation enabled before the widget existed keeps that
entry, so there the launcher can be removed on its own. Generated startup
configuration is untouched either way.

Remove it with:

```bash
omarchy plugin remove cruise42.autostart-editor
```

Removing the plugin does not automatically delete configuration it previously
generated. That behavior is intentional: uninstalling a UI must not silently
delete a user's startup configuration.

## Backend protocol

Read current state:

```bash
backend/autostart_editor_backend.py inspect
```

Apply state by writing one JSON object to standard input:

```bash
printf '%s\n' '{"revision":"...","workspaces":[],"applications":[]}' |
  backend/autostart_editor_backend.py apply
```

Both commands return a single JSON object with an `ok` boolean. Failed commands
return a non-zero status and a safe `error` message on standard output, so the
panel reads standard output whenever it is present rather than trusting the exit
status alone.

A successful `apply` may also carry a `warnings` array. Writes have already
landed by the time Hyprland is reloaded, so a reload complaint is reported as a
warning next to the new `revision` instead of a failure that would leave the
panel holding a revision the files no longer match.

## Current limitations

- Window placement relies on `StartupWMClass` when an application provides it;
  some Wayland applications publish a different runtime app ID.
- Chrome-style web applications often share a single window class. The panel
  refuses to add a second application whose class is already in use, naming the
  entry it clashes with, because Hyprland takes one rule per class. Generated
  rules can match on initial title instead, but that is not yet selectable from
  the panel.
- The panel no longer displays each entry's window class or match type; both are
  still stored and applied.
- The plugin manages ten workspaces by default, matching Omarchy's standard
  numbered workspace workflow.
- There is not yet a guided cleanup command for removing generated rules.

## License

MIT
