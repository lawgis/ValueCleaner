from qgis.PyQt.QtCore import Qt

try:
    from qgis.PyQt.QtCore import QVariant
except ImportError:  # newer Qt builds
    QVariant = None
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QCheckBox, QDialog,
    QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QRadioButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout)
from qgis.core import QgsProject, QgsVectorLayer

from . import cleaner_core, layer_ops


def _enum(owner, scoped, plain):
    """Qt5/Qt6 compatible enum lookup, e.g. Qt.CheckState.Checked vs Qt.Checked."""
    try:
        return getattr(getattr(owner, scoped[0]), scoped[1])
    except AttributeError:
        return getattr(owner, plain)


CHECKED = _enum(Qt, ("CheckState", "Checked"), "Checked")
UNCHECKED = _enum(Qt, ("CheckState", "Unchecked"), "Unchecked")
FLAG_CHECKABLE = _enum(Qt, ("ItemFlag", "ItemIsUserCheckable"), "ItemIsUserCheckable")
FLAG_EDITABLE = _enum(Qt, ("ItemFlag", "ItemIsEditable"), "ItemIsEditable")
WAIT_CURSOR = _enum(Qt, ("CursorShape", "WaitCursor"), "WaitCursor")
NO_EDIT = _enum(QAbstractItemView, ("EditTrigger", "NoEditTriggers"), "NoEditTriggers")
ROLE_CLUSTER = _enum(Qt, ("ItemDataRole", "UserRole"), "UserRole")
STRETCH = _enum(QHeaderView, ("ResizeMode", "Stretch"), "Stretch")
TO_CONTENTS = _enum(QHeaderView, ("ResizeMode", "ResizeToContents"), "ResizeToContents")
YES = _enum(QMessageBox, ("StandardButton", "Yes"), "Yes")

LARGE_FIELD_WARNING = 5000  # distinct values


# QVariant.String exists on Qt5 builds only, so look it up without assuming it.
_STRING_TYPE = getattr(QVariant, "String", None) if QVariant is not None else None


def _is_text_field(field):
    if _STRING_TYPE is not None and field.type() == _STRING_TYPE:
        return True
    name = field.typeName().lower()
    return any(word in name for word in ("char", "text", "string"))


def _show(value):
    """Wrap in quotes so stray leading/trailing spaces are visible."""
    return "“%s”" % value


class CleanerDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.clusters = []
        self.layer = None
        self.setWindowTitle("Value Cleaner")
        self.setMinimumWidth(560)
        self.setMinimumHeight(560)
        self._build_ui()
        self._fill_layers()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)

        intro = QLabel(
            "Finds spellings of the same value that differ by case, spacing, "
            "punctuation or small typos (for example “Kaduna”, "
            "“kaduna ” and “Kadunna”) so you can fix them in one go.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.layer_combo = QComboBox()
        self.layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        form.addRow("Layer", self.layer_combo)
        self.field_combo = QComboBox()
        self.field_combo.currentIndexChanged.connect(self._clear_results)
        form.addRow("Text field", self.field_combo)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.60, 1.00)
        self.threshold.setSingleStep(0.01)
        self.threshold.setDecimals(2)
        self.threshold.setValue(cleaner_core.DEFAULT_THRESHOLD)
        self.threshold.setToolTip(
            "How alike two spellings must be to be grouped. Higher = stricter.\n"
            "1.00 only groups case, spacing and punctuation differences.\n"
            "Values containing different numbers (Zone 1 / Zone 2) are never grouped.")
        self.threshold.valueChanged.connect(self._clear_results)
        form.addRow("Similarity (0.60–1.00)", self.threshold)
        root.addLayout(form)

        self.selected_only = QCheckBox("Only selected features")
        self.selected_only.stateChanged.connect(self._clear_results)
        root.addWidget(self.selected_only)

        self.find_button = QPushButton("Find similar values")
        self.find_button.clicked.connect(self._find)
        root.addWidget(self.find_button)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Group / spelling found", "Features", "Will become"])
        self.tree.setEditTriggers(NO_EDIT)
        self.tree.setRootIsDecorated(True)
        self.tree.header().setSectionResizeMode(0, STRETCH)
        self.tree.header().setSectionResizeMode(1, TO_CONTENTS)
        self.tree.header().setSectionResizeMode(2, STRETCH)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.tree, 1)

        hint = QLabel("Untick a group to leave it alone. Double-click “Will become” "
                      "to type a different correct spelling.")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.summary = QLabel("")
        root.addWidget(self.summary)

        self.new_layer_radio = QRadioButton("Save to a new layer (original untouched)")
        self.in_place_radio = QRadioButton(
            "Edit this layer (one undo step; save layer edits to keep)")
        self.new_layer_radio.setChecked(True)
        root.addWidget(self.new_layer_radio)
        root.addWidget(self.in_place_radio)

        buttons = QHBoxLayout()
        buttons.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply)
        buttons.addWidget(close_button)
        buttons.addWidget(self.apply_button)
        root.addLayout(buttons)

    def _fill_layers(self):
        active = self.iface.activeLayer()
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        chosen = 0
        position = 0
        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsVectorLayer):
                continue
            if not any(_is_text_field(f) for f in layer.fields()):
                continue
            self.layer_combo.addItem(layer.name(), layer.id())
            if active is not None and layer.id() == active.id():
                chosen = position
            position += 1
        self.layer_combo.setCurrentIndex(chosen if position else -1)
        self.layer_combo.blockSignals(False)
        self._on_layer_changed()

    # ------------------------------------------------------------ handlers
    def _on_layer_changed(self, *_):
        layer_id = self.layer_combo.currentData()
        self.layer = QgsProject.instance().mapLayer(layer_id) if layer_id else None
        self.field_combo.blockSignals(True)
        self.field_combo.clear()
        if self.layer is not None:
            for field in self.layer.fields():
                if _is_text_field(field):
                    self.field_combo.addItem(field.name())
            has_selection = self.layer.selectedFeatureCount() > 0
            self.selected_only.setEnabled(has_selection)
            if not has_selection:
                self.selected_only.setChecked(False)
        self.field_combo.blockSignals(False)
        self._clear_results()

    def _clear_results(self, *_):
        self.tree.blockSignals(True)
        self.tree.clear()
        self.tree.blockSignals(False)
        self.clusters = []
        self.apply_button.setEnabled(False)
        self.summary.setText("")

    def _find(self):
        if self.layer is None or not self.field_combo.currentText():
            QMessageBox.information(self, "Value Cleaner",
                                    "Choose a layer and a text field first.")
            return
        field = self.field_combo.currentText()
        QApplication.setOverrideCursor(WAIT_CURSOR)
        try:
            counts = layer_ops.collect_value_counts(
                self.layer, field, self.selected_only.isChecked())
        finally:
            QApplication.restoreOverrideCursor()
        if not counts:
            self._clear_results()
            self.summary.setText("No text values found in that field.")
            return
        if len(counts) > LARGE_FIELD_WARNING:
            answer = QMessageBox.question(
                self, "Value Cleaner",
                "This field has %d distinct values. Matching may take a minute. Continue?"
                % len(counts))
            if answer != YES:
                return
        QApplication.setOverrideCursor(WAIT_CURSOR)
        try:
            self.clusters = cleaner_core.build_clusters(counts, self.threshold.value())
        finally:
            QApplication.restoreOverrideCursor()
        self._populate(len(counts))

    def _populate(self, distinct_count):
        self.tree.blockSignals(True)
        self.tree.clear()
        for number, cluster in enumerate(self.clusters, 1):
            top = QTreeWidgetItem(self.tree)
            top.setFlags(top.flags() | FLAG_CHECKABLE | FLAG_EDITABLE)
            top.setCheckState(0, CHECKED)
            top.setText(0, "Group %d — %d spellings" % (number, len(cluster.variants)))
            top.setText(1, str(cluster.total))
            top.setText(2, cluster.canonical)
            top.setData(0, ROLE_CLUSTER, number - 1)
            for raw, count in cluster.variants:
                child = QTreeWidgetItem(top)
                child.setText(0, _show(raw))
                child.setText(1, str(count))
                child.setData(0, ROLE_CLUSTER, raw)
            self._refresh_children(top)
            top.setExpanded(True)
        self.tree.blockSignals(False)
        if not self.clusters:
            self.summary.setText(
                "Checked %d distinct values: nothing similar found. "
                "Try a lower similarity to be less strict." % distinct_count)
            self.apply_button.setEnabled(False)
        else:
            self._update_summary()

    def _on_double_click(self, item, column):
        if item.parent() is None and column == 2:
            self.tree.editItem(item, 2)

    def _on_item_changed(self, item, column):
        if item.parent() is not None:
            return
        if column == 2:
            self.tree.blockSignals(True)
            item.setText(2, item.text(2).strip())
            self._refresh_children(item)
            self.tree.blockSignals(False)
        self._update_summary()

    def _refresh_children(self, top):
        target = top.text(2).strip()
        for index in range(top.childCount()):
            child = top.child(index)
            raw = child.data(0, ROLE_CLUSTER)
            child.setText(2, "— (unchanged)" if raw == target else "→ " + target)

    def _collect_mapping(self):
        """Build {old: new} from the ticked groups. Returns (mapping, empty_group_found)."""
        mapping = {}
        has_empty = False
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            if top.checkState(0) != CHECKED:
                continue
            target = top.text(2).strip()
            if not target:
                has_empty = True
                continue
            cluster = self.clusters[top.data(0, ROLE_CLUSTER)]
            mapping.update(cluster.changes(target))
        return mapping, has_empty

    def _update_summary(self):
        mapping, has_empty = self._collect_mapping()
        features = 0
        for cluster in self.clusters:
            for raw, count in cluster.variants:
                if raw in mapping:
                    features += count
        text = "%d groups found. Ticked groups would rewrite %d different values in %d features." % (
            len(self.clusters), len(mapping), features)
        if has_empty:
            text += " A ticked group has an empty “Will become” and is skipped."
        self.summary.setText(text)
        self.apply_button.setEnabled(bool(mapping))

    # --------------------------------------------------------------- apply
    def _apply(self):
        mapping, _ = self._collect_mapping()
        if not mapping or self.layer is None:
            return
        field = self.field_combo.currentText()
        only_selected = self.selected_only.isChecked()
        QApplication.setOverrideCursor(WAIT_CURSOR)
        try:
            if self.in_place_radio.isChecked():
                changed = layer_ops.apply_in_place(self.layer, field, mapping, only_selected)
                self.iface.messageBar().pushSuccess(
                    "Value Cleaner",
                    "%d features updated in “%s”. The layer is in edit mode: "
                    "save layer edits to keep them, or press Ctrl+Z to undo." % (
                        changed, self.layer.name()))
            else:
                copy, changed = layer_ops.make_cleaned_copy(
                    self.layer, field, mapping, only_selected)
                QgsProject.instance().addMapLayer(copy)
                self.iface.messageBar().pushSuccess(
                    "Value Cleaner",
                    "%d features corrected in the new layer “%s”. It is a temporary "
                    "layer: right-click it and choose Make Permanent to keep it." % (
                        changed, copy.name()))
        except Exception as error:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Value Cleaner", "Could not apply the changes:\n%s" % error)
            return
        QApplication.restoreOverrideCursor()
        self.accept()
