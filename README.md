# Omarchy Autostart Editor

A native Omarchy Shell panel for managing login applications, workspace names,
workspace-to-monitor assignments, and startup placement without hand-editing
Hyprland Lua or XDG autostart files.

> This project is an early development version. Review generated changes and
> keep backups before using it on an important session.

## Design

- `AutostartEditor.qml` provides an adaptive, theme-aware Omarchy panel.
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

It refuses to apply if `hyprland.lua` or its state changed after the panel was
loaded. Files are replaced atomically, and unrelated Hyprland configuration and
autostart entries are preserved.

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
return a non-zero status and a safe `error` message.

## Current limitations

- Window placement relies on `StartupWMClass` when an application provides it;
  some Wayland applications publish a different runtime app ID.
- Chrome-style web applications may share a class and require a future title or
  initial-title matching workflow.
- The plugin manages ten workspaces by default, matching Omarchy's standard
  numbered workspace workflow.
- There is not yet a guided cleanup command for removing generated rules.

## License

MIT
