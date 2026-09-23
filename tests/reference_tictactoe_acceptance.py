"""Independent target behavior oracle from the user's native success case."""
import hashlib
import importlib.util
from itertools import product
from pathlib import Path


def verify_all_lines(manifest, reference_logic):
    from srtp.project_viewer import ProjectHost
    path=Path(reference_logic)
    spec=importlib.util.spec_from_file_location('native_reference_oracle',path)
    oracle=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    assert len(oracle.ALL_LINES)==49
    host=ProjectHost(manifest)
    try:
        for line in oracle.ALL_LINES:
            host.controller.reset()
            board=oracle.create_board()
            fillers=[c for c in product(range(3),repeat=3) if c not in line][:2]
            sequence=[line[0],fillers[0],line[1],fillers[1],line[2]]
            for index,coordinate in enumerate(sequence):
                assert oracle.is_valid_move(board,*coordinate)
                oracle.set_cell(board,*coordinate,1 if index%2==0 else 2)
                actual=host.mouse('mouse.button.primary',{'coordinate':coordinate})
                assert actual.accepted, (coordinate,actual.message)
                expected=oracle.game_status(board)
                assert host.controller.snapshot().terminal==(expected!='ongoing'),(line,index,expected)
            assert oracle.game_status(board)=='win_1'
            assert not host.mouse('mouse.button.primary',{'coordinate':fillers[-1]}).accepted
            assert host.controller.verify_replay()['passed']
        return {'winning_lines_tested':49,'terminal_and_replay':True,
            'oracle_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    finally:
        host.close()
