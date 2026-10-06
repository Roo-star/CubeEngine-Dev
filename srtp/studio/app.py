"""CubeEngine Studio — real embedded scenes, hierarchy, assets and experiments."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QSize, QTimer, QSignalBlocker, QUrl, QLocale, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut, QDesktopServices, QPixmap, QPalette, QColor, QIcon, QDoubleValidator
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton,
    QToolButton, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout, QSplitter, QStackedWidget,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem, QLineEdit,
    QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QScrollArea,
    QProgressBar, QFileDialog, QDialog, QMenu, QAbstractItemView, QSizePolicy)

from .icons import icon
from .theme import STYLE
from .port import FieldPort
from .viewport import EmbeddedViewport, ROOT
from .demos import DEMOS
from ..workbench import SrtpWorkbench, REFERENCE_GAMES
from ..training.config import TrainingConfig
from ..training.session import default_python


class NumberField(QLineEdit):
    """Plain numeric entry avoids accidental mouse-wheel changes while scrolling."""
    numeric=True
    valueChanged=Signal(object)
    def __init__(self,integer=False,minimum=0,maximum=1000000,decimals=2):
        super().__init__()
        self.integer,self.decimals=integer,0 if integer else decimals
        self.last_value=0
        validator=QDoubleValidator(minimum,maximum,self.decimals,self)
        locale=QLocale.c(); locale.setNumberOptions(QLocale.RejectGroupSeparator)
        validator.setLocale(locale); validator.setNotation(QDoubleValidator.StandardNotation)
        self.setValidator(validator)
        self.setAlignment(Qt.AlignRight); self.setProperty('role','number')
        self.setValue(self.last_value)
        self.editingFinished.connect(lambda:self.setValue(self.value()))
    def value(self):
        value=float(self.text()) if self.hasAcceptableInput() else self.last_value
        return int(value) if self.integer else value
    def setValue(self,value):
        changed=self.last_value!=value
        self.last_value=int(value) if self.integer else float(value)
        text=f'{self.last_value:.{self.decimals}f}'
        if self.text()!=text:self.setText(text)
        if changed:self.valueChanged.emit(self.last_value)


class CoordinateField(NumberField):
    def __init__(self,scale=False):
        super().__init__(minimum=.01 if scale else -10000,maximum=10000)
        self.setProperty('role','axis'); self.setValue(1 if scale else 0)

def box(parent=None, horizontal=False, margins=0, spacing=10):
    w = QWidget(parent)
    layout = QHBoxLayout(w) if horizontal else QVBoxLayout(w)
    layout.setContentsMargins(margins, margins, margins, margins); layout.setSpacing(spacing)
    return w, layout

def label(text='', role=None, wrap=False):
    w = QLabel(text); w.setWordWrap(wrap)
    if role: w.setProperty('role', role)
    return w

def button(text, glyph, callback, role=None, tooltip=None, compact=False):
    w = QPushButton(text)
    if glyph: w.setIcon(icon(glyph, '#241a35' if role == 'primary' else '#bdc5d4'))
    w.setIconSize(QSize(19, 19)); w.setCursor(Qt.PointingHandCursor)
    w.setMinimumHeight(38)
    if compact: w.setFixedSize(38, 38)
    if role: w.setProperty('role', role)
    if tooltip: w.setToolTip(tooltip)
    w.setAccessibleName(tooltip or text)
    if callback: w.clicked.connect(lambda checked=False: callback())
    return w

def card(title=None, glyph=None, name='card'):
    w = QFrame(); w.setObjectName(name)
    layout = QVBoxLayout(w); layout.setContentsMargins(17, 16, 17, 16); layout.setSpacing(12)
    if title:
        row, line = box(horizontal=True)
        if glyph:
            symbol = QLabel(); symbol.setPixmap(icon(glyph).pixmap(21, 21)); line.addWidget(symbol)
        line.addWidget(label(title, 'heading')); line.addStretch()
        layout.addWidget(row)
    return w, layout

class Studio(QMainWindow):
    def __init__(self, project=None, source=None):
        super().__init__()
        self.setWindowTitle('CubeEngine Studio')
        self.setWindowIcon(icon('cube', '#c3adff'))
        self.setMinimumSize(1100, 680)
        self.port = FieldPort()
        self.c = SrtpWorkbench(self.port, asynchronous_jobs=True)
        self.c.studio = self
        self.state, self.dirty = None, False
        self.manifest, self.loaded_manifest, self.package_key = None, None, None
        self.source_identity, self.source_targets = None, {}
        self.scene_requested=False
        self.demo_selected=False
        self.nodes, self.assets, self.scene_data = {}, {}, {}
        self.selected, self.pending_selection = None, None
        self.editing_properties, self.active_view = False, 'scene'
        self.last_status, self.console_lines, self.dialogs = '', [], {}
        self.last_scene_revision, self.last_tree_ids = None, ()
        self.closing = False
        self.check_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='studio core check')
        self.check_future=None
        self.port.values.update({'srtp_reference_selector':next(iter(REFERENCE_GAMES)),
            'srtp_preview_mode':'Transformed 3D', 'srtp_llm_intent':'', 'srtp_llm_manifest_path':''})
        self.build()
        self.build_dialogs()
        self.port.changed.connect(self.field_changed)
        self.timer = QTimer(self); self.timer.setInterval(80); self.timer.timeout.connect(self.poll)
        self.timer.start()
        for key, callback in (('Ctrl+O', self.import_source), ('Ctrl+S', self.save_draft), ('F5', self.toggle_play),
                              ('Ctrl+Z',lambda:self.edit_history('undo')),('Ctrl+Y',lambda:self.edit_history('redo')),
                              ('Ctrl+Shift+O', self.attach_target)):
            shortcut = QShortcut(QKeySequence(key), self); shortcut.activated.connect(callback)
        if project:
            def initial_project():
                if source:self.load_source(source)
                self.c.open_project_manifest(Path(project))
            QTimer.singleShot(150,initial_project)
        elif source: QTimer.singleShot(150, lambda:self.load_source(source))

    def build(self):
        root, columns = box(horizontal=True, margins=0, spacing=0); self.setCentralWidget(root)
        rail = QFrame(); rail.setObjectName('rail'); rail.setFixedWidth(66)
        rail_layout = QVBoxLayout(rail); rail_layout.setContentsMargins(12, 18, 12, 14); rail_layout.setSpacing(13)
        brand = QLabel(); brand.setPixmap(icon('cube', '#c7b1ff', 32).pixmap(32, 32)); brand.setAlignment(Qt.AlignCenter)
        rail_layout.addWidget(brand); rail_layout.addSpacing(22)
        for glyph, tip, callback in [('folder','匯入遊戲 · Ctrl+O',self.import_menu),
            ('layers','場景物件',lambda:self.sidebar.setVisible(not self.sidebar.isVisible())),
            ('image','資產瀏覽器',lambda:self.toggle_bottom(0)),
            ('brain','AI 訓練設定',lambda:self.show_dialog('training')),
            ('chart','實驗與結果',lambda:self.show_dialog('runs'))]:
            b = button('', glyph, callback, 'quiet', tip, True)
            rail_layout.addWidget(b)
        rail_layout.addStretch()
        rail_layout.addWidget(button('', 'terminal', lambda:self.toggle_bottom(1), 'quiet', '診斷與事件', True))
        rail_layout.addWidget(button('', 'sliders', lambda:self.show_dialog('conversion'), 'quiet', '轉換設定', True))
        rail_layout.addWidget(button('', 'help', lambda:self.show_dialog('help'), 'quiet', '操作指南', True))
        columns.addWidget(rail)
        workspace, vertical = box(margins=16, spacing=14); columns.addWidget(workspace, 1)
        header, line = box(horizontal=True, spacing=14)
        titles, title_layout = box(spacing=2)
        title_layout.addWidget(label('CUBEENGINE  /  WORKSPACE', 'section'))
        self.project_title = label('開始你的空間創作', 'title'); title_layout.addWidget(self.project_title)
        self.project_title.setMinimumWidth(0)
        self.project_title.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        line.addWidget(titles, 1)
        self.project_badge = label('本機工作區', 'badge'); line.addWidget(self.project_badge)
        line.addWidget(button('匯入', 'folder', self.import_menu))
        line.addWidget(button('空間轉換', 'layers', lambda:self.show_dialog('conversion'), 'primary'))
        line.addWidget(button('', 'save', self.save_draft, tooltip='儲存場景佈局草稿 · Ctrl+S', compact=True))
        vertical.addWidget(header)
        self.splitter = QSplitter(Qt.Horizontal); self.splitter.setChildrenCollapsible(False)
        self.sidebar, left = card(name='sidebar'); self.sidebar.setMinimumWidth(190)
        self.sidebar.setMaximumWidth(340)
        left.addWidget(label('切換範例並運行', 'section'))
        self.example_selector = QComboBox()
        self.example_selector.setAccessibleName('切換範例並運行')
        self.example_selector.addItem('選擇內建遊戲…', None)
        for title, path in REFERENCE_GAMES.items():
            self.example_selector.addItem(title.split(' · ')[0], str(path))
        self.example_selector.activated.connect(self.select_reference)
        left.addWidget(self.example_selector)
        self.demo_menu=QMenu(self)
        self.demo_actions=[]
        for title,path in DEMOS:
            self.demo_actions.append(self.demo_menu.addAction(icon('cube'),title,
                lambda checked=False,p=path:self.load_demo(p)))
        self.demo_button=button('3D Demo', 'cube', None, tooltip='開啟原有四款 3D Demo，不需選檔案')
        self.demo_button.setMenu(self.demo_menu); left.addWidget(self.demo_button)
        browser_title, browser_line = box(horizontal=True)
        browser_line.addWidget(label('場景物件', 'heading'), 1)
        browser_line.addWidget(button('', 'folder', self.project_menu, 'quiet', '目前專案 / 範例', True))
        left.addWidget(browser_title)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜尋物件…'); self.search.addAction(icon('search'), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.filter_tree); left.addWidget(self.search)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setIndentation(15)
        self.tree.setIconSize(QSize(17, 17)); self.tree.setUniformRowHeights(True)
        self.tree.currentItemChanged.connect(self.tree_selection)
        left.addWidget(self.tree, 1)
        self.tree_note = label('載入場景後顯示真實物件', 'muted', True); left.addWidget(self.tree_note)
        left.addWidget(button('掛載場景', 'plus', self.attach_target))
        self.splitter.addWidget(self.sidebar)
        center, middle = box(spacing=10); center.setMinimumWidth(370)
        toolbar, tools = box(horizontal=True, spacing=5)
        self.view_buttons = {}
        for key, text, glyph in [('scene','場景','cube'),('game','試玩','play'),('source','原作 2D','image')]:
            b = button(text, glyph, lambda k=key:self.switch_view(k), 'quiet'); b.setCheckable(True)
            self.view_buttons[key] = b; tools.addWidget(b)
        self.view_buttons['scene'].setChecked(True)
        demo_tab=button('3D Demo','cube',lambda:self.switch_view('demo'),'quiet')
        demo_tab.setCheckable(True); demo_tab.hide(); tools.addWidget(demo_tab)
        self.view_buttons['demo']=demo_tab
        tools.addStretch()
        self.play_button = button('', 'play', self.toggle_play, tooltip='播放 / 停止 · F5', compact=True)
        self.pause_button = button('', 'pause', self.pause, tooltip='暫停 / 繼續', compact=True)
        self.pause_button.setCheckable(True)
        self.restart_button = button('', 'reset', self.restart, tooltip='重新開始', compact=True)
        tools.addWidget(self.play_button); tools.addWidget(self.pause_button); tools.addWidget(self.restart_button)
        middle.addWidget(toolbar)
        self.center_splitter = QSplitter(Qt.Vertical); self.center_splitter.setChildrenCollapsible(False)
        stage, stage_layout = box(spacing=0)
        self.views = QStackedWidget()
        self.target_view = EmbeddedViewport(); self.source_view = EmbeddedViewport(); self.demo_view=EmbeddedViewport()
        self.target_view.event.connect(self.scene_event); self.source_view.event.connect(self.source_event)
        self.target_view.log.connect(self.append_console); self.source_view.log.connect(self.append_console)
        self.demo_view.event.connect(self.demo_event); self.demo_view.log.connect(self.append_console)
        self.views.addWidget(self.target_view); self.views.addWidget(self.source_view); self.views.addWidget(self.demo_view)
        stage_layout.addWidget(self.views, 1)
        viewport_footer, footer = box(horizontal=True, margins=8, spacing=8)
        self.scene_hint = label('場景內選取物件 · 右鍵旋轉 · 滾輪縮放', 'muted'); footer.addWidget(self.scene_hint, 1)
        self.scene_hint.setMinimumWidth(0); self.scene_hint.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.layer_combo = QComboBox(); self.layer_combo.addItem('全部圖層'); self.layer_combo.setMaximumWidth(120)
        self.layer_combo.currentIndexChanged.connect(lambda index:self.target_view.send('layer',layer=None if index==0 else index-1))
        footer.addWidget(self.layer_combo)
        self.slice_button=button('', 'layers',lambda:self.target_view.send('slice'),'quiet','只顯示所選深度',True)
        self.slice_button.setCheckable(True); footer.addWidget(self.slice_button)
        self.undo_button=button('', 'reset',lambda:self.edit_history('undo'),'quiet','復原 · Ctrl+Z',True)
        self.redo_button=button('', 'arrow',lambda:self.edit_history('redo'),'quiet','重做 · Ctrl+Y',True)
        footer.addWidget(self.undo_button); footer.addWidget(self.redo_button)
        self.frame_button=button('', 'focus',lambda:(self.demo_view if self.active_view=='demo' else self.target_view).send('camera'), 'quiet', '框選整個場景', True)
        footer.addWidget(self.frame_button)
        stage_layout.addWidget(viewport_footer)
        self.center_splitter.addWidget(stage)
        self.bottom, bottom_layout = card(name='card'); self.bottom.setMinimumHeight(120)
        self.bottom_tabs = QTabWidget(); self.bottom_tabs.setDocumentMode(True)
        self.asset_list = QListWidget(); self.asset_list.setViewMode(QListWidget.IconMode)
        self.asset_list.setIconSize(QSize(32,32)); self.asset_list.setGridSize(QSize(135,80))
        self.asset_list.setResizeMode(QListWidget.Adjust); self.asset_list.setWordWrap(True)
        self.asset_list.itemClicked.connect(self.select_asset)
        self.console = QPlainTextEdit(); self.console.setReadOnly(True); self.console.setMaximumBlockCount(400)
        self.console.setStyleSheet('font-family:Consolas;font-size:14px;border:none;background:transparent;')
        self.bottom_tabs.addTab(self.asset_list, icon('image'), '資產')
        self.bottom_tabs.addTab(self.console, icon('terminal'), '事件與診斷')
        bottom_layout.addWidget(self.bottom_tabs); self.center_splitter.addWidget(self.bottom)
        self.center_splitter.setSizes([560,170]); middle.addWidget(self.center_splitter, 1)
        self.splitter.addWidget(center)
        self.right = QSplitter(Qt.Vertical); self.right.setMinimumWidth(310); self.right.setMaximumWidth(430)
        self.right.setChildrenCollapsible(False)
        self.inspector, prop_layout = card('屬性', 'sliders')
        prop_layout.setSpacing(8)
        self.inspector.setMinimumHeight(200)
        self.inspector_scroll = QScrollArea(); self.inspector_scroll.setWidgetResizable(True)
        content, props = box(spacing=8)
        self.selected_name = label('選取場景物件', 'heading', True); props.addWidget(self.selected_name)
        self.selected_type = label('在場景或左側物件樹中選取', 'muted', True); props.addWidget(self.selected_type)
        self.visible_check = QCheckBox('在場景中顯示'); self.visible_check.setChecked(True)
        self.visible_check.toggled.connect(self.change_visibility); props.addWidget(self.visible_check)
        self.transform_box, transform_layout = box(spacing=7)
        axis_row,axis_layout=box(horizontal=True,spacing=5)
        spacer=QWidget(); spacer.setFixedWidth(32); axis_layout.addWidget(spacer)
        for axis,tint in zip('XYZ',('#e4a2a9','#acd7b0','#99beee')):
            item=label(axis,'section'); item.setAlignment(Qt.AlignCenter); item.setStyleSheet('color:'+tint)
            axis_layout.addWidget(item,1)
        transform_layout.addWidget(axis_row)
        self.transform_fields = {}
        for prop, title in [('position','位置'),('rotation','旋轉'),('scale','縮放')]:
            row, fields = box(horizontal=True, spacing=5)
            title_label=label(title,'muted'); title_label.setFixedWidth(32); fields.addWidget(title_label)
            for axis in 'XYZ':
                value=CoordinateField(prop=='scale')
                value.setToolTip(title+' · '+axis+'（局部座標）'); value.setAccessibleName(title+' '+axis)
                value.setMinimumWidth(62)
                value.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Fixed)
                value.editingFinished.connect(self.apply_transform)
                self.transform_fields[prop,axis] = value; fields.addWidget(value,1)
            transform_layout.addWidget(row)
        props.addWidget(self.transform_box)
        row, fields=box(horizontal=True)
        self.focus_button=button('聚焦','focus',lambda:self.target_view.send('focus'))
        self.revert_button=button('還原草稿','reset',lambda:self.target_view.send('revert'))
        fields.addWidget(self.focus_button); fields.addWidget(self.revert_button); props.addWidget(row)
        self.draft_note=label('場景編輯獨立於已批准的遊戲規則。','muted',True); props.addWidget(self.draft_note)
        props.addStretch(); self.inspector_scroll.setWidget(content); prop_layout.addWidget(self.inspector_scroll)
        self.right.addWidget(self.inspector)
        self.build_training()
        self.right.addWidget(self.training_card); self.right.setSizes([440,340])
        self.splitter.addWidget(self.right); self.splitter.setSizes([220,760,340])
        self.splitter.setStretchFactor(0,0); self.splitter.setStretchFactor(1,1); self.splitter.setStretchFactor(2,0)
        vertical.addWidget(self.splitter,1)
        status, status_layout=box(horizontal=True)
        self.status_label=label('準備就緒 · 匯入來源或掛載場景開始','muted'); status_layout.addWidget(self.status_label,1)
        self.renderer_label=label('本機工作區','section'); status_layout.addWidget(self.renderer_label)
        vertical.addWidget(status)
        self.set_inspector_enabled(False)

    def build_training(self):
        panel=self.c.training_panel
        self.training_card, layout=card(name='training'); self.training_card.setMinimumHeight(335)
        layout.setSpacing(8)
        row, title=box(horizontal=True)
        symbol=QLabel(); symbol.setPixmap(icon('brain','#c3acff').pixmap(22,22)); title.addWidget(symbol)
        title.addWidget(label('AI 自對弈','heading'),1)
        title.addWidget(button('', 'sliders', lambda:self.show_dialog('training'),'quiet','訓練參數與輸出',True))
        layout.addWidget(row)
        self.eligibility=label('等待批准版本','badge'); self.eligibility.setWordWrap(True); layout.addWidget(self.eligibility)
        self.training_description=label('具備對手、且通過適配檢查的遊戲才可訓練。','muted',True)
        layout.addWidget(self.training_description)
        metrics, grid=box(horizontal=True,spacing=20)
        self.metric_labels={}
        for key,title in [('iteration','迭代'),('self_play_games','自對弈'),('arena_games','對決')]:
            column, items=box(spacing=2); value=label('—','title'); self.metric_labels[key]=value
            items.addWidget(value); items.addWidget(label(title,'muted')); grid.addWidget(column,1)
        layout.addWidget(metrics)
        progress=QProgressBar(); progress.setRange(0,1000); progress.setTextVisible(False)
        self.training_progress=progress
        self.port.bind('training_progress',progress,0); layout.addWidget(progress)
        self.training_status=label('本機運算 · 可安全取消','muted',True); layout.addWidget(self.training_status)
        layout.addStretch()
        actions, row=box(horizontal=True)
        self.train_start=button('開始試跑','play',panel.start,'primary')
        self.train_cancel=button('', 'stop',panel.cancel,'danger','安全取消訓練',True)
        self.port.bind('training_start',self.train_start); self.port.bind('training_cancel',self.train_cancel)
        self.train_start.setEnabled(False); self.train_cancel.setEnabled(False)
        row.addWidget(self.train_start,1); row.addWidget(self.train_cancel); layout.addWidget(actions)
        actions, row=box(horizontal=True)
        resume=button('續跑','clock',panel.resume_last,'quiet','選取上一個相容 checkpoint')
        self.port.bind('training_resume_last',resume); resume.setEnabled(False)
        row.addWidget(resume,1); row.addWidget(button('詳情與結果','chart',lambda:self.show_dialog('runs'),'quiet'),2)
        layout.addWidget(actions)

    def dialog(self, key, title, size=(760,600)):
        d=QDialog(self); d.setWindowTitle(title)
        area=QApplication.primaryScreen().availableGeometry()
        d.resize(min(size[0],area.width()-40),min(size[1],area.height()-60)); d.setModal(False)
        layout=QVBoxLayout(d); layout.setContentsMargins(24,22,24,22); layout.setSpacing(16)
        self.dialogs[key]=d
        layout.addWidget(label(title,'title'))
        return d,layout

    def build_dialogs(self):
        d,layout=self.dialog('training','AI 自對弈 · 實驗設定',(890,750))
        scroll=QScrollArea(); scroll.setWidgetResizable(True); content, items=box(spacing=14)
        items.addWidget(label('先完成短程試跑，再增加訓練規模。續跑的迭代數是額外追加輪次。','muted',True))
        form=QGridLayout(); form.setHorizontalSpacing(12); form.setVerticalSpacing(10)
        form.addWidget(label('試跑規模','heading'),0,0,1,2)
        form.addWidget(label('模型與執行','heading'),0,3,1,2)
        form.setColumnMinimumWidth(2,22)
        names={'iterations':'追加迭代','episodes':'每輪自對弈局數','simulations':'每步搜尋次數','epochs':'訓練 epochs',
               'arena_games':'新舊對決局數（偶數）','channels':'網路通道','batch_size':'Batch size','seed':'Seed',
               'learning_rate':'學習率','acceptance':'採用門檻','device':'裝置'}
        columns=[['iterations','episodes','simulations','arena_games','seed'],
                 ['epochs','channels','batch_size','learning_rate','acceptance','device']]
        for key,value in TrainingConfig().to_mapping().items():
            if isinstance(value,int):
                w=NumberField(integer=True)
            elif isinstance(value,float):
                w=NumberField(maximum=100,decimals=5)
            else:
                w=QComboBox(); w.addItems(['cpu','cuda'])
            self.port.bind('training_'+key,w,value)
            column=0 if key in columns[0] else 1; row=columns[column].index(key)+1
            form.addWidget(label(names.get(key,key)),row,column*3); form.addWidget(w,row,column*3+1)
        items.addLayout(form)
        for key,title,value,choose in [('output','輸出資料夾',str(ROOT/'.cubeengine_training'),True),
            ('resume','相容 checkpoint（空白表示新模型）','',True),('python','訓練 Python',default_python(),False)]:
            items.addWidget(label(title,'heading')); row, fields=box(horizontal=True)
            w=QLineEdit(); self.port.bind('training_'+key,w,value); fields.addWidget(w,1)
            if choose: fields.addWidget(button('', 'folder',lambda k=key:self.training_path(k),tooltip='選擇'+title,compact=True))
            items.addWidget(row)
        scroll.setWidget(content); layout.addWidget(scroll,1)
        layout.addWidget(button('完成設定','check',d.close,'primary'))
        d,layout=self.dialog('runs','實驗與結果',(850,700))
        self.run_reasons=label('','muted',True); layout.addWidget(self.run_reasons)
        self.run_metrics=label('尚無實驗','heading',True); layout.addWidget(self.run_metrics)
        self.port.bind('training_metrics',self.run_metrics)
        self.run_status=label('',wrap=True); self.port.bind('training_status',self.run_status); layout.addWidget(self.run_status)
        self.run_path=QLineEdit(); self.run_path.setReadOnly(True); self.port.bind('training_run_path',self.run_path); layout.addWidget(self.run_path)
        run_log=QPlainTextEdit(); run_log.setReadOnly(True); self.port.bind('training_log',run_log); layout.addWidget(run_log,1)
        row, buttons=box(horizontal=True)
        buttons.addWidget(button('開啟輸出','folder',self.c.training_panel.open_results))
        verify=button('重放驗證','check',self.c.training_panel.verify_results); verify.setEnabled(False)
        self.port.bind('training_verify',verify); buttons.addWidget(verify)
        buttons.addWidget(button('重新檢查資格','reset',self.c.training_panel.refresh)); layout.addWidget(row)
        d,layout=self.dialog('conversion','空間轉換與版本批准',(920,710))
        layout.addWidget(label('來源分析 → 批准來源 → 空間轉換 → 批准 Target','muted'))
        tabs=QTabWidget(); layout.addWidget(tabs,1)
        content, items=box(margins=12)
        items.addWidget(label('下次轉換尺寸','heading'))
        row, fields=box(horizontal=True)
        for axis in 'xyz':
            fields.addWidget(label(axis.upper(),'muted')); w=NumberField(integer=True,minimum=2,maximum=100); self.port.bind('srtp_target_'+axis,w,3)
            w.valueChanged.connect(self.c.apply_transform_target); fields.addWidget(w,1)
        items.addWidget(row); items.addWidget(label('你想如何改變玩法？','heading'))
        intent=QPlainTextEdit(); intent.setPlaceholderText('描述想探索的空間、視角或玩法…'); intent.setMaximumHeight(110)
        self.port.bind('srtp_llm_intent',intent,''); intent.textChanged.connect(self.c.intent_changed); items.addWidget(intent)
        actions, action_line=box(horizontal=True)
        self.compile_button=button('分析來源','code',self.c.compile_llm_source_to_ir)
        self.lift_button=button('生成空間版本','layers',self.c.run_spatial_lift)
        self.approve_button=button('批准候選','check',self.c.approve_pending_llm_manifest,'primary')
        self.cancel_conversion=button('', 'stop',self.c.cancel_conversion,'danger','取消轉換',True)
        for w in (self.compile_button,self.lift_button,self.approve_button,self.cancel_conversion): action_line.addWidget(w)
        items.addWidget(actions)
        items.addWidget(label('生成使用已配置的模型服務，會產生服務費用。','muted'))
        candidate=QLineEdit(); candidate.setPlaceholderText('候選 project.manifest.json'); self.port.bind('srtp_llm_manifest_path',candidate); items.addWidget(candidate)
        activity=QPlainTextEdit(); activity.setReadOnly(True); self.port.bind('srtp_core_activity',activity); items.addWidget(activity,1)
        tabs.addTab(content,'轉換與批准')
        parameters, param_layout=box(margins=12)
        scroll=QScrollArea(); scroll.setWidgetResizable(True)
        fields, _=box(spacing=15); self.port.bind('srtp_parameter_rows',fields); scroll.setWidget(fields); param_layout.addWidget(scroll)
        tabs.addTab(parameters,'來源參數')
        for title,tag in [('規則','srtp_core_rule_summary'),('來源證據','srtp_source_summary'),('診斷','srtp_diagnostics'),('交接資料','srtp_handoff')]:
            text=QPlainTextEdit(); text.setReadOnly(True); self.port.bind(tag,text); tabs.addTab(text,title)
        hidden=QWidget(); QVBoxLayout(hidden); self.port.bind('srtp_transform_rows',hidden)
        d,layout=self.dialog('diagnostics','版本與引擎檢查',(900,700))
        layout.addWidget(label('內建自測不代表目前遊戲已通過訓練資格；當前資格請看右下 AI 模組。','muted',True))
        row,actions=box(horizontal=True)
        actions.addWidget(button('Core 自測','check',lambda:self.core_check('gate')))
        actions.addWidget(button('9 API 自測','brain',lambda:self.core_check('ai')))
        actions.addWidget(button('產物資料夾','folder',self.open_bundle))
        layout.addWidget(row)
        tabs=QTabWidget();layout.addWidget(tabs,1);self.diagnostic_tabs=tabs
        for title,tag in [('版本／hash','srtp_core_session_summary'),('檢查結果','srtp_core_verification'),
                          ('分析規則','srtp_schema_output'),('最近規則結果','srtp_core_last_result')]:
            text=QPlainTextEdit();text.setReadOnly(True);self.port.bind(tag,text);tabs.addTab(text,title)
        d,layout=self.dialog('help','工作臺操作',(650,500))
        layout.addWidget(label('場景是實際引擎畫面','heading'))
        layout.addWidget(label('原有四款示範在左側「3D Demo」選單：貪吃蛇、掃雷、四子棋、2048。選擇後直接在中央試玩，不需 Manifest 或重新轉換。',wrap=True))
        layout.addWidget(label('在「場景」選取物件，以右側 XYZ 欄位調整佈局；「試玩」才執行遊戲規則。右鍵旋轉、滾輪縮放，聚焦可快速找到選中物件。',wrap=True))
        layout.addWidget(label('來源、規則與編輯草稿各有歸屬','heading'))
        layout.addWidget(label('左側「切換範例並運行」會直接啟動內建遊戲。自行匯入來源後，按播放才執行原作。原作 2D 支援 Pygame 與 Turtle；掛載已批准的空間版本後，才能切換 3D 場景及試玩。場景草稿可從匯入選單讀回。',wrap=True))
        layout.addWidget(label('快捷鍵','heading')); layout.addWidget(label('Ctrl+O 匯入  ·  Ctrl+Shift+O 掛載場景\nCtrl+S 儲存草稿  ·  F5 播放 / 停止',wrap=True)); layout.addStretch()

    def show_dialog(self,key):
        d=self.dialogs[key]; d.show(); d.raise_(); d.activateWindow()

    def training_path(self,key):
        value=QFileDialog.getExistingDirectory(self,'選擇輸出資料夾',str(ROOT)) if key=='output' else QFileDialog.getOpenFileName(self,'選擇相容 checkpoint',str(ROOT/'.cubeengine_training'),'Checkpoint (checkpoint.json)')[0]
        if value: self.port.set_value('training_'+key,value)

    def import_menu(self):
        menu=QMenu(self)
        menu.addAction(icon('file'),'匯入遊戲入口…',self.import_source)
        menu.addAction(icon('folder'),'匯入完整專案…',lambda:self.import_source(directory=True))
        menu.addSeparator(); menu.addAction(icon('cube'),'掛載已批准場景…',self.attach_target)
        menu.addAction(icon('file'),'開啟場景佈局草稿…',self.load_draft)
        menu.addSeparator();menu.addAction(icon('export'),'匯出來源分析套件…',self.export_analysis)
        menu.exec(self.cursor().pos())

    def project_menu(self):
        menu=QMenu(self); menu.addAction('開啟目前來源資料夾',self.open_source_folder)
        menu.addAction('版本與引擎檢查…',lambda:self.show_dialog('diagnostics'));menu.addAction('開啟已批准產物資料夾',self.open_bundle)
        menu.addSeparator()
        for title,path in REFERENCE_GAMES.items(): menu.addAction(icon('play'),title.split(' · ')[0]+' · 運行',lambda checked=False,p=path:self.load_source(p,run=True))
        menu.exec(self.cursor().pos())

    def import_source(self,directory=False):
        value=QFileDialog.getExistingDirectory(self,'選擇遊戲專案',str(ROOT)) if directory else QFileDialog.getOpenFileName(self,'選擇遊戲入口',str(ROOT),'Game source (*.py *.pyw *.html);;All files (*)')[0]
        if value: self.load_source(value)

    def select_reference(self,index):
        path=self.example_selector.itemData(index)
        if path:self.load_source(path,run=True)

    def load_source(self,path,run=False,restore_target=True):
        self.port.set_value('srtp_source_path',str(path)); self.c.import_source()
        package=self.c.package
        if not package:
            self.switch_view('source',start=False)
            self.source_view.failure(str(self.port.get_value('srtp_status')))
            return
        from ..llm_compiler_v1.evidence import source_package_hash
        self.source_identity=(str(package.entrypoint.resolve()),source_package_hash(package.llm_handoff()))
        index=self.example_selector.findData(str(package.entrypoint))
        self.example_selector.setCurrentIndex(max(0,index))
        previous=self.source_targets.get(self.source_identity)
        if restore_target and previous and previous.is_file():self.c.open_project_manifest(previous)
        if package.runtime.kind=='project_ir':
            self.switch_view('scene');return
        self.switch_view('source',start=run)
        self.scene_requested=False
        if not run:
            self.source_view.message.setText('原作已載入\n\n按上方播放，在此執行遊戲。')
        if not self.manifest:
            self.tree_note.setText('目前可玩原作 2D。\n掛載已批准的 3D 版本後，這裡會顯示場景物件。')

    def load_demo(self,path):
        # This is an explicit demo-library action, never a failed-IR fallback.
        self.load_source(path,restore_target=False)
        if not self.c.package:return
        self.demo_selected=True
        self.view_buttons['demo'].show()
        for key in ('scene','game'):self.view_buttons[key].hide()
        self.tree_note.setText('原有 3D Demo · 可直接試玩\n原作素材見下方資產。')
        self.selected_name.setText('3D Demo')
        self.selected_type.setText('原有示範版本')
        self.draft_note.setText('此視圖用於試玩。要編輯正式場景，請掛載已批准版本。')
        self.switch_view('demo')

    def attach_target(self):
        path=QFileDialog.getOpenFileName(self,'掛載已批准的 Project Manifest',str(ROOT),'Manifest (*.json)')[0]
        if path: self.c.open_project_manifest(Path(path))

    def source_changed(self):
        if not hasattr(self,'target_view'): return
        self.target_view.stop(); self.source_view.stop()
        self.demo_view.stop(); self.demo_selected=False
        self.view_buttons['demo'].hide()
        for key in ('scene','game'):self.view_buttons[key].show()
        self.loaded_manifest,self.manifest,self.state,self.package_key=None,None,None,None
        self.source_identity=None
        self.scene_requested=False
        self.nodes,self.assets,self.scene_data={}, {}, {}
        self.selected=None; self.tree.clear(); self.asset_list.clear(); self.last_tree_ids=()
        self.update_inspector(); self.tree_note.setText('掛載場景後顯示實際物件階層')
        self.undo_button.setEnabled(False); self.redo_button.setEnabled(False)
        self.slice_button.setEnabled(False)
        for key in ('scene','game'):
            self.view_buttons[key].setEnabled(False)
            self.view_buttons[key].setToolTip('先掛載這款遊戲已批准的 3D 場景；原作 2D 可直接運行。')

    def set_state(self,state):
        self.state=state
        previous=self.manifest
        core=self.c.core_controller
        if core and core.has_active_project:
            # The controller owns the exact approved path, never a filename guess.
            path=getattr(self.c,'_studio_manifest_path',None)
            if path: self.manifest=Path(path)
            elif self.c.package and self.c.package.runtime.kind=='project_ir': self.manifest=Path(self.c.package.entrypoint)
            if self.manifest:
                if self.demo_selected:
                    self.demo_view.stop(); self.demo_selected=False
                    self.view_buttons['demo'].hide()
                    for key in ('scene','game'):self.view_buttons[key].show()
                if self.manifest!=previous:self.scene_requested=True
                if self.source_identity:self.source_targets[self.source_identity]=self.manifest
                for key in ('scene','game'):
                    self.view_buttons[key].setEnabled(True); self.view_buttons[key].setToolTip('')
        self.dirty=True

    def open_scene(self):
        if not self.manifest: return
        self.loaded_manifest=self.manifest
        source_root=self.c.package.root if self.c.package else self.manifest.parent
        self.target_view.launch_project(self.manifest,source_root)
        self.status_label.setText('正在載入 Scene／Asset…')

    def switch_view(self,key,start=True):
        if key=='demo' and not self.demo_selected:return
        if key in ('scene','game') and not self.manifest:
            self.status_label.setText('尚未掛載已批准的 3D 場景，先在中央運行原作。')
            key='source'
        self.active_view=key
        for name,w in self.view_buttons.items(): w.setChecked(name==key)
        self.views.setCurrentWidget(self.demo_view if key=='demo' else self.source_view if key=='source' else self.target_view)
        for control in (self.layer_combo,self.slice_button,self.undo_button,self.redo_button):control.setVisible(key in ('scene','game'))
        self.frame_button.setVisible(key!='source')
        self.demo_view.send('pause',paused=key!='demo')
        if key=='source':
            self.target_view.send('pause',paused=True)
            self.scene_hint.setText('原作 2D · 原始程式直接在此執行')
            if start and not self.source_view.running: self.start_source()
            self.source_view.send('pause',paused=False)
        elif key=='demo':
            self.source_view.send('pause',paused=True); self.target_view.send('pause',paused=True)
            if start and not self.demo_view.running:self.demo_view.launch_demo(self.c.package)
            self.demo_view.send('pause',paused=False)
            self.scene_hint.setText('3D Demo · 原有示範 · 右鍵旋轉／滾輪縮放')
        else:
            self.source_view.send('pause',paused=True)
            # A running Source preview belongs to its exact bundle. Approval
            # of a new Target must replace it before routing gameplay input.
            if self.manifest and (not self.target_view.running or self.manifest != self.loaded_manifest):
                self.open_scene()
            self.target_view.send('mode',playing=key=='game'); self.target_view.send('pause',paused=False)
            self.scene_hint.setText('點選物件 · 右鍵旋轉 · 滾輪縮放' if key=='scene' else '遊戲執行中 · 輸入交由原規則判定')
        self.set_inspector_enabled(self.selected is not None and key=='scene')
        self.refresh_play_controls()

    def start_source(self):
        package=self.c.package
        if not package or package.runtime.kind=='project_ir':
            self.source_view.failure('請先匯入原始遊戲 source。已生成的 Manifest 不含原作執行入口。'); return
        self.source_view.launch_source(package)

    def toggle_play(self):
        if self.active_view=='demo':
            if self.demo_view.running:self.demo_view.stop()
            else:self.demo_view.launch_demo(self.c.package)
        elif self.active_view=='source':
            if self.source_view.running:self.source_view.stop()
            else:self.start_source()
        else:self.switch_view('scene' if self.active_view=='game' else 'game')
        self.refresh_play_controls()

    def pause(self):
        if self.active_view=='demo':self.demo_view.send('pause',paused=not self.demo_view.paused)
        elif self.active_view=='source':self.source_view.send('pause',paused=not self.source_view.paused)
        else:self.target_view.send('pause',paused=not self.scene_data.get('paused',False))

    def restart(self):
        if self.active_view=='demo':self.demo_view.send('reset')
        elif self.active_view=='source':self.start_source()
        else:self.target_view.send('reset')

    def refresh_play_controls(self):
        running=self.demo_view.running if self.active_view=='demo' else self.source_view.running if self.active_view=='source' else self.active_view=='game'
        self.play_button.setIcon(icon('stop' if running else 'play','#c8b5ff'))
        if self.active_view=='demo':
            self.pause_button.setEnabled(self.demo_view.ready); self.pause_button.setChecked(self.demo_view.paused)
            self.restart_button.setEnabled(self.demo_view.ready); return
        self.pause_button.setEnabled(self.source_view.ready if self.active_view=='source' else self.active_view=='game' and self.target_view.ready)
        self.pause_button.setChecked(self.source_view.paused if self.active_view=='source' else self.active_view=='game' and self.scene_data.get('paused',False))
        self.restart_button.setEnabled(self.source_view.running if self.active_view=='source' else self.target_view.ready)

    def scene_event(self,event):
        kind=event.get('event')
        if kind=='scene':
            first=not self.scene_data
            self.scene_data=event; self.nodes={n['id']:n for n in event['nodes']}; self.assets=event.get('assets',{})
            if first:
                self.target_view.send('mode',playing=self.active_view=='game')
                self.target_view.resize_renderer()
                dims=event.get('dimensions',[])
                block=QSignalBlocker(self.layer_combo); self.layer_combo.clear(); self.layer_combo.addItem('全部圖層')
                if len(dims)==3:self.layer_combo.addItems(['Z 層 '+str(i+1) for i in range(dims[2])])
                del block
                self.populate_assets()
            ids=tuple(self.nodes)
            if ids!=self.last_tree_ids:
                self.last_tree_ids=ids; self.populate_tree()
            selected=event.get('selected')
            self.selected=selected
            if selected in self.tree_items:
                block=QSignalBlocker(self.tree); self.tree.setCurrentItem(self.tree_items[selected]); del block
            self.update_inspector()
            self.renderer_label.setText('3D 場景 · 即時同步')
            state='場景編輯' if not event.get('playing') else ('暫停' if event.get('paused') else '試玩')
            detail=(str(len(self.nodes))+' 個物件') if not event.get('playing') else ('本局已結束' if event.get('terminal') else '已執行 '+str(event.get('revision',0))+' 步')
            self.status_label.setText(state+' · '+detail)
            self.refresh_play_controls()
            self.undo_button.setEnabled(self.active_view=='scene' and event.get('can_undo',False))
            self.redo_button.setEnabled(self.active_view=='scene' and event.get('can_redo',False))
            self.slice_button.setEnabled(len(event.get('dimensions',[]))==3)
            self.slice_button.setChecked(event.get('slice_only',False))
            block=QSignalBlocker(self.layer_combo)
            self.layer_combo.setCurrentIndex(0 if event.get('layer') is None else event['layer']+1)
            del block
        elif kind=='error':self.append_console(event['message']); self.status_label.setText(event['message'][:120])
        elif kind=='saved':self.status_label.setText('已儲存場景草稿：'+event['path'])
        elif kind=='loaded':self.status_label.setText('已載入相同版本的場景草稿')

    def source_event(self,event):
        if event['event']=='source_frame' and self.active_view=='source':self.status_label.setText('原作正在中央視窗執行'); self.renderer_label.setText('2D 原作 · 即時運行')
        elif event['event']=='source_state' and self.active_view=='source':self.status_label.setText('原作已暫停' if event['paused'] else '原作正在中央視窗執行')
        elif event['event']=='error':self.append_console(event['message']); self.status_label.setText(event['message'][:120])
        elif event['event']=='stopped' and self.active_view=='source':
            self.status_label.setText('原作已停止，按播放重新開始。' if not event['code'] else '原作無法執行，原因已顯示在中央。')
            self.renderer_label.setText('2D 原作 · 已停止')
        self.refresh_play_controls()

    def demo_event(self,event):
        if event['event'] in ('demo_frame','demo_state') and self.active_view=='demo':
            self.renderer_label.setText('3D Demo · 原有示範')
            self.status_label.setText('3D Demo 已暫停' if event['paused'] else '3D Demo 正在中央視窗執行')
            self.selected_type.setText(' × '.join(map(str,event['dimensions']))+' · '+str(event['object_count'])+' 個呈現物件')
        elif event['event']=='error':self.append_console(event['message']); self.status_label.setText(event['message'][:120])
        elif event['event']=='stopped' and self.active_view=='demo':self.status_label.setText('3D Demo 已停止，可按播放重開。')
        self.refresh_play_controls()

    def populate_tree(self):
        self.tree.clear(); self.tree_items={}
        scene_root=QTreeWidgetItem([self.c.package.title if self.c.package else 'Scene'])
        scene_root.setIcon(0,icon('cube','#c4acff')); self.tree.addTopLevelItem(scene_root)
        for key,node in self.nodes.items():
            name=node.get('name') or key.split(':')[-1]
            item=QTreeWidgetItem([name]); item.setData(0,Qt.UserRole,key); item.setToolTip(0,key)
            kind='image' if 'renderer' in node['components'] else 'layers' if not node['components'] else 'cube'
            item.setIcon(0,icon(kind)); self.tree_items[key]=item
        for key,node in self.nodes.items():
            parent=self.tree_items.get(node.get('parent'))
            if parent:parent.addChild(self.tree_items[key])
            else:scene_root.addChild(self.tree_items[key])
        self.tree.expandToDepth(0)
        self.tree_note.setText(str(len(self.nodes))+' 個 Scene 物件 · 與引擎同步')
        self.filter_tree(self.search.text())

    def filter_tree(self,text):
        query=text.lower().strip()
        for key,item in getattr(self,'tree_items',{}).items():item.setHidden(bool(query and query not in key.lower() and query not in item.text(0).lower()))
        if query:
            for item in getattr(self,'tree_items',{}).values():
                if not item.isHidden():
                    p=item.parent()
                    while p:p.setHidden(False); p.setExpanded(True); p=p.parent()

    def tree_selection(self,current,previous):
        if current:self.target_view.send('select',id=current.data(0,Qt.UserRole))

    def set_inspector_enabled(self,enabled):
        self.transform_box.setEnabled(enabled); self.visible_check.setEnabled(enabled)
        self.focus_button.setEnabled(self.selected is not None)
        self.revert_button.setEnabled(enabled and bool(self.scene_data.get('changes')))

    def update_inspector(self):
        node=self.nodes.get(self.selected)
        self.set_inspector_enabled(bool(node) and self.active_view=='scene')
        self.transform_box.setVisible(bool(node)); self.visible_check.setVisible(bool(node))
        self.focus_button.setVisible(bool(node)); self.revert_button.setVisible(bool(node))
        if not node:
            self.selected_name.setText('選取場景物件'); self.selected_type.setText('在場景或左側物件樹中選取')
            self.draft_note.setText('掛載場景後可調整物件位置、旋轉與縮放。')
            for (prop,axis),field in self.transform_fields.items():field.setValue(1 if prop=='scale' else 0)
            return
        self.selected_name.setText(node['name']); self.selected_type.setText(' · '.join(node['components']) or 'Group')
        self.editing_properties=True
        block=QSignalBlocker(self.visible_check); self.visible_check.setChecked(node['visible']); del block
        for (prop,axis),field in self.transform_fields.items():
            if not field.hasFocus():field.setValue(node[prop]['XYZ'.index(axis)])
        self.editing_properties=False
        changes=self.scene_data.get('changes',0)
        self.draft_note.setText(str(changes)+' 個物件有佈局草稿 · 試玩仍使用批准版本' if changes else 'XYZ 為物件局部座標。場景編輯不改寫遊戲規則。')

    def apply_transform(self):
        if self.editing_properties or not self.selected or self.active_view!='scene':return
        values={p:[self.transform_fields[p,a].value() for a in 'XYZ'] for p in ('position','rotation','scale')}
        for (p,a),field in self.transform_fields.items():field.setValue(values[p]['XYZ'.index(a)])
        self.target_view.send('transform',id=self.selected,**values)

    def change_visibility(self,value):
        if not self.editing_properties and self.selected and self.active_view=='scene':self.target_view.send('visibility',id=self.selected,visible=value)

    def populate_assets(self):
        self.asset_list.clear()
        for key,path in self.assets.items():
            p=Path(path); item=QListWidgetItem(icon('image' if p.suffix.lower() in ('.png','.jpg','.jpeg') else 'file'),key.split(':')[-1])
            if p.suffix.lower() in ('.png','.jpg','.jpeg'):item.setIcon(QIcon(QPixmap(str(p))))
            item.setData(Qt.UserRole,str(p)); item.setToolTip(key+'\n'+str(p)); self.asset_list.addItem(item)
        groups={}
        for node in self.nodes.values():
            if 'renderer' in node['components']:
                groups.setdefault(json.dumps(node.get('visual',{}),sort_keys=True),[]).append(node)
        for nodes in groups.values():
            node=nodes[0]; name=node['name'].split(' (')[0]
            item=QListWidgetItem(icon('cube','#b8a0ed',32),name+' ×'+str(len(nodes)))
            item.setData(Qt.UserRole,{'node':node['id']})
            item.setToolTip('點擊定位實際場景物件\n'+str(len(nodes))+' 個使用相同呈現的物件\n'+json.dumps(node.get('visual',{}),ensure_ascii=False))
            self.asset_list.addItem(item)

    def select_asset(self,item):
        value=item.data(Qt.UserRole)
        if isinstance(value,dict):self.target_view.send('select',id=value['node']); self.switch_view('scene')
        else:
            self.selected_name.setText(Path(value).name); self.selected_type.setText('Asset · '+str(Path(value).suffix)); self.set_inspector_enabled(False)
            self.transform_box.hide(); self.visible_check.hide(); self.focus_button.hide(); self.revert_button.hide()
            self.draft_note.setText(value)

    def save_draft(self):
        if not self.target_view.ready:self.status_label.setText('載入場景後才可儲存佈局草稿。'); return
        value=QFileDialog.getSaveFileName(self,'儲存場景佈局草稿',str(ROOT/'scene-layout.json'),'Scene draft (*.json)')[0]
        if value:self.target_view.send('export',path=value)

    def load_draft(self):
        if not self.target_view.ready:
            self.status_label.setText('先掛載草稿對應的場景版本。'); return
        value=QFileDialog.getOpenFileName(self,'開啟場景佈局草稿',str(ROOT),'Scene draft (*.json)')[0]
        if value:
            self.switch_view('scene'); self.target_view.send('import',path=value)

    def edit_history(self,op):
        # Text fields retain their normal text undo/redo shortcuts.
        focus=QApplication.focusWidget()
        if isinstance(focus,(QLineEdit,QPlainTextEdit)):
            getattr(focus,op)(); return
        if self.active_view=='scene':self.target_view.send(op)

    def toggle_bottom(self,index):
        self.bottom.setVisible(not self.bottom.isVisible() or self.bottom_tabs.currentIndex()!=index)
        self.bottom_tabs.setCurrentIndex(index)

    def open_source_folder(self):
        if self.c.package:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.c.package.root)))

    def open_bundle(self):
        if self.manifest:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.manifest.parent)))

    def export_analysis(self):
        if not self.c.package:
            self.status_label.setText('先匯入來源，再匯出分析資料。');return
        path=QFileDialog.getSaveFileName(self,'匯出來源分析套件',str(self.c.package.root/'source-game-package.json'),'Analysis (*.json)')[0]
        if path:self.port.set_value('srtp_output_path',path);self.c.save_package()

    def core_check(self,kind):
        if self.check_future and not self.check_future.done():return
        self.diagnostic_tabs.setCurrentIndex(1)
        self.port.set_value('srtp_core_verification','正在背景執行引擎內建自測…')
        def run():
            from ..ir_acceptance import IRAcceptanceController
            probe=IRAcceptanceController(ROOT)
            try:
                if kind=='ai':probe.select_project('target');return probe.run_ai_conformance()
                return probe.run_integration_gate()
            finally:probe.close()
        self.check_future=self.check_pool.submit(run)

    def field_changed(self,tag,value):
        if tag=='srtp_status' and value and value!=self.last_status:
            self.last_status=value; self.append_console(str(value)); self.status_label.setText(str(value)[:120])

    def append_console(self,text):
        self.console.appendPlainText(str(text))

    def poll(self):
        if self.closing:return
        if self.check_future and self.check_future.done():
            try:report=self.check_future.result()
            except Exception as error:report={'passed':False,'error':str(error)}
            self.check_future=None
            self.port.set_value('srtp_core_verification',json.dumps(report,ensure_ascii=False,indent=2))
        self.c.poll_conversion(); self.c.training_panel.poll()
        package=self.c.package
        key=str(package.entrypoint) if package else None
        if key!=self.package_key:
            self.package_key=key
            index=self.example_selector.findData(key) if key else -1
            self.project_title.setText(self.example_selector.itemText(index) if index>0 else package.title if package else '開始你的空間創作')
            self.project_title.setToolTip(package.title if package else '')
            if package and not self.manifest:
                self.assets={p:str(package.root/p) for p in package.assets}; self.populate_assets()
        if self.scene_requested and self.manifest and self.manifest!=self.loaded_manifest:
            self.scene_requested=False
            self.scene_data={}; self.last_tree_ids=(); self.switch_view('scene')
        panel=self.c.training_panel
        eligible=panel.snapshot is not None
        state='eligible' if eligible else 'unavailable' if panel.target and not panel.future else 'pending'
        if self.eligibility.property('state')!=state:
            self.eligibility.setProperty('state',state)
            self.eligibility.style().unpolish(self.eligibility); self.eligibility.style().polish(self.eligibility)
        self.eligibility.setText('Demo 試玩' if self.demo_selected else '可訓練 · 雙人對弈' if eligible else '正在檢查資格…' if panel.future else '目前不適用' if panel.target else '等待批准版本')
        reasons=' '.join(panel.reasons)
        description='3×3×3 · 完整資訊 · 版本已綁定' if eligible else '這款遊戲沒有雙人對手。' if 'opponent' in reasons else '資格與不合格原因可在詳情查看。' if panel.target else '批准空間版本後，自動檢查對手與狀態編碼。'
        self.training_description.setText('此示範版本未接入自對弈訓練。' if self.demo_selected else description)
        self.run_reasons.setText('\n'.join(panel.reasons) if panel.reasons else '九項 Game API 通過；存檔已綁定目前 Rule 與 adapter。' if eligible else '目前沒有可訓練的批准版本。')
        counters=panel.last_event.get('counters',{})
        current_run=bool(panel.started and panel.target and getattr(panel,'run_target',None)==panel.target)
        for key,w in self.metric_labels.items():w.setText(str(panel.last_event.get('iteration',0) if key=='iteration' else counters.get(key,0)) if current_run else '—')
        self.training_progress.setValue(round(float(self.port.values.get('training_progress',0))*1000) if current_run else 0)
        phase=panel.last_event.get('phase','')
        names={'self_play_move':'自對弈中','self_play_game':'自對弈中','training_batch':'訓練中','training_epoch':'訓練中','arena_move':'新舊模型對決','arena_game':'新舊模型對決','completed':'試跑完成','cancelled':'已安全取消','failed':'執行失敗，請查看詳情','interrupted':'已中止，保留完整存檔'}
        status=str(self.port.get_value('training_status') or '')
        result='本機運算 · 可安全取消'
        if panel.session.running:result=names.get(phase,'正在啟動…') if current_run else '正在安全停止上一款遊戲的訓練…'
        elif current_run:
            result=names.get(phase,'試跑完成' if ': completed' in status or status.startswith('Verified') else '詳情中保留上次執行結果')
        if status.startswith('Could not start:'):result='無法啟動 · 請查看詳情中的原因'
        self.training_status.setText(result); self.training_status.setToolTip(status)
        active=bool(self.c.conversion_jobs.active)
        self.compile_button.setEnabled(bool(package) and not active); self.lift_button.setEnabled(bool(package) and not active)
        self.approve_button.setEnabled(bool(getattr(self.c,'_pending_llm_manifest',None)) and not active); self.cancel_conversion.setEnabled(active)
        self.project_badge.setText('3D Demo' if self.demo_selected else '已批准場景' if self.manifest else '來源已載入' if package else '本機工作區')

    def closeEvent(self,event):
        self.closing=True; self.timer.stop()
        self.target_view.shutdown(); self.source_view.shutdown(); self.demo_view.shutdown(); self.c.close()
        self.check_pool.shutdown(wait=False,cancel_futures=True)
        for dialog in self.dialogs.values():dialog.close()
        event.accept()

def configure_application(app):
    app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    palette=app.palette()
    for role,value in [(QPalette.Window,'#1d2129'),(QPalette.Base,'#151920'),(QPalette.AlternateBase,'#242b35'),
                       (QPalette.Text,'#e7e9f0'),(QPalette.WindowText,'#e7e9f0'),(QPalette.Button,'#292f39'),
                       (QPalette.ButtonText,'#e7e9f0'),(QPalette.Highlight,'#57466e'),(QPalette.HighlightedText,'#ffffff')]:
        palette.setColor(role,QColor(value))
    app.setPalette(palette)
    app.setApplicationName('CubeEngine Studio')

def main():
    parser=argparse.ArgumentParser(description='CubeEngine Studio')
    parser.add_argument('--project',type=Path); parser.add_argument('--source',type=Path)
    parser.add_argument('--size',default='1480x920'); parser.add_argument('--capture',type=Path)
    args=parser.parse_args()
    app=QApplication.instance() or QApplication(sys.argv[:1]); configure_application(app)
    window=Studio(args.project,args.source)
    width,height=map(int,args.size.lower().split('x'))
    area=app.primaryScreen().availableGeometry()
    window.resize(min(width,area.width()-30),min(height,area.height()-40)); window.show()
    if args.capture:
        def capture():
            args.capture.parent.mkdir(parents=True,exist_ok=True)
            app.primaryScreen().grabWindow(int(window.winId())).save(str(args.capture))
            window.close(); app.quit()
        QTimer.singleShot(16000,capture)
    return app.exec()

if __name__=='__main__':raise SystemExit(main())
