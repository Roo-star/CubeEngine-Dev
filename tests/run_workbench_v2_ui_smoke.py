"""Exercise the real Qt Workbench -> embedded viewer -> trained AI first move."""
import json
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from srtp.studio.app import Studio, configure_application
from srtp.studio.capture import capture_window

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / '.cubeengine_llm/tic.tac.toe/target_20261006-220937/project.manifest.json'
SCREENSHOT = ROOT / 'artifacts/workbench-v2-ai-ui.png'


def main(receipt=None,screenshot=None):
    app = QApplication([])
    configure_application(app)
    window = Studio(project=TARGET)
    area=app.primaryScreen().availableGeometry()
    window.resize(min(1600,area.width()-30),min(1000,area.height()-40))
    window.show()
    begun = time.monotonic()
    state = {'selected': False, 'result': None}

    def tick():
        if not state['selected'] and window.c.training_panel.preparation and window.c.training_panel.preparation.trainable:
            window.ai_selector.setCurrentIndex(2)  # AI moves first
            window.switch_workspace('Simulator', 'game')
            state['selected'] = True
        scene = window.scene_data
        if state['selected'] and scene.get('playing') and scene.get('revision', 0) >= 1:
            capture_window(window,screenshot or SCREENSHOT)
            state['result'] = {'passed': True, 'revision': scene['revision'],
                               'ai_status': scene.get('ai_status'), 'screenshot': str(screenshot or SCREENSHOT)}
            app.quit()
        elif time.monotonic() - begun > 90:
            state['result'] = {'passed': False, 'scene': scene, 'status': window.status_label.text(),
                               'logs': window.console.toPlainText()[-2000:]}
            app.quit()

    timer = QTimer()
    timer.setInterval(200)
    timer.timeout.connect(tick)
    timer.start()
    app.exec()
    timer.stop()
    window.close()
    (receipt or ROOT/'docs/WORKBENCH_V2_UI_ACCEPTANCE_20261013.json').write_text(
        json.dumps(state['result'],ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    print(json.dumps(state['result'], ensure_ascii=False, default=str))
    return 0 if state['result'] and state['result']['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
