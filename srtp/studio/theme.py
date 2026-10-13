"""Workbench V2 grayscale palette translated from the nine Figma wireframes."""

STYLE = '''
QWidget { color: #17191b; font-family: "Segoe UI"; font-size: 11px; }
QMainWindow, QDialog { background: #e9eaec; }
QWidget#workspaceBar { background: #17191b; }
QWidget#workspaceBar QLabel { color: #e9eaec; }
QFrame#chrome { background: #f4f4f3; border-bottom: 1px solid #d1d3d4; }
QFrame#sidebar { background: #1e2226; border: none; border-right: 1px solid #414449; border-radius: 0; }
QFrame#sidebar QLabel { color: #e9eaec; }
QFrame#sidebar QLabel[role="section"], QFrame#sidebar QLabel[role="muted"] { color: #aeb3b8; }
QFrame#rules { background: #1e2226; border: none; border-left: 1px solid #414449; border-radius: 0; }
QFrame#rules QLabel { color: #e9eaec; }
QFrame#rules QLabel[role="muted"] { color: #aeb3b8; }
QFrame#card, QFrame#training { background: #fafaf9; border: 1px solid #d1d3d4; border-radius: 0; }
QFrame#scene { background: #c8cccf; border: none; border-radius: 0; }
QFrame#trainingStage { background: #f4f4f3; border-top: 1px solid #d1d3d4; }
QWidget#statusBar { background: #17191b; }
QWidget#statusBar QLabel { color: #e9eaec; }
QLabel { background: transparent; border: none; }
QLabel[role="title"] { font-size: 14px; font-weight: 600; }
QLabel[role="heading"] { font-size: 13px; font-weight: 600; }
QLabel[role="section"] { font-size: 10px; color: #686d73; font-weight: 600; }
QLabel[role="muted"] { color: #686d73; font-size: 11px; }
QLabel[role="badge"] { color: #17191b; background: #e1e2e3; border: 1px solid #c8cacc; border-radius: 2px; padding: 2px 5px; }
QLabel[role="badge"][state="eligible"] { color: #17191b; background: #d6d8da; }
QLabel[role="badge"][state="unavailable"] { color: #555a5f; background: #ececeb; }
QLabel[role="chromeBrand"] { color: #17191b; font-size: 12px; font-weight: 700; }
QLabel[role="chromeMenu"] { color: #555a5f; font-size: 10px; }
QToolButton[role="chromeMenu"] { background: transparent; border: none; padding: 0; color: #555a5f; }
QToolButton[role="chromeMenu"]::menu-indicator { image: none; }
QWidget#workspaceBar QLabel[role="badge"] { background: transparent; border: none; color: #b6babd; }
QPushButton, QToolButton { background: #ffffff; color: #17191b; border: 1px solid #d1d3d4; border-radius: 3px; padding: 4px 9px; }
QPushButton:hover, QToolButton:hover { background: #f0f0ef; border-color: #8b8f93; }
QPushButton:pressed, QToolButton:pressed { background: #e0e1e2; }
QPushButton:disabled, QToolButton:disabled { color: #9a9da1; background: #ececeb; border-color: #e0e1e2; }
QPushButton[role="primary"] { background: #17191b; color: #ffffff; border-color: #17191b; font-weight: 600; }
QPushButton[role="primary"]:hover { background: #34383c; }
QPushButton[role="primary"]:disabled { background: #a5a8ab; border-color: #a5a8ab; color: #ececeb; }
QPushButton[role="quiet"] { background: transparent; border-color: transparent; }
QPushButton[role="quiet"]:hover { background: #e3e4e4; border-color: #d1d3d4; }
QPushButton[role="workspaceTab"] { background: transparent; color: #b6babd; border: none; border-bottom: 2px solid transparent; border-radius: 0; }
QPushButton[role="workspaceTab"]:checked { background: #292d31; color: #ffffff; border-bottom-color: #ffffff; }
QPushButton[role="workspaceTab"]:hover { color: #ffffff; background: #292d31; }
QPushButton[role="danger"] { background: #ffffff; color: #17191b; border-color: #d1d3d4; }
QPushButton:checked { background: #d8dadc; color: #17191b; border-color: #8b8f93; }
QFrame#sidebar QPushButton { background: #292d31; border-color: #414449; color: #e9eaec; }
QFrame#sidebar QPushButton:hover { background: #383c40; }
QFrame#sidebar QPushButton[role="primary"] { background: #f3f3f2; color: #17191b; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #fafaf9; border: 1px solid #d1d3d4; border-radius: 3px; color: #17191b; padding: 4px 7px; selection-background-color: #b7bbbe; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #6d7276; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { color: #9a9da1; background: #ececeb; }
QFrame#sidebar QLineEdit, QFrame#sidebar QPlainTextEdit, QFrame#sidebar QComboBox { background: #292d31; color: #e9eaec; border-color: #414449; }
QLineEdit[role="axis"], QLineEdit[role="number"] { font-family: "Consolas"; font-size: 11px; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView { background: #fafaf9; color: #17191b; selection-background-color: #d8dadc; }
QTreeWidget, QListWidget { background: transparent; border: none; outline: 0; }
QTreeWidget::item, QListWidget::item { padding: 4px 3px; border: none; }
QTreeWidget::item:hover, QListWidget::item:hover { background: #d8dadc; }
QTreeWidget::item:selected, QListWidget::item:selected { background: #c8cbce; color: #17191b; }
QFrame#sidebar QTreeWidget::item { color: #e9eaec; }
QFrame#sidebar QTreeWidget::item:hover { background: #34383c; }
QFrame#sidebar QTreeWidget::item:selected { background: #414449; color: #ffffff; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab { background: transparent; color: #686d73; padding: 5px 10px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #17191b; background: #ffffff; border-bottom-color: #17191b; }
QFrame#rules QTabBar::tab { color: #b4b9bd; }
QFrame#rules QTabBar::tab:selected { background: #292d31; color: #ffffff; border-bottom-color: #ffffff; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 7px; }
QScrollBar::handle:vertical { background: #acafb1; min-height: 22px; border-radius: 3px; }
QSplitter::handle { background: #d1d3d4; width: 1px; height: 1px; }
QProgressBar { background: #e3e4e5; border: none; border-radius: 2px; max-height: 5px; min-height: 5px; }
QProgressBar::chunk { background: #17191b; border-radius: 2px; }
QMenu { background: #ffffff; border: 1px solid #d1d3d4; padding: 3px; }
QMenu::item { padding: 5px 18px; }
QMenu::item:selected { background: #e1e2e3; }
QToolTip { color: #ffffff; background: #292d31; border: 1px solid #414449; padding: 5px; }
QCheckBox { spacing: 5px; }
QCheckBox::indicator { width: 13px; height: 13px; }
'''
