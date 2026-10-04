"""Prism Studio: native desktop presentation around the existing controllers."""
from datetime import datetime
import json
import os
from pathlib import Path
import time

from .prism_canvas import draw_state, pick_cell
from .training.config import TrainingConfig
from .training.session import ROOT, default_python

BG = (16, 17, 22)
PANEL = (22, 23, 30)
CARD = (29, 30, 39)
LINE = (47, 49, 62)
TEXT = (234, 233, 242)
MUTED = (157, 161, 180)
PURPLE = (181, 161, 255)
CYAN = (117, 205, 220)
MINT = (155, 219, 200)


def fit_initial_viewport(width, height):
    """Fit the first window to Windows' DPI-adjusted desktop work area."""
    if os.name == "nt":
        import ctypes
        from ctypes.wintypes import RECT
        rect = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
            width = min(width, max(800, rect.right - rect.left - 32))
            height = min(height, max(620, rect.bottom - rect.top - 32))
            return width, height, rect.left + (rect.right - rect.left - width) // 2, rect.top + (rect.bottom - rect.top - height) // 2
    return width, height, 30, 30


def friendly_status(message):
    for prefix, display in (("Sealed Project Manifest", "已掛載批准版本，畫布與訓練資格已更新。"),
                            ("Loaded ", "來源已匯入，可以試玩原作或開始空間轉換。"),
                            ("Target approved", "空間版本已批准，可正式試玩與檢查訓練資格。"),
                            ("Ursina is running", "已開啟目前批准的三維遊戲。"),
                            ("Source 2D preview", "正在查看原作。按「試玩」開啟原始遊戲。"),
                            ("Transformed 3D preview", "正在查看空間版本。按「試玩」開啟正式三維遊戲。"),
                            ("Accepted ", "操作已由目前規則接受，畫布已更新。")):
        if message.startswith(prefix):
            return display
    return message


def install_theme(dpg):
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            for key, color in {"WindowBg": BG, "ChildBg": PANEL, "PopupBg": CARD,
                               "Text": TEXT, "TextDisabled": (110, 115, 135), "Border": LINE,
                               "FrameBg": (37, 38, 49), "FrameBgHovered": (46, 47, 62),
                               "FrameBgActive": (57, 53, 79), "Button": (37, 38, 49),
                               "ButtonHovered": (56, 52, 77), "ButtonActive": (77, 67, 110),
                               "Header": (41, 40, 54), "HeaderHovered": (55, 50, 76),
                               "HeaderActive": (67, 58, 94), "CheckMark": PURPLE,
                               "SliderGrab": PURPLE, "SliderGrabActive": CYAN,
                               "Separator": LINE, "Tab": PANEL, "TabHovered": (61, 54, 83),
                               "TabActive": (51, 47, 67), "PlotHistogram": PURPLE,
                               "TitleBg": PANEL, "TitleBgActive": PANEL,
                               "ScrollbarBg": PANEL, "ScrollbarGrab": (60, 61, 77),
                               "NavHighlight": PURPLE}.items():
                dpg.add_theme_color(getattr(dpg, "mvThemeCol_" + key), color)
            for key, values in {"WindowPadding": (16, 14), "FramePadding": (11, 7),
                                "ItemSpacing": (8, 7), "ItemInnerSpacing": (7, 6),
                                "WindowRounding": (12,), "ChildRounding": (10,),
                                "FrameRounding": (6,), "PopupRounding": (10,),
                                "TabRounding": (6,), "ScrollbarSize": (9,),
                                "ChildBorderSize": (1,)}.items():
                dpg.add_theme_style(getattr(dpg, "mvStyleVar_" + key), *values)
    dpg.bind_theme(theme)
    for name, color, text in (("primary", PURPLE, (24, 20, 36)), ("selected", (53, 47, 73), TEXT),
                               ("quiet", PANEL, MUTED), ("danger", (82, 46, 56), (255, 184, 193))):
        with dpg.theme(tag="prism_theme_" + name):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, color)
                dpg.add_theme_color(dpg.mvThemeCol_Text, text)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, tuple(min(255, c + 13) for c in color))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, tuple(max(0, c - 10) for c in color))
    with dpg.theme(tag="prism_canvas_theme"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (17, 18, 24))
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
    with dpg.theme(tag="prism_rail_theme"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, BG)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
    with dpg.theme(tag="prism_training_theme"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, CARD)
            dpg.add_theme_color(dpg.mvThemeCol_Border, (67, 59, 90))
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 16, 14)
    with dpg.theme(tag="prism_properties_theme"):
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 16, 14)
    fonts = {}
    cjk = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msjh.ttc"
    if cjk.is_file():
        with dpg.font_registry():
            with dpg.font(str(cjk), 16, tag="prism_font_body"):
                dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
                dpg.add_font_range_hint(dpg.mvFontRangeHint_Chinese_Full)
                dpg.add_font_range(0x2000, 0x27ff)
            for name, size in (("small", 13), ("title", 23)):
                with dpg.font(str(cjk), size, tag="prism_font_" + name):
                    dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
                    # Use the same glyph coverage for user-provided project names.
                    dpg.add_font_range_hint(dpg.mvFontRangeHint_Chinese_Full)
                    dpg.add_font_range(0x2000, 0x27ff)
        dpg.bind_font("prism_font_body")
        fonts = {name: "prism_font_" + name for name in ("body", "small", "title")}
    return fonts


