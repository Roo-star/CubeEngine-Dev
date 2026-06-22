import math
import os

from ursina import *
from panda3d.core import Filename

from tictactoe3d_logic import (
    create_board,
    get_cell,
    set_cell,
    is_valid_move,
    game_status,
    ai_move_basic,
)


# ------------------------------------------------------------
# 基本設定
# ------------------------------------------------------------
# 視覺設計思路（全局透視優先 + 魔方風格）：
#   1. 27 個格子緊貼排成一個大立方體，CELL_GAP == 1（格子單位間距=1）。
#   2. 每格略縮（CELL_SCALE<1），相鄰格間露出背景，形成「魔方黑縫」般的格線。
#   3. 【最重要】所有格子一律維持低透明度「玻璃」，確保任何視角都能看穿整個
#      立方體、看到每一顆棋子（包含被包在最中心的格子）。
#      裡外層切換「只」改變哪一層可被選中/高亮，不改變遮擋關係。
#   4. 落子後：該格外殼染上對應隊伍顏色（玩家1藍 / 玩家2紅），一眼可辨。
GRID_SIZE = 3
CELL_GAP = 1.0          # 無間隙：格子緊貼
CELL_SCALE = 0.9        # 每格略縮，留出魔方般的縫隙
HOVER_SCALE = 0.98      # 懸停時稍微放大
BOARD_SCALE = 3.0
ROTATE_SPEED = 180

CAMERA_START_Z = -22
ZOOM_STEP = 1.5
ZOOM_MIN_Z = -34
ZOOM_MAX_Z = -10

MODE_AI = "ai"
MODE_PVP = "pvp"

# 隊伍顏色（棋子 + 落子後格子外殼共用同一色系）
TEAM1_RGB = (70, 140, 255)     # 玩家1：藍
TEAM2_RGB = (255, 75, 75)      # 玩家2 / AI：紅

# 空格玻璃色（低透明度，保證全局透視）：
#   設計依據：玩家覺得「裡層選中時」那種更透的觀感最舒服，故把它做成
#   外層預設；原本外層較實的透明度則調低後給裡層的非選中格使用。
CELL_ALPHA_SELECTABLE = 30     # 外層預設可選格（更透 → 觀察舒服）
CELL_ALPHA_DIM = 16            # 非當前可選層的格（更淡，幾乎只剩輪廓）
CELL_COLOR_SELECTABLE = color.rgba(125, 170, 230, CELL_ALPHA_SELECTABLE)  # 當前可選層的空格
CELL_COLOR_DIM = color.rgba(110, 130, 165, CELL_ALPHA_DIM)                # 非當前可選層的空格
CELL_COLOR_HOVER = color.rgba(255, 215, 90, 150)                          # 懸停高亮（半透明暖黃）

# 落子後格子外殼透明度（仍保持半透明，維持全局透視；比空格略實一點以辨識隊伍色）
CELL_OCCUPIED_ALPHA = 75

# ------------------------------------------------------------
# UI 視覺語言（設計 token）
# ------------------------------------------------------------
# 風格定位：深色科技感、霓虹青色點綴、半透明毛玻璃面板、清楚層級。
UI = {
    # 文字
    "text":        color.rgb(225, 234, 248),   # 主要文字
    "text_dim":    color.rgb(150, 165, 190),   # 次要/提示文字
    "accent":      color.rgb(90, 215, 245),     # 霓虹青：標題/重點
    # 面板
    "panel":       color.rgba(20, 26, 38, 225),     # 毛玻璃面板底
    "panel_line":  color.rgba(90, 215, 245, 90),    # 面板邊框（青色細線感）
    "scrim":       color.rgba(6, 9, 16, 200),       # 結算/選單背後的壓暗遮罩
    # 按鈕
    "btn":         color.rgba(38, 50, 72, 235),     # 按鈕底
    "btn_hi":      color.rgba(58, 78, 110, 255),    # 按鈕 hover
    "btn_accent":  color.rgba(34, 120, 150, 240),   # 主要動作按鈕（青色系）
    "btn_accent_hi": color.rgba(46, 150, 185, 255),
    "btn_text":    color.rgb(232, 244, 252),
}

# 字級（統一比例，避免大小亂跳）
FS_TITLE = 1.5
FS_LABEL = 1.0
FS_HINT = 0.82

