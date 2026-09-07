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
  property var workspaces: []
  property var applications: []
  property var installedApplications: []

  readonly property string pluginId: "cruise42.autostart-editor"
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

  function cloneObject(value) {
    return JSON.parse(JSON.stringify(value))
  }

  function markDirty(message) {
    dirty = true
    statusError = false
    statusText = message || "Unsaved changes"
  }

  function updateWorkspace(index, key, value) {
    var copy = cloneObject(workspaces)
    copy[index][key] = value
    if (key === "default" && value) {
      for (var i = 0; i < copy.length; i++) {
        if (i !== index && copy[i].monitor === copy[index].monitor)
          copy[i].default = false
      }
    }
    if (key === "monitor" && copy[index].default) {
      for (var j = 0; j < copy.length; j++) {
        if (j !== index && copy[j].monitor === value) copy[j].default = false
      }
    }
    workspaces = copy
    markDirty()
  }

  function updateApplication(index, key, value) {
    var copy = cloneObject(applications)
    copy[index][key] = value
    applications = copy
    markDirty()
  }

  function addSelectedApplication() {
    var index = installedPicker.currentIndex
    if (index < 0 || index >= installedApplications.length) return
    var selected = installedApplications[index]
    for (var i = 0; i < applications.length; i++) {
      if (applications[i].desktopId === selected.desktopId) {
        statusError = true
        statusText = selected.name + " is already configured"
        return
      }
    }
    var workspace = workspaces.length ? Number(workspaces[0].id) : 1
    var copy = cloneObject(applications)
    copy.push({
      desktopId: selected.desktopId,
      name: selected.name,
      windowClass: selected.windowClass,
      matchType: "class",
      ruleOptions: {},
      command: selected.command,
      workspace: workspace,
      delay: 0,
      enabled: true,
      autostartSource: "plugin"
    })
    applications = copy
    markDirty("Added " + selected.name)
  }

  function removeApplication(index) {
    var copy = cloneObject(applications)
    var removed = copy.splice(index, 1)
    applications = copy
    markDirty(removed.length ? "Removed " + removed[0].name : "Unsaved changes")
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
      focusDelayMs = Number(data.focusDelayMs || 8000)
      monitors = data.monitors || []
      monitorNames = monitors.map(function(item) { return String(item.name) })
      workspaces = data.workspaces || []
      applications = data.applications || []
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
      workspaces: workspaces,
      applications: applications
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
      statusError = false
      statusText = data.message || "Changes applied"
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
      if (exitCode !== 0 || !output) {
        root.statusError = true
        root.statusText = error || "Inspection backend returned no data"
        return
      }
      root.acceptInspection(output)
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
      if (exitCode !== 0 || !output) {
        root.statusError = true
        root.statusText = error || "Apply backend returned no data"
        return
      }
      root.acceptApply(output)
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
                  model: root.workspaces

                  delegate: Ui.BorderSurface {
                    required property int index
                    required property var modelData
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
                      columns: window.width >= 760 ? 4 : 2
                      columnSpacing: Style.space(10)
                      rowSpacing: Style.space(6)

                      Text {
                        text: "Workspace " + modelData.id
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        font.bold: true
                        Layout.preferredWidth: 110
                      }
                      Ui.TextField {
                        Layout.fillWidth: true
                        placeholderText: "Optional name"
                        text: modelData.name
                        onEditingFinished: root.updateWorkspace(index, "name", text.trim())
                      }
                      Ui.Dropdown {
                        Layout.preferredWidth: 180
                        showLabel: false
                        options: root.monitorNames
                        value: modelData.monitor
                        onChanged: function(value) { root.updateWorkspace(index, "monitor", value) }
                      }
                      Ui.Button {
                        Layout.preferredWidth: 120
                        text: modelData.default ? "Default" : "Set default"
                        iconText: modelData.default ? "󰄬" : ""
                        selected: modelData.default
                        bordered: true
                        focusable: true
                        onClicked: root.updateWorkspace(index, "default", !modelData.default)
                      }
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
                detail: root.applications.length + " configured"
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

            QQC.ScrollView {
              Layout.fillWidth: true
              Layout.fillHeight: true
              clip: true
              QQC.ScrollBar.horizontal.policy: QQC.ScrollBar.AlwaysOff

              ColumnLayout {
                width: parent.width
                spacing: Style.space(6)

                Text {
                  visible: root.applications.length === 0 && !root.loading
                  Layout.fillWidth: true
                  Layout.topMargin: Style.space(24)
                  text: "No login applications configured. Choose an installed application above."
                  color: root.muted
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  horizontalAlignment: Text.AlignHCenter
                  wrapMode: Text.WordWrap
                }

                Repeater {
                  model: root.applications

                  delegate: Ui.BorderSurface {
                    required property int index
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: applicationGrid.implicitHeight + Style.space(16)
                    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.04)
                    radius: Style.cornerRadius

                    GridLayout {
                      id: applicationGrid
                      readonly property bool compact: width < 1080
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      anchors.margins: Style.space(8)
                      columns: compact ? 2 : 6
                      columnSpacing: Style.space(10)
                      rowSpacing: Style.space(8)

                      FieldColumn {
                        Layout.fillWidth: true
                        Layout.minimumWidth: applicationGrid.compact ? 0 : 150
                        Layout.preferredWidth: 200
                        label: "Name"
                        content: Ui.TextField {
                          width: parent.width
                          text: modelData.name
                          onEditingFinished: root.updateApplication(index, "name", text.trim())
                        }
                      }
                      FieldColumn {
                        Layout.fillWidth: true
                        Layout.minimumWidth: applicationGrid.compact ? 0 : 150
                        Layout.preferredWidth: 200
                        label: modelData.matchType === "title" ? "App / initial title" : "App / window class"
                        content: Text {
                          width: parent.width
                          height: Style.spacing.controlHeight
                          text: modelData.windowClass
                          color: root.muted
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.bodySmall
                          verticalAlignment: Text.AlignVCenter
                          elide: Text.ElideMiddle
                        }
                      }
                      FieldColumn {
                        Layout.fillWidth: true
                        Layout.minimumWidth: applicationGrid.compact ? 0 : 260
                        Layout.preferredWidth: 380
                        Layout.columnSpan: applicationGrid.compact ? 2 : 1
                        label: "Command"
                        content: Ui.TextField {
                          width: parent.width
                          text: modelData.command
                          enabled: modelData.autostartSource !== "external"
                          opacity: enabled ? 1 : 0.65
                          onEditingFinished: root.updateApplication(index, "command", text.trim())
                        }
                      }
                      FieldColumn {
                        Layout.fillWidth: applicationGrid.compact
                        Layout.minimumWidth: applicationGrid.compact ? 0 : 115
                        Layout.preferredWidth: 125
                        label: "Workspace"
                        content: Ui.Dropdown {
                          width: parent.width
                          showLabel: false
                          options: root.workspaces.map(function(item) { return String(item.id) })
                          value: String(modelData.workspace)
                          onChanged: function(value) { root.updateApplication(index, "workspace", Number(value)) }
                        }
                      }
                      FieldColumn {
                        Layout.fillWidth: applicationGrid.compact
                        Layout.minimumWidth: applicationGrid.compact ? 0 : 90
                        Layout.preferredWidth: 100
                        label: "Delay"
                        content: Ui.NumberField {
                          width: parent.width
                          label: ""
                          from: 0
                          to: 300
                          value: Number(modelData.delay)
                          enabled: modelData.autostartSource !== "external"
                          opacity: enabled ? 1 : 0.65
                          fieldWidth: parent.width
                          onModified: function(value) { root.updateApplication(index, "delay", value) }
                        }
                      }
                      ColumnLayout {
                        Layout.fillWidth: applicationGrid.compact
                        Layout.minimumWidth: applicationGrid.compact ? 0 : 135
                        Layout.preferredWidth: 145
                        Layout.columnSpan: applicationGrid.compact ? 2 : 1
                        spacing: Style.space(4)
                        Text {
                          text: "Launch"
                          color: root.muted
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                          font.bold: true
                        }
                        RowLayout {
                          Ui.Button {
                            text: modelData.autostartSource === "external" ? "External" : (modelData.enabled ? "On" : "Off")
                            selected: modelData.enabled
                            bordered: true
                            focusable: true
                            enabled: modelData.autostartSource !== "external"
                            opacity: enabled ? 1 : 0.65
                            onClicked: root.updateApplication(index, "enabled", !modelData.enabled)
                          }
                          Ui.Button {
                            iconText: "󰆴"
                            tooltipText: "Remove " + modelData.name
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

  component FieldColumn: ColumnLayout {
    property string label: ""
    property Component content
    spacing: Style.space(4)
    Text {
      text: parent.label
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
    }
    Loader {
      Layout.fillWidth: true
      Layout.minimumWidth: 0
      sourceComponent: parent.content
    }
  }
}
