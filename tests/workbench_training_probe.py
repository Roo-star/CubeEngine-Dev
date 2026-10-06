"""Exercise real DPG callbacks/rendering with the local worker and capture it."""
import json
from pathlib import Path
import sys
import time

import dearpygui.dearpygui as dpg

from srtp.training.artifacts import atomic_json
from srtp.workbench import main


def run():
    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts/workbench-ai"
    output.mkdir(exist_ok=True, parents=True)
    target = root / "tests/fixtures/workbench_training_target_20261004/project.manifest.json"
    sys.argv = ["workbench", "--legacy-ui", "--project", str(target)]
    original = dpg.render_dearpygui_frame
    started = False
    verified = False
    captured = False
    frames = 0
    done_frames = 0
    deadline = time.monotonic() + 90
    def frame():
        nonlocal started, verified, captured, frames, done_frames
        original()
        if time.monotonic() > deadline:
            dpg.stop_dearpygui()
            raise AssertionError("Workbench callback probe timed out")
        status = dpg.get_value("training_status") or ""
        if not started and (dpg.get_value("training_eligibility") or "").startswith("ELIGIBLE"):
            dpg.get_item_configuration("training_start")["callback"]()
            started = True
        elif started and not captured:
            frames += 1
            if "completed" in status and dpg.get_item_configuration("training_verify")["enabled"] and not verified:
                dpg.get_item_configuration("training_resume_last")["callback"]()
                assert dpg.get_value("training_resume").endswith("checkpoint.json")
                dpg.get_item_configuration("training_verify")["callback"]()
                verified = True
            if verified and status.startswith("Verified"):
                assert frames > 30, "Render loop did not stay active during training"
                atomic_json(output / "ui-probe.json", {"passed": True, "rendered_frames_during_run": frames,
                            "run": dpg.get_value("training_run_path"), "resume_selected": dpg.get_value("training_resume"),
                            "replay_status": status, "method": "Actual Dear PyGui widget callbacks and framebuffer; no synthetic mouse input."})
                dpg.output_frame_buffer(str(output / "workbench-trained.png"))
                captured = True
        elif captured:
            done_frames += 1
            if done_frames > 10:
                dpg.stop_dearpygui()
    dpg.render_dearpygui_frame = frame
    main()
    assert captured
    print("Workbench render/callback probe passed", flush=True)


if __name__ == "__main__":
    run()
