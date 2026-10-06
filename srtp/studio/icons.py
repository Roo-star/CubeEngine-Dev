"""One 24 px vector icon family; no font glyphs or emoji dependencies."""
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

PATHS = {
    'folder': 'M3 7 L3 20 L21 20 L21 7 L12 7 L10 4 L3 4 Z',
    'cube': 'M12 3 L21 8 L21 17 L12 22 L3 17 L3 8 Z M3 8 L12 13 L21 8 M12 13 L12 22',
    'layers': 'M12 3 L22 8 L12 13 L2 8 Z M3 12 L12 17 L21 12 M3 16 L12 21 L21 16',
    'play': 'M8 4 L20 12 L8 20 Z',
    'pause': 'M8 5 L8 19 M16 5 L16 19',
    'stop': 'M6 6 L18 6 L18 18 L6 18 Z',
    'reset': 'M4 5 L4 11 L10 11 M4 11 L7 5 L14 3 L20 7 L21 14 L17 20 L10 21 L6 18',
    'plus': 'M12 5 L12 19 M5 12 L19 12',
    'close': 'M6 6 L18 18 M18 6 L6 18',
    'search': 'M16 16 L21 21',
    'sliders': 'M5 3 L5 8 M5 12 L5 21 M12 3 L12 14 M12 18 L12 21 M19 3 L19 5 M19 9 L19 21 M2 8 L8 8 L8 12 L2 12 Z M9 14 L15 14 L15 18 L9 18 Z M16 5 L22 5 L22 9 L16 9 Z',
    'terminal': 'M4 6 L10 12 L4 18 M13 18 L21 18',
    'image': 'M3 4 L21 4 L21 20 L3 20 Z M3 16 L8 11 L13 16 L17 12 L21 16',
    'code': 'M8 6 L2 12 L8 18 M16 6 L22 12 L16 18 M14 3 L10 21',
    'save': 'M4 3 L17 3 L21 7 L21 21 L3 21 L3 3 Z M8 3 L8 9 L17 9 L17 3 M7 21 L7 14 L17 14 L17 21',
    'export': 'M8 5 L4 5 L4 21 L20 21 L20 17 M11 3 L21 3 L21 13 M21 3 L10 14',
    'arrow': 'M4 12 L20 12 M14 6 L20 12 L14 18',
    'chevron': 'M9 6 L15 12 L9 18',
    'check': 'M4 12 L9 17 L20 6',
    'focus': 'M8 3 L3 3 L3 8 M16 3 L21 3 L21 8 M3 16 L3 21 L8 21 M21 16 L21 21 L16 21 M9 9 L15 9 L15 15 L9 15 Z',
    'move': 'M12 2 L12 22 M2 12 L22 12 M9 5 L12 2 L15 5 M9 19 L12 22 L15 19 M5 9 L2 12 L5 15 M19 9 L22 12 L19 15',
    'cursor': 'M5 2 L20 13 L13 14 L10 21 Z',
    'eye': 'M2 12 L6 6 L12 4 L18 6 L22 12 L18 18 L12 20 L6 18 Z',
    'brain': 'M10 4 L7 3 L4 6 L4 9 L2 11 L3 15 L5 16 L5 19 L9 21 L12 19 L15 21 L19 19 L19 16 L21 14 L21 10 L19 8 L19 5 L16 3 L13 4 L12 7 L10 4 M12 7 L12 19 M5 9 L8 10 L8 14 M19 8 L16 10 L16 14 M5 16 L8 14 L12 16 M19 16 L16 14 L12 16',
    'chart': 'M4 3 L4 21 L22 21 M8 16 L8 12 M13 16 L13 8 M18 16 L18 4',
    'clock': 'M12 6 L12 12 L16 14',
    'file': 'M5 2 L14 2 L20 8 L20 22 L5 22 Z M14 2 L14 8 L20 8 M8 13 L17 13 M8 17 L15 17',
    'help': 'M8 8 L8 6 L12 4 L16 6 L16 9 L12 12 L12 15 M12 19 L12 19.1',
}
_cache = {}

def icon(name, color='#bdc3d1', size=24):
    key = name, color, size
    if key in _cache:
        return _cache[key]
    pix = QPixmap(size * 2, size * 2)
    pix.setDevicePixelRatio(2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 24, size / 24)
    p.setPen(QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    path, command = QPainterPath(), ''
    tokens = PATHS.get(name, PATHS['cube']).split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token[0] in 'MLZ':
            command, rest = token[0], token[1:]
            if command == 'Z':
                path.closeSubpath(); i += 1; continue
            x = float(rest) if rest else float(tokens[i + 1])
            if not rest: i += 1
            y = float(tokens[i + 1]); i += 2
            (path.moveTo if command == 'M' else path.lineTo)(x, y)
    p.drawPath(path)
    if name in ('clock', 'help'):
        p.drawEllipse(QRectF(2, 2, 20, 20))
    elif name == 'search':
        p.drawEllipse(QRectF(3, 3, 14, 14))
    elif name == 'eye':
        p.drawEllipse(QRectF(9, 9, 6, 6))
    p.end()
    result = QIcon(pix)
    _cache[key] = result
    return result
