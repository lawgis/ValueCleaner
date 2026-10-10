"""Reading values from a layer and writing the corrections back."""
from qgis.core import (NULL, QgsFeature, QgsFeatureRequest, QgsField,
                       QgsVectorDataProvider, QgsVectorLayer)


def _request_for(layer, idx, only_selected, with_geometry=False):
    request = QgsFeatureRequest()
    request.setSubsetOfAttributes([idx])
    if only_selected:
        request.setFilterFids(list(layer.selectedFeatureIds()))
    if not with_geometry:
        flag = getattr(QgsFeatureRequest, "NoGeometry", None)
        if flag is None:
            flag = QgsFeatureRequest.Flag.NoGeometry
        request.setFlags(flag)
    return request


def collect_value_counts(layer, field_name, only_selected=False):
    """Return {value: number_of_features} for the non-empty values of a text field."""
    idx = layer.fields().indexOf(field_name)
    counts = {}
    for feature in layer.getFeatures(_request_for(layer, idx, only_selected)):
        value = feature.attribute(idx)
        if value is None or value == NULL:
            continue
        if not isinstance(value, str):
            value = str(value)
        if not value.strip():
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def can_edit_in_place(layer):
    """True if the layer's data source accepts attribute edits."""
    try:
        caps = layer.dataProvider().capabilities()
        return bool(caps & QgsVectorDataProvider.ChangeAttributeValues)
    except Exception:
        return True


def apply_in_place(layer, field_name, mapping, only_selected=False):
    """Rewrite values through the layer's edit buffer.

    The whole change is one undo step (Ctrl+Z) and nothing is saved to disk
    until the user saves the layer edits. Returns the number of features changed.
    """
    if not can_edit_in_place(layer):
        raise RuntimeError(
            "This layer's data source cannot be edited. "
            "Use 'Save to a new layer' instead.")
    idx = layer.fields().indexOf(field_name)
    if not layer.isEditable() and not layer.startEditing():
        raise RuntimeError("Could not switch the layer into edit mode.")
    changed = 0
    layer.beginEditCommand("Value Cleaner")
    try:
        for feature in layer.getFeatures(_request_for(layer, idx, only_selected)):
            value = feature.attribute(idx)
            if isinstance(value, str) and value in mapping:
                layer.changeAttributeValue(feature.id(), idx, mapping[value])
                changed += 1
        layer.endEditCommand()
    except Exception:
        layer.destroyEditCommand()
        raise
    return changed


def make_cleaned_copy(layer, field_name, mapping, only_selected=False):
    """Build a temporary copy of the layer with the values corrected.

    The original layer is not touched. With only_selected, the copy still holds
    every feature but only the selected ones are corrected.
    Returns (new_layer, number_of_features_changed).
    """
    from qgis.core import QgsWkbTypes

    idx = layer.fields().indexOf(field_name)
    if layer.isSpatial():
        uri = "%s?crs=%s" % (QgsWkbTypes.displayString(layer.wkbType()),
                              layer.crs().authid())
    else:
        uri = "None"
    copy = QgsVectorLayer(uri, "%s (cleaned)" % layer.name(), "memory")
    provider = copy.dataProvider()
    provider.addAttributes([QgsField(f) for f in layer.fields()])
    copy.updateFields()

    selected = set(layer.selectedFeatureIds()) if only_selected else None
    changed = 0
    batch = []
    for feature in layer.getFeatures():
        attributes = feature.attributes()
        value = attributes[idx]
        if (isinstance(value, str) and value in mapping
                and (selected is None or feature.id() in selected)):
            attributes[idx] = mapping[value]
            changed += 1
        new_feature = QgsFeature(copy.fields())
        new_feature.setGeometry(feature.geometry())
        new_feature.setAttributes(attributes)
        batch.append(new_feature)
        if len(batch) >= 5000:
            provider.addFeatures(batch)
            batch = []
    if batch:
        provider.addFeatures(batch)
    copy.updateExtents()
    return copy, changed