# 面朝向玩家時的井字提示線（只亮最正對的單一面）
FACE_HINT_COLOR = color.rgb(120, 225, 250)  # 淡青
FACE_HINT_MAX_ALPHA = 0.7                    # 顯示亮度
FACE_HINT_THICKNESS = 3
FACE_HINT_DOT_THRESHOLD = 0.5               # 面法向與「指向相機」點積 > 此值才算「正對」


def ai_move(board):
    """
    AI 落子接口（固定保留）。
    目前先使用 ai_move_basic；未來可在此替換為訓練好的模型推理。
    """
    return ai_move_basic(board, 2)


def pick_ui_font():
    """優先挑可顯示中文的字型，避免文字顯示方框。

    重要：panda3d 在 Windows 需要用 Filename.from_os_specific 轉換路徑，
    直接給 C:/... 反斜線路徑可能載入失敗而回退到不含中文的預設字型。
    """
    candidates = [
        r"C:\Windows\Fonts\msjh.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            return Filename.from_os_specific(path).get_fullpath()
    return None


class TicTacToe3DUrsina:
    def __init__(self):
        # 字型已在 main 入口設為 Text.default_font；此處再記錄一份供顯式套用。
        self.ui_font = pick_ui_font()

        self.board = create_board()
        self.current_player = 1
        self.mode = MODE_AI
        self.status = "ongoing"
        self.move_history = []  # [(player, x, y, z), ...]
        self.select_inner = False  # False=外層可選, True=只選內層中心

        self.dragging = False
        self.drag_start_mouse = Vec2(0, 0)
        self.drag_start_rotation = Vec3(0, 0, 0)
        self.hovered_cell = None
        self.zoom_target_z = CAMERA_START_Z

        self.player1_color = color.rgb(*TEAM1_RGB)
        self.player2_color = color.rgb(*TEAM2_RGB)
        self.ui_text_color = color.rgba(235, 240, 255, 255)

        self.root = Entity()
        self.board_root = Entity(parent=self.root, rotation=(25, -30, 0), scale=BOARD_SCALE)
        self.cells = {}

        self._create_scene()
        self._create_ui()
        self._show_mode_menu()
        self.refresh_board_visual()

    # --------------------------------------------------------
    # 場景 / UI
    # --------------------------------------------------------
    def _create_scene(self):
        window.title = "3D 井字棋 (Ursina)"
        window.color = color.rgb(18, 22, 30)
        camera.position = (0, 0, CAMERA_START_Z)
        camera.fov = 65

        DirectionalLight(parent=scene, y=2.2, z=-3, rotation=(35, 35, 0))
        AmbientLight(color=color.rgba(190, 195, 215, 0.85))

        for x in range(GRID_SIZE):
            for y in range(GRID_SIZE):
                for z in range(GRID_SIZE):
                    pos = self.grid_to_world(x, y, z)
                    cell = Entity(
                        parent=self.board_root,
                        model="cube",
                        collider="box",
                        position=pos,
                        scale=CELL_SCALE,
                        color=CELL_COLOR_SELECTABLE,
                    )
                    cell.base_scale = CELL_SCALE
                    cell.board_pos = (x, y, z)
                    cell.marker = None
                    cell.marker_kind = 0
                    self.cells[(x, y, z)] = cell

        self._create_face_hints()

    def _create_face_hints(self):
        """為立方體 6 個面各建一組「井字分隔線」（2 橫 2 縱）。

        平時全透明；update() 時依各面是否朝向玩家動態調整亮度，
        讓最朝向相機的那一面的井字線淺淺亮起，作為該面的視覺參考。
        """
        # 6 個面：本地外法向量 + 該面所在的常數座標值（棋盤外緣 ±1.5）
        half = (GRID_SIZE - 1) / 2 * CELL_GAP + 0.5 * CELL_GAP  # = 1.5
        line_min, line_max = -half, half
        div = [-0.5 * CELL_GAP, 0.5 * CELL_GAP]  # 兩條分隔線的位置

        # 每個面：(法向量, 產生 4 條線段端點的函式)
        faces = [
            Vec3(0, 0, 1), Vec3(0, 0, -1),
            Vec3(0, 1, 0), Vec3(0, -1, 0),
            Vec3(1, 0, 0), Vec3(-1, 0, 0),
        ]

        self.face_hints = []  # [(normal, entity), ...]
        for normal in faces:
            verts, segments = self._face_grid_segments(normal, half, line_min, line_max, div)
            # 關鍵：mode='line' 時把每條線當成 triangles 的一個 entry，
            # Mesh 會為每個 entry 建「獨立」的線段，避免被連成一條折線
            # 而產生穿過立方體的斜對角線。
            ent = Entity(
                parent=self.board_root,
                model=Mesh(
                    vertices=verts,
                    triangles=segments,
                    mode="line",
                    thickness=FACE_HINT_THICKNESS,
                ),
                color=color.rgba(FACE_HINT_COLOR.r * 255, FACE_HINT_COLOR.g * 255,
                                 FACE_HINT_COLOR.b * 255, 0),
                unlit=True,
            )
            ent.collider = None
            self.face_hints.append((normal, ent))

    @staticmethod
    def _face_grid_segments(normal, plane, lo, hi, div):
        """產生某一面上「井字」的 4 條分隔線（2 橫 2 縱）。

        回傳 (vertices, segments)：
          vertices = 所有端點；
          segments = [[i0, i1], [i2, i3], ...]，每對索引代表一條獨立線段。
        """
        verts = []
        segments = []
        nx, ny, nz = normal
        c = plane  # 固定軸的常數值

        def add_seg(p0, p1):
            i = len(verts)
            verts.append(p0)
            verts.append(p1)
            segments.append([i, i + 1])

        if nz != 0:
            # 面固定 z=±c，面內為 (x, y)
            z = c * nz
            for x in div:  # 2 條縱線（沿 y）
                add_seg(Vec3(x, lo, z), Vec3(x, hi, z))
            for y in div:  # 2 條橫線（沿 x）
                add_seg(Vec3(lo, y, z), Vec3(hi, y, z))
        elif ny != 0:
            # 面固定 y=±c，面內為 (x, z)
            y = c * ny
            for x in div:
                add_seg(Vec3(x, y, lo), Vec3(x, y, hi))
            for zz in div:
                add_seg(Vec3(lo, y, zz), Vec3(hi, y, zz))
        else:
            # 面固定 x=±c，面內為 (y, z)
            x = c * nx
            for y in div:
                add_seg(Vec3(x, y, lo), Vec3(x, y, hi))
            for zz in div:
                add_seg(Vec3(x, lo, zz), Vec3(x, hi, zz))

        return verts, segments

    def _make_text(self, text, **kwargs):
        """建立 Text 並顯式套用中文字型，避免顯示方框/空白。"""
        kwargs.setdefault("color", self.ui_text_color)
        t = Text(text=text, **kwargs)
        if self.ui_font:
            t.font = self.ui_font
            t.text = text  # 重設文字以套用新字型重繪
        return t

    def _make_button(self, text, accent=False, **kwargs):
        """建立風格化 Button：毛玻璃底 + hover 高亮 + 中文字型。

        accent=True 時用青色系（主要動作，如「再來一局」「確認」）。
        """
        base_color = UI["btn_accent"] if accent else UI["btn"]
        hi_color = UI["btn_accent_hi"] if accent else UI["btn_hi"]

        kwargs.setdefault("text_color", UI["btn_text"])
        kwargs.setdefault("color", base_color)
        kwargs.setdefault("radius", 0.28)  # 圓角，現代感
        b = Button(text=text, **kwargs)
        b.highlight_color = hi_color
        b.pressed_color = base_color.tint(-0.15)

        if self.ui_font and b.text_entity:
            b.text_entity.font = self.ui_font
            b.text_entity.text = text
            b.text_entity.color = kwargs["text_color"]
        return b

    def _make_panel(self, scale, **kwargs):
        """建立毛玻璃風格面板：半透明底 + 青色細邊框。"""
        kwargs.setdefault("parent", camera.ui)
        kwargs.setdefault("model", "quad")
        panel = Entity(scale=scale, color=UI["panel"], **kwargs)
        # 青色邊框：用一個略大的線框 quad 疊在後面當描邊
        Entity(
            parent=panel,
            model=Quad(radius=0.04, mode="line", thickness=2),
            color=UI["panel_line"],
            z=0.01,
        )
        return panel

    def _create_ui(self):
        self._create_hud()
        self._create_result_panel()
        self._create_mode_menu()

    def _create_hud(self):
        """左上角狀態資訊面板 + 右上角操作按鈕 + 底部提示。"""
        # 左上資訊面板（毛玻璃）
        info = Entity(
            parent=camera.ui, model=Quad(radius=0.06), color=UI["panel"],
            scale=(0.40, 0.27), position=(-0.66, 0.34),
        )
        Entity(parent=info, model=Quad(radius=0.06, mode="line", thickness=2),
               color=UI["panel_line"], z=0.01)

        self._make_text("3D 井字棋", parent=camera.ui, position=(-0.85, 0.45),
                        scale=FS_TITLE, color=UI["accent"])

        self.mode_text = self._make_text(
            "模式: 人機對戰", parent=camera.ui, position=(-0.85, 0.39), scale=FS_LABEL)
        self.turn_text = self._make_text(
            "輪到: 玩家1", parent=camera.ui, position=(-0.85, 0.34), scale=FS_LABEL)
        self.state_text = self._make_text(
            "狀態: 對局進行中", parent=camera.ui, position=(-0.85, 0.29), scale=FS_LABEL)
        self.layer_text = self._make_text(
            "選擇層: 外層", parent=camera.ui, position=(-0.85, 0.24), scale=FS_LABEL,
            color=UI["text_dim"])

        # 右上操作按鈕（垂直排列，統一尺寸/間距）
        bx, by, gap = 0.66, 0.42, 0.085
        self.restart_btn = self._make_button(
            "重新開始", parent=camera.ui, scale=(0.2, 0.07),
            position=(bx, by), on_click=self.restart_game)
        self.undo_btn = self._make_button(
            "悔棋", parent=camera.ui, scale=(0.2, 0.07),
            position=(bx, by - gap), on_click=self.undo_move)
        self.switch_btn = self._make_button(
            "切換模式", parent=camera.ui, scale=(0.2, 0.07),
            position=(bx, by - gap * 2), on_click=self.toggle_mode)

        # 底部操作提示（次要色）
        self._make_text(
            "左鍵: 指到格子=落子 / 空白拖拽=旋轉      右鍵: 外/裡層切換      滾輪: 縮放",
            parent=camera.ui, position=(0, -0.46), origin=(0, 0),
            scale=FS_HINT, color=UI["text_dim"])

    def _create_result_panel(self):
        """勝負結算面板。重點修正：避免文字被父層非等比縮放壓扁。

        做法：面板底用一個 quad，但「文字」直接掛在 camera.ui（等比），
        只靠 position 對齊到面板中央，這樣文字不會被面板的長寬比拉扁。
        """
        self.result_overlay = Entity(parent=camera.ui, enabled=False)

        # 全螢幕壓暗遮罩
        Entity(parent=self.result_overlay, model="quad", scale=(2, 1),
               color=UI["scrim"], z=0.02)
        # 中央面板底（毛玻璃 + 青邊框）
        Entity(parent=self.result_overlay, model=Quad(radius=0.08),
               color=UI["panel"], scale=(0.5, 0.34), z=0.01)
        Entity(parent=self.result_overlay,
               model=Quad(radius=0.08, mode="line", thickness=2),
               color=UI["panel_line"], scale=(0.5, 0.34), z=0.005)

        # 標題（大）與副標（步數），分兩個 Text，各自等比、行距充足
        self.result_title = self._make_text(
            "", parent=self.result_overlay, position=(0, 0.10), origin=(0, 0),
            scale=1.8, color=UI["accent"])
        self.result_sub = self._make_text(
            "", parent=self.result_overlay, position=(0, 0.012), origin=(0, 0),
            scale=1.0, color=UI["text"])

        self.result_restart_btn = self._make_button(
            "再來一局", accent=True, parent=self.result_overlay,
            scale=(0.24, 0.085), position=(0, -0.09),
            on_click=self.restart_game)

    def _create_mode_menu(self):
        """開局模式選擇面板。"""
        self.mode_menu = Entity(parent=camera.ui, enabled=False)
        Entity(parent=self.mode_menu, model="quad", scale=(2, 1),
               color=UI["scrim"], z=0.02)
        Entity(parent=self.mode_menu, model=Quad(radius=0.08),
               color=UI["panel"], scale=(0.5, 0.42), z=0.01)
        Entity(parent=self.mode_menu,
               model=Quad(radius=0.08, mode="line", thickness=2),
               color=UI["panel_line"], scale=(0.5, 0.42), z=0.005)

        self._make_text("選擇對戰模式", parent=self.mode_menu, position=(0, 0.13),
                        origin=(0, 0), scale=1.5, color=UI["accent"])
        self._make_button(
            "人機對戰", accent=True, parent=self.mode_menu,
            scale=(0.28, 0.09), position=(0, 0.0),
            on_click=lambda: self.set_mode(MODE_AI))
        self._make_button(
            "雙人對戰", parent=self.mode_menu,
            scale=(0.28, 0.09), position=(0, -0.12),
            on_click=lambda: self.set_mode(MODE_PVP))

    # --------------------------------------------------------
    # 互動邏輯
    # --------------------------------------------------------
    def input(self, key):
        if key == "right mouse down" and self.status == "ongoing":
            self.select_inner = not self.select_inner
            self.refresh_board_visual()
            self.update_status_texts()

        if key == "scroll up":
            self.zoom_target_z = clamp(self.zoom_target_z + ZOOM_STEP, ZOOM_MIN_Z, ZOOM_MAX_Z)
        if key == "scroll down":
            self.zoom_target_z = clamp(self.zoom_target_z - ZOOM_STEP, ZOOM_MIN_Z, ZOOM_MAX_Z)

        if key == "left mouse down" and not self.mode_menu.enabled:
            if self.try_click_place():
                return
            # 只有滑鼠在空白處時，才進入拖拽旋轉。
            if mouse.hovered_entity is None:
                self.dragging = True
                self.drag_start_mouse = Vec2(mouse.x, mouse.y)
                self.drag_start_rotation = Vec3(
                    self.board_root.rotation_x,
                    self.board_root.rotation_y,
                    self.board_root.rotation_z,
                )

        if key == "left mouse up":
            self.dragging = False

    def update(self):
        if self.dragging:
            dx = mouse.x - self.drag_start_mouse.x
            dy = mouse.y - self.drag_start_mouse.y
            # 修正方向：拖拽方向與旋轉方向一致
            self.board_root.rotation_y = self.drag_start_rotation.y - dx * ROTATE_SPEED
            self.board_root.rotation_x = clamp(
                self.drag_start_rotation.x + dy * ROTATE_SPEED,
                -85,
                85,
            )

        camera.z = lerp(camera.z, self.zoom_target_z, time.dt * 10)
        self.update_hover_highlight()
        self.update_face_hints()

    def update_face_hints(self):
        """只讓「最正對玩家的那一個面」顯示井字線，其餘面全部隱藏。

        重點修正：之前 6 面會同時/漸變亮起，側面的井字線在斜視角下會投影成
        穿過立方體的斜線（看起來亂）。改成「永遠只顯示朝向相機最強的單一面」，
        該面的線就是乾淨的 2 橫 2 縱，不會再出現斜對角線。
        """
        # 相機看向 +Z（相機在 -Z），故「指向相機」= 世界 -Z 方向。
        to_camera = Vec3(0, 0, -1)
        world_quat = self.board_root.getQuat(base.render)

        # 先找出最正對玩家的面
        best_idx = -1
        best_facing = FACE_HINT_DOT_THRESHOLD
        for i, (normal, _ent) in enumerate(self.face_hints):
            world_n = Vec3(world_quat.xform(Vec3(normal)))
            facing = world_n.dot(to_camera)
            if facing > best_facing:
                best_facing = facing
                best_idx = i

        # 只點亮該面，其餘全部透明
        transparent = color.rgba(
            FACE_HINT_COLOR.r * 255, FACE_HINT_COLOR.g * 255,
            FACE_HINT_COLOR.b * 255, 0)
        lit = color.rgba(
            FACE_HINT_COLOR.r * 255, FACE_HINT_COLOR.g * 255,
            FACE_HINT_COLOR.b * 255, FACE_HINT_MAX_ALPHA * 255)

        for i, (_normal, ent) in enumerate(self.face_hints):
            ent.color = lit if i == best_idx else transparent

    def update_hover_highlight(self):
        self.hovered_cell = None
        if self.status == "ongoing" and not self.mode_menu.enabled:
            if not (self.mode == MODE_AI and self.current_player != 1):
                hovered = mouse.hovered_entity
                if hovered and hasattr(hovered, "board_pos"):
                    x, y, z = hovered.board_pos
                    if self.is_cell_selectable(x, y, z) and is_valid_move(self.board, x, y, z):
                        self.hovered_cell = hovered

        for (x, y, z), cell in self.cells.items():
            if get_cell(self.board, x, y, z) == 0:
                self.apply_empty_cell_visual(cell, x, y, z, hovered=(cell == self.hovered_cell))

    def try_click_place(self):
        if self.status != "ongoing":
            return False
        if self.mode == MODE_AI and self.current_player != 1:
            return False

        hovered = mouse.hovered_entity
        if not hovered or not hasattr(hovered, "board_pos"):
            return False

        x, y, z = hovered.board_pos
        if not self.is_cell_selectable(x, y, z):
            return False
        if not is_valid_move(self.board, x, y, z):
            return False

        self.place_move(x, y, z, self.current_player)
        return True

    # --------------------------------------------------------
    # 遊戲流程
    # --------------------------------------------------------
    def place_move(self, x, y, z, player):
        if not is_valid_move(self.board, x, y, z):
            return

        set_cell(self.board, x, y, z, player)
        self.move_history.append((player, x, y, z))
        self.refresh_board_visual()

        self.status = game_status(self.board)
        if self.status != "ongoing":
            self.on_game_end()
            return

        self.current_player = 2 if player == 1 else 1
        self.update_status_texts()

        if self.mode == MODE_AI and self.current_player == 2:
            invoke(self.ai_turn, delay=0.35)

    def ai_turn(self):
        if self.mode != MODE_AI or self.status != "ongoing" or self.current_player != 2:
            return

        move = ai_move(self.board)
        if move is None:
            return

        x, y, z = move
        if not is_valid_move(self.board, x, y, z):
            return

        self.place_move(x, y, z, 2)

    def undo_move(self):
        if not self.move_history:
            return

        if self.mode == MODE_AI:
            # 人機模式：儘量回到「輪到玩家1下」。
            # 一般情況會退兩步（我方 + AI）；若回合不完整則退一步。
            steps = 2 if len(self.move_history) % 2 == 0 else 1
        else:
            # 雙人模式：退最近一步。
            steps = 1

        steps = min(steps, len(self.move_history))
        for _ in range(steps):
            _, x, y, z = self.move_history.pop()
            set_cell(self.board, x, y, z, 0)

        self.status = game_status(self.board)
        self.result_overlay.enabled = False
        self.dragging = False

        if self.mode == MODE_AI:
            self.current_player = 1
        else:
            self.current_player = 1 if len(self.move_history) % 2 == 0 else 2

        self.refresh_board_visual()
        self.update_status_texts()

    def restart_game(self):
        self.board = create_board()
        self.current_player = 1
        self.status = "ongoing"
        self.move_history.clear()
        self.result_overlay.enabled = False
        self.dragging = False
        self.refresh_board_visual()
        self.update_status_texts()

    def toggle_mode(self):
        next_mode = MODE_PVP if self.mode == MODE_AI else MODE_AI
        self.set_mode(next_mode)

    def set_mode(self, mode):
        self.mode = mode
        self.mode_menu.enabled = False
        self.result_overlay.enabled = False
        self.restart_game()

    def _show_mode_menu(self):
        self.result_overlay.enabled = False
        self.mode_menu.enabled = True
        self.update_status_texts()

    def on_game_end(self):
        self.update_status_texts()
        self.result_overlay.enabled = True

        if self.status == "draw":
            self.result_title.text = "平局"
            self.result_title.color = UI["text"]
            self.result_sub.text = "棋盤已滿，未分勝負"
            return

        winner = 1 if self.status == "win_1" else 2
        winner_moves = sum(1 for p, _, _, _ in self.move_history if p == winner)

        if winner == 1:
            name = "玩家1"
            self.result_title.color = color.rgb(*TEAM1_RGB)
        else:
            name = "AI" if self.mode == MODE_AI else "玩家2"
            self.result_title.color = color.rgb(*TEAM2_RGB)

        self.result_title.text = f"{name} 獲勝"
        self.result_sub.text = f"在第 {winner_moves} 步獲勝"

    # --------------------------------------------------------
    # 視覺更新
    # --------------------------------------------------------
    def refresh_board_visual(self):
        for (x, y, z), cell in self.cells.items():
            value = get_cell(self.board, x, y, z)

            if value == 0:
                if cell.marker:
                    destroy(cell.marker)
                    cell.marker = None
                cell.marker_kind = 0
                self.apply_empty_cell_visual(cell, x, y, z, hovered=False)
            else:
                self.apply_occupied_cell_visual(cell, x, y, z, value)

        self.update_status_texts()

    def apply_empty_cell_visual(self, cell, x, y, z, hovered=False):
        """空格：低透明度玻璃，永遠看得穿（全局透視）。

        裡外層切換「只」影響哪一層可被選中/高亮，不改變遮擋：
          - 當前可選層的空格：稍亮一點 + 可點。
          - 非當前可選層的空格：更淡一點 + 不可點。
        兩者都很透明，不會擋住任何棋子。
        """
        selectable = self.is_cell_selectable(x, y, z)

        if hovered:
            cell.color = CELL_COLOR_HOVER
            cell.scale = HOVER_SCALE
            cell.collider = "box"
            return

        cell.scale = cell.base_scale

        if selectable:
            cell.color = CELL_COLOR_SELECTABLE
            cell.collider = "box"
        else:
            cell.color = CELL_COLOR_DIM
            cell.collider = None

    def apply_occupied_cell_visual(self, cell, x, y, z, value):
        """已落子格：外殼染上隊伍色（藍/紅）的半透明玻璃，內部放 3D X / O。

        外殼維持半透明，確保即使是最中心的格子落子後，從任何視角、
        任何層模式都能清楚看到它的顏色與棋子（全局透視優先）。
        """
        if cell.marker is None or cell.marker_kind != value:
            if cell.marker:
                destroy(cell.marker)
            cell.marker = self.create_marker(value, cell)
            cell.marker_kind = value

        cell.scale = cell.base_scale

        team_rgb = TEAM1_RGB if value == 1 else TEAM2_RGB
        cell.color = color.rgba(*team_rgb, CELL_OCCUPIED_ALPHA)
        cell.collider = None

    def create_marker(self, player, parent_cell):
        """建立棋子：玩家1=單一個乾淨 3D X，玩家2=單一個乾淨 3D O（不疊層）。"""
        marker_root = Entity(parent=parent_cell)

        if player == 1:
            # 3D X：單一組兩條交叉的立方長條（有厚度 → 立體），不再疊兩層。
            for angle in (45, -45):
                Entity(
                    parent=marker_root,
                    model="cube",
                    scale=(0.7, 0.16, 0.16),
                    rotation=(0, 0, angle),
                    color=self.player1_color,
                )
        else:
            # 3D O：單一個圓環（用小球沿一個圓排列，只一圈，不再疊兩圈）。
            segment_count = 20
            radius = 0.32
            for i in range(segment_count):
                angle = (2 * math.pi * i) / segment_count
                px = math.cos(angle) * radius
                py = math.sin(angle) * radius
                Entity(
                    parent=marker_root,
                    model="sphere",
                    scale=0.14,
                    position=(px, py, 0),
                    color=self.player2_color,
                )

        return marker_root

    def update_status_texts(self):
        self.mode_text.text = "模式: 人機對戰" if self.mode == MODE_AI else "模式: 雙人對戰"
        self.layer_text.text = "選擇層: 裡層中心" if self.select_inner else "選擇層: 外層"

        if self.status == "ongoing":
            turn_name = "玩家1" if self.current_player == 1 else ("AI" if self.mode == MODE_AI else "玩家2")
            self.turn_text.text = f"輪到: {turn_name}"
            self.state_text.text = "狀態: 對局進行中"
        elif self.status == "draw":
            self.turn_text.text = "輪到: -"
            self.state_text.text = "狀態: 平局"
        elif self.status == "win_1":
            self.turn_text.text = "輪到: -"
            self.state_text.text = "狀態: 玩家1 獲勝"
        elif self.status == "win_2":
            self.turn_text.text = "輪到: -"
            win_name = "AI" if self.mode == MODE_AI else "玩家2"
            self.state_text.text = f"狀態: {win_name} 獲勝"

    # --------------------------------------------------------
    # 工具方法
    # --------------------------------------------------------
    @staticmethod
    def grid_to_world(x, y, z):
        offset = (GRID_SIZE - 1) / 2
        return Vec3(
            (x - offset) * CELL_GAP,
            (y - offset) * CELL_GAP,
            (z - offset) * CELL_GAP,
        )

    def is_cell_selectable(self, x, y, z):
        if self.select_inner:
            return (x, y, z) == (1, 1, 1)
        return (x, y, z) != (1, 1, 1)


if __name__ == "__main__":
    app = Ursina()

    # 在建立任何 Text/Button 之前設好預設中文字型，否則文字會顯示成方框/空白。
    _font = pick_ui_font()
    if _font:
        Text.default_font = _font

    game = TicTacToe3DUrsina()

    def input(key):
        game.input(key)

    def update():
        game.update()

    app.run()
