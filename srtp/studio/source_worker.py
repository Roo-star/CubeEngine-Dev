"""Pygame host: original game code, original logical pixels, scaled native surface.

SDL owns rendering/input inside Studio's HWND. The game file is never rewritten.
The wrapper only selects SDL's scaled display mode and owns process lifecycle.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import queue
import sys
import threading

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent-window', required=True)
    parser.add_argument('--entry', type=Path, required=True)
    parser.add_argument('--cwd', type=Path, required=True)
    parser.add_argument('--probe', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    # SDL owns its child HWND. Borrowing Qt's HWND through SDL_WINDOWID lets
    # source programs that call set_mode/quit repeatedly destroy Qt's surface.
    os.environ.pop('SDL_WINDOWID',None)
    os.environ['SDL_VIDEODRIVER'] = 'windows'
    os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
    import pygame
    original_mode, original_flip, original_update = pygame.display.set_mode, pygame.display.flip, pygame.display.update
    ready = [False]
    commands=queue.Queue(maxsize=64)
    native=[0]
    paused=[False]
    def mode(size=(0, 0), flags=0, depth=0, display=0, vsync=0):
        from .native import embed_owned_window
        if native[0]:
            # A source may create a bootstrap display before its real display.
            # SDL's SCALED renderer cannot safely reuse a reparented HWND.
            pygame.display.quit(); pygame.display.init(); native[0]=0
        surface = original_mode(size, (flags & ~pygame.FULLSCREEN) | pygame.SCALED | pygame.RESIZABLE | pygame.HIDDEN, depth, display, vsync)
        native[0]=pygame.display.get_wm_info()['window']
        embed_owned_window(native[0],int(args.parent_window))
        ready[0]=False
        print('CUBE_STUDIO ' + json.dumps({'event':'source_ready', 'size':list(size),
              'window':pygame.display.get_wm_info().get('window')}), flush=True)
        return surface
    def frame(method, *a, **kw):
        while paused[0] or not commands.empty():
            try:command=commands.get(timeout=.1 if paused[0] else 0)
            except queue.Empty:continue
            if command.get('op')=='screenshot':
                pygame.image.save(pygame.display.get_surface(),command['path'])
                print('CUBE_STUDIO '+json.dumps({'event':'screenshot','path':command['path']}),flush=True)
            elif command.get('op')=='resize' and native[0]:
                from .native import resize_owned_window
                resize_owned_window(native[0],command['width'],command['height'])
            elif command.get('op')=='pause':
                paused[0]=bool(command.get('paused'))
                print('CUBE_STUDIO '+json.dumps({'event':'source_state','paused':paused[0]}),flush=True)
            elif command.get('op')=='probe_pointer' and args.probe:
                for kind in (pygame.MOUSEBUTTONDOWN,pygame.MOUSEBUTTONUP):
                    pygame.event.post(pygame.event.Event(kind,button=command.get('button',1),pos=tuple(command['pos'])))
        result = method(*a, **kw)
        if not ready[0]:
            ready[0] = True
            print('CUBE_STUDIO ' + json.dumps({'event':'source_frame'}), flush=True)
        return result
    pygame.display.set_mode = mode
    pygame.display.flip = lambda *a, **k: frame(original_flip, *a, **k)
    pygame.display.update = lambda *a, **k: frame(original_update, *a, **k)
    def owner():
        for line in sys.stdin:
            try:
                cmd = json.loads(line)
                if cmd.get('op') == 'quit': break
                commands.put_nowait(cmd)
            except (ValueError, pygame.error, queue.Full): pass
        os._exit(0)  # Owner disappeared: never leave uploaded source running.
    threading.Thread(target=owner, daemon=True).start()
    os.chdir(str(args.cwd))
    sys.path.insert(0, str(args.entry.parent))
    sys.argv = [str(args.entry)]
    runpy.run_path(str(args.entry), run_name='__main__')

if __name__ == '__main__': main()
