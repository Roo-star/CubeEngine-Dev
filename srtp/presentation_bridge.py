"""Source-presentation discovery and reusable 2D-to-3D texture mapping.

The bridge deliberately separates visual evidence from game mechanics.  It
never guesses a new art style: source images and source-authored colour/font
data are mapped to repeatable surface textures, while unsupported assets stay
explicitly unresolved for the later designer/LLM handoff.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple


MINE_TILE_INDEX = {
    "covered": 0,
    "empty": 1,
    "flag": 2,
    "question": 3,
    "mine": 5,
    "exploded_mine": 6,
    "1": 8,
    "2": 9,
    "3": 10,
    "4": 11,
    "5": 12,
    "6": 13,
    "7": 14,
    "8": 15,
}


@dataclass
class PresentationProfile:
    adapter_id: str
    source_root: Optional[Path]
    strategy: str = "procedural_fallback"
    role_paths: Dict[str, Path] = field(default_factory=dict)
    source_data: Dict[str, Any] = field(default_factory=dict)
    unresolved: Tuple[str, ...] = ()

    def image_for(self, role: str, value: Optional[int] = None):
        """Return a PIL image ready to become an Ursina texture."""

        try:
            from PIL import Image, ImageDraw, ImageFont
        except ModuleNotFoundError:
            return None

        if self.adapter_id == "minesweeper":
            atlas = self.role_paths.get("tile_atlas")
            index = MINE_TILE_INDEX.get(role)
            if atlas is None or index is None or not atlas.is_file():
                return None
            with Image.open(str(atlas)) as source:
                image = source.convert("RGBA")
                tile_width = image.width // 8
                tile_height = image.height // 2
                x = (index % 8) * tile_width
                y = (index // 8) * tile_height
                return image.crop((x, y, x + tile_width, y + tile_height)).copy()

        if self.adapter_id == "snake":
            path = self.role_paths.get(role)
            if path is None or not path.is_file():
                return None
            with Image.open(str(path)) as source:
                image = source.convert("RGBA")
            if role != "food":
                grass = Image.new("RGBA", image.size, (167, 209, 61, 255))
                grass.alpha_composite(image)
                return grass
            return image

        if self.adapter_id == "2048" and role == "tile":
            number = int(value or 0)
            colours = self.source_data.get("colours", {})
            background = tuple(colours.get(str(number), colours.get("0", [205, 193, 180])))
            foreground = tuple(colours.get("dark", [119, 110, 101])) if number <= 4 else tuple(colours.get("light", [249, 246, 242]))
            image = Image.new("RGBA", (128, 128), background + ((255,) if len(background) == 3 else ()))
            if not number:
                return image
            font_size = 60 if number < 100 else 48 if number < 1000 else 36
            font = _source_font(ImageFont, str(self.source_data.get("font", "Verdana")), font_size)
            draw = ImageDraw.Draw(image)
            text = str(number)
            box = draw.textbbox((0, 0), text, font=font)
            width, height = box[2] - box[0], box[3] - box[1]
            draw.text(((128 - width) / 2, (128 - height) / 2 - box[1]), text, fill=foreground, font=font)
            return image
        return None


def discover_presentation(adapter_id: str, source_root: Optional[Path]) -> PresentationProfile:
    root = Path(source_root).resolve() if source_root else None
    profile = PresentationProfile(adapter_id=adapter_id, source_root=root)
    if root is None or not root.is_dir():
        profile.unresolved = ("source_root",)
        return profile

    if adapter_id == "snake":
        graphics = _case_insensitive_child(root, "Graphics")
        if graphics:
            names = (
                "head_up", "head_down", "head_left", "head_right",
                "tail_up", "tail_down", "tail_left", "tail_right",
                "body_horizontal", "body_vertical", "body_tl", "body_tr", "body_bl", "body_br",
            )
            for name in names:
                path = graphics / (name + ".png")
                if path.is_file():
                    profile.role_paths[name] = path
            food = graphics / "apple.png"
            if food.is_file():
                profile.role_paths["food"] = food
        profile.strategy = "source_sprite_surface_projection" if profile.role_paths else "procedural_fallback"
        profile.unresolved = () if profile.role_paths else ("snake_sprite_roles",)
        return profile

    if adapter_id == "minesweeper":
        candidates = list(root.rglob("images/tiles/2000.png"))
        if candidates:
            profile.role_paths["tile_atlas"] = candidates[0]
            profile.strategy = "source_spritesheet_cube_faces"
        else:
            profile.unresolved = ("minesweeper_tile_atlas",)
        return profile

    if adapter_id == "2048":
        config = root / "constants.json"
        if config.is_file():
            try:
                value = json.loads(config.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                value = {}
            theme = value.get("colour", {}).get("light", {}) if isinstance(value, Mapping) else {}
            profile.source_data = {
                "font": value.get("font", "Verdana") if isinstance(value, Mapping) else "Verdana",
                "colours": dict(theme) if isinstance(theme, Mapping) else {},
            }
            profile.role_paths["configuration"] = config
            profile.strategy = "source_palette_generated_cube_faces"
        else:
            profile.unresolved = ("2048_visual_configuration",)
        return profile

    if adapter_id == "connect":
        profile.strategy = "source_vector_shape_to_3d_primitive"
    return profile


def presentation_manifest(adapter_id: str, source_root: Path, assets) -> Dict[str, Any]:
    """Serializable evidence exposed in Rule Schema for designer inspection."""

    profile = discover_presentation(adapter_id, source_root)
    source_assets = [str(item).replace("\\", "/") for item in assets]
    mapped_roles = {name: str(path.relative_to(source_root)).replace("\\", "/") for name, path in profile.role_paths.items() if path.is_relative_to(source_root)}
    return {
        "strategy": profile.strategy,
        "source_assets": source_assets,
        "mapped_roles": mapped_roles,
        "target_policy": "map source visuals onto all exposed 3D faces; keep state symbols attached to their cells",
        "unresolved": list(profile.unresolved),
        "requires_generative_3d": bool(profile.unresolved and source_assets),
    }


def _case_insensitive_child(root: Path, name: str) -> Optional[Path]:
    target = name.lower()
    return next((path for path in root.iterdir() if path.is_dir() and path.name.lower() == target), None)


def _source_font(image_font, name: str, size: int):
    candidates = [
        Path("C:/Windows/Fonts") / (name.lower().replace(" ", "") + ".ttf"),
        Path("C:/Windows/Fonts/verdana.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            try:
                return image_font.truetype(str(path), size)
            except OSError:
                continue
    return image_font.load_default()
