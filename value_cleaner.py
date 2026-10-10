import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .cleaner_dialog import CleanerDialog


class ValueCleaner:
    MENU = "&Value Cleaner"

    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "Value Cleaner", self.iface.mainWindow())
        self.action.setToolTip("Find and fix inconsistent spellings in a text field")
        self.action.triggered.connect(self.run)
        self.iface.addPluginToVectorMenu(self.MENU, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginVectorMenu(self.MENU, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action = None

    def run(self, checked=False):
        dialog = CleanerDialog(self.iface, self.iface.mainWindow())
        try:
            dialog.exec()
        except AttributeError:
            dialog.exec_()
