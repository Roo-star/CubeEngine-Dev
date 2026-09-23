"""Import a user's sealed IR bundle as conversion input, without inventing source code."""
from pathlib import Path

from .report import ParseReport, SourceEvidence
from .source_game import AnalysisCoverage, RuntimeSpec, SourceGamePackage, TransformationPlan


def import_manifest(path: Path, python_executable: str) -> SourceGamePackage:
    from .llm_compiler_v1.compiler import load_compile_report_from_bundle
    from .ir_v2 import compile_rule_ir

    path = path.resolve()
    if path.name != 'project.manifest.json':
        raise ValueError('Select project.manifest.json from a complete Project bundle.')
    bundle = load_compile_report_from_bundle(path.parent)
    rule = bundle.documents['rule_ir']
    from .session_random import session_sources
    runtime = compile_rule_ir(rule, random_sources=session_sources(rule))
    try:
        topology = next(iter(runtime.state.topologies.values()))
        dimensions = dict(zip(('x', 'y', 'z'), topology))
    finally:
        runtime.close()
    root = path.parent
    target = dict(dimensions)
    target.setdefault('z', 3)
    # This is a UI summary of supplied IR, not a claim that Python was reconstructed.
    summary = {'space': {'dimensions': dimensions}, 'supplied_rule_ir': rule,
               'input_kind': 'project_manifest'}
    return SourceGamePackage(
        title=bundle.manifest.get('metadata', {}).get('title') or path.parent.name,
        root=root, entrypoint=path,
        runtime=RuntimeSpec(kind='project_ir', command=[python_executable, '-m', 'srtp.project_viewer',
            '--manifest', str(path), '--source-root', str(root)],
            cwd=str(Path(__file__).resolve().parents[1]), framework='CubeEngine Project', language='IR'),
        rule_report=ParseReport(summary, source_path=str(path), source_format='project_manifest',
            provenance={'supplied_bundle': [SourceEvidence('sealed_bundle', path.name, 1.0,
                'User-supplied Project bundle; pinned IR documents are authoritative.', 1)]}),
        parameters=[], transformation=TransformationPlan(dimensions, target),
        coverage=AnalysisCoverage({'supplied IR bundle': 'proven', 'original source equivalence': 'unknown'}),
        files=sorted(str(p.relative_to(root)).replace('\\', '/') for p in root.rglob('*.json')
                     if p.resolve().is_relative_to(root)),
    )
