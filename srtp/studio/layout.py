"""Hash-bound presentation drafts; validate the entire document before applying."""
import math


def save_json(path, document):
    """Editor artifacts need no training or numerical-library imports."""
    import json,os
    from pathlib import Path
    path=Path(path)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(document,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(temporary,path)

FORMAT = 'cubeengine.scene-layout-draft/1.0'


def validate_layout_objects(objects):
    from pathlib import Path
    if not isinstance(objects,list) or len(objects)>128:raise ValueError('Invalid layout object list')
    ids=set(); result=[]
    for spec in objects:
        if not isinstance(spec,dict):raise ValueError('Invalid layout object')
        key=spec.get('id')
        if not isinstance(key,str) or not key.startswith('editor:') or len(key)>100 or key in ids:
            raise ValueError('Invalid or duplicate layout object ID')
        ids.add(key)
        if spec.get('kind') not in ('cube','sphere','plane','mesh'):raise ValueError('Unsupported layout geometry')
        if spec['kind']=='mesh':
            p=Path(spec.get('path',''))
            if not p.is_file() or p.suffix.lower() not in ('.obj','.bam') or p.stat().st_size>32_000_000:
                raise ValueError('Draft mesh must be an existing OBJ or BAM up to 32 MB')
        for prop in ('position','rotation','scale'):
            if prop in spec:vector(spec[prop],prop=='scale')
        result.append(dict(spec))
    return result


def vector(value, scale=False):
    if not isinstance(value, list) or len(value) != 3 or any(isinstance(v, bool) for v in value):
        raise ValueError('Expected three numeric transform components')
    result = [float(v) for v in value]
    if any(not math.isfinite(v) or abs(v) > 10000 or (scale and v <= .001) for v in result):
        raise ValueError('Transform outside supported range')
    return result


def validate_draft(document, project_hash, identifiers):
    if not isinstance(document, dict) or document.get('format') != FORMAT:
        raise ValueError('Invalid CubeEngine scene draft')
    if document.get('project_hash') != project_hash:
        raise ValueError('Draft belongs to another game or version')
    overrides = document.get('overrides')
    if not isinstance(overrides, dict) or len(overrides) > len(identifiers):
        raise ValueError('Invalid draft object list')
    result = {}
    for key, values in overrides.items():
        if key not in identifiers or not isinstance(values, dict):
            raise ValueError('Draft references a missing scene object')
        props = {}
        for prop, value in values.items():
            if prop == 'visible' and type(value) is bool:
                props[prop] = value
            elif prop in ('position', 'rotation', 'scale'):
                props[prop] = vector(value, prop == 'scale')
            else:
                raise ValueError('Draft contains an unsupported property')
        result[key] = props
    return result
