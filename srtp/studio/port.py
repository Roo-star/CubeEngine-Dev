"""Widget-independent value port for the existing conversion/training controllers.

The controllers keep their existing stable field names; no Dear PyGui context is
created. Only the small dynamic source-parameter builder needs widget factories.
"""
from contextlib import contextmanager
from PySide6.QtWidgets import (QWidget, QLabel, QLineEdit, QPlainTextEdit, QSpinBox,
    QDoubleSpinBox, QComboBox, QProgressBar, QAbstractButton, QVBoxLayout, QHBoxLayout, QPushButton)
from PySide6.QtCore import QSignalBlocker, Signal, QObject

class FieldPort(QObject):
    changed = Signal(str, object)
    def __init__(self):
        super().__init__()
        self.values, self.widgets, self.config = {}, {}, {}
        self.parents = []

    def bind(self, tag, widget, default=None):
        self.widgets[tag] = widget
        if default is not None:
            self.set_value(tag, default)
        return widget

    def get_value(self, tag):
        w = self.widgets.get(tag)
        if isinstance(w, (QSpinBox, QDoubleSpinBox)) or getattr(w,'numeric',False): return w.value()
        if isinstance(w, QLineEdit): return w.text()
        if isinstance(w, QPlainTextEdit): return w.toPlainText()
        if isinstance(w, QComboBox): return w.currentText()
        return self.values.get(tag)

    def set_value(self, tag, value):
        self.values[tag] = value
        w = self.widgets.get(tag)
        if w is not None:
            blocker = QSignalBlocker(w)
            if isinstance(w, (QSpinBox, QDoubleSpinBox)) or getattr(w,'numeric',False): w.setValue(value)
            elif isinstance(w, (QLineEdit, QLabel, QAbstractButton)): w.setText(str(value if value is not None else ''))
            elif isinstance(w, QPlainTextEdit):
                if w.toPlainText() != str(value or ''): w.setPlainText(str(value or ''))
            elif isinstance(w, QComboBox): w.setCurrentText(str(value or ''))
            elif isinstance(w, QProgressBar): w.setValue(round(float(value) * 1000))
            del blocker
        self.changed.emit(tag, value)

    def configure_item(self, tag, **values):
        self.config.setdefault(tag, {}).update(values)
        w = self.widgets.get(tag)
        if w is None: return
        if 'enabled' in values: w.setEnabled(values['enabled'])
        if 'show' in values: w.setVisible(values['show'])
        if 'label' in values and isinstance(w, QAbstractButton): w.setText(values['label'])
        if 'items' in values and isinstance(w, QComboBox):
            blocker = QSignalBlocker(w)
            w.clear(); w.addItems(values['items'])
            del blocker

    def does_item_exist(self, tag):
        return True  # Nonvisual fields remain observable values, not fake widgets.

    def show_item(self, tag):
        if tag in self.widgets: self.widgets[tag].show()

    def hide_item(self, tag):
        if tag in self.widgets: self.widgets[tag].hide()

    def delete_item(self, tag, children_only=False):
        w = self.widgets.get(tag)
        if w is None or w.layout() is None: return
        while w.layout().count():
            item = w.layout().takeAt(0)
            if item.widget():
                child = item.widget()
                for key, bound in list(self.widgets.items()):
                    if bound is child or child.isAncestorOf(bound): self.widgets.pop(key)
                child.deleteLater()

    @contextmanager
    def group(self, parent=None, horizontal=False, **kwargs):
        w = QWidget()
        layout = QHBoxLayout(w) if horizontal else QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(5)
        host = self.widgets.get(parent) if parent else self.parents[-1] if self.parents else None
        if host: host.layout().addWidget(w)
        self.parents.append(w)
        try: yield w
        finally: self.parents.pop()

    def _add(self, w, tag=None, parent=None):
        host = self.widgets.get(parent) if parent else self.parents[-1] if self.parents else None
        if host: host.layout().addWidget(w)
        if tag: self.bind(tag, w)
        return w

    def add_text(self, value='', tag=None, **kwargs):
        w = QLabel(str(value)); w.setWordWrap(True)
        return self._add(w, tag, kwargs.get('parent'))

    def add_input_text(self, default_value='', tag=None, **kwargs):
        return self._add(QLineEdit(str(default_value)), tag, kwargs.get('parent'))

    def add_button(self, label='', callback=None, tag=None, **kwargs):
        w = QPushButton(label)
        if callback: w.clicked.connect(lambda: callback())
        return self._add(w, tag, kwargs.get('parent'))

    def add_spacer(self, height=5, **kwargs):
        if self.parents: self.parents[-1].layout().addSpacing(height)
