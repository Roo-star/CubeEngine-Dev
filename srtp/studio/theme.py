"""Shared colour, spacing and state treatment for the complete desktop UI."""
STYLE = '''
QWidget { color: #e7e9f0; font-family: "Segoe UI"; font-size: 15px; }
QMainWindow, QDialog { background: #111419; }
QFrame#rail { background: #181c22; border-right: 1px solid #2b303a; }
QFrame#sidebar { background: #191d24; border: 1px solid #2b3039; border-radius: 12px; }
QFrame#card { background: #1d2129; border: 1px solid #303641; border-radius: 12px; }
QFrame#training { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #282532,stop:1 #1d222a); border: 1px solid #464055; border-radius: 12px; }
QFrame#scene { background: #0d1015; border: 1px solid #353c48; border-radius: 10px; }
QLabel { background: transparent; border: none; }
QLabel[role="muted"] { color: #a5aebf; font-size: 14px; }
QLabel[role="section"] { color: #969faf; font-size: 13px; font-weight: 600; letter-spacing: 1px; }
QLabel[role="title"] { font-size: 25px; font-weight: 600; color: #f4f4f7; }
QLabel[role="heading"] { font-size: 18px; font-weight: 600; }
QLabel[role="badge"] { color: #9fe0c4; background: #253e36; border: 1px solid #345447; border-radius: 5px; padding: 3px 8px; font-size: 13px; }
QLabel[role="badge"][state="pending"] { color: #aab4c4; background: #29313c; border-color: #3a4758; }
QLabel[role="badge"][state="unavailable"] { color: #e0c59a; background: #3b3328; border-color: #5c4b36; }
QPushButton, QToolButton { background: #292f39; border: 1px solid #3b424f; border-radius: 7px; padding: 8px 13px; min-height: 22px; color: #e3e7ef; }
QPushButton:hover, QToolButton:hover { background: #363c49; border-color: #6c7487; }
QPushButton:pressed, QToolButton:pressed { background: #424557; }
QPushButton:focus, QToolButton:focus { border-color: #bca5ff; }
QPushButton:disabled, QToolButton:disabled { color: #777f8e; background: #22262e; border-color: #303640; }
QPushButton[role="primary"] { color: #171220; background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #c4aeff,stop:1 #a58aed); border: 1px solid #d0bfff; font-weight: 600; }
QPushButton[role="primary"]:hover { background: #ccbaff; }
QPushButton[role="primary"]:disabled { color: #8e869e; background: #3a334b; border-color: #4b435d; }
QPushButton[role="quiet"], QToolButton[role="quiet"] { background: transparent; border-color: transparent; }
QPushButton[role="quiet"]:hover, QToolButton[role="quiet"]:hover { background: #2e3440; border-color: #414957; }
QPushButton:checked, QToolButton:checked { background: #3a334d; color: #dbcaff; border-color: #66587f; }
QPushButton[role="danger"] { background: #45313a; color: #ffbbc4; border-color: #78505b; }
QPushButton[role="danger"]:disabled { background: #242831; color: #777f8e; border-color: #333b48; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #151920; border: 1px solid #3b4351; border-radius: 6px; padding: 7px 9px; selection-background-color: #635582; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #b69cf5; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: #7e899a; background: #1b2028; border-color: #303744; }
QLineEdit[role="axis"] { padding: 6px 7px; font-family: "Consolas"; font-size: 15px; }
QLineEdit[role="number"] { font-family: "Consolas"; font-size: 15px; }
QAbstractSpinBox, QAbstractSpinBox QLineEdit { font-family: "Consolas"; font-size: 15px; }
QAbstractSpinBox QLineEdit { border: none; padding: 0; background: #151920; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView { background: #242b35; selection-background-color: #4b4162; padding: 6px; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button { width: 18px; }
QTreeWidget, QListWidget { background: transparent; border: none; outline: 0; }
QTreeWidget::item, QListWidget::item { padding: 7px 5px; border: 1px solid transparent; border-radius: 5px; }
QTreeWidget::item:hover, QListWidget::item:hover { background: #282f3a; }
QTreeWidget::item:selected, QListWidget::item:selected { background: #393247; border: 1px solid #5d5074; color: #f1e9ff; }
QHeaderView::section { background: #20262e; border: none; padding: 6px; color: #a5aebf; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab { background: transparent; color: #a7b0c1; padding: 10px 16px; margin-right: 5px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #e3d6ff; border-bottom-color: #b49bef; background: #292634; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 3px; }
QScrollBar::handle:vertical { background: #505968; min-height: 24px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSplitter::handle { background: transparent; width: 8px; height: 8px; }
QSplitter::handle:hover { background: #534966; }
QProgressBar { background: #14181f; border: none; border-radius: 4px; max-height: 7px; min-height: 7px; }
QProgressBar::chunk { background: #b59bef; border-radius: 4px; }
QMenu { background: #222832; border: 1px solid #475060; padding: 7px; }
QMenu::item { padding: 8px 24px; border-radius: 5px; }
QMenu::item:selected { background: #41394f; }
QToolTip { color: #edf0f7; background: #323947; border: 1px solid #5b6678; padding: 7px; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 18px; height: 18px; }
'''