class PrismStudio:
    def __init__(self, dpg, controller, references):
        self.dpg, self.c, self.references = dpg, controller, references
        self.fonts = install_theme(dpg)
        self.view, self.state, self.state_signature = "source", None, None
        self.yaw, self.pitch, self.zoom, self.layer, self.opacity = .72, .52, 1.0, None, .8
        self.cells, self.hover, self.drag_point = [], None, None
        self.canvas_size = (740, 470)
        self.dirty, self.canvas_focus = True, False
        self.last_status, self.last_source, self.last_poll = None, None, 0
        self.activity, self.recents = [], []
        self.scale, self.library_open = 1.0, True
        self.preferences_path = ROOT / ".cubeengine_workbench/studio.json"
        try:
            data = json.loads(self.preferences_path.read_text(encoding="utf-8"))
            self.recents = data.get("recents", [])[:8]
        except (OSError, ValueError):
            pass

    def text(self, value, tag=None, color=TEXT, font=None, **kwargs):
        item = self.dpg.add_text(value, color=color, **({"tag": tag} if tag else {}), **kwargs)
        if font in self.fonts:
            self.dpg.bind_item_font(item, self.fonts[font])
        return item

    def button(self, label, callback, tag=None, theme=None, **kwargs):
        item = self.dpg.add_button(label=label, callback=callback, **({"tag": tag} if tag else {}), **kwargs)
        if theme:
            self.dpg.bind_item_theme(item, "prism_theme_" + theme)
        return item

    def show(self, tag):
        dpg = self.dpg
        w, h = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        config = dpg.get_item_configuration(tag)
        dpg.configure_item(tag, pos=(max(12, (w - config.get("width", 700)) // 2),
                                    max(12, (h - config.get("height", 520)) // 2)))
        dpg.show_item(tag)
        dpg.focus_item(tag)
        self.canvas_focus = False

    def build(self):
        dpg = self.dpg
        self.build_dialogs()
        with dpg.window(tag="srtp_primary", label="CubeEngine · Prism Studio", no_scrollbar=True):
            with dpg.menu_bar():
                with dpg.menu(label="檔案"):
                    dpg.add_menu_item(label="匯入遊戲入口...", callback=lambda: self.show("srtp_file_dialog"))
                    dpg.add_menu_item(label="匯入專案資料夾...", callback=lambda: self.show("srtp_directory_dialog"))
                    dpg.add_separator()
                    dpg.add_menu_item(label="掛載 Project Manifest...", callback=lambda: self.show("srtp_project_manifest_dialog"))
                    dpg.add_menu_item(label="掛載 Rule IR...", callback=lambda: self.show("srtp_rule_ir_dialog"))
                    dpg.add_menu_item(label="匯出分析資料...", callback=lambda: self.show("prism_export"))
                with dpg.menu(label="檢視"):
                    dpg.add_menu_item(label="來源 2D", callback=lambda: self.set_view("source"))
                    dpg.add_menu_item(label="空間 3D", callback=lambda: self.set_view("target"))
                    dpg.add_menu_item(label="來源 / 空間對照", callback=lambda: self.set_view("compare"))
                    dpg.add_menu_item(label="診斷與原始資料...", callback=lambda: self.show("prism_diagnostics"))
                    dpg.add_menu_item(label="顯示 / 隱藏遊戲庫", callback=self.toggle_library)
                with dpg.menu(label="介面大小"):
                    for scale in (1, 1.25, 1.5):
                        dpg.add_menu_item(label="{0}%".format(int(scale * 100)), callback=lambda s, a, u: self.set_scale(u), user_data=scale)
            with dpg.child_window(tag="prism_header", height=58, border=False, no_scrollbar=True):
                self.text("◇  CubeEngine", "prism_brand", font="title", color=TEXT, pos=(6, 8))
                self.button("工作室", lambda: None, "prism_nav_studio", "selected", pos=(243, 10), width=82)
                self.button("產物", lambda: self.show("prism_artifacts"), "prism_nav_artifacts", "quiet", pos=(334, 10), width=72)
                self.button("實驗紀錄", lambda: self.show("prism_run_details"), "prism_nav_runs", "quiet", pos=(413, 10), width=105)
                self.text("●  本機工作區", "prism_local", color=MINT, font="small", pos=(1060, 20))
                self.button("匯出專案  ↗", lambda: self.show("prism_export"), "prism_export_button", pos=(1190, 10), width=132)
            with dpg.child_window(tag="prism_journey", height=47, border=False, no_scrollbar=True):
                self.text("尚未匯入遊戲", "prism_project_name", font="small", pos=(8, 12))
                for index, label in enumerate(("1  匯入來源", "2  空間轉換", "3  審核與試玩", "4  AI 訓練")):
                    self.button(label, lambda s, a, u: self.stage(u), "prism_stage_" + str(index), "quiet",
                                user_data=index, width=133, pos=(244 + index * 148, 3))
                self.button("轉換與批准", lambda: self.show("prism_conversion"), "prism_journey_action", pos=(1185, 3), width=137)
            with dpg.group(horizontal=True, tag="prism_columns"):
                self.build_library()
                self.build_workspace()
                with dpg.child_window(tag="srtp_right_column", width=334, height=690, border=False, no_scrollbar=True):
                    dpg.bind_item_theme("srtp_right_column", "prism_rail_theme")
                    self.build_properties()
                    with dpg.child_window(tag="srtp_training_dock", height=-1, border=True, no_scrollbar=True):
                        dpg.bind_item_theme("srtp_training_dock", "prism_training_theme")
                        self.build_training()
            with dpg.child_window(tag="prism_footer", height=29, border=False, no_scrollbar=True):
                self.text("● Ready", "prism_footer_state", color=MINT, font="small", pos=(5, 2))
                self.text("Prism Studio  /  WorkbenchAI", "prism_footer_brand", color=MUTED, font="small", pos=(1050, 2))
        with dpg.handler_registry():
            dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=self.canvas_click)
            dpg.add_mouse_wheel_handler(callback=self.canvas_wheel)
            dpg.add_key_press_handler(callback=self.key)

    def build_library(self):
        dpg = self.dpg
        with dpg.child_window(tag="srtp_library_column", width=206, height=690, border=True):
            self.text("LIBRARY", color=MUTED, font="small")
            dpg.add_spacer(height=5)
            self.button("＋  匯入遊戲", lambda: self.show("prism_import"), "prism_import_button", width=-1, height=39)
            dpg.add_spacer(height=10)
            self.text("目前來源", color=MUTED, font="small")
            self.text("選擇來源開始", "prism_library_title", wrap=165)
            self.text("", "prism_library_info", color=MUTED, font="small", wrap=165)
            dpg.add_separator()
            self.text("遊戲範例", color=MUTED, font="small")
            for index, (label, path) in enumerate(self.references.items()):
                name = label.split(" · ")[0]
                self.button(name, lambda s, a, u: self.load_reference(u), "prism_reference_" + str(index), "quiet",
                            user_data=label, width=-1, height=32)
            dpg.add_separator()
            self.text("目前版本的產物", color=MUTED, font="small")
            for label, detail in (("□  原始遊戲", "source"), ("◇  空間版本", "target"),
                                  ("□  Rule · 規則", "rule"), ("□  Scene / Input", "artifacts")):
                self.button(label, lambda s, a, u: self.artifact(u), theme="quiet", user_data=detail, width=-1)
            dpg.add_spacer(height=8)
            self.button("最近開啟...", lambda: self.show("prism_import"), theme="quiet", width=-1)

    def build_workspace(self):
        dpg = self.dpg
        with dpg.child_window(tag="srtp_viewport_column", width=780, height=690, border=False, no_scrollbar=True):
            dpg.bind_item_theme("srtp_viewport_column", "prism_rail_theme")
            with dpg.child_window(tag="prism_canvas_toolbar", height=44, border=False, no_scrollbar=True):
                for index, (key, label) in enumerate((("source", "Source 2D"), ("target", "Target 3D"), ("compare", "對照"))):
                    self.button(label, lambda s, a, u: self.set_view(u), "prism_view_" + key, "quiet",
                                user_data=key, width=96 if index < 2 else 60, pos=(index * 102, 2))
                self.button("重設", self.reset, "prism_reset", pos=(590, 2), width=66)
                self.button("試玩", self.play, "srtp_play", pos=(665, 2), width=100)
            with dpg.child_window(tag="srtp_viewport_content", height=470, border=True, no_scrollbar=True):
                dpg.bind_item_theme("srtp_viewport_content", "prism_canvas_theme")
                with dpg.drawlist(tag="prism_canvas", width=740, height=470):
                    with dpg.draw_node(tag="prism_scene_layer"):
                        pass
                self.text("CubeEngine", "prism_canvas_title", pos=(20, 17))
                self.text("匯入你的遊戲，探索另一個維度。", "prism_canvas_subtitle", color=MUTED, font="small", pos=(20, 46))
                self.text("", "prism_canvas_hint", color=MUTED, font="small", pos=(20, 430))
                with dpg.group(tag="prism_source_card", pos=(38, 130)):
                    self.text("從原作，開始新的空間。", "prism_source_heading", font="title")
                    self.text("", "prism_source_metadata", color=MUTED, wrap=450)
                    dpg.add_spacer(height=12)
                    self.button("開啟原作", self.play_source, "prism_play_source", width=162, height=40)
                    self.text("原始遊戲在自己的視窗執行。", color=MUTED, font="small")
                with dpg.group(tag="prism_compare_card", show=False, pos=(20, 88)):
                    self.text("SOURCE / 原作", color=CYAN, font="small")
                    self.text("", "prism_compare_text", color=MUTED, wrap=175, font="small")
                    self.button("開啟原作 ↗", self.play_source, "prism_compare_play_source", width=134)
            with dpg.child_window(tag="prism_view_controls", height=42, border=False, no_scrollbar=True):
                self.text("視圖", color=MUTED, font="small", pos=(0, 10))
                dpg.add_combo(["整體 Cube"], tag="prism_layer", default_value="整體 Cube", callback=self.change_layer,
                              width=124, pos=(44, 2))
                self.button("復位視角", self.reset_camera, "prism_camera_reset", theme="quiet", pos=(179, 2), width=90)
                dpg.add_slider_float(tag="prism_opacity", min_value=.15, max_value=1, default_value=.8,
                                     format="透明度 %.2f", callback=self.change_opacity, width=145, pos=(450, 6))
            with dpg.child_window(tag="prism_activity_panel", height=-1, border=False, no_scrollbar=True):
                with dpg.group(horizontal=True):
                    self.text("活動", color=TEXT, font="small")
                    self.button("診斷 ↗", lambda: self.show("prism_diagnostics"), theme="quiet", height=25)
                    self.button("版本資訊 ↗", lambda: self.show("prism_artifacts"), theme="quiet", height=25)
                self.text("準備就緒", "prism_activity", color=MUTED, font="small", wrap=720)

    def build_properties(self):
        dpg = self.dpg
        with dpg.child_window(tag="srtp_inspector_dock", height=291, border=True):
            dpg.bind_item_theme("srtp_inspector_dock", "prism_properties_theme")
            self.text("空間設定", "prism_properties_title")
            with dpg.tab_bar():
                with dpg.tab(label="空間"):
                    self.text("下次轉換尺寸", color=MUTED, font="small")
                    with dpg.group(horizontal=True):
                        for axis in "xyz":
                            dpg.add_input_int(label=axis.upper(), tag="srtp_target_" + axis, width=73, default_value=3,
                                              min_value=2, min_clamped=True, step=0, callback=self.c.apply_transform_target)
                    self.text("轉換意圖", color=MUTED, font="small")
                    self.text("", "srtp_target_volume", color=CYAN, font="small", show=False)
                    dpg.add_input_text(tag="srtp_llm_intent", hint="描述想探索的三維玩法…", multiline=True,
                                       height=48, width=-1, callback=self.c.intent_changed)
                    self.button("轉換設定與批准 ↗", lambda: self.show("prism_conversion"), width=-1)
                with dpg.tab(label="來源參數"):
                    with dpg.child_window(tag="srtp_parameter_rows", height=-1, border=False):
                        pass
                with dpg.tab(label="規則"):
                    self.text("", "prism_rule_brief", color=MUTED, font="small", wrap=277)
                    self.button("完整規則與驗證 ↗", lambda: self.show("prism_diagnostics"), width=-1)

    def build_training(self):
        dpg, panel = self.dpg, self.c.training_panel
        self.text("AI 自對弈", "prism_training_title", pos=(16, 14))
        self.text("等待批准版本", "prism_eligibility", color=MUTED, font="small", pos=(16, 43))
        self.text("轉換完成後，檢查此遊戲是否具備對手。", "prism_training_summary", color=MUTED, font="small", wrap=287, pos=(16, 69))
        with dpg.group(horizontal=True, tag="prism_training_preset", pos=(16, 100)):
            self.button("短程試跑設定", lambda: self.show("prism_training_settings"), "prism_training_settings_button", width=183)
            self.button("詳情", lambda: self.show("prism_run_details"), "prism_run_details_button", width=82)
        dpg.add_progress_bar(tag="training_progress", default_value=0, width=-1, height=5, pos=(16, 147))
        self.text("自對弈  ›  訓練  ›  新舊對決  ›  存檔", "prism_training_steps", color=MUTED, font="small", pos=(16, 165))
        self.text("尚未啟動訓練", "prism_training_metrics", wrap=285, pos=(16, 194))
        self.text("本機 CPU · 可安全取消", "prism_training_status", color=MUTED, font="small", wrap=285, pos=(16, 228))
        with dpg.group(tag="prism_training_actions", horizontal=True):
            self.button("開始短程試跑", panel.start, "training_start", "primary", enabled=False, width=177, height=37)
            self.button("取消", panel.cancel, "training_cancel", "danger", enabled=False, width=86, height=37)
        with dpg.group(horizontal=True, tag="prism_training_secondary"):
            self.button("使用上一存檔", panel.resume_last, "training_resume_last", "quiet", enabled=False, width=164, height=28)
            self.button("結果 ↗", panel.open_results, "prism_results", "quiet", width=99, height=28)

    def build_dialogs(self):
        dpg, c, panel = self.dpg, self.c, self.c.training_panel
        with dpg.value_registry():
            dpg.add_string_value(tag="srtp_reference_selector", default_value=next(iter(self.references)))
            dpg.add_string_value(tag="srtp_preview_mode", default_value="Source 2D")
        with dpg.window(label="匯入遊戲", tag="prism_import", show=False, width=670, height=500):
            self.text("從自己的來源開始。", font="title")
            self.text("匯入完整專案，或選擇可執行的遊戲入口。", color=MUTED)
            with dpg.group(horizontal=True):
                self.button("選擇入口檔案...", lambda: self.show("srtp_file_dialog"), theme="primary")
                self.button("選擇專案資料夾...", lambda: self.show("srtp_directory_dialog"))
                self.button("已生成的 Manifest...", lambda: self.show("srtp_project_manifest_dialog"))
            self.text("最近開啟", color=MUTED)
            with dpg.child_window(tag="prism_recent_list", height=220, border=False):
                pass
            dpg.add_input_text(tag="srtp_source_path", hint="入口檔案或專案資料夾路徑", width=-1)
            self.button("從此路徑匯入", lambda: (c.import_source(), dpg.hide_item("prism_import")))
        self.refresh_recents()
        with dpg.window(label="空間轉換與批准", tag="prism_conversion", show=False, width=790, height=595):
            self.text("每一步，都有可檢查的版本。", font="title")
            self.text("1  來源分析  →  批准來源  →  2  空間轉換  →  批准 Target", color=MUTED)
            self.text("", "prism_conversion_state", color=MINT, wrap=728)
            with dpg.group(horizontal=True):
                self.button("分析來源", c.compile_llm_source_to_ir, "prism_compile_source", width=175)
                self.button("生成空間版本", c.run_spatial_lift, "prism_compile_target", width=175)
                self.button("批准目前候選", c.approve_pending_llm_manifest, "prism_approve", theme="primary", width=175)
                self.button("取消轉換", c.cancel_conversion, "prism_cancel_conversion", width=130)
            self.text("生成使用已配置的模型服務；批准後才可正式試玩或訓練。", color=MUTED, font="small")
            self.text("候選版本", color=MUTED)
            dpg.add_input_text(tag="srtp_llm_manifest_path", width=-1, hint="待批准的 project.manifest.json")
            self.text("", "srtp_lift_policy", color=MUTED, wrap=720)
            self.text("", "srtp_core_attachment", color=MUTED, wrap=720)
            dpg.add_input_text(tag="srtp_core_activity", readonly=True, multiline=True, height=200, width=-1)
            with dpg.group(horizontal=True):
                self.button("掛載已批准版本...", lambda: self.show("srtp_project_manifest_dialog"))
                self.button("查看診斷", lambda: self.show("prism_diagnostics"))
        with dpg.window(label="產物與版本資訊", tag="prism_artifacts", show=False, width=780, height=570):
            self.text("屬於同一款遊戲的完整版本。", font="title")
            self.text("", "prism_artifact_summary", wrap=720, color=MUTED)
            self.text("", "prism_artifact_binding", wrap=720, color=CYAN, font="small")
            dpg.add_input_text(tag="srtp_core_session_summary", multiline=True, readonly=True, height=220, width=-1)
            with dpg.group(horizontal=True):
                self.button("開啟產物資料夾 ↗", self.open_bundle)
                self.button("掛載 Manifest...", lambda: self.show("srtp_project_manifest_dialog"))
                self.button("掛載 Rule...", lambda: self.show("srtp_rule_ir_dialog"))
        with dpg.window(label="匯出", tag="prism_export", show=False, width=675, height=365):
            self.text("保留你的來源與分析。", font="title")
            self.text("分析套件包含來源證據、參數與轉換說明。\n正式遊戲的四份 IR 及資產仍位於已批准的產物資料夾。", color=MUTED, wrap=615)
            dpg.add_input_text(tag="srtp_output_path", hint="source-game-package.json", width=-1)
            self.button("儲存分析套件", c.save_package, theme="primary", width=-1)
            self.button("開啟已批准的產物資料夾 ↗", self.open_bundle, width=-1)
            self.text("", "prism_export_status", color=MUTED, wrap=610)
        with dpg.window(label="診斷與原始資料", tag="prism_diagnostics", show=False, width=940, height=655):
            with dpg.tab_bar():
                with dpg.tab(label="目前版本"):
                    dpg.add_combo(items=[], tag="srtp_core_project", width=480, callback=c.select_core_project)
                    with dpg.group(horizontal=True):
                        self.button("重設遊戲", c.reset_core)
                        self.button("驗證重放", c.verify_core_replay)
                        self.button("Core 內建自測", lambda: c.run_core_self_test("gate"))
                        self.button("9 API 內建自測", lambda: c.run_core_self_test("ai"))
                    self.text("內建自測與當前 Target 的训练資格是不同檢查。", color=MUTED, font="small")
                    self.text("", "srtp_core_scene_title")
                    self.text("", "srtp_core_scene_help", color=MUTED, wrap=860)
                    with dpg.child_window(tag="srtp_core_scene_layers", height=90, border=False):
                        self.text("主畫布可旋轉、分層與選取格點；正式試玩使用獨立 3D 視窗。", color=MUTED)
                    dpg.add_input_text(tag="srtp_core_last_result", multiline=True, readonly=True, height=130, width=-1)
                    dpg.add_input_text(tag="srtp_core_verification", multiline=True, readonly=True, height=160, width=-1)
                for label, tag in (("來源證據", "srtp_source_summary"), ("規則", "srtp_core_rule_summary"),
                                   ("分析規則", "srtp_schema_output"), ("診斷", "srtp_diagnostics"),
                                   ("事件", "srtp_core_activity_view"), ("交接資料", "srtp_handoff")):
                    with dpg.tab(label=label):
                        dpg.add_input_text(tag=tag, multiline=True, readonly=True, width=-1, height=548)
                with dpg.tab(label="來源變體"):
                    self.text("修改可證明的安全參數，儲存為另一份來源。", color=MUTED)
                    dpg.add_combo(items=["N/A"], tag="srtp_safe_parameter", width=-1, callback=lambda: c.select_safe_parameter())
                    dpg.add_input_text(tag="srtp_safe_parameter_value", width=-1)
                    self.button("套用至副本", c.apply_safe_source_parameter)
            # Stable data tags used by existing controller paths.
            with dpg.group(show=False):
                for tag in ("srtp_parameters", "srtp_transform", "srtp_library_details", "srtp_source_plane", "srtp_status",
                            "srtp_viewport_heading", "srtp_viewport_body"):
                    dpg.add_text("", tag=tag)
                with dpg.group(tag="srtp_transform_rows"):
                    pass
                with dpg.group(tag="srtp_standard_view"):
                    pass
                with dpg.group(tag="srtp_core_view"):
                    pass
        with dpg.window(label="AI 自對弈 · 訓練設定", tag="prism_training_settings", show=False, width=730, height=625):
            self.text("從一個小實驗開始。", font="title")
            self.text("續跑時，迭代數表示本次額外增加的輪次。", color=MUTED)
            with dpg.group(tag="training_settings"):
                for key, label in (("iterations", "追加迭代"), ("episodes", "每輪自對弈局數"),
                                   ("simulations", "MCTS / 每步"), ("epochs", "訓練 epochs"), ("arena_games", "新舊對決局數（偶數）")):
                    dpg.add_input_int(label=label, tag="training_" + key, default_value=getattr(TrainingConfig(), key), width=180)
                with dpg.collapsing_header(label="進階參數"):
                    for key in ("channels", "batch_size", "seed"):
                        dpg.add_input_int(label=key, tag="training_" + key, default_value=getattr(TrainingConfig(), key), width=180)
                    dpg.add_input_float(label="Learning rate", tag="training_learning_rate", default_value=.001, format="%.4f", width=180)
                    dpg.add_input_float(label="採用門檻", tag="training_acceptance", default_value=.55, width=180)
                    dpg.add_combo(["cpu", "cuda"], label="裝置", tag="training_device", default_value="cpu", width=180)
                    dpg.add_input_text(label="Python", tag="training_python", default_value=default_python(), width=550)
                self.text("輸出位置", color=MUTED)
                dpg.add_input_text(tag="training_output", default_value=str(ROOT / ".cubeengine_training"), width=-1)
                self.button("選擇輸出資料夾...", lambda: self.show("training_output_dialog"))
                self.text("相容 checkpoint.json（留空表示從初始模型開始）", color=MUTED)
                dpg.add_input_text(tag="training_resume", width=-1)
                with dpg.group(horizontal=True):
                    self.button("選擇存檔...", lambda: self.show("training_checkpoint_dialog"))
                    self.button("清除續跑存檔", lambda: dpg.set_value("training_resume", ""))
            self.button("完成設定", lambda: dpg.hide_item("prism_training_settings"), theme="primary", width=-1)
        with dpg.window(label="AI 自對弈 · 狀態與結果", tag="prism_run_details", show=False, width=830, height=650):
            self.text("資格、版本與每一次實驗。", font="title")
            self.text("", "training_eligibility", color=MINT, wrap=770)
            self.text(panel.reasons[0], "training_reasons", color=MUTED, wrap=770)
            with dpg.group(horizontal=True):
                self.button("掛載 Target...", lambda: self.show("srtp_project_manifest_dialog"))
                self.button("重新檢查", panel.refresh)
                self.button("開啟結果資料夾 ↗", panel.open_results)
                self.button("驗證已存對局", panel.verify_results, "training_verify", enabled=False)
            self.text("尚未開始", "training_metrics", wrap=770)
            self.text("本機訓練", "training_status", wrap=770)
            self.text("", "training_run_path", color=MUTED, font="small", wrap=770)
            dpg.add_input_text(tag="training_log", multiline=True, readonly=True, width=-1, height=310)

    def refresh_recents(self):
        dpg = self.dpg
        dpg.delete_item("prism_recent_list", children_only=True)
        if not self.recents:
            self.text("尚無最近的來源專案。", color=MUTED, parent="prism_recent_list")
        for entry in self.recents:
            self.button(entry.get("title", Path(entry["path"]).name), lambda s, a, u: self.load_path(u),
                        user_data=entry["path"], width=-1, parent="prism_recent_list")

    def remember_source(self):
        package = self.c.package
        if package is None:
            return
        path = str(package.entrypoint.resolve())
        if path != self.last_source:
            self.last_source = path
            self.recents = [{"title": package.title, "path": path}] + [r for r in self.recents if r["path"] != path][:7]
            try:
                self.preferences_path.parent.mkdir(parents=True, exist_ok=True)
                from .training.artifacts import atomic_json
                atomic_json(self.preferences_path, {"recents": self.recents})
            except OSError as exc:
                self.c._message("Recent project history could not be saved: " + str(exc), error=True)
            self.refresh_recents()

    def load_reference(self, label):
        self.dpg.set_value("srtp_reference_selector", label)
        self.c.load_selected_reference()
        self.source_changed()

    def load_path(self, path):
        self.dpg.set_value("srtp_source_path", path)
        self.c.import_source()
        self.dpg.hide_item("prism_import")
        if not self.c.core_controller:
            self.source_changed()

    def source_changed(self):
        self.state, self.state_signature, self.cells = None, None, []
        self.view, self.layer, self.hover = "source", None, None
        self.dirty = True
        self.had_markers = False
        self.dpg.hide_item("prism_import")
        self.remember_source()

    def set_state(self, state):
        first = self.state is None or self.state.project_id != state.project_id
        self.state, self.state_signature = state, state.state_hash
        if first:
            self.view = "target" if state.variant == "target" else "source"
            self.layer, self.hover = None, None
            count = state.dimensions[2] if len(state.dimensions) == 3 else 1
            self.dpg.configure_item("prism_layer", items=["整體 Cube"] + ["Z 層 " + str(i + 1) for i in range(count)])
            self.dpg.set_value("prism_layer", "整體 Cube")
        self.dirty = True

    def set_view(self, view):
        self.view = view
        self.dpg.set_value("srtp_preview_mode", "Source 2D" if view == "source" else "Transformed 3D")
        self.c.set_preview_mode()
        self.canvas_focus, self.dirty = False, True

    def stage(self, index):
        self.show(("prism_import", "prism_conversion", "prism_conversion", "prism_run_details")[index])

    def artifact(self, kind):
        if kind in ("source", "target"):
            self.set_view(kind)
        else:
            self.show("prism_diagnostics" if kind == "rule" else "prism_artifacts")

    def play_source(self, *args):
        if self.c.package and self.c.package.runtime.kind == "project_ir":
            self.c._message("此輸入是生成版本。請匯入原始 source，才可開啟原作對照。")
            return
        self.c.launch_original()

    def play(self, *args):
        if self.view == "source" and self.c.package and self.c.package.runtime.kind == "project_ir":
            self.play_source()
            return
        self.dpg.set_value("srtp_preview_mode", "Source 2D" if self.view == "source" else "Transformed 3D")
        self.c.toggle_preview()

    def reset(self, *args):
        if self.c.core_controller and self.c.core_controller.has_active_project:
            self.c.reset_core()
        self.reset_camera()

    def reset_camera(self, *args):
        self.yaw, self.pitch, self.zoom, self.dirty = .72, .52, 1.0, True

    def change_layer(self, sender, value):
        self.layer = None if value == "整體 Cube" else int(value.rsplit(" ", 1)[-1]) - 1
        self.hover, self.dirty = None, True

    def change_opacity(self, sender, value):
        self.opacity, self.dirty = value, True

    def open_bundle(self, *args):
        target = self.c.training_panel.target
        path = target.parent if target else getattr(self.c, "_pending_llm_source_dir", None)
        if path and Path(path).is_dir():
            os.startfile(str(path))
        else:
            self.c._message("尚無可開啟的產物資料夾。請先完成轉換或掛載版本。")

    def canvas_available(self):
        if not self.dpg.is_item_hovered("prism_canvas"):
            return False
        return not any(self.dpg.is_item_shown(tag) for tag in
                       ("prism_import", "prism_conversion", "prism_diagnostics", "prism_export", "prism_artifacts",
                        "prism_training_settings", "prism_run_details", "srtp_file_dialog", "srtp_directory_dialog",
                        "srtp_project_manifest_dialog", "srtp_rule_ir_dialog", "training_output_dialog", "training_checkpoint_dialog"))

    def canvas_click(self, *args):
        self.canvas_focus = self.canvas_available()
        if not self.canvas_focus or self.view == "source" or not self.state:
            return
        if self.is_hidden():
            return
        origin = self.dpg.get_item_rect_min("prism_canvas")
        mouse = self.dpg.get_mouse_pos(local=False)
        coordinate = pick_cell(self.cells, (mouse[0] - origin[0], mouse[1] - origin[1]))
        if coordinate is not None:
            self.c.click_core_cell(user_data=coordinate)

    def canvas_wheel(self, sender, value):
        if self.canvas_available():
            self.zoom = min(1.7, max(.55, self.zoom + value * .08))
            self.dirty = True

    def key(self, sender, value):
        if self.canvas_focus and self.canvas_available() and not self.is_hidden():
            previous = self.dpg.get_value("srtp_preview_mode")
            self.dpg.set_value("srtp_preview_mode", "Project Session")
            try:
                self.c.handle_core_key(sender, value)
            finally:
                self.dpg.set_value("srtp_preview_mode", previous)

    def is_hidden(self):
        core = self.c.core_controller
        if not core or not core.has_active_project:
            return True
        rule = core.projects[core.active_key].rule
        snapshot = self.c.training_panel.snapshot
        # Generated metadata can incorrectly label a hidden-information game
        # as perfect information. Only a proven state encoder permits showing
        # raw grid values or editing through this abstract preview.
        return not (rule.get("state", {}).get("information_model") == "perfect" and snapshot
                    and snapshot["rule"]["content_hash"] == rule["content_hash"])

    def target_title(self):
        core = self.c.core_controller
        if core and core.has_active_project:
            return core.projects[core.active_key].manifest.get("metadata", {}).get("title", self.state.label if self.state else "Target")
        return "Target"

    def markers(self):
        snapshot = self.c.training_panel.snapshot
        if not snapshot or not self.state or snapshot["rule"]["document_id"] != self.state.rule_id:
            return {}
        names = {p["id"]: p.get("name", p["id"]) for p in snapshot["rule"]["participants"]}
        adapter = snapshot["adapter"]
        result = {}
        for value in adapter["tensor"]["value_map"]:
            owner = value["owner"]
            name = names[adapter["players"][owner]] if owner else ""
            result[str(value["rule_value"])] = (name, CYAN if owner == "positive" else PURPLE)
        return result

    def set_scale(self, scale):
        self.scale = float(scale)
        self.dpg.set_global_font_scale(self.scale)
        self.resize()

    def toggle_library(self, *args):
        self.library_open = not self.library_open
        self.resize()

    def resize(self, *args):
        dpg = self.dpg
        width, height = dpg.get_viewport_client_width(), dpg.get_viewport_client_height()
        scale = self.scale
        library = self.library_open and width >= 1230 * scale
        dpg.configure_item("srtp_library_column", show=library)
        rail = int(334 * scale)
        left = int(206 * scale) if library else 0
        center = max(330, width - left - rail - (50 if library else 41))
        column_height = max(430, height - 215 - int(16 * (scale - 1)))
        for tag in ("srtp_library_column", "srtp_viewport_column", "srtp_right_column"):
            dpg.configure_item(tag, height=column_height)
        dpg.configure_item("srtp_library_column", width=max(206, left))
        dpg.configure_item("srtp_right_column", width=rail)
        dpg.configure_item("srtp_viewport_column", width=center)
        inspector = min(int(291 * scale), max(200, column_height - int(250 * scale) - 39))
        dpg.configure_item("srtp_inspector_dock", height=inspector)
        canvas_height = max(235, column_height - int(196 * scale))
        self.canvas_size = (center - 2, canvas_height - 2)
        dpg.configure_item("srtp_viewport_content", height=canvas_height)
        dpg.configure_item("prism_canvas", width=self.canvas_size[0], height=self.canvas_size[1])
        dpg.configure_item("prism_canvas_hint", pos=(20, canvas_height - 33), wrap=center - 40)
        dpg.configure_item("prism_canvas_subtitle", wrap=center - 40)
        dpg.configure_item("srtp_play", pos=(center - int(107 * scale), 2), width=int(100 * scale))
        dpg.configure_item("prism_reset", pos=(center - int(182 * scale), 2), width=int(66 * scale), show=center > 590 * scale)
        for index, key in enumerate(("source", "target", "compare")):
            dpg.configure_item("prism_view_" + key, pos=(int(index * 102 * scale), 2), width=int((96 if index < 2 else 60) * scale))
        dpg.configure_item("prism_canvas_toolbar", height=int(44 * scale))
        for tag, x, size in (("prism_nav_studio", 243, 82), ("prism_nav_artifacts", 334, 72), ("prism_nav_runs", 413, 105)):
            dpg.configure_item(tag, pos=(int(x * scale), 10), width=int(size * scale))
        dpg.configure_item("prism_opacity", pos=(center - 150, 6), show=center > 500)
        dpg.configure_item("prism_source_card", pos=(32, max(110, canvas_height * .30)))
        dpg.configure_item("prism_source_metadata", wrap=center - 80)
        dpg.configure_item("prism_export_button", pos=(width - 177, 10))
        dpg.configure_item("prism_local", pos=(width - 310, 20), show=width > 1050 * scale)
        dpg.configure_item("prism_journey_action", pos=(width - int(182 * scale), 3), width=int(137 * scale),
                           show=width > (234 if library else 0) + 4 * 146 * scale + 190 * scale)
        dpg.configure_item("prism_project_name", show=library)
        for index in range(4):
            dpg.configure_item("prism_stage_" + str(index), pos=((234 if library else 0) + int(index * 146 * scale), 3), width=int(133 * scale))
        dpg.configure_item("prism_activity", wrap=center - 10)
        dpg.configure_item("prism_footer_brand", pos=(width - int(335 * scale), 2), show=width > 1050 * scale)
        # Keep start/cancel fixed at the bottom, independent of status length.
        train_height = column_height - inspector - 9
        compact = train_height < 360 * scale
        dpg.configure_item("prism_training_summary", show=not compact, wrap=rail - 36)
        dpg.configure_item("prism_training_steps", show=not compact)
        dpg.configure_item("prism_training_preset", pos=(16, int((75 if compact else 100) * scale)))
        dpg.configure_item("training_progress", width=rail - 35, pos=(16, int((118 if compact else 147) * scale)))
        action_y = train_height - int(83 * scale) - 6
        progress_y = int((118 if compact else 147) * scale)
        metrics_y = min(int((140 if compact else 194) * scale), action_y - int(29 * scale))
        dpg.configure_item("prism_training_metrics", pos=(16, metrics_y), wrap=rail - 36,
                           show=metrics_y > progress_y + 12)
        dpg.configure_item("prism_eligibility", pos=(16, int(43 * scale)))
        dpg.configure_item("prism_training_summary", pos=(16, int(69 * scale)))
        dpg.configure_item("prism_training_steps", pos=(16, int(165 * scale)))
        dpg.configure_item("prism_training_status", pos=(16, int(228 * scale)), wrap=rail - 36)
        dpg.configure_item("prism_training_actions", pos=(16, action_y))
        dpg.configure_item("prism_training_secondary", pos=(16, train_height - int(35 * scale) - 8))
        for tag in ("training_start", "training_cancel"):
            dpg.configure_item(tag, height=int(37 * scale))
        for tag in ("training_resume_last", "prism_results"):
            dpg.configure_item(tag, height=int(28 * scale))
        dpg.configure_item("prism_training_status", show=not compact)
        for tag, fraction in (("training_start", .64), ("training_cancel", .31),
                              ("training_resume_last", .59), ("prism_results", .36),
                              ("prism_training_settings_button", .65), ("prism_run_details_button", .3)):
            dpg.configure_item(tag, width=int((rail - 36) * fraction))
        self.dirty = True

    def poll(self):
        dpg, c, panel = self.dpg, self.c, self.c.training_panel
        if c.core_controller is None and self.state is not None:
            self.source_changed()
        self.remember_source()
        if self.canvas_available():
            origin, mouse = dpg.get_item_rect_min("prism_canvas"), dpg.get_mouse_pos(local=False)
            point = (mouse[0] - origin[0], mouse[1] - origin[1])
            hover = pick_cell(self.cells, point) if self.view != "source" else None
            if hover != self.hover:
                self.hover, self.dirty = hover, True
            if dpg.is_mouse_button_down(dpg.mvMouseButton_Right):
                if self.drag_point:
                    self.yaw += (point[0] - self.drag_point[0]) * .007
                    self.pitch = min(1.25, max(-.3, self.pitch + (point[1] - self.drag_point[1]) * .006))
                    self.dirty = True
                self.drag_point = point
            else:
                self.drag_point = None
        else:
            self.drag_point = None
        if time.monotonic() - self.last_poll > .15:
            self.last_poll = time.monotonic()
            self.sync_labels()
        if self.dirty:
            self.draw_canvas()
            self.dirty = False

    def sync_labels(self):
        dpg, c, panel = self.dpg, self.c, self.c.training_panel
        package = c.package
        title = package.title if package else "尚未匯入遊戲"
        active_title = self.target_title() if self.state and self.view != "source" else title
        dpg.set_value("prism_project_name", active_title if len(active_title) < 23 else active_title[:21] + "…")
        dpg.set_value("prism_library_title", title if len(title) < 23 else title[:21] + "…")
        dpg.set_value("prism_library_info", "{0} · {1} 個檔案\n{2} 個素材".format(package.runtime.framework, len(package.files), len(package.assets)) if package else "")
        status = dpg.get_value("srtp_status") or ""
        if status != self.last_status:
            self.last_status = status
            self.activity.append(datetime.now().strftime("%H:%M:%S") + "   " + friendly_status(status))
            self.activity = self.activity[-40:]
            dpg.set_value("prism_activity", "\n".join(line[:105] for line in self.activity[-2:]))
            dpg.set_value("prism_export_status", status)
        pending = getattr(c, "_pending_llm_manifest", None)
        if pending:
            dpg.set_value("srtp_llm_manifest_path", str(pending))
        active = bool(c.conversion_jobs.active)
        for tag in ("prism_compile_source", "prism_compile_target"):
            dpg.configure_item(tag, enabled=bool(package) and not active)
        dpg.configure_item("prism_approve", enabled=bool(package and (pending or dpg.get_value("srtp_llm_manifest_path"))) and not active)
        dpg.configure_item("prism_cancel_conversion", enabled=active)
        dpg.set_value("prism_conversion_state", "正在轉換 · 可以取消" if active else status)
        current = 3 if panel.session.running else 2 if self.state and self.state.variant == "target" else 1 if package else 0
        for index in range(4):
            dpg.bind_item_theme("prism_stage_" + str(index), "prism_theme_selected" if index == current else "prism_theme_quiet")
        for key in ("source", "target", "compare"):
            dpg.bind_item_theme("prism_view_" + key, "prism_theme_selected" if key == self.view else "prism_theme_quiet")
        source_available = package is not None and package.runtime.kind != "project_ir"
        dpg.configure_item("prism_play_source", enabled=source_available)
        dpg.configure_item("prism_compare_play_source", enabled=source_available)
        dpg.configure_item("srtp_play", enabled=package is not None and (self.view != "source" or source_available),
                           label="■  停止" if c._active_process_running() else "試玩")
        state = self.state
        dpg.set_value("prism_rule_brief", ("{0}\n{1} 個合法動作\n{2}".format(state.outcome_status, state.legal_actions, " / ".join(state.winners) if state.terminal else "規則決定每一次操作") if state else "完成分析後，可查看目前版本的規則。"))
        dpg.set_value("prism_artifact_summary", "目前來源：" + title + ("\n" + str(package.entrypoint) if package else ""))
        identity = panel.snapshot
        dpg.set_value("prism_artifact_binding", ("Rule  " + identity["rule"]["content_hash"] + "\nAdapter  " + identity["adapter"]["content_hash"]) if identity else "目前沒有已驗證的訓練版本。")
        eligible = panel.snapshot is not None
        eligibility = "可訓練 · 雙人 / 完整資訊" if eligible else "檢查中…" if panel.future else "不適用於此訓練器" if panel.target else "等待批准的 Target"
        if panel.target and not eligible and not panel.future:
            reasons = " ".join(panel.reasons)
            if "opponent" in reasons:
                eligibility = "不適用 · 沒有雙人對手"
            elif "Hidden information" in reasons:
                eligibility = "不適用 · 含隱藏資訊"
            elif "Random/chance" in reasons:
                eligibility = "不適用 · 含隨機轉移"
            elif "3 x 3 x 3" in reasons:
                eligibility = "不適用 · 目前僅支援 3×3×3"
        dpg.set_value("prism_eligibility", eligibility)
        dpg.configure_item("prism_eligibility", color=MINT if eligible else MUTED)
        reason = (panel.snapshot["title"] + " · 版本已鎖定") if eligible else "詳情中列出不合格原因；仍可繼續試玩。" if panel.target and not panel.future else "掛載已批准的空間版本後檢查資格。"
        dpg.set_value("prism_training_summary", reason)
        phase = panel.last_event.get("phase", "")
        names = {"self_play_move": "自對弈中", "self_play_game": "自對弈中", "training_batch": "訓練中",
                 "training_epoch": "訓練中", "arena_move": "新舊模型對決", "arena_game": "新舊模型對決",
                 "completed": "試跑完成", "cancelled": "已安全停止", "failed": "執行失敗，查看詳情", "interrupted": "已中止，查看詳情"}
        raw_status = dpg.get_value("training_status") or ""
        counters = panel.last_event.get("counters", {})
        summary = "迭代 {0}    自對弈 {1}    對決 {2}".format(panel.last_event.get("iteration", 0), counters.get("self_play_games", 0), counters.get("arena_games", 0))
        dpg.set_value("prism_training_metrics", summary if panel.started else "短程試跑  /  本機運算" if eligible else "仍可繼續試玩與匯出")
        label = names.get(phase, "正在執行…" if panel.session.running else "本機 CPU · 可安全取消")
        if "completed" in raw_status:
            label = "試跑完成 · 可查看樣本與模型"
        elif "Verified" in raw_status:
            label = "已用原規則驗證對局"
        elif "Stopping" in raw_status or "stopping" in raw_status:
            label = "正在安全停止，保留完整存檔…"
        elif "Compatible checkpoint selected" in raw_status:
            label = "已選相容存檔 · 開始即可續跑"
        elif "cancelled" in raw_status.lower():
            label = "已安全停止 · 可從完整存檔續跑"
        elif "failed" in raw_status.lower() or "error" in raw_status.lower() or "Could not" in raw_status or "incompatible" in raw_status:
            label = "需要處理：請開啟詳情查看原因"
        elif not eligible and not panel.session.running:
            label = "自對弈不影響原本的遊戲功能。"
        dpg.set_value("prism_training_status", label)
        dpg.set_value("prism_footer_state", "●  " + ("AI 正在執行" if panel.session.running else "正在轉換" if active else "就緒") + "   /   " + title[:24])
        if eligible and not getattr(self, "had_markers", False):
            self.had_markers, self.dirty = True, True

    def draw_canvas(self):
        dpg, package = self.dpg, self.c.package
        title = package.title if package else "CubeEngine"
        available = self.state is not None and (self.view == "source" or self.state.variant == "target")
        show_state = available and self.view != "source"
        dpg.configure_item("prism_source_card", show=not show_state)
        dpg.configure_item("prism_compare_card", show=show_state and self.view == "compare")
        dpg.set_value("prism_canvas_title", self.target_title() if show_state else title)
        dpg.set_value("prism_canvas_subtitle", ("已批准 Target · 拓樸預覽，內部狀態已隱去" if self.is_hidden() else "已批准 Target · 規則狀態預覽") if show_state else "SOURCE / 原始遊戲" if self.view == "source" else "TARGET / 尚未掛載已批准版本")
        if show_state:
            core = self.c.core_controller
            legal = set()
            if core:
                declarations = {action["id"]: action for action in core.projects[core.active_key].rule["actions"]}
                for action in core.sessions[core.active_key].rule_runtime.legal_actions():
                    for param in declarations.get(action.action_id, {}).get("parameters", []):
                        value = action.parameters.get(param["name"])
                        if param.get("type") == "core:coord" and isinstance(value, (list, tuple)):
                            legal.add(tuple(value))
            offset = 205 if self.view == "compare" else 0
            dpg.apply_transform("prism_scene_layer", dpg.create_translation_matrix((offset, 0, 0)))
            render_size = (max(180, self.canvas_size[0] - offset), self.canvas_size[1])
            self.cells = draw_state(dpg, "prism_scene_layer", self.state, render_size, yaw=self.yaw, pitch=self.pitch,
                                    zoom=self.zoom, layer=self.layer, opacity=self.opacity, markers=self.markers(),
                                    legal=legal, hidden=self.is_hidden(), hover=self.hover)
            for cell in self.cells:
                x, y, z = cell["center"]
                cell["center"] = (x + offset, y, z)
            hint = "僅顯示拓樸，保護未驗證的內部狀態 · 試玩使用 3D 視窗" if self.is_hidden() else "右鍵旋轉 · 滾輪縮放 · 分層後點選格點"
            if self.hover and not self.is_hidden():
                hint = "格點 " + str(self.hover) + "  ·  點選交由目前規則判定"
            dpg.set_value("prism_canvas_hint", hint)
        else:
            self.cells = []
            dpg.delete_item("prism_scene_layer", children_only=True)
            dpg.set_value("prism_source_heading", "從原作，開始新的空間。" if self.view == "source" else "準備好你的空間版本。")
            metadata = "選擇完整遊戲專案或可執行入口。"
            if package:
                dims = package.transformation.source_dimensions
                metadata = "{0}\n{1}  ·  {2} 個素材\n來源尺寸  {3} × {4}\n\n{5}".format(title, package.runtime.framework.upper(), len(package.assets), dims.get("x", "?"), dims.get("y", "?"),
                    "保留原作玩法，再探索新的維度。" if self.view == "source" else "先分析來源、批准規則，再生成並批准 Target。")
                if package.runtime.kind == "project_ir":
                    dpg.set_value("prism_source_heading", "已載入生成專案。")
                    metadata = title + "\n\n此輸入是生成版本。\n匯入原始 source 後，才可開啟原作對照。"
            dpg.set_value("prism_source_metadata", metadata)
            dpg.set_value("prism_canvas_hint", "正式遊戲在獨立視窗執行；此處保留創作工作區。")
        if package:
            dims = package.transformation.source_dimensions
            dpg.set_value("prism_compare_text", "{0}\n\n{1} × {2}\n{3}\n\nTARGET / 空間\n".format(title, dims.get("x", "?"), dims.get("y", "?"), package.runtime.framework) + self.target_title() + "\n" + (
                " × ".join(map(str, self.state.dimensions)) if self.state else "尚未批准"))
            if package.runtime.kind == "project_ir":
                dpg.set_value("prism_compare_text", "尚未載入原作。\n請匯入可執行 source。\n\nTARGET / 空間\n" + self.target_title())
