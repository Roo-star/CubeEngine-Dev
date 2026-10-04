"""Ursina host for approved Project IR. Rules and input come from the bundle."""
from pathlib import Path
import argparse

from .ir_acceptance import IRAcceptanceController
from .input_ir_v2 import PhysicalInputEvent
from .llm_compiler_v1.compiler import load_compile_report_from_bundle
from .scene_presentation import ScenePresentation


class ProjectHost:
    def __init__(self, manifest, source_root=None):
        manifest = Path(manifest).resolve()
        report = load_compile_report_from_bundle(manifest.parent, manifest_path=manifest)
        if not report.compile_ready:
            raise ValueError('Approve the Project Manifest before 3D Play.')
        self.controller = IRAcceptanceController(Path(__file__).resolve().parents[1], autoload_reference=False)
        try:
            self.controller.open_project_bundle(manifest, asset_project_root=source_root)
            self.snapshot = self.controller.snapshot()
            if len(self.snapshot.dimensions) not in (2, 3):
                raise ValueError('3D board presentation supports 2D and 3D grid topologies.')
        except Exception:
            self.controller.close()
            raise
        self.sequence = 0
        self.quit_requested = False
        self.rule = report.documents['rule_ir']
        bundle = self.controller.bundles[self.controller.active_key]
        self.presentation = ScenePresentation(bundle.scene, bundle.assets, legacy_appearance=True,
            volume_rule=self.rule if report.manifest.get('variant')=='target' else None)
        self.projection = bundle.scene.create_projection_session()
        self.interaction = {}
        self.refresh_scene()

    @property
    def presentation_notice(self):
        return ('Legacy appearance: compatibility markers; source visual fidelity not verified.'
                if self.presentation.compatibility_nodes else '')

    def click(self, coordinate):
        return self._host_result(self.controller.click(coordinate))

    def _host_result(self,result):
        if result.accepted and 'quit' in result.host_commands:
            self.quit_requested=True
        return result

    def _next_sequence(self):
        key = self.controller.active_key
        value = self.controller.sequence.get(key, 0) + 1
        self.controller.sequence[key] = value
        self.sequence = value
        return value

    def key(self, control, phase='press'):
        return self._host_result(self.controller.dispatch_physical(PhysicalInputEvent(self._next_sequence(), 'keyboard', control, phase)))

    def advance(self, seconds):
        session = self.controller.sessions[self.controller.active_key]
        if self.rule.get('flow', {}).get('scheduler', {}).get('clock') in ('fixed_tick', 'real_time'):
            session.rule_runtime.advance_time_ns(max(0, int(seconds * 1_000_000_000)))
            session.scene_projection.synchronize(session.rule_runtime.state)

    def refresh_scene(self):
        session = self.controller.sessions[self.controller.active_key]
        return self.presentation.synchronize(self.projection, session.rule_runtime.state, self.interaction)

    def mouse(self, control, context, phase='press', *, click=False, gesture=None,
              direction=None, distance_px=0):
        sequence = self._next_sequence()
        from .input_pointer_contract import pointer_data,scene_pick_context
        if context.get('node_id'):
            context=scene_pick_context(self.presentation.nodes,context['node_id'],context)
        data = pointer_data(context,click=click,gesture=gesture,direction=direction,
                            distance_px=distance_px)
        return self._host_result(self.controller.dispatch_physical(PhysicalInputEvent(
            sequence, 'mouse', control, phase, position=(0, 0), data=data)))

    def mouse_click(self, control, context):
        pressed=self.mouse(control,context,click=True)
        released=self.mouse(control,context,'release',click=True)
        return released if released.accepted or pressed.code=='input_not_resolved' else pressed

    def mouse_gesture(self, control, context, gesture, *, direction=None, distance_px=0):
        pressed=self.mouse(control,context,gesture=gesture,direction=direction,distance_px=distance_px)
        released=self.mouse(control,context,'release',gesture=gesture,direction=direction,
                            distance_px=distance_px)
        return released if released.accepted or pressed.code=='input_not_resolved' else pressed

    def close(self):
        self.controller.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--smoke', action='store_true', help='Render briefly and exit for host verification.')
    parser.add_argument('--screenshot', type=Path, help='Save this rendered board for verification.')
    parser.add_argument('--ai-opponent', choices=('off', 'first', 'second'), default='off',
                        help='Play against the newest trained AI model (first: AI moves first).')
    parser.add_argument('--ai-difficulty', choices=('easy', 'medium', 'hard'), default='hard',
                        help='Default AI difficulty in the game menu (chance of the best move: 40/60/80%%).')
    parser.add_argument('--ai-training-root', type=Path, action='append',
                        help='Folder of training runs (default: ai_training next to the Target bundle).')
    args = parser.parse_args()
    host = ProjectHost(args.manifest, args.source_root)
    driver = None
    if args.ai_opponent != 'off':
        import threading
        from .ai_training.opponent import AiOpponent, AiTurnDriver
        roots = args.ai_training_root or [args.manifest.resolve().parent.parent / 'ai_training']
        opponent = AiOpponent(host.rule, roots, args.ai_opponent, difficulty=args.ai_difficulty)
        threading.Thread(target=opponent.prepare, daemon=True).start()  # PyTorch import / adapter off the UI thread
        driver = AiTurnDriver(host, opponent)
    from ursina import Ursina, Entity, EditorCamera, Text, Button, color, mouse, time, application, invoke, window, camera
    from panda3d.core import Filename
    font = Path('C:/Windows/Fonts/arial.ttf')
    if font.exists():
        from .ursina_fonts import ursina_font
        Text.default_font = ursina_font(font)
    app = Ursina(borderless=False, fullscreen=False, size=(1280, 800), development_mode=False)
    window.title = 'CubeEngine - Project IR / Ursina 3D'
    from .ursina_scene_backend import UrsinaSceneBackend, rgba255
    window.color = rgba255(20, 25, 36)
    dims = host.snapshot.dimensions
    depth = dims[2] if len(dims) == 3 else 1
    errors = host.presentation.diagnostics()
    if errors:
        host.close()
        raise ValueError('Compiled presentation cannot run: ' + '; '.join(errors[:6]))
    backend = UrsinaSceneBackend(host.presentation, args.manifest.parent / 'render_cache')
    camera_rig = None
    menu = None  # the AI game menu (set below when there is an AI opponent)
    class ProjectView(Entity):
        def __init__(self):
            super().__init__()
            self.layer = None
            self.slice_only = False
            self.failed = False
            self.last_revision = None
            from .pointer_gesture import PointerGesture
            self.pointer = PointerGesture()
            self.message = ('Right-drag: orbit   Wheel: zoom' + chr(10) +
                            ('[ / ]: select depth   Z: only this layer   V: all layers   Restart: new game'
                             if host.presentation.volume_layout else
                             '[ / ]: select depth   Restart: new game'))
            self.header = Text(text='', position=(-window.aspect_ratio/2+.02, .47), scale=.7)
            # Which AI model this game is played against (top right, under the buttons).
            self.ai_version = Text(text='', origin=(.5, .5), position=(window.aspect_ratio/2-.02, .36),
                                   scale=.95) if driver is not None else None
            right = window.aspect_ratio/2 - .08
            self.controls = [
                Button(text='All layers', position=(right-.33,.46), scale=(.15,.045), on_click=lambda:self.set_layer(None)),
                Button(text='Previous', position=(right-.16,.46), scale=(.14,.045), on_click=lambda:self.step_layer(-1)),
                Button(text='Next', position=(right,.46), scale=(.12,.045), on_click=lambda:self.step_layer(1)),
                Button(text='Restart', position=(right,.40), scale=(.12,.045), on_click=self.restart)]
            self.slice_button = None
            if host.presentation.volume_layout:
                self.slice_button = Button(text='Only this layer', position=(right-.46,.40),
                                           scale=(.18,.045), on_click=self.toggle_slice)
                self.controls.append(self.slice_button)
            # Opens the AI game menu (sides and difficulty).
            self.menu_button = Button(text='Menu', position=(-window.aspect_ratio/2+.08, .30), scale=(.12,.045),
                                      on_click=lambda: menu.open()) if driver is not None else None
            self.refresh()

        def restart(self):
            self.pointer.clear()
            host.interaction = {}
            host.controller.reset()
            self.failed = False
            self.refresh()

        def set_layer(self, layer):
            self.pointer.clear()
            host.interaction = {}
            self.layer = layer
            if self.slice_only:
                if layer is None:
                    self.slice_only = False
                if camera_rig is not None:
                    camera_rig.focus_depth(layer)
            self.refresh()

        def toggle_slice(self):
            if not host.presentation.volume_layout:
                return
            self.pointer.clear()
            host.interaction = {}
            self.slice_only = not self.slice_only
            if self.slice_only and self.layer is None:
                self.layer = 0
            if camera_rig is not None:
                camera_rig.focus_depth(self.layer if self.slice_only else None)
            self.refresh()

        def step_layer(self, delta):
            current = -1 if self.layer is None else self.layer
            value = (current + delta + 1) % (depth + 1) - 1
            self.set_layer(None if value == -1 else value)

        def refresh(self):
            host.refresh_scene()
            backend.selected_layer = self.layer
            backend.slice_layer = self.layer if self.slice_only else None
            backend.sync(incremental=True)
            state = host.controller.snapshot()
            self.last_revision = state.revision
            self.header.text = (state.label + ' | ' + ' x '.join(map(str, dims)) +
                ' | depth: ' + ('all' if self.layer is None else str(self.layer)) +
                (' (only this layer)' if self.slice_only else '') +
                '\n' + (('Finished: ' + ', '.join(state.winners)) if state.terminal else 'Turn: ' + state.current_actor_name) +
                '\n' + self.message + ('\n' + host.presentation_notice if host.presentation_notice else ''))
            if self.ai_version is not None:
                self.ai_version.text = '<orange>' + driver.version_line  # a colour tag survives text updates
            if self.slice_button is not None:
                self.slice_button.text = 'Show every layer' if self.slice_only else 'Only this layer'

        def input(self, key):
            if menu is not None and menu.enabled:
                return  # the menu is open: nothing reaches the board
            try:
                if key in ('[', ']'):
                    # All cells remain visible. Only picking changes with depth.
                    self.step_layer(1 if key == ']' else -1)
                elif host.presentation.volume_layout and key == 'z':
                    self.toggle_slice()
                elif host.presentation.volume_layout and key == 'v':
                    self.set_layer(None)
                elif driver is not None and driver.ai_to_move():
                    # Nothing reaches Input IR on the AI's turn; view keys above and Restart still work.
                    if key.endswith('mouse down') or len(key) == 1:
                        self.message = 'AI is thinking... (Restart is still available)'
                elif key in ('left mouse down', 'right mouse down', 'left mouse up', 'right mouse up'):
                    context = backend.pick_context(mouse.hovered_entity)
                    if not context and mouse.hovered_entity is None:
                        context = {'background':True}
                    control = 'mouse.button.primary' if key.startswith('left') else 'mouse.button.secondary'
                    position = (mouse.x, mouse.y)
                    if key.endswith('down'):
                        if context:
                            self.pointer.press(control, position, context)
                    else:
                        gesture = self.pointer.release_event(control, position, context,
                                                             viewport_height=window.size[1])
                        if gesture and gesture['gesture']=='click':
                            self.message = host.mouse_click(control,gesture['context']).message
                        elif gesture and gesture['gesture'] in ('swipe','background_click'):
                            self.message = host.mouse_gesture(control,gesture['context'],gesture['gesture'],
                                direction=gesture.get('direction'),distance_px=gesture.get('distance_px',0)).message
                    host.interaction=self.pointer.presentation_state(context)
                else:
                    from .input_adapter_contract import keyboard_event
                    event=keyboard_event(key)
                    if event:
                        result=host.key(*event)
                        if result.code!='input_not_resolved':
                            self.message = result.message
                if host.quit_requested:
                    application.quit()
                    return
                self.refresh()
            except Exception as exc:
                self.failed = True
                self.header.text = 'Runtime error: ' + str(exc)

        def update(self):
            if self.failed:
                return
            try:
                self.header.x = -window.aspect_ratio/2+.02
                if self.ai_version is not None:
                    self.ai_version.x = window.aspect_ratio/2-.02
                if self.menu_button is not None:
                    self.menu_button.x = -window.aspect_ratio/2+.08
                right = window.aspect_ratio/2-.08
                offsets = ((-.33,-.16,0,0,-.46,-.16) if self.slice_button is not None
                           else (-.33,-.16,0,0,-.16))
                for button, offset in zip(self.controls, offsets):
                    button.x = right+offset
                backend.hover(mouse.hovered_entity)
                backend.advance_visuals(min(time.dt, .25))
                self.pointer.move((mouse.x, mouse.y))
                interaction=self.pointer.presentation_state(backend.pick_context(mouse.hovered_entity))
                interaction_changed=interaction!=host.interaction
                host.interaction=interaction
                host.advance(min(time.dt, .25))
                ai_changed = driver is not None and driver.tick()
                state = host.controller.sessions[host.controller.active_key].rule_runtime.state
                if state.revision != self.last_revision or interaction_changed or ai_changed:
                    self.refresh()
            except Exception as exc:
                self.failed = True
                self.header.text = 'Runtime error: ' + str(exc)
    view = ProjectView()
    from .project_camera import ProjectCameraRig
    camera_rig = ProjectCameraRig(backend)
    if camera_rig.notice:
        view.message = camera_rig.notice + '\n' + view.message
        view.refresh()
    def restore_camera():
        camera_rig.restore()
        view.slice_only = False
        view.pointer.clear()
        host.interaction = {}
        view.refresh()
    view.controls.append(Button(text='Reset view', position=(window.aspect_ratio/2-.24,.40), scale=(.14,.045), on_click=restore_camera))
    if driver is not None:
        from .ai_training.opponent import DIFFICULTIES
        normal, chosen = rgba255(60, 66, 80), rgba255(214, 140, 30)

        class GameMenu(Entity):
            """Main menu for games with an AI opponent: who moves first, and how often the AI plays its best move."""

            def __init__(self):
                super().__init__(parent=camera.ui, z=-.5)
                self.ai_side = driver.opponent.side
                self.difficulty = driver.opponent.difficulty
                Entity(parent=self, model='quad', color=rgba255(14, 18, 28, 240), scale=(1.0, .7), z=.1)
                Text(parent=self, text='Play against the AI', origin=(0, 0), position=(0, .27), scale=1.8)
                Text(parent=self, text='Who moves first?', origin=(0, 0), position=(0, .17), scale=1.0)
                self.side_buttons = {
                    'second': Button(parent=self, text='You move first', position=(-.17, .10), scale=(.3, .065),
                                     on_click=lambda: self.pick(side='second')),
                    'first': Button(parent=self, text='AI moves first', position=(.17, .10), scale=(.3, .065),
                                    on_click=lambda: self.pick(side='first'))}
                Text(parent=self, text='AI difficulty (chance it plays its best move)', origin=(0, 0),
                     position=(0, .01), scale=1.0)
                self.difficulty_buttons = {}
                for name, x in zip(('easy', 'medium', 'hard'), (-.28, 0, .28)):
                    label = '{0} ({1:.0%})'.format(name.capitalize(), DIFFICULTIES[name])
                    self.difficulty_buttons[name] = Button(parent=self, text=label, position=(x, -.06),
                                                           scale=(.25, .065),
                                                           on_click=lambda name=name: self.pick(difficulty=name))
                Button(parent=self, text='Start game', position=(0, -.18), scale=(.3, .075),
                       color=rgba255(46, 125, 50), on_click=self.start_game)
                self.note = Text(parent=self, text='', origin=(0, 0), position=(0, -.29), scale=.8)
                self.open()

            def pick(self, side=None, difficulty=None):
                self.ai_side = side or self.ai_side
                self.difficulty = difficulty or self.difficulty
                for key, button in self.side_buttons.items():
                    button.color = chosen if key == self.ai_side else normal
                for key, button in self.difficulty_buttons.items():
                    button.color = chosen if key == self.difficulty else normal

            def open(self):
                driver.paused = True
                self.enabled = True
                view.ai_version.enabled = False  # the menu shows the same line
                self.pick()
                self.note.text = driver.version_line.replace('\n', '   ')

            def start_game(self):
                driver.opponent.configure(side=self.ai_side, difficulty=self.difficulty)
                driver.paused = False
                self.enabled = False
                view.ai_version.enabled = True
                view.restart()  # a new game with these settings

            def update(self):
                if self.enabled:
                    self.note.text = driver.version_line.replace('\n', '   ')

        menu = GameMenu()
    if args.smoke:
        invoke(application.quit, delay=2)
    if args.screenshot:
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        invoke(lambda: app.win.saveScreenshot(Filename.from_os_specific(str(args.screenshot.resolve()))), delay=1)
    try:
        app.run()
    finally:
        backend.close()
        host.close()
        if driver is not None:
            driver.opponent.close()


if __name__ == '__main__':
    main()
