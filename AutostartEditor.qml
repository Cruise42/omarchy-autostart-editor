import QtQuick
import QtQuick.Controls as QQC
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui as Ui

Item {
  id: root

  property var shell: null
  property var manifest: null
  property bool closingFromHost: false
  property bool loading: false
  property bool applying: false
  property bool dirty: false
  property string statusText: ""
  property bool statusError: false
  property string revision: ""
  property bool legacyImport: false
  property bool refocusDefaults: true
  property int focusDelayMs: 8000
  property var monitors: []
  property var monitorNames: []
  property var installedApplications: []
  // Workspace IDs as strings, for the per-application workspace dropdown.
  property var workspaceIds: []

  // Row state lives in ListModels so a single field edit updates one row
  // through setProperty instead of replacing the model and resetting the view.
  ListModel { id: workspaceModel; dynamicRoles: true }
  ListModel { id: applicationModel; dynamicRoles: true }

  readonly property string pluginId: "cruise42.autostart-editor"
  // The host prefers a panel's own `opened` flag over its bookkeeping when
  // deciding which way to toggle, so report the window itself. Reading the
  // window keeps the answer true even when it is dismissed by its decoration.
  readonly property bool opened: window.visible
  readonly property string backendPath: {
    if (manifest && manifest.__sourceDir)
      return String(manifest.__sourceDir) + "/backend/autostart_editor_backend.py"
    return String(Qt.resolvedUrl("backend/autostart_editor_backend.py")).replace(/^file:\/\//, "")
  }
  readonly property color foreground: Color.foreground
  readonly property color background: Color.background
  readonly property color accent: Color.accent
  readonly property color muted: Color.muted
  readonly property color urgent: Color.urgent
  readonly property string fontFamily: Style.font.family

  function open(payloadJson) {
    closingFromHost = false
    window.visible = true
    refresh()
    Qt.callLater(function() { closeButton.forceActiveFocus() })
  }

  function close() {
    closingFromHost = true
    window.visible = false
    closingFromHost = false
  }

  function requestClose() {
    if (dirty) {
      confirmDialog.title = "Discard unsaved changes?"
      confirmDialog.message = "Changes made in this panel have not been applied."
      confirmDialog.actionText = "Discard"
      confirmDialog.action = function() { root.dismissNow() }
      confirmDialog.open()
      return
    }
    dismissNow()
  }

  function dismissNow() {
    if (shell && typeof shell.hide === "function") shell.hide(pluginId)
    else window.visible = false
  }

  function markDirty(message) {
    dirty = true
    statusError = false
    statusText = message || "Unsaved changes"
  }

  function replaceRows(model, rows) {
    model.clear()
    for (var i = 0; i < rows.length; i++) model.append(rows[i])
  }

  function workspaceList() {
    var out = []
    for (var i = 0; i < workspaceModel.count; i++) {
      var row = workspaceModel.get(i)
      out.push({
        id: Number(row.id),
        name: String(row.name),
        monitor: String(row.monitor),
        default: row.default === true
      })
    }
    return out
  }

  function applicationList() {
    var out = []
    for (var i = 0; i < applicationModel.count; i++) {
      var row = applicationModel.get(i)
      out.push({
        desktopId: String(row.desktopId),
        legacyPath: String(row.legacyPath || ""),
        name: String(row.name),
        windowClass: String(row.windowClass),
        matchType: String(row.matchType),
        ruleOptions: row.ruleOptions || {},
        placeInWorkspace: row.placeInWorkspace !== false,
        command: String(row.command),
        workspace: Number(row.workspace),
        delay: Number(row.delay),
        enabled: row.enabled === true,
        autostartSource: String(row.autostartSource)
      })
    }
    return out
  }

  function refreshWorkspaceIds() {
    var ids = []
    for (var i = 0; i < workspaceModel.count; i++)
      ids.push(String(workspaceModel.get(i).id))
    workspaceIds = ids
  }

  function updateWorkspace(index, key, value) {
    if (index < 0 || index >= workspaceModel.count) return
    var row = workspaceModel.get(index)
    // Focus-out fires editingFinished even when nothing was typed; only a real
    // change may arm Apply.
    if (row[key] === value) return
    var monitor = key === "monitor" ? value : String(row.monitor)
    workspaceModel.setProperty(index, key, value)
    if ((key === "default" && value) || (key === "monitor" && row.default === true)) {
      for (var i = 0; i < workspaceModel.count; i++) {
        if (i === index) continue
        var other = workspaceModel.get(i)
        if (other.default === true && String(other.monitor) === String(monitor))
          workspaceModel.setProperty(i, "default", false)
      }
    }
    markDirty()
  }

  function updateApplication(index, key, value) {
    if (index < 0 || index >= applicationModel.count) return
    if (applicationModel.get(index)[key] === value) return
    applicationModel.setProperty(index, key, value)
    markDirty()
  }

  function addSelectedApplication() {
    var index = installedPicker.currentIndex
    if (index < 0 || index >= installedApplications.length) return
    var selected = installedApplications[index]
    for (var i = 0; i < applicationModel.count; i++) {
      var row = applicationModel.get(i)
      if (String(row.desktopId) === String(selected.desktopId)) {
        statusError = true
        statusText = selected.name + " is already configured"
        return
      }
      // The backend applies one window rule per class and rejects duplicates,
      // so refuse the pair here rather than failing on every later Apply.
      if (selected.windowClass && String(row.windowClass) === String(selected.windowClass)) {
        statusError = true
        statusText = selected.name + " shares the window class “" + selected.windowClass
          + "” with " + row.name + ", so only one of them can be placed"
        return
      }
    }
    applicationModel.append({
      desktopId: String(selected.desktopId),
      legacyPath: "",
      name: String(selected.name),
      windowClass: String(selected.windowClass),
      matchType: "class",
      ruleOptions: {},
      placeInWorkspace: true,
      command: String(selected.command),
      workspace: workspaceModel.count ? Number(workspaceModel.get(0).id) : 1,
      delay: 0,
      enabled: true,
      autostartSource: "plugin"
    })
    markDirty("Added " + selected.name)
  }

  function removeApplication(index) {
    if (index < 0 || index >= applicationModel.count) return
    var row = applicationModel.get(index)
    var name = String(row.name)
    if (String(row.autostartSource) === "external") {
      // The autostart entry belongs to the application, so Apply cannot delete
      // it. Say so instead of leaving an entry that keeps starting unseen.
      confirmDialog.title = "Remove workspace placement for " + name + "?"
      confirmDialog.message = name + " is started by an autostart entry that belongs to the "
        + "application itself. Removing it here drops only the workspace placement rule — "
        + name + " keeps starting at login until you turn it off in its own settings."
      confirmDialog.actionText = "Remove placement"
      confirmDialog.action = function() { root.dropApplication(index, name) }
      confirmDialog.open()
      return
    }
    dropApplication(index, name)
  }

  function dropApplication(index, name) {
    if (index < 0 || index >= applicationModel.count) return
    applicationModel.remove(index)
    markDirty("Removed " + name)
  }

  function refresh() {
    if (dirty) {
      confirmDialog.title = "Discard unsaved changes?"
      confirmDialog.message = "Reloading will replace changes currently shown in the panel."
      confirmDialog.actionText = "Reload"
      confirmDialog.action = function() { root.runInspect() }
      confirmDialog.open()
      return
    }
    runInspect()
  }

  function runInspect() {
    if (inspectProcess.running || applyProcess.running) return
    loading = true
    statusError = false
    statusText = "Discovering monitors and applications…"
    inspectProcess.command = [backendPath, "inspect"]
    inspectProcess.running = true
  }

  function acceptInspection(raw) {
    try {
      var data = JSON.parse(String(raw || ""))
      if (!data.ok) throw new Error(data.error || "Inspection failed")
      revision = String(data.revision || "")
      legacyImport = data.legacyImport === true
      refocusDefaults = data.refocusDefaults !== false
      // A stored 0 is a valid delay, so only a missing value takes the default.
      var storedDelay = Number(data.focusDelayMs)
      focusDelayMs = isNaN(storedDelay) ? 8000 : storedDelay
      monitors = data.monitors || []
      monitorNames = monitors.map(function(item) { return String(item.name) })
      replaceRows(workspaceModel, data.workspaces || [])
      replaceRows(applicationModel, data.applications || [])
      refreshWorkspaceIds()
      installedApplications = data.installedApplications || []
      dirty = false
      statusError = false
      if (data.warnings && data.warnings.length)
        statusText = data.warnings.join(" · ")
      else
        statusText = "Ready · " + monitorNames.length + " monitor" + (monitorNames.length === 1 ? "" : "s")
    } catch (error) {
      statusError = true
      statusText = String(error)
    }
  }

  function applyChanges() {
    if (applyProcess.running || inspectProcess.running || !dirty) return
    applying = true
    statusError = false
    statusText = "Applying changes…"
    applyProcess.pendingPayload = JSON.stringify({
      revision: revision,
      legacyImport: legacyImport,
      refocusDefaults: refocusDefaults,
      focusDelayMs: focusDelayMs,
      workspaces: root.workspaceList(),
      applications: root.applicationList()
    }) + "\n"
    applyProcess.command = [backendPath, "apply"]
    applyProcess.running = true
  }

  function acceptApply(raw) {
    try {
      var data = JSON.parse(String(raw || ""))
      if (!data.ok) throw new Error(data.error || "Apply failed")
      revision = String(data.revision || revision)
      dirty = false
      legacyImport = false
      // The write succeeded; Hyprland may still have complained about it.
      var warnings = data.warnings || []
      statusError = warnings.length > 0
      statusText = warnings.length ? warnings.join(" · ") : (data.message || "Changes applied")
      // Row sources change on the backend once legacy entries are migrated.
      for (var i = 0; i < applicationModel.count; i++) {
        if (String(applicationModel.get(i).autostartSource) === "legacy") {
          applicationModel.setProperty(i, "autostartSource", "plugin")
          applicationModel.setProperty(i, "legacyPath", "")
        }
      }
    } catch (error) {
      statusError = true
      statusText = String(error)
    }
  }

  Process {
    id: inspectProcess
    stdout: StdioCollector { id: inspectStdout; waitForEnd: true }
    stderr: StdioCollector { id: inspectStderr; waitForEnd: true }
    onExited: function(exitCode) {
      root.loading = false
      var output = String(inspectStdout.text || "").trim()
      var error = String(inspectStderr.text || "").trim()
      // The backend reports its own failures as JSON on stdout with a non-zero
      // status, so stdout is authoritative whenever it is present.
      if (output) {
        root.acceptInspection(output)
        return
      }
      root.statusError = true
      root.statusText = error || (exitCode !== 0
        ? "Inspection backend failed with exit code " + exitCode
        : "Inspection backend returned no data")
    }
  }

  Process {
    id: applyProcess
    property string pendingPayload: ""
    stdinEnabled: true
    onStarted: {
      write(pendingPayload)
      pendingPayload = ""
    }
    stdout: StdioCollector { id: applyStdout; waitForEnd: true }
    stderr: StdioCollector { id: applyStderr; waitForEnd: true }
    onExited: function(exitCode) {
      root.applying = false
      var output = String(applyStdout.text || "").trim()
      var error = String(applyStderr.text || "").trim()
      // Stale revisions, validation failures and Hyprland errors all arrive as
      // JSON on stdout alongside a non-zero status; keep the real message.
      if (output) {
        root.acceptApply(output)
        return
      }
      root.statusError = true
      root.statusText = error || (exitCode !== 0
        ? "Apply backend failed with exit code " + exitCode
        : "Apply backend returned no data")
    }
  }

  QtObject {
    id: confirmDialog
    property string title: ""
    property string message: ""
    property string actionText: "Continue"
    property var action: function() {}
    function open() { confirmPopup.open() }
  }

  FloatingWindow {
    id: window
    title: dirty ? "Autostart Editor — Unsaved changes" : "Autostart Editor"
    color: root.background
    // The manifest keeps this plugin loaded, so the window is instantiated at
    // shell start. Stay hidden until the host actually summons the panel.
    visible: false
    implicitWidth: 1050
    implicitHeight: 760
    minimumSize: Qt.size(620, 500)

    onVisibleChanged: {
      if (!visible && !root.closingFromHost && root.shell && typeof root.shell.hide === "function")
        root.shell.hide(root.pluginId)
    }

    Shortcut { sequence: "Escape"; onActivated: root.requestClose() }
    Shortcut { sequence: "Ctrl+R"; onActivated: root.refresh() }
    Shortcut { sequence: "Ctrl+Return"; onActivated: root.applyChanges() }

    ColumnLayout {
      anchors.fill: parent
      anchors.margins: Style.space(18)
      spacing: Style.space(14)

      RowLayout {
        Layout.fillWidth: true
        spacing: Style.space(12)

        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(2)
          Text {
            text: "Autostart Editor"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.iconLarge
            font.bold: true
          }
          Text {
            text: "Login applications and workspace placement"
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }

        Ui.Button {
          text: "Reload"
          iconText: "󰑓"
          bordered: true
          focusable: true
          enabled: !root.loading && !root.applying
          opacity: enabled ? 1 : 0.5
          onClicked: root.refresh()
        }
        Ui.Button {
          text: root.applying ? "Applying…" : "Apply"
          iconText: root.applying ? "󰔟" : "󰄬"
          selected: root.dirty
          bordered: true
          focusable: true
          enabled: root.dirty && !root.loading && !root.applying
          opacity: enabled ? 1 : 0.5
          onClicked: root.applyChanges()
        }
        Ui.Button {
          id: closeButton
          text: "Close"
          bordered: true
          focusable: true
          onClicked: root.requestClose()
        }
      }

      Ui.PanelSeparator { Layout.fillWidth: true; foreground: root.foreground }

      QQC.SplitView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        orientation: Qt.Vertical

        Item {
          QQC.SplitView.preferredHeight: Math.min(300, window.height * 0.38)
          QQC.SplitView.minimumHeight: 150

          ColumnLayout {
            anchors.fill: parent
            spacing: Style.space(8)

            SectionHeading {
              Layout.fillWidth: true
              title: "Workspaces"
              detail: root.monitorNames.length
                ? "Connected: " + root.monitorNames.join(", ")
                : "No connected monitor discovered"
              error: root.monitorNames.length === 0
            }

            QQC.ScrollView {
              Layout.fillWidth: true
              Layout.fillHeight: true
              clip: true
              QQC.ScrollBar.horizontal.policy: QQC.ScrollBar.AlwaysOff

              ColumnLayout {
                width: parent.width
                spacing: Style.space(4)

                Repeater {
                  model: workspaceModel

                  delegate: Ui.BorderSurface {
                    required property int index
                    required property var model
                    Layout.fillWidth: true
                    implicitHeight: workspaceRow.implicitHeight + Style.space(12)
                    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, index % 2 ? 0.025 : 0.045)
                    radius: Style.cornerRadius

                    GridLayout {
                      id: workspaceRow
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      anchors.margins: Style.space(6)
                      // The four capped columns need ~640px including gaps and
                      // margins, so only fall back to two below that. The fifth
                      // column is a spacer that soaks up any leftover width.
                      columns: window.width >= 680 ? 5 : 2
                      columnSpacing: Style.space(10)
                      rowSpacing: Style.space(6)

                      Text {
                        text: "Workspace " + model.id
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        font.bold: true
                        Layout.preferredWidth: 110
                      }
                      Ui.TextField {
                        Layout.fillWidth: true
                        // Workspace names are short labels, not sentences.
                        Layout.maximumWidth: 220
                        placeholderText: "Optional name"
                        text: model.name
                        onEditingFinished: root.updateWorkspace(index, "name", text.trim())
                      }
                      Ui.Dropdown {
                        // Fits the longest DRM connector names, e.g. HDMI-A-1.
                        Layout.preferredWidth: 150
                        showLabel: false
                        options: root.monitorNames
                        value: model.monitor
                        onChanged: function(value) { root.updateWorkspace(index, "monitor", value) }
                      }
                      Ui.Button {
                        Layout.preferredWidth: 120
                        text: model.default ? "Default" : "Set default"
                        iconText: model.default ? "󰄬" : ""
                        selected: model.default
                        bordered: true
                        focusable: true
                        onClicked: root.updateWorkspace(index, "default", !model.default)
                      }
                      Item { Layout.fillWidth: true }
                    }
                  }
                }
              }
            }
          }
        }

        Item {
          QQC.SplitView.fillHeight: true
          QQC.SplitView.minimumHeight: 220

          ColumnLayout {
            anchors.fill: parent
            anchors.topMargin: Style.space(8)
            spacing: Style.space(8)

            RowLayout {
              Layout.fillWidth: true
              SectionHeading {
                Layout.fillWidth: true
                title: "Login applications"
                detail: applicationModel.count + " configured"
              }
              QQC.ComboBox {
                id: installedPicker
                Layout.preferredWidth: Math.min(300, window.width * 0.32)
                model: root.installedApplications
                textRole: "name"
                enabled: root.installedApplications.length > 0
              }
              Ui.Button {
                text: "Add"
                iconText: "󰐕"
                bordered: true
                focusable: true
                enabled: installedPicker.enabled
                opacity: enabled ? 1 : 0.5
                onClicked: root.addSelectedApplication()
              }
            }

            Item {
              Layout.fillWidth: true
              Layout.fillHeight: true

            ListView {
              id: applicationList
              anchors.fill: parent
              clip: true
              spacing: Style.space(6)
              model: applicationModel
              QQC.ScrollBar.vertical: QQC.ScrollBar {}

              delegate: Ui.BorderSurface {
                    required property int index
                    required property var model
                    width: ListView.view.width
                    height: implicitHeight
                    implicitHeight: applicationLayout.implicitHeight + Style.space(16)
                    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.04)
                    radius: Style.cornerRadius

                    ColumnLayout {
                      id: applicationLayout
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      anchors.margins: Style.space(8)
                      spacing: Style.space(8)

                      RowLayout {
                        Layout.fillWidth: true
                        spacing: Style.space(10)

                        ColumnLayout {
                          Layout.fillWidth: false
                          Layout.preferredWidth: 130
                          spacing: Style.space(4)
                          Text {
                            text: "Placement"
                            color: root.muted
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                            font.bold: true
                          }
                          Ui.Button {
                            Layout.fillWidth: true
                            text: model.placeInWorkspace === false ? "Anywhere" : "Workspace"
                            selected: model.placeInWorkspace !== false
                            bordered: true
                            focusable: true
                            onClicked: root.updateApplication(index, "placeInWorkspace", model.placeInWorkspace === false)
                          }
                        }

                        ColumnLayout {
                          // Fixed rather than content-sized, so the command
                          // field starts at the same place in every card.
                          // Fits the longest desktop-entry names, e.g.
                          // "LibreOffice Impress".
                          Layout.fillWidth: false
                          Layout.preferredWidth: 220
                          spacing: Style.space(4)
                          Text {
                            text: "Name"
                            color: root.muted
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                            font.bold: true
                          }
                          Ui.TextField {
                            Layout.fillWidth: true
                            text: model.name
                            onEditingFinished: root.updateApplication(index, "name", text.trim())
                          }
                        }

                        ColumnLayout {
                          Layout.fillWidth: true
                          // The one genuinely long field — paths plus
                          // arguments — so it takes the rest of the row.
                          Layout.maximumWidth: 640
                          spacing: Style.space(4)
                          Text {
                            text: "Command"
                            color: root.muted
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                            font.bold: true
                          }
                          Ui.TextField {
                            Layout.fillWidth: true
                            text: model.command
                            enabled: model.autostartSource !== "external"
                            opacity: enabled ? 1 : 0.65
                            onEditingFinished: root.updateApplication(index, "command", text.trim())
                          }
                        }

                        Item { Layout.fillWidth: true }
                      }
                      RowLayout {
                        Layout.fillWidth: true
                        spacing: Style.space(10)

                        ColumnLayout {
                          // Values are workspace numbers, at most two digits.
                          // A nested layout fills by default; these controls
                          // are fixed width, so they opt out.
                          Layout.fillWidth: false
                          Layout.preferredWidth: 90
                          spacing: Style.space(4)
                          Text {
                            text: "Workspace"
                            color: root.muted
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                            font.bold: true
                          }
                          Ui.Dropdown {
                            Layout.fillWidth: true
                            showLabel: false
                            options: root.workspaceIds
                            value: String(model.workspace)
                            enabled: model.placeInWorkspace !== false
                            opacity: enabled ? 1 : 0.45
                            onChanged: function(value) { root.updateApplication(index, "workspace", Number(value)) }
                          }
                        }

                        ColumnLayout {
                          // Three digits at most, plus the stepper controls.
                          Layout.fillWidth: false
                          Layout.preferredWidth: 110
                          spacing: Style.space(4)
                          Text {
                            text: "Delay (seconds)"
                            color: root.muted
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                            font.bold: true
                          }
                          Ui.NumberField {
                            Layout.fillWidth: true
                            label: ""
                            from: 0
                            to: 300
                            value: Number(model.delay)
                            enabled: model.autostartSource !== "external"
                            opacity: enabled ? 1 : 0.65
                            fieldWidth: width
                            onModified: function(value) { root.updateApplication(index, "delay", value) }
                          }
                        }

                        Item { Layout.fillWidth: true }

                        ColumnLayout {
                          // "External" is the widest label, plus the remove button.
                          Layout.fillWidth: false
                          Layout.preferredWidth: 130
                          spacing: Style.space(4)
                          Text {
                            text: "Launch"
                            color: root.muted
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                            font.bold: true
                          }
                          RowLayout {
                            Layout.fillWidth: true
                            spacing: Style.space(6)
                            Ui.Button {
                              Layout.fillWidth: true
                              text: model.autostartSource === "external" ? "External" : (model.enabled ? "On" : "Off")
                              selected: model.enabled
                              bordered: true
                              focusable: true
                              enabled: model.autostartSource !== "external"
                              opacity: enabled ? 1 : 0.65
                              onClicked: root.updateApplication(index, "enabled", !model.enabled)
                            }
                            Ui.Button {
                              iconText: "󰆴"
                              tooltipText: "Remove " + model.name
                              bordered: true
                              focusable: true
                              onClicked: root.removeApplication(index)
                            }
                          }
                        }
                      }
                    }
              }
            }

            // A sibling of the ListView, not a child: inside it the message
            // would land in the flickable content item, which has no height
            // while the model is empty.
            Text {
              anchors.centerIn: parent
              width: Math.max(0, parent.width - Style.space(48))
              visible: applicationModel.count === 0 && !root.loading
              text: "No login applications configured. Choose an installed application above."
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              horizontalAlignment: Text.AlignHCenter
              wrapMode: Text.WordWrap
            }
            }
          }
        }
      }

      Ui.PanelSeparator { Layout.fillWidth: true; foreground: root.foreground }

      RowLayout {
        Layout.fillWidth: true
        QQC.BusyIndicator {
          visible: root.loading || root.applying
          running: visible
          Layout.preferredWidth: 20
          Layout.preferredHeight: 20
        }
        Text {
          Layout.fillWidth: true
          text: root.statusText
          color: root.statusError ? root.urgent : root.muted
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          elide: Text.ElideRight
        }
        Text {
          visible: root.dirty
          text: "UNSAVED"
          color: root.accent
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          font.bold: true
          font.letterSpacing: 1
        }
      }
    }

    QQC.Popup {
      id: confirmPopup
      anchors.centerIn: parent
      width: Math.min(440, window.width - Style.space(48))
      modal: true
      focus: true
      closePolicy: QQC.Popup.CloseOnEscape

      background: Ui.BorderSurface {
        color: Color.popups.background
        radius: Style.cornerRadius
        borderSpec: Border.localOrSurfaceSpec("popups", "border", Color.popups.border, Color.popups.border, Style.normalBorderWidth)
      }

      contentItem: ColumnLayout {
        spacing: Style.space(14)
        Text {
          Layout.fillWidth: true
          text: confirmDialog.title
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
        }
        Text {
          Layout.fillWidth: true
          text: confirmDialog.message
          color: root.muted
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          wrapMode: Text.WordWrap
        }
        RowLayout {
          Layout.alignment: Qt.AlignRight
          Ui.Button { text: "Cancel"; bordered: true; focusable: true; onClicked: confirmPopup.close() }
          Ui.Button {
            text: confirmDialog.actionText
            selected: true
            bordered: true
            focusable: true
            onClicked: {
              confirmPopup.close()
              confirmDialog.action()
            }
          }
        }
      }
    }
  }

  component SectionHeading: RowLayout {
    property string title: ""
    property string detail: ""
    property bool error: false
    spacing: Style.space(10)
    Text {
      text: parent.title
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.subtitle
      font.bold: true
    }
    Text {
      Layout.fillWidth: true
      text: parent.detail
      color: parent.error ? root.urgent : root.muted
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }
  }

}
