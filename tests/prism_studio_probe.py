"""Real DPG layout/callback and real CPU training acceptance for Prism Studio."""
from pathlib import Path
import sys
import time

import dearpygui.dearpygui as dpg

from srtp.training.artifacts import atomic_json
from srtp.prism_studio import PrismStudio
from srtp.workbench import main


def run():
    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts/prism-studio"
    output.mkdir(exist_ok=True, parents=True)
    target = root / "tests/fixtures/workbench_training_target_20261004/project.manifest.json"
    sys.argv = ["workbench", "--project", str(target), "--size", "1440x940"]
    original_init, original_frame = PrismStudio.__init__, dpg.render_dearpygui_frame
    holder = {}

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        holder["studio"] = self
    PrismStudio.__init__ = init
    frame, ready, started, verified, final_frame = 0, False, False, False, None
    training_frames = 0
    deadline = time.monotonic() + 120
    checks = []

    def capture(name):
        dpg.output_frame_buffer(str(output / (name + ".png")))

    def check_layout(name):
        rail, dock = dpg.get_item_state("srtp_right_column"), dpg.get_item_state("srtp_training_dock")
        low = [rail["pos"][i] + dock["pos"][i] for i in range(2)]
        high = [low[i] + dock["rect_size"][i] for i in range(2)]
        for tag in ("training_start", "training_cancel", "training_resume_last"):
            a, b = dpg.get_item_rect_min(tag), dpg.get_item_rect_max(tag)
            assert a[0] >= low[0] and a[1] >= low[1] and b[0] <= high[0] and b[1] <= high[1], (name, tag, low, high, a, b)
        a, b = dpg.get_item_rect_max("prism_view_compare"), dpg.get_item_rect_min("srtp_play")
        assert a[0] <= b[0], (name, "View tabs overlap Play", a, b)
        assert high[0] <= dpg.get_viewport_client_width(), (name, high)
        checks.append({"name": name, "cancel_and_resume_visible": True, "toolbar_no_overlap": True})

    def render():
        nonlocal frame, ready, started, verified, final_frame, training_frames
        original_frame()
        if time.monotonic() > deadline:
            raise AssertionError("Prism probe timed out")
        studio = holder["studio"]
        c = studio.c
        if not ready:
            if c.training_panel.snapshot is None:
                return
            ready = True
        frame += 1
        if frame == 20:
            for point in ((0, 0, 2), (0, 1, 0), (2, 0, 1), (1, 2, 2), (1, 1, 1)):
                c.click_core_cell(user_data=point)
                assert c.core_last_result["accepted"], c.core_last_result
            assert studio.state.revision == 5
        if frame == 30:
            check_layout("1440x940 / 100%")
            capture("prism-live-game")
        if frame == 40:
            studio.set_view("compare")
        if frame == 50:
            capture("prism-compare")
        if frame == 60:
            studio.show("prism_training_settings")
        if frame == 70:
            capture("prism-training-settings")
        if frame == 80:
            dpg.hide_item("prism_training_settings")
            studio.set_view("target")
            studio.set_scale(1.25)
        if frame == 95:
            check_layout("1440x940 / 125%")
            capture("prism-125percent")
        if frame == 105:
            studio.set_scale(1.5)
        if frame == 120:
            check_layout("1440x940 / 150%")
            capture("prism-150percent")
        if frame == 130:
            studio.set_scale(1)
            dpg.set_viewport_width(1366)
            dpg.set_viewport_height(768)
        if frame == 150:
            check_layout("1366x768 / 100%")
            capture("prism-1366")
        if frame == 160:
            dpg.set_viewport_width(1024)
        if frame == 180:
            check_layout("1024x768 / 100%")
            capture("prism-1024")
        if frame == 190:
            dpg.set_viewport_width(1440)
            dpg.set_viewport_height(940)
            studio.load_reference(next(label for label in studio.references if label.startswith("Minesweeper")))
            assert c.training_panel.snapshot is None and studio.state is None
            assert not dpg.get_item_configuration("training_start")["enabled"]
        if frame == 210:
            capture("prism-minesweeper-source")
            studio.load_reference(next(iter(studio.references)))
            c.open_project_manifest(target)
        if frame > 225 and not started and c.training_panel.snapshot:
            dpg.get_item_configuration("training_start")["callback"]()
            started = True
        if started and not final_frame:
            training_frames += 1
            status = dpg.get_value("training_status") or ""
            if "completed" in status and dpg.get_item_configuration("training_verify")["enabled"] and not verified:
                dpg.get_item_configuration("training_resume_last")["callback"]()
                assert dpg.get_value("training_resume").endswith("checkpoint.json")
                dpg.get_item_configuration("training_verify")["callback"]()
                verified = True
            if verified and status.startswith("Verified"):
                assert training_frames > 30
                for point in ((0, 0, 2), (0, 1, 0), (2, 0, 1), (1, 2, 2), (1, 1, 1)):
                    c.click_core_cell(user_data=point)
                final_frame = frame
        if final_frame and frame == final_frame + 15:
            check_layout("Completed run / controls retained")
            capture("prism-completed")
            atomic_json(output / "acceptance.json", {"passed": True, "layouts": checks,
                        "real_game_input_transitions": 5, "source_switch_clears_target": True,
                        "training_rendered_frames": training_frames, "run": dpg.get_value("training_run_path"),
                        "resume": dpg.get_value("training_resume"), "replay": dpg.get_value("training_status"),
                        "method": "Real DPG callbacks/framebuffer and RuleRuntime, real CPU worker; no paid API."})
        if final_frame and frame > final_frame + 25:
            dpg.stop_dearpygui()
    dpg.render_dearpygui_frame = render
    try:
        main()
    finally:
        PrismStudio.__init__ = original_init
        dpg.render_dearpygui_frame = original_frame
    assert final_frame
    print("PRISM ACCEPTANCE PASSED", flush=True)


if __name__ == "__main__":
    run()
