"""Typed local editor commands for the real Ursina Scene/Asset renderer.

Runs in the game process. Stdin carries bounded commands; stdout carries scene
metadata, never the raw Rule board. Closing the owner pipe exits the child.
"""
import json
from copy import deepcopy
import queue
import sys
import threading
import time
from .layout import FORMAT, vector, validate_draft

PREFIX = 'CUBE_STUDIO '

class SceneBridge:
    def __init__(self, host, backend, view, rig, app, manifest, allow_probe=False):
        self.host, self.backend, self.view, self.rig, self.app = host, backend, view, rig, app
        self.manifest = manifest
        self.allow_probe = allow_probe
        self.playing, self.paused, self.selected = False, False, None
        self.commands, self.overrides = queue.Queue(maxsize=128), {}
        self.undo_stack, self.redo_stack = [], []
        self.last_publish, self.last_signature, self.closed = 0, None, False
        self.original = {key: {'position': list(e.position), 'rotation': list(e.rotation),
                              'scale': list(e.scale), 'visible': bool(e.enabled)} for key, e in backend.entities.items()}
        threading.Thread(target=self._reader, daemon=True, name='studio commands').start()
        for widget in [view.header] + view.controls: widget.enabled = False
        if view.ai_label is not None: view.ai_label.enabled = False
        self.publish(force=True)

    def _reader(self):
        for line in sys.stdin:
            try:
                if len(line) > 65536: continue
                self.commands.put_nowait(json.loads(line))
            except (ValueError, queue.Full):
                continue
        self.closed = True

    def emit(self, event, **payload):
        print(PREFIX + json.dumps({'event': event, **payload}, ensure_ascii=False), flush=True)

    def nodes(self):
        rows = []
        for key, node in self.backend.presentation.nodes.items():
            e = self.backend.entities[key]
            kinds = [c['type'] for c in node['components'].values()]
            renderer=next((c.get('properties',{}) for c in node['components'].values() if c['type']=='renderer'),{})
            rows.append({'id': key, 'name': node.get('name', key.split(':')[-1]), 'parent': node.get('parent'),
                         'components': kinds, 'position': list(e.position), 'rotation': list(e.rotation),
                         'scale': list(e.scale), 'visible': bool(e.enabled),
                         'coordinate': node.get('rule_context', {}).get('coordinate'),
                         'visual': {p: renderer[p] for p in ('geometry','color','texture','material','presentation_role') if p in renderer}})
        return rows

    def publish(self, force=False):
        state = self.host.controller.snapshot()
        signature = (state.revision, self.selected, self.playing, self.paused, len(self.overrides),
                     self.view.ai_status,
                     self.app.win.getXSize(), self.app.win.getYSize())
        if force or signature != self.last_signature:
            self.last_signature = signature
            self.emit('scene', nodes=self.nodes(), selected=self.selected,
                      playing=self.playing, paused=self.paused, revision=state.revision,
                      ai_status=self.view.ai_status,
                      actor=state.current_actor_name, terminal=state.terminal,
                      winners=list(state.winners), message=self.view.message,
                      assets={k: str(v) for k, v in self.backend.paths.items()},
                      dimensions=list(state.dimensions), changes=len(self.overrides),
                      can_undo=bool(self.undo_stack),can_redo=bool(self.redo_stack),
                      layer=self.view.layer,slice_only=self.view.slice_only,
                      viewport=[self.app.win.getXSize(),self.app.win.getYSize()],
                      native_handle=int(self.app.win.getWindowHandle().getIntHandle()))

    def select(self, identifier):
        self.selected = identifier if identifier in self.backend.entities else None
        self.publish(force=True)

    def restore(self):
        for key, props in self.original.items():
            e = self.backend.entities.get(key)
            if e:
                e.position, e.rotation, e.scale = props['position'], props['rotation'], props['scale']
                e.enabled = props['visible']

    def apply_draft(self):
        for key, values in self.overrides.items():
            e = self.backend.entities.get(key)
            if e is None: continue
            for prop, value in values.items():
                setattr(e, 'enabled' if prop == 'visible' else prop, value)

    def remember(self):
        self.undo_stack.append(deepcopy(self.overrides)); self.undo_stack=self.undo_stack[-64:]
        self.redo_stack.clear()

    @property
    def project_hash(self):
        return self.host.controller.projects[self.host.controller.active_key].manifest['content_hash']

    def command(self, command):
        from ursina import application, camera, Vec3
        op = command.get('op')
        if op == 'quit':
            application.quit(); return
        if op == 'resize':
            from panda3d.core import WindowProperties
            from ursina import window
            width, height = max(64, min(4096, int(command['width']))), max(64, min(4096, int(command['height'])))
            # Ursina 5 caches _size; changing Panda properties alone stretches
            # camera.ui and leaves the old aspect ratio in the mouse mapping.
            window.size = (width, height)
            props = WindowProperties(); props.setSize(width, height); props.setOrigin(0, 0)
            self.app.win.requestProperties(props)
        elif op == 'mode':
            next_playing = bool(command.get('playing'))
            if next_playing != self.playing:
                self.restore()
                self.view.restart()
                self.playing, self.paused = next_playing, False
                if self.view.ai_label is not None: self.view.ai_label.enabled = self.playing
                if not self.playing: self.apply_draft()
        elif op == 'pause': self.paused = bool(command.get('paused'))
        elif op == 'reset':
            self.view.restart()
            if not self.playing: self.apply_draft()
        elif op == 'select': self.selected = command.get('id') if command.get('id') in self.backend.entities else None
        elif op == 'focus':
            e = self.backend.entities.get(self.selected)
            if e:
                self.rig.editor.position = e.world_position
                camera.position = (0, 0, -max(3, max(e.world_scale) * 5))
                self.rig.editor.target_z = camera.z
        elif op == 'camera':
            self.rig.restore()
            preset=command.get('preset','isometric')
            if preset not in ('isometric','front','top'):
                raise ValueError('Unknown camera preset')
            if preset in ('front','top'):
                rotation=(0,0,0) if preset=='front' else (90,0,0)
                self.rig.editor.rotation=rotation
                self.rig.editor.smoothing_helper.rotation=rotation
        elif op == 'layer': self.view.set_layer(command.get('layer'))
        elif op == 'slice': self.view.toggle_slice()
        elif op in ('transform', 'visibility'):
            if self.playing: raise ValueError('Stop Play before editing the scene')
            key = command.get('id')
            if key not in self.backend.entities: raise ValueError('Scene object no longer exists')
            values = ({'visible': bool(command['visible'])} if op == 'visibility' else
                      {p: vector(command[p], p == 'scale') for p in ('position', 'rotation', 'scale') if p in command})
            self.remember()
            self.overrides.setdefault(key, {}).update(values)
            self.apply_draft()
        elif op == 'revert':
            if self.playing: raise ValueError('Stop Play before reverting scene edits')
            self.remember()
            self.overrides.clear(); self.restore()
        elif op in ('undo','redo'):
            if self.playing: raise ValueError('Stop Play before editing the scene')
            source,target=(self.undo_stack,self.redo_stack) if op=='undo' else (self.redo_stack,self.undo_stack)
            if source:
                target.append(deepcopy(self.overrides)); self.overrides=source.pop()
                self.restore(); self.apply_draft()
        elif op=='import':
            from pathlib import Path
            if self.playing: raise ValueError('Stop Play before loading a draft')
            path=Path(command['path'])
            if path.stat().st_size>4_000_000: raise ValueError('場景草稿過大')
            document=json.loads(path.read_text(encoding='utf-8'))
            changes=validate_draft(document,self.project_hash,self.backend.entities)
            self.remember(); self.overrides=changes; self.restore(); self.apply_draft()
            self.emit('loaded',path=str(path))
        elif op == 'export':
            from pathlib import Path
            from srtp.training.artifacts import atomic_json
            atomic_json(Path(command['path']), {'format': FORMAT,
                'project_hash': self.project_hash,
                'manifest': str(self.manifest), 'overrides': self.overrides,
                'semantics': 'Editor presentation draft only. Does not change approved gameplay or training.'})
            self.emit('saved', path=command['path'])
        elif op == 'screenshot':
            from panda3d.core import Filename
            self.app.win.saveScreenshot(Filename.from_os_specific(command['path']))
            self.emit('screenshot', path=command['path'])
        elif op == 'probe_pointer' and self.allow_probe:
            if not self.playing: raise ValueError('Probe input requires Play mode')
            key=command['id']
            context=self.backend.pick_context(self.backend.entities[key])
            result=self.host.mouse_click(command.get('control','mouse.button.primary'),context)
            self.view.refresh()
            self.emit('probe_result',accepted=result.accepted,revision=result.state.revision)
        self.publish(force=True)

    def update(self):
        from ursina import application
        if self.closed:
            application.quit(); return
        for _ in range(24):
            try: command = self.commands.get_nowait()
            except queue.Empty: break
            try: self.command(command)
            except Exception as error: self.emit('error', message=str(error))
        if not self.playing:
            self.apply_draft()
            self.backend.hover(self.backend.entities.get(self.selected))
        if time.monotonic() - self.last_publish > .15:
            self.last_publish = time.monotonic()
            self.publish()
