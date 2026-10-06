"""Lifecycle commands for the original adapter demos, without Project/AI pins."""
import hashlib
import json
import os
import queue
import sys
import threading


class DemoBridge:
    def __init__(self, viewer, app, probe=False):
        self.viewer, self.app, self.probe = viewer, app, probe
        self.commands = queue.Queue(maxsize=64)
        self.first = True
        self.layout_hud(app.win.getXSize()/max(1,app.win.getYSize()))
        # Ursina and its native dependencies are already initialised here.
        threading.Thread(target=self.reader, daemon=True, name='demo owner').start()

    def reader(self):
        for line in sys.stdin:
            try:
                if len(line) > 65536: continue
                command = json.loads(line)
                if command.get('op') == 'quit': break
                self.commands.put_nowait(command)
            except (ValueError, queue.Full): pass
        os._exit(0)

    def emit(self, event, **payload):
        print('CUBE_STUDIO ' + json.dumps({'event': event, **payload}, ensure_ascii=False), flush=True)

    def state(self, event='demo_state'):
        v = self.viewer
        self.emit(event, adapter=v.adapter_id, dimensions=list(v.dimensions),
                  paused=v.host_paused, message=v.last_message,
                  viewport=[self.app.win.getXSize(), self.app.win.getYSize()],
                  native_handle=int(self.app.win.getWindowHandle().getIntHandle()),
                  object_count=len(v.cells)+len(v.dynamic)+len(v.bounds)+len(v.layer_guides))

    def update(self):
        if self.first:
            self.first = False; self.state('demo_frame')
        for _ in range(64):
            try: command = self.commands.get_nowait()
            except queue.Empty: break
            try: self.command(command)
            except Exception as error: self.emit('error', message=str(error))

    def layout_hud(self, aspect):
        # The original demo HUD assumed a wide standalone window. Anchor it
        # inside the actual embedded aspect ratio, including compact layouts.
        v=self.viewer; left=-aspect/2+.025
        v.title.position=(left,.46); v.title.scale=1.15
        v.status.position=(left,.39); v.status.scale=.95
        v.status.wordwrap=max(40,round(aspect*70))
        v.help.origin=(-.5,-.5); v.help.position=(left,-.47)
        v.help.scale=.85; v.help.wordwrap=max(40,round(aspect*75))
        v.opacity_slider.position=(aspect/2-.24,.45); v.opacity_slider.scale=.42

    def command(self, command):
        from ursina import window
        op = command.get('op'); v = self.viewer
        if op == 'pause':
            v.host_paused = bool(command.get('paused')); self.state()
        elif op == 'resize':
            size=tuple(max(64, min(4096, int(command[k]))) for k in ('width', 'height'))
            window.size = size
            from panda3d.core import WindowProperties
            props=WindowProperties(); props.setOrigin(0,0); props.setSize(*size)
            self.app.win.requestProperties(props)
            self.layout_hud(size[0]/size[1])
            self.state()
        elif op == 'reset':
            v.game = v._new_game(); v.accumulator = 0
            v.last_message = 'Restarted'; v.render(); self.state()
        elif op == 'camera': v._reset_view()
        elif op == 'screenshot':
            from panda3d.core import Filename
            self.app.win.saveScreenshot(Filename.fromOsSpecific(command['path']))
            self.emit('screenshot', path=command['path'])
        elif self.probe and op == 'probe_key': v.input(command['key']); self.state()
        elif self.probe and op == 'probe_click': v.click(tuple(command['coordinate'])); self.state()
        elif self.probe and op == 'probe_state':
            # Test observations of the existing adapter, never replacement rules.
            def plain(value):
                if isinstance(value, dict): return {str(k): plain(x) for k, x in value.items()}
                if isinstance(value, set): return [plain(x) for x in sorted(value)]
                if isinstance(value, (list, tuple)): return [plain(x) for x in value]
                return value
            state = {k: plain(x) for k, x in vars(v.game).items() if k != 'random'}
            digest = hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()
            self.emit('demo_probe', digest=digest, state=state)
