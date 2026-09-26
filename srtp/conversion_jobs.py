"""One conversion worker, UI-thread events, cooperative cancellation and atomic publication."""
from contextlib import contextmanager
from queue import Queue, Empty
from threading import Event, Lock, Thread
import uuid


class CompilationCancelled(Exception):
    pass


class CancellationToken:
    def __init__(self):
        self.event=Event()
        self.lock=Lock()
        self.published=False

    def check(self):
        if self.event.is_set():
            raise CompilationCancelled('Conversion cancelled')

    def cancel(self):
        # Publication owns the lock only while writing/publishing files. Never
        # block the UI waiting on disk I/O; cancellation is too late in that phase.
        if not self.lock.acquire(blocking=False):
            return False
        try:
            if self.published:
                return False
            self.event.set()
            return True
        finally:
            self.lock.release()

    @contextmanager
    def publication(self):
        # Cancel and publication have a defined ordering; cancelled jobs cannot
        # race into replacing the last successful bundle.
        with self.lock:
            self.check()
            yield
            self.published=True


class ConversionJobs:
    def __init__(self):
        self.active=None
        self.events=Queue()
        self.closed=False

    def submit(self, tag, worker):
        if self.closed or self.active is not None:
            return False
        token=CancellationToken(); identifier=uuid.uuid4().hex
        self.active={'id':identifier,'tag':tag,'token':token}
        def progress(value):
            token.check()
            self.events.put({'kind':'progress','id':identifier,'tag':tag,'value':value})
        def run():
            try:
                result=worker(progress,token)
                token.check()
                event={'kind':'complete','value':result}
            except CompilationCancelled:
                event={'kind':'cancelled','value':None}
            except Exception as error:
                import traceback
                event={'kind':'error','value':str(error),'traceback':traceback.format_exc()}
            self.events.put(dict(event,id=identifier,tag=tag))
        thread=Thread(target=run,name='CubeEngine conversion',daemon=True)
        self.active['thread']=thread
        thread.start()
        return True

    def cancel(self):
        return self.active is not None and self.active['token'].cancel()

    def poll(self):
        events=[]
        while True:
            try: event=self.events.get_nowait()
            except Empty: break
            if self.active is None or event['id']!=self.active['id']:
                continue
            events.append(event)
            if event['kind']!='progress':
                self.active=None
        return events

    def close(self):
        self.closed=True
        self.cancel()
