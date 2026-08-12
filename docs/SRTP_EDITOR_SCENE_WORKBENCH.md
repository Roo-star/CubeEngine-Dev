# CubeEngine Workbench: Embedded Scene Specification

## 1. Decision

The requested Unity/RPG Maker-style editor should not be built by adding more controls around a popup Ursina window. Dear PyGui remains useful for prototypes, but the production Workbench shell should move to **PySide6/PyQt6 with dockable panels**. The Panda3D/Ursina window can then be hosted through the native-window bridge (`QWindow.fromWinId` and `QWidget.createWindowContainer`) or a dedicated Qt-compatible render surface.

This is a planned shell migration. It is not claimed to be complete in the current Dear PyGui implementation.

## 2. Default layout

```text
+----------------------+-----------------------------+----------------------+
| Project / Assets     | Scene | Game                | Inspector            |
| files, imports,      | embedded 3D viewport        | component properties |
| prefabs, materials   | gizmos, grid, cameras       | source provenance    |
+----------------------+-----------------------------+----------------------+
| Hierarchy            | Timeline / Rule Graph       | Diagnostics          |
| scene nodes          | events, state transitions   | Console / Import Log |
+----------------------+-----------------------------+----------------------+
```

All panels are dockable, resizable and recoverable through saved layouts.

## 3. Scene view behavior

The Scene view is an editor, not Play Mode:

- select one or many scene objects;
- translate, rotate and scale with local/world gizmos;
- create/delete/duplicate/reparent nodes;
- place cameras, lights, grids, spawn points and gameplay entities;
- switch orthographic/perspective and standard views;
- snap by cell, vertex, surface, angle or numeric increment;
- isolate/focus layers without hiding committed gameplay state;
- adjust board opacity without changing runtime material data;
- display source-to-target presentation mappings and unresolved roles;
- show Rule IR references and source provenance for the selected object.

The current Ursina viewer now follows the layer rule needed by the future Scene view: a focused layer controls selection/colliders; stateful pieces, revealed cells and placed objects remain visible on every layer.

## 4. Edit and Play isolation

Pressing Play creates a runtime clone of Scene/Rule state. Runtime mutations never silently overwrite the editable scene.

- **Edit** — author scene, assets, rules and bindings.
- **Play** — route focus to game input and execute compiled rules.
- **Pause** — freeze scheduling but permit inspection.
- **Step** — advance one event/tick for debugging.
- **Stop** — discard runtime state or explicitly apply selected changes.

## 5. Inspector

The Inspector is schema-driven. Selecting a board, entity, camera, light, action, outcome or asset shows only properties relevant to its component types.

Every property exposes:

- value and unit/type;
- source, inferred, generated or designer-authored ownership;
- source file/line evidence where applicable;
- safe edit/read-only/unresolved state;
- validation feedback;
- reset to source and create-variant actions.

Parameters must not be generic genre dropdowns. For a Minesweeper source, `neighborhood kernel`, `mine distribution` and `first-click safety` are relevant. For Sokoban, `push chain`, `wall collision`, `goal occupancy` and map layers are relevant. Unsupported properties are absent, not mysterious `N/A` rows.

## 6. Hierarchy and assets

The Hierarchy represents Scene IR nodes. The Asset Browser represents imported source files and generated resources. They are different concerns.

Every asset receives a stable GUID and import record. Reimport updates derived artifacts while preserving scene references and designer overrides. Source assets remain unchanged; generated meshes/materials live in a CubeEngine cache or variant directory.

## 7. Rule Graph

The Rule Graph provides a visual escape hatch comparable to event systems in creator-focused engines:

- event nodes (input, tick, collision, timer, state change);
- conditions and typed queries;
- state effects and entity operations;
- functions/subgraphs and reusable rule resources;
- deterministic random nodes with explicit streams;
- code nodes with declared inputs/outputs and sandbox policy.

LLM output appears as a proposed graph diff with evidence and tests. Designers can accept, edit or reject it.

## 8. Input and viewport controls

Editor controls and game controls are separate input contexts. Default editor navigation can retain right-drag orbit, wheel zoom, W/A/S/D 90-degree views and Z/V layer tools, while a game uses arrows, click, right click or configurable actions. When Play Mode owns focus, conflicts are resolved through the Input IR rather than hard-coded event order.

## 9. Implementation slices

1. Create a PySide6 application shell with `QMainWindow`, dock widgets and saved layouts.
2. Embed a minimal Panda3D/Ursina scene in the central widget on Windows.
3. Add Scene/Hierarchy selection synchronization and a command-based undo stack.
4. Add schema-driven Inspector and asset GUID database.
5. Separate Edit/Play state and input contexts.
6. Add Rule Graph, source provenance and LLM proposal review.

Each slice must have an automated smoke test plus Windows manual acceptance checklist.

## 10. Research references

- Unity documents a dockable editor whose Hierarchy and Scene views are linked, with an Inspector for the selected object's components and properties: <https://docs.unity3d.com/Manual/UsingTheSceneView.html>, <https://docs.unity3d.com/Manual/Hierarchy.html>, <https://docs.unity3d.com/es/current/Manual/UsingTheInspector.html>.
- Godot treats central editor screens, custom inspectors and source importers as plugin extension points: <https://docs.godotengine.org/en/stable/tutorials/plugins/editor/making_main_screen_plugins.html>, <https://docs.godotengine.org/en/stable/tutorials/plugins/editor/inspector_plugins.html>, <https://docs.godotengine.org/en/stable/tutorials/plugins/editor/import_plugins.html>.
- RPG Maker separates Map and Event modes and centers direct placement/editing rather than exposing only raw parameters: <https://rpgmakerofficial.com/product/MZ_help-en/01_03.html>, <https://rpgmakerofficial.com/product/MZ_help-en/01_04.html>.
- Qt provides the Windows-native embedding bridge proposed for the Panda3D/Ursina viewport: <https://doc.qt.io/qt-6/platform-integration.html>.
