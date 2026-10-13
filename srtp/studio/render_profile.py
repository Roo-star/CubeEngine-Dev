"""Small, explicit render contract for the Alpha renderer; no gameplay fields."""
import math

FORMAT = 'cubeengine.render-profile/1.0'
DEFAULT = {'style': 'Shaded', 'background': '#30343a', 'tint': '#ffffff', 'textures': True}


def hex_color(value):
    if not isinstance(value, str) or len(value) != 7 or value[0] != '#':
        raise ValueError('Expected a color in #RRGGBB format')
    try:
        return tuple(int(value[i:i+2], 16) / 255 for i in (1, 3, 5))
    except ValueError:
        raise ValueError('Expected a color in #RRGGBB format') from None


def validate_profile(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT):
        raise ValueError('Invalid render profile fields')
    if value['style'] not in ('Shaded', 'Unlit', 'Wireframe') or type(value['textures']) is not bool:
        raise ValueError('Invalid render style or texture setting')
    hex_color(value['background']); hex_color(value['tint'])
    return dict(value)


def spatial_settings(dimensions, spacing, cube_size):
    if (not isinstance(dimensions, list) or len(dimensions) != 3 or
            any(type(v) is not int or not 1 <= v <= 32 for v in dimensions)):
        raise ValueError('Volume preview dimensions must be integers from 1 to 32')
    for value in (spacing, cube_size):
        if isinstance(value, bool) or not math.isfinite(float(value)) or not .1 <= float(value) <= 5:
            raise ValueError('Cell spacing and cube size must be between 0.1 and 5')
    return {'dimensions': dimensions, 'spacing': float(spacing), 'cube_size': float(cube_size)}
