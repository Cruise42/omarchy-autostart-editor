# Changelog

## 0.2.0

### Added

- A bar widget (`BarWidget.qml`) that toggles the panel, so it no longer needs a
  keybinding or a terminal. The plugin now declares the `bar-widget` kind
  alongside `panel`, and defaults to the bar's right section.
- The panel reports an `opened` flag, which is what the shell reads to decide
  which way to toggle.
- A successful `apply` may return a `warnings` array.

### Fixed

- Backend error messages reached the panel as "Apply backend returned no data".
  Failures arrive as JSON on standard output with a non-zero status, so the panel
  now trusts standard output whenever it is present. This had been hiding every
  stale-revision, validation and Hyprland message.
- Relocating a workspace off a disconnected output kept its default flag,
  producing two defaults on one monitor — a state validation then rejected, so
  every Apply failed until the panel was reloaded and the edits discarded.
- A malformed `config.json` raised a traceback instead of answering in the JSON
  protocol.
- Migrating a legacy entry left the old `.desktop` file in place, so the
  application started twice. The real path is now carried through the import
  rather than rebuilt from a stripped identifier, and is confined to the
  autostart directory before anything is unlinked.
- Generated `.desktop` files were rejected by `desktop-file-validate`: the `Exec`
  path used shell single quotes where the specification requires double quotes,
  which broke autostart for any install path containing a space, and the
  ownership key contained a dot. Both are fixed, and the previous key is still
  recognised as owned.
- Two applications whose identifiers differed only in punctuation shared one
  generated file name, so one silently never started.
- A Hyprland complaint after a successful write failed the whole Apply, leaving
  the panel on a stale revision with no way forward except discarding the edits.
  It is now reported as a warning next to the new revision.
- The panel accepted two applications sharing a window class, which the backend
  then rejected on every Apply.
- Editing one field rebuilt the whole application list, resetting scroll
  position, losing focus and interrupting the delay stepper mid-click. Rows are
  now held in a `ListModel` and updated individually.
- A stored default-workspace focus delay of `0` was silently read back as 8000.
- The empty-state message was clipped, having been placed inside the list view's
  content item.
- The panel could appear without being summoned, since it stayed loaded and had
  no initial visibility.
- Removing an application-owned entry dropped the row while leaving the entry
  itself, so it kept starting and could no longer be seen. The panel now
  explains that only the placement rule is removed.
- Re-importing an entry this plugin had written double-wrapped its launch
  command and doubled its file-name prefix.
- Tabbing through a field marked the configuration unsaved without any edit.

### Changed

- Input fields are sized to their expected content rather than stretching, and
  each application occupies two rows instead of three.
- The window-class column was removed from the panel; it could not be edited.
- The "read-only preview" footer shown after a legacy import was removed — that
  import has always been editable, and nothing is written until Apply.

## 0.1.0

- First development release.
