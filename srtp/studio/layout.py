"""Hash-bound presentation drafts; validate the entire document before applying."""
import math

FORMAT = 'cubeengine.scene-layout-draft/1.0'


def vector(value, scale=False):
    if not isinstance(value, list) or len(value) != 3 or any(isinstance(v, bool) for v in value):
        raise ValueError('Expected three numeric transform components')
    result = [float(v) for v in value]
    if any(not math.isfinite(v) or abs(v) > 10000 or (scale and v <= .001) for v in result):
        raise ValueError('Transform outside supported range')
    return result


def validate_draft(document, project_hash, identifiers):
    if not isinstance(document, dict) or document.get('format') != FORMAT:
        raise ValueError('不是 CubeEngine 場景草稿')
    if document.get('project_hash') != project_hash:
        raise ValueError('草稿屬於另一個遊戲或版本，無法套用')
    overrides = document.get('overrides')
    if not isinstance(overrides, dict) or len(overrides) > len(identifiers):
        raise ValueError('草稿物件清單無效')
    result = {}
    for key, values in overrides.items():
        if key not in identifiers or not isinstance(values, dict):
            raise ValueError('草稿參照不存在的場景物件')
        props = {}
        for prop, value in values.items():
            if prop == 'visible' and type(value) is bool:
                props[prop] = value
            elif prop in ('position', 'rotation', 'scale'):
                props[prop] = vector(value, prop == 'scale')
            else:
                raise ValueError('草稿包含不支援的屬性')
        result[key] = props
    return result
