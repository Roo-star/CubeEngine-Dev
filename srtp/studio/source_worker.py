"""Owned native host for unchanged Pygame and Turtle source games.

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
    parser.add_argument('--framework', choices=('pygame', 'turtle'), default='pygame')
    parser.add_argument('--probe', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    commands = queue.Queue(maxsize=64)
    def owner():
        for line in sys.stdin:
            try:
                cmd = json.loads(line)
                if cmd.get('op') == 'quit': break
                commands.put_nowait(cmd)
            except (ValueError, queue.Full): pass
        os._exit(0)  # Owner disappeared: never leave uploaded source running.
    if args.framework == 'turtle':
        from .turtle_host import install
        install(args, commands)
    else:
        install_pygame(args, commands)
    # Native dependency initialisers can change CRT stdio modes. Import them
    # before another thread blocks reading stdin (notably NumPy on Windows).
    threading.Thread(target=owner, daemon=True).start()
    os.chdir(str(args.cwd))
    sys.path.insert(0, str(args.entry.parent))
    sys.argv = [str(args.entry)]
    runpy.run_path(str(args.entry), run_name='__main__')


def install_pygame(args, commands):
    # SDL owns its child HWND. Borrowing Qt's HWND through SDL_WINDOWID lets
    # source programs that call set_mode/quit repeatedly destroy Qt's surface.
    os.environ.pop('SDL_WINDOWID',None)
    os.environ['SDL_VIDEODRIVER'] = 'windows'
    os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
    # Pygame 2.6's SCALED window defaults to integer scaling; an embedded
    # viewport can be smaller than the original logical canvas. Its boolean
    # switch uses the literal SDL_HINT_ name, unlike SDL's filtering hint.
    os.environ['SDL_HINT_RENDER_SCALE_QUALITY'] = '1'
    os.environ['SDL_RENDER_SCALE_QUALITY'] = 'linear'
    import pygame
    # Embedded execution uses the same compatibility boundary as Run Source.
    from ..runtime_bootstrap import _install_pygame_windows_font_fallback, _install_numpy_legacy_aliases
    _install_pygame_windows_font_fallback()
    _install_numpy_legacy_aliases()
    original_mode, original_flip, original_update = pygame.display.set_mode, pygame.display.flip, pygame.display.update
    ready = [False]
    native=[0]
    display_signature=[None]
    paused=[False]
    def mode(size=(0, 0), flags=0, depth=0, display=0, vsync=0):
        from .native import embed_owned_window
        signature = (tuple(size), flags, depth, display, vsync)
        if native[0] and signature == display_signature[0] and pygame.display.get_surface() is not None:
            # Source modules can retain the display Surface, then request the
            # same mode again in another class/module. Quitting SDL invalidates
            # every retained Surface (Snake and 2048 both exercise this).
            surface = pygame.display.get_surface()
            surface.fill((0, 0, 0))
            return surface
        if native[0]:
            # A source may create a bootstrap display before its real display.
            # SDL's SCALED renderer cannot safely reuse a reparented HWND.
            pygame.display.quit(); pygame.display.init(); native[0]=0
        surface = original_mode(size, (flags & ~pygame.FULLSCREEN) | pygame.SCALED | pygame.RESIZABLE | pygame.HIDDEN, depth, display, vsync)
        native[0]=pygame.display.get_wm_info()['window']
        embed_owned_window(native[0],int(args.parent_window))
        display_signature[0] = signature
        ready[0]=False
        print('CUBE_STUDIO ' + json.dumps({'event':'source_ready', 'size':list(size),
              'window':pygame.display.get_wm_info().get('window')}), flush=True)
        return surface
    def pump_commands():
        while paused[0] or not commands.empty():
            try:command=commands.get(timeout=.1 if paused[0] else 0)
            except queue.Empty:
                # Windows sends synchronous focus/visibility messages between
                # native child windows. A paused game must still service SDL's
                # window queue or opening the 3D sibling can stall in Win32.
                if paused[0]: pygame.event.pump()
                continue
            if command.get('op')=='screenshot':
                pygame.image.save(pygame.display.get_surface(),command['path'])
                print('CUBE_STUDIO '+json.dumps({'event':'screenshot','path':command['path']}),flush=True)
            elif command.get('op')=='resize' and native[0]:
                from .native import resize_owned_window
                resize_owned_window(native[0],command['width'],command['height'])
                pygame.event.pump()
                original_flip()  # Idle games still repaint their last frame.
            elif command.get('op')=='pause':
                paused[0]=bool(command.get('paused'))
                print('CUBE_STUDIO '+json.dumps({'event':'source_state','paused':paused[0]}),flush=True)
            elif command.get('op')=='probe_pointer' and args.probe:
                # Test-only virtual cursor; games may read mouse.get_pos()
                # instead of event.pos. This never moves the system cursor.
                pygame.mouse.get_pos = lambda pos=tuple(command['pos']): pos
                for kind in (pygame.MOUSEBUTTONDOWN,pygame.MOUSEBUTTONUP):
                    pygame.event.post(pygame.event.Event(kind,button=command.get('button',1),pos=tuple(command['pos'])))
            elif command.get('op')=='probe_key' and args.probe:
                key=pygame.key.key_code(command['key'])
                for kind in (pygame.KEYDOWN,pygame.KEYUP):
                    pygame.event.post(pygame.event.Event(kind,key=key,mod=0,unicode=''))

    def frame(method, *a, **kw):
        pump_commands()
        result = method(*a, **kw)
        if not ready[0]:
            ready[0] = True
            print('CUBE_STUDIO ' + json.dumps({'event':'source_frame'}), flush=True)
        return result
    pygame.display.set_mode = mode
    pygame.display.flip = lambda *a, **k: frame(original_flip, *a, **k)
    pygame.display.update = lambda *a, **k: frame(original_update, *a, **k)
    # Event-driven sources can redraw only after a move. Keep pause, resize
    # and test input alive while such a game waits for its next event.
    for name in ('get', 'poll'):
        original = getattr(pygame.event, name)
        def receive(*a, _original=original, **kw):
            pump_commands()
            return _original(*a, **kw)
        setattr(pygame.event, name, receive)

if __name__ == '__main__': main()
