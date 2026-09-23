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
        return self.presentation.synchronize(self.projection, session.rule_runtime.state)

    def mouse(self, control, context, phase='press'):
        sequence = self._next_sequence()
        data = {}
        if 'coordinate' in context:
            data['rule_coordinate'] = list(context['coordinate'])
        if 'entity_id' in context:
            data['rule_entity_id'] = context['entity_id']
        return self._host_result(self.controller.dispatch_physical(PhysicalInputEvent(
            sequence, 'mouse', control, phase, position=(0, 0), data=data)))

    def mouse_click(self, control, context):
        pressed=self.mouse(control,context)
        released=self.mouse(control,context,'release')
        return released if released.accepted or pressed.code=='input_not_resolved' else pressed

    def close(self):
        self.controller.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--smoke', action='store_true', help='Render briefly and exit for host verification.')
    parser.add_argument('--screenshot', type=Path, help='Save this rendered board for verification.')
    args = parser.parse_args()
    host = ProjectHost(args.manifest, args.source_root)
    from ursina import Ursina, Entity, EditorCamera, Text, Button, color, mouse, time, application, invoke, window
    from panda3d.core import Filename
    font = Path('C:/Windows/Fonts/arial.ttf')
    if font.exists():
        Text.default_font = Filename.from_os_specific(str(font)).get_fullpath()
    app = Ursina(borderless=False, fullscreen=False, size=(1280, 800), development_mode=False)
    window.title = 'CubeEngine - Project IR / Ursina 3D'
    window.color = color.rgb(20, 25, 36)
    dims = host.snapshot.dimensions
    depth = dims[2] if len(dims) == 3 else 1
    from .ursina_scene_backend import UrsinaSceneBackend
    errors = host.presentation.diagnostics()
    if errors:
        host.close()
        raise ValueError('Compiled presentation cannot run: ' + '; '.join(errors[:6]))
    backend = UrsinaSceneBackend(host.presentation, args.manifest.parent / 'render_cache')
    class ProjectView(Entity):
        def __init__(self):
            super().__init__()
            self.layer = None
            self.failed = False
            self.last_revision = None
            from .pointer_gesture import PointerGesture
            self.pointer = PointerGesture()
            self.message = 'Right-drag to orbit; wheel zoom; [ / ] select depth; Restart button resets the game.'
            self.header = Text(text='', position=(-window.aspect_ratio/2+.02, .47), scale=.7)
            right = window.aspect_ratio/2 - .08
            self.controls = [
                Button(text='All layers', position=(right-.33,.46), scale=(.15,.045), on_click=lambda:self.set_layer(None)),
                Button(text='Previous', position=(right-.16,.46), scale=(.14,.045), on_click=lambda:self.step_layer(-1)),
                Button(text='Next', position=(right,.46), scale=(.12,.045), on_click=lambda:self.step_layer(1)),
                Button(text='Restart', position=(right,.40), scale=(.12,.045), on_click=self.restart)]
            self.refresh()

        def restart(self):
            self.pointer.clear()
            host.controller.reset()
            self.failed = False
            self.refresh()

        def set_layer(self, layer):
            self.pointer.clear()
            self.layer = layer
            self.refresh()

        def step_layer(self, delta):
            current = -1 if self.layer is None else self.layer
            value = (current + delta + 1) % (depth + 1) - 1
            self.set_layer(None if value == -1 else value)

        def refresh(self):
            host.refresh_scene()
            backend.selected_layer = self.layer
            backend.sync(incremental=True)
            state = host.controller.snapshot()
            self.last_revision = state.revision
            self.header.text = (state.label + ' | ' + ' x '.join(map(str, dims)) +
                ' | depth: ' + ('all' if self.layer is None else str(self.layer)) +
                '\n' + (('Finished: ' + ', '.join(state.winners)) if state.terminal else 'Turn: ' + state.current_actor_name) +
                '\n' + self.message + ('\n' + host.presentation_notice if host.presentation_notice else ''))

        def input(self, key):
            try:
                if key in ('[', ']'):
                    # All cells remain visible. Only picking changes with depth.
                    self.step_layer(1 if key == ']' else -1)
                elif key in ('left mouse down', 'right mouse down', 'left mouse up', 'right mouse up'):
                    context = backend.pick_context(mouse.hovered_entity)
                    control = 'mouse.button.primary' if key.startswith('left') else 'mouse.button.secondary'
                    position = (mouse.x, mouse.y)
                    if key.endswith('down'):
                        self.pointer.press(control, position, context)
                    else:
                        clicked = self.pointer.release(control, position, context)
                        if clicked:
                            self.message = host.mouse_click(control, clicked).message
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
                right = window.aspect_ratio/2-.08
                for button, offset in zip(self.controls, (-.33,-.16,0,0,-.16)):
                    button.x = right+offset
                backend.hover(mouse.hovered_entity)
                backend.advance_visuals(min(time.dt, .25))
                self.pointer.move((mouse.x, mouse.y))
                host.advance(min(time.dt, .25))
                state = host.controller.sessions[host.controller.active_key].rule_runtime.state
                if state.revision != self.last_revision:
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
        view.pointer.clear()
    view.controls.append(Button(text='Reset view', position=(window.aspect_ratio/2-.24,.40), scale=(.14,.045), on_click=restore_camera))
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


if __name__ == '__main__':
    main()
