import QtQuick
import qs.Ui

// Bar launcher for the Autostart Editor panel.
//
// The widget owns no editor state. It asks the shell to toggle the panel, so
// the host keeps sole ownership of the panel's lifecycle and a click here
// behaves exactly like a keybinding or `omarchy-shell shell toggle`.
BarWidget {
  id: root
  moduleName: "cruise42.autostart-editor"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // nf-md-rocket_launch, in the same Material Design range as the panel's
    // own icons so it inherits the bar font rather than shipping one.
    text: "󱓞"
    tooltipText: "Autostart Editor"
    onPressed: function(pressedButton) {
      if (!root.bar || pressedButton !== Qt.LeftButton) return
      root.bar.run("omarchy-shell shell toggle " + root.moduleName + " '{}'")
    }
  }
}
