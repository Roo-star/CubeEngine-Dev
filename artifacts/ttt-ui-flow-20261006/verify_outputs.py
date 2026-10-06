"""Offline checks of this UI run's original approved outputs; no API calls."""
import hashlib
import importlib.util
import json
from itertools import product
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from srtp.project_viewer import ProjectHost
from srtp.training.adapter import inspect_target,Ineligible

SOURCE=ROOT/'.cubeengine_llm/tic.tac.toe/source_20261006-220709/project.manifest.json'
TARGET=ROOT/'.cubeengine_llm/tic.tac.toe/target_20261006-220937/project.manifest.json'
ORACLE=Path('E:/CubeEngine/Dev/3D/tictactoe3d_logic.py')

def load_module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def click(host,coordinate):
    return host.mouse_click('mouse.button.primary',
        {'coordinate':coordinate,'rule_topology':host.rule['topologies'][0]['id']})

hashes={str(p):digest(p) for manifest in (SOURCE,TARGET)
        for p in [manifest,*sorted((manifest.parent/'ir').glob('*.json'))]}
source_game=load_module(ROOT/'srtp/reference_games/pygame_tictactoe/main.py','original_2d')
sequences=[[(0,0),(0,1),(1,0),(1,1),(2,0)],
           [(0,0),(1,0),(1,1),(2,0),(2,2)],
           [(0,0),(1,0),(2,0),(1,1),(0,1),(2,1),(1,2),(0,2),(2,2)]]
host=ProjectHost(SOURCE)
try:
    assert host.controller.snapshot().dimensions==(3,3)
    for sequence in sequences:
        host.controller.reset();game=source_game.TicTacToe()
        assert not click(host,(-1,0)).accepted
        for coordinate in sequence:
            assert game.place(*coordinate)
            result=click(host,coordinate)
            assert result.accepted,(coordinate,result.message)
            assert host.controller.snapshot().terminal==bool(game.winner or game.draw)
            assert not click(host,coordinate).accepted
        assert host.controller.verify_replay()['passed']
        assert host.key('keyboard.key.r').accepted
        assert not host.controller.snapshot().terminal
finally:host.close()

oracle=load_module(ORACLE,'native_3d_oracle')
assert len(oracle.ALL_LINES)==49
host=ProjectHost(TARGET)
try:
    assert host.controller.snapshot().dimensions==(3,3,3)
    for line in oracle.ALL_LINES:
        host.controller.reset();board=oracle.create_board()
        fillers=[c for c in product(range(3),repeat=3) if c not in line][:2]
        sequence=[line[0],fillers[0],line[1],fillers[1],line[2]]
        for index,coordinate in enumerate(sequence):
            assert oracle.is_valid_move(board,*coordinate)
            oracle.set_cell(board,*coordinate,1 if index%2==0 else 2)
            result=click(host,coordinate)
            assert result.accepted,(line,index,result.message)
            assert host.controller.snapshot().terminal==(oracle.game_status(board)!='ongoing')
        assert oracle.game_status(board)=='win_1'
        assert host.controller.snapshot().winners==('X',)
        empty=next(c for c in product(range(3),repeat=3) if c not in sequence)
        assert not click(host,empty).accepted
        assert host.controller.verify_replay()['passed']
finally:host.close()

try:
    inspect_target(TARGET)
    eligibility={'eligible':True}
except Ineligible as error:
    eligibility={'eligible':False,'reasons':error.reasons}

assert all(digest(Path(path))==value for path,value in hashes.items())
report={'api_requests':0,'source_sequences':len(sequences),'native_3d_winning_lines':49,
        'occupied_cell_rejection':True,'terminal_input_rejection':True,'replay':True,
        'source_restart':True,'approved_manifest_and_ir_unchanged':True,
        'artifact_sha256':hashes,'native_oracle_sha256':digest(ORACLE),
        'training_eligibility':eligibility,
        'scope':'Offline original-source/native-rule comparison via InputIR press/release; native mouse/UI captured separately. No full performance/visual parity or AI training acceptance.'}
(OUT/'offline-output-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:v for k,v in report.items() if k!='artifact_sha256'},ensure_ascii=False,indent=2))
