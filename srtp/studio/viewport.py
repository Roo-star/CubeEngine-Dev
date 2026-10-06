"""Native scene surfaces with asynchronous, owned renderer processes."""
import json
from pathlib import Path
import sys
import time
from PySide6.QtCore import Qt, QProcess, QTimer, Signal
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel, QWidget

ROOT = Path(__file__).resolve().parents[2]

class NativeSurface(QWidget):
    resized = Signal()
    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_NativeWindow)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(180, 180)
        self.setStyleSheet('background:#10151d;')
    def resizeEvent(self, event):
        super().resizeEvent(event); self.resized.emit()

class EmbeddedViewport(QFrame):
    event = Signal(dict)
    log = Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('scene')
        self.process, self.buffer, self.mode = None, b'', None
        self.ready = False
        self.paused = False
        self.probe = False
        self.generation = 0
        self.started_at, self.last_frame = 0, None
        layout = QVBoxLayout(self); layout.setContentsMargins(1, 1, 1, 1); layout.setSpacing(0)
        self.message = QLabel('匯入遊戲，或掛載已批准場景\n\n在這裡編輯與試玩你的作品')
        self.message.setAlignment(Qt.AlignCenter); self.message.setWordWrap(True)
        self.message.setStyleSheet('font-size:18px;color:#a6b0c2;padding:25px;')
        layout.addWidget(self.message, 1)
        self.surface = NativeSurface(); self.surface.hide()
        layout.addWidget(self.surface, 1)
        self.resize_timer = QTimer(self); self.resize_timer.setSingleShot(True); self.resize_timer.setInterval(60)
        self.resize_timer.timeout.connect(self.resize_renderer)
        self.surface.resized.connect(self.resize_timer.start)

    @property
    def running(self):
        return self.process is not None and self.process.state() != QProcess.NotRunning

    def dimensions(self):
        ratio = self.surface.devicePixelRatioF()
        return max(64, round(self.surface.width() * ratio)), max(64, round(self.surface.height() * ratio))

    def launch(self, args, mode):
        self.stop()
        if sys.platform != 'win32':
            self.message.setText('目前原生嵌入支援 Windows。'); return
        self.generation += 1
        self.mode, self.ready, self.buffer, self.started_at = mode, False, b'', time.monotonic()
        self.paused=False
        self.message.hide(); self.surface.show()
        self.surface.winId()
        process = QProcess(self); self.process = process
        process.setWorkingDirectory(str(ROOT)); process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(lambda p=process: self.read_output(p))
        process.finished.connect(lambda code, status, p=process: self.finished(p, code))
        process.errorOccurred.connect(lambda error, p=process: self.failure(p.errorString()) if p is self.process else None)
        process.start(sys.executable, args)
        generation = self.generation
        QTimer.singleShot(18000, lambda: self.timeout(generation))

    def launch_project(self, manifest, source_root):
        self.message.hide(); self.surface.show(); self.layout().activate()
        width, height = self.dimensions()
        args = ['-u', '-m', 'srtp.project_viewer', '--manifest', str(manifest), '--source-root', str(source_root),
                '--parent-window', str(int(self.surface.winId())), '--studio', '--width', str(width), '--height', str(height)]
        if self.probe: args.append('--studio-probe')
        self.launch(args, 'target')

    def launch_source(self, package):
        if package.runtime.framework.lower() != 'pygame':
            self.failure('此來源尚未有嵌入適配器：' + package.runtime.framework + '。請先使用 Pygame 來源。')
            return
        args=['-u', '-m', 'srtp.studio.source_worker', '--entry', str(package.entrypoint),
              '--cwd', str(package.runtime.cwd), '--parent-window', str(int(self.surface.winId()))]
        if self.probe:args.append('--probe')
        self.launch(args,'source')

    def timeout(self, generation):
        if generation == self.generation and self.running and not self.ready:
            self.failure('場景未能在 18 秒內啟動。請在診斷中查看原因。'); self.stop(keep_message=True)

    def read_output(self, process):
        data = bytes(process.readAllStandardOutput())
        if process is not self.process: return
        self.buffer += data
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            text = line.decode('utf-8', errors='replace').strip()
            if text.startswith('CUBE_STUDIO '):
                try: event = json.loads(text[len('CUBE_STUDIO '):])
                except ValueError: continue
                if event['event'] in ('scene', 'source_frame'):
                    self.ready = True; self.message.hide(); self.surface.show()
                    self.last_frame = event
                if event['event']=='source_state':self.paused=event['paused']
                self.event.emit(event)
            elif text: self.log.emit(text)
        if len(self.buffer) > 4_000_000: self.buffer = b''

    def send(self, op, **payload):
        if self.running:
            self.process.write((json.dumps({'op':op, **payload}, ensure_ascii=False) + '\n').encode('utf-8'))

    def resize_renderer(self):
        if self.running:
            width, height = self.dimensions(); self.send('resize', width=width, height=height)

    def failure(self, message):
        self.surface.hide(); self.message.setText(message); self.message.show()
        self.event.emit({'event':'error', 'message':message})

    def finished(self, process, code):
        if process is self.process:
            self.process = None; self.ready = False
            self.surface.hide(); self.message.setText('遊戲已停止。點擊播放可重新開啟。' if not code else '場景啟動失敗。請查看診斷。')
            self.message.show(); self.event.emit({'event':'stopped', 'code':code})
        process.deleteLater()

    def stop(self, keep_message=False):
        process, self.process = self.process, None
        self.generation += 1; self.ready = False
        if process and process.state() != QProcess.NotRunning:
            process.write(b'{"op":"quit"}\n'); process.closeWriteChannel()
            # No UI wait: bounded termination belongs only to this child.
            timer=QTimer(process); timer.setSingleShot(True)
            timer.timeout.connect(lambda p=process: p.kill() if p.state() != QProcess.NotRunning else None)
            timer.start(1200)
        self.surface.hide()
        if not keep_message: self.message.setText('場景已停止。點擊播放可重新開啟。')
        self.message.show()

    def shutdown(self):
        process = self.process
        if process and process.state() != QProcess.NotRunning:
            process.write(b'{"op":"quit"}\n'); process.closeWriteChannel()
            if not process.waitForFinished(800):
                process.kill(); process.waitForFinished(800)
        self.process = None
