"""Embed a source-owned Turtle/Tk screen; keep its canvas and event handlers.

All Tk operations run on the source's main thread. The command reader belongs
to source_worker, so owner loss also terminates a source stuck outside Tk.
"""
import json
import queue
import turtle

from .native import embed_owned_window, resize_owned_window


def install(args, commands):
    original_init = turtle._Screen.__init__
    original_timer = turtle.TurtleScreen.ontimer
    original_update = turtle.TurtleScreen.update
    paused = [False]

    def emit(event, **values):
        print('CUBE_STUDIO ' + json.dumps({'event': event, **values}), flush=True)

    def init(screen, *a, **kw):
        original_init(screen, *a, **kw)
        canvas = screen.getcanvas()._canvas
        root = canvas.winfo_toplevel()
        host = {'window': None}
        # Suppress source input while paused, without suspending the UI or the
        # command pump. Use an earlier bindtag instead of replacing callbacks.
        tag = 'CubeStudioPause'
        for widget in (canvas, root):
            widget.bindtags((tag,) + tuple(widget.bindtags()))
        for sequence in ('<Button>', '<ButtonRelease>', '<Key>', '<KeyRelease>', '<Motion>', '<MouseWheel>'):
            canvas.bind_class(tag, sequence, lambda event: 'break' if paused[0] else None)

        def poll():
            try:
                if host['window'] is None:
                    # Tk's wrapper HWND owns the whole client and canvas.
                    root.update_idletasks()
                    host['window'] = int(root.wm_frame(), 0)
                    embed_owned_window(host['window'], int(args.parent_window))
                    emit('source_ready', window=host['window'], framework='turtle')
                    root.after_idle(lambda: emit('source_frame'))
                for _ in range(64):
                    try: command = commands.get_nowait()
                    except queue.Empty: break
                    op = command.get('op')
                    if op == 'resize':
                        resize_owned_window(host['window'], command['width'], command['height'])
                    elif op == 'pause':
                        paused[0] = bool(command.get('paused'))
                        emit('source_state', paused=paused[0])
                    elif op == 'probe_pointer' and args.probe:
                        x, y = command['pos']
                        canvas.event_generate('<Button-%d>' % command.get('button', 1),
                            x=round(x * screen.xscale - canvas.canvasx(0)),
                            y=round(-y * screen.yscale - canvas.canvasy(0)))
                    elif op == 'probe_key' and args.probe:
                        canvas.event_generate('<KeyPress>', keysym=command['key'])
                    elif op == 'probe_canvas' and args.probe:
                        fills = {}
                        for item in canvas.find_all():
                            if canvas.type(item) in ('oval', 'polygon', 'line'):
                                fill = canvas.itemcget(item, 'fill')
                                fills[fill] = fills.get(fill, 0) + 1
                        emit('source_canvas', fills=fills)
                root.after(16, poll)
            except Exception as error:
                emit('error', message=str(error))
                raise
        root.after_idle(poll)

    def timer(screen, callback, t=0):
        def fire():
            if paused[0]: original_timer(screen, fire, 25)
            else: callback()
        return original_timer(screen, fire, t)

    def update(screen):
        # Animation loops calling update() must yield while paused; Tk's
        # command timer still services resume/resize during these updates.
        import time
        result = original_update(screen)
        while paused[0]:
            time.sleep(.016)
            original_update(screen)
        return result

    turtle._Screen.__init__ = init
    turtle.TurtleScreen.ontimer = timer
    turtle.TurtleScreen.update = update
