"""Windows desktop acceptance for all source examples and native lifecycle.

Uses real Qt controls and game processes. Inputs use explicit test-only ports,
not OS input injection. Captures are real desktop/game pixels. No model API.
Run: .venv-workbench/Scripts/python -m tests.studio_source_probe
"""
import ctypes
import hashlib
import json
from pathlib import Path
import tempfile
import time
import traceback
from types import SimpleNamespace

from PySide6.QtCore import QTimer, QPoint
from PySide6.QtWidgets import QApplication
from srtp.studio.app import Studio, configure_application
from srtp.workbench import REFERENCE_GAMES

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/source-switch'


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    configure_application(app)
    w = Studio(); w.resize(1440, 840); w.show()
    v = w.source_view; v.probe = True
    events, checks, old_windows = [], [], []
    v.event.connect(events.append)
    u = ctypes.windll.user32
    u.GetParent.argtypes = [ctypes.c_void_p]; u.GetParent.restype = ctypes.c_void_p
    u.IsWindow.argtypes = [ctypes.c_void_p]; u.IsWindow.restype = ctypes.c_int
    report = {'method': __doc__, 'api_calls': 0, 'checks': checks}
    started = time.monotonic()
    ticks = [0]
    heartbeat = QTimer(); heartbeat.timeout.connect(lambda: ticks.__setitem__(0, ticks[0] + 1)); heartbeat.start(20)

    def wait(condition, timeout=20):
        deadline = time.monotonic() + timeout
        while not condition():
            app.processEvents()
            if time.monotonic() > deadline:
                raise AssertionError('Timed out: ' + v.message.text() + '\n' + w.console.toPlainText()[-2000:])
            time.sleep(.02)
        app.processEvents()

    def settle(seconds=.25):
        until = time.monotonic() + seconds
        wait(lambda: time.monotonic() >= until, seconds+2)

    def check(name, condition):
        assert condition, name
        checks.append(name); print(name, flush=True)

    def choose(index):
        events.clear()
        w.example_selector.setCurrentIndex(index)
        w.example_selector.activated.emit(index)
        wait(lambda: v.ready)
        settle()
        check('Selector starts example %d in the source viewport' % index, w.active_view == 'source')
        window = next(e['window'] for e in reversed(events) if e['event'] == 'source_ready')
        check('Example %d owns an embedded child window' % index, u.GetParent(window) == int(v.surface.winId()))
        wait(lambda: all(not u.IsWindow(old) for old in old_windows if old != window), 3)
        old_windows.append(window)

    def image(name):
        path = OUT / (name + '.png')
        events.clear(); v.send('screenshot', path=str(path))
        wait(lambda: any(e['event'] == 'screenshot' and e['path'] == str(path) for e in events))
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def canvas():
        events.clear(); v.send('probe_canvas')
        wait(lambda: any(e['event'] == 'source_canvas' for e in events))
        return next(e['fills'] for e in events if e['event'] == 'source_canvas')

    def pointer(pos):
        v.send('probe_pointer', pos=pos); settle()

    try:
        # An ordinary import remains nonexecuting, while the explicitly named
        # built-in Run selector has a runnable result immediately.
        w.load_source(next(iter(REFERENCE_GAMES.values())))
        check('Generic import does not execute source', not v.running)
        check('Unavailable 3D tabs explain the prerequisite', all(not w.view_buttons[k].isEnabled() and '批准' in w.view_buttons[k].toolTip() for k in ('scene', 'game')))
        for index in range(1, len(REFERENCE_GAMES)+1):
            choose(index)
            w.pause_button.click(); wait(lambda: v.paused)
            before_ticks = ticks[0]; settle(.2)
            check('Example %d pause leaves the Studio responsive' % index, ticks[0] > before_ticks+3)
            w.pause_button.click(); wait(lambda: not v.paused)
            if index == 1:
                before = image('ttt-before'); pointer([100, 170])
                check('Tic Tac Toe accepts a board move', image('ttt-after') != before)
            elif index == 2:
                before = image('snake-before')
                v.send('probe_key', key='down'); settle(.45)
                check('Snake continues drawing after its repeated set_mode call', image('snake-after') != before and v.running)
            elif index == 3:
                before = image('mines-before'); pointer([35, 85])
                check('Minesweeper responds after replacing its bootstrap display', image('mines-after') != before)
            elif index == 4:
                before = canvas(); pointer([-168, 0]); after = canvas()
                check('Turtle Connect Four drops a yellow piece', after.get('yellow', 0) == before.get('yellow', 0)+1)
                w.pause_button.click(); wait(lambda: v.paused)
                pointer([-112, 0]); check('Paused Turtle suppresses gameplay input', canvas() == after)
                w.pause_button.click(); wait(lambda: not v.paused)
                pointer([-112, 0]); check('Resumed Turtle accepts the next player', canvas().get('red', 0) == before.get('red', 0)+1)
            elif index == 5:
                menu = image('2048-menu')
                for pos in ([150, 295], [150, 350], [250, 420]): pointer(pos)
                board = image('2048-board')
                check('2048 leaves its menu and renders the retained game Surface', board != menu and v.running)
                for key in ('left', 'down', 'right', 'up'):
                    v.send('probe_key', key=key); settle(.15)
                check('2048 accepts moves while its idle loop does not redraw', image('2048-moved') != board)
                w.pause_button.click(); wait(lambda: v.paused)
                w.pause_button.click(); wait(lambda: not v.paused)
            w.resize(1240, 780); settle(.3)
            check('Example %d survives a compact viewport resize' % index, v.running and v.ready)
            capture=app.primaryScreen().grabWindow(int(w.winId()))
            capture.save(str(OUT / ('example-%d.png' % index)))
            # A valid software Surface is insufficient: SDL can be running
            # while integer downscaling presents an entirely black HWND.
            origin=v.surface.mapTo(w,QPoint(0,0)); ratio=capture.devicePixelRatio()
            pixels=capture.toImage()
            colors=set()
            for x in range(1,20):
                for y in range(1,20):
                    px=round((origin.x()+v.surface.width()*x/20)*ratio)
                    py=round((origin.y()+v.surface.height()*y/20)*ratio)
                    colors.add(pixels.pixelColor(px,py).rgb())
            check('Example %d presents visible game pixels after downscaling' % index, len(colors)>3)
            w.resize(1440, 840)
            old = old_windows[-1]
            w.restart_button.click(); wait(lambda: v.ready); settle()
            check('Example %d restarts without destroying the Qt surface' % index, bool(u.IsWindow(int(v.surface.winId()))))
            wait(lambda: not u.IsWindow(old), 3)
            old_windows.append(next(e['window'] for e in reversed(events) if e['event'] == 'source_ready'))

        # Previously approved scene stays associated with the exact source in
        # this session; it must never follow a different example.
        choose(1)
        target = ROOT / 'tests/fixtures/workbench_training_target_20261004/project.manifest.json'
        w.c.open_project_manifest(target); wait(lambda: w.target_view.ready)
        check('Approved scene still loads after cycling all five sources', len(w.nodes) > 27)
        choose(2)
        check('Other games cannot inherit Tic Tac Toe scene or training', w.manifest is None and w.c.training_panel.target is None and not w.train_start.isEnabled())
        choose(1)
        check('Returning to unchanged source restores its approved scene binding', w.manifest == target.resolve() and w.view_buttons['scene'].isEnabled())
        w.view_buttons['scene'].click(); wait(lambda: w.target_view.ready)
        check('Restored 3D scene renders actual objects', len(w.nodes) > 27)
        w.switch_view('source')

        # Neutral fixture exercises the same display-lifetime bug without a
        # game-name path, and tests actionable error/retry/stop behaviour.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'neutral_window.py'
            code = 'import pygame\npygame.init()\npygame.display.set_mode((10,10))\nfirst=pygame.display.set_mode((320,240))\nsecond=pygame.display.set_mode((320,240))\nassert first is second\nfirst.fill((90,140,180))\npygame.display.flip()\n'
            path.write_text(code + 'raise RuntimeError("native lifecycle failure probe")\n', encoding='utf-8')
            package = SimpleNamespace(entrypoint=path, runtime=SimpleNamespace(cwd=path.parent, framework='pygame'))
            v.launch_source(package); wait(lambda: not v.running)
            check('Failure shows the actual cause in the viewport', 'native lifecycle failure probe' in v.message.text() and v.message.isVisible())
            path.write_text(code + 'while True:\n pygame.event.get()\n pygame.display.flip()\n pygame.time.wait(16)\n', encoding='utf-8')
            v.launch_source(package); wait(lambda: v.ready)
            check('Renamed neutral source can retain its Surface and retry', v.running and not v.message.isVisible())
            v.stop(); wait(lambda: not v.running)
            check('Stop returns to an actionable viewport', v.message.isVisible())
        report.update(passed=True, ui_ticks=ticks[0], elapsed_seconds=round(time.monotonic()-started, 2))
    except Exception:
        report.update(passed=False, error=traceback.format_exc())
        print(report['error'], flush=True)
    finally:
        heartbeat.stop()
        (OUT / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        (OUT / 'runtime.log').write_text(w.console.toPlainText(), encoding='utf-8')
        w.close(); app.processEvents()
    return 0 if report.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(run())
