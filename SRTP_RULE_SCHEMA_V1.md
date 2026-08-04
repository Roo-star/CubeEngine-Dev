# CubeEngine Rule Schema v1 — field contract

`cubeengine.srtp/rule-schema-v1` 是 SRTP 三種輸入途徑共同填充的唯一模型：

- Function 1：JSON/Python deterministic extraction。
- Function 2：自然語言與 unresolved semantic completion。
- Function 3：骨幹表單 designer override。

後續 Function 4 只讀這份模型做 2D→3D mapping；STAL 不再讀任意來源腳本。

## 最小結構

```json
{
  "schema_version": "cubeengine.srtp/rule-schema-v1",
  "source": {"path": "", "format": "json", "sha256": "", "provenance": {}},
  "game": {"id": "game_id", "name": "Game", "description": "", "classification": {}},
  "space": {
    "topology": "rectangular_grid",
    "dimensions": {"x": 3, "y": 3, "z": 1},
    "coordinate_anchor": "cell_center",
    "coordinate_system": {"origin": [0, 0, 0], "axis_order": ["x", "y", "z"], "index_base": 0},
    "adjacency": {"kind": "orthogonal", "diagonals": false},
    "boundaries": {"x": "bounded", "y": "bounded", "z": "bounded"}
  },
  "participants": [],
  "entities": [],
  "state": {"board": {"empty_value": 0, "cell_states": {"0": "empty"}}, "variables": [], "information": "unknown"},
  "setup": {"placements": [], "generators": []},
  "flow": {"model": "unknown", "turn_order": [], "phases": [], "tick_rate": null},
  "actions": [],
  "randomness": {"model": "deterministic", "events": []},
  "goals": [],
  "outcomes": [],
  "modes": [{"id": "default", "name": "Default", "overrides": {}}],
  "ui_hints": {},
  "extensions": {}
}
```

## 必填語義

並非每一款遊戲都必須有棋子、對手、隨機性或終局；但一份可生成及可玩的 Schema 至少需要：

- 一個可尋址空間或 graph；矩形 grid 必須有正整數 X/Y/Z。
- 明確 coordinate anchor。
- 至少一個 player/system/chance action。
- 每個 executable action 的 actor、target、preconditions 和 effects。
- effects 使用的每個 state/entity/participant 都能被引用。
- flow model；turn-based 必須能決定 acting role。
- stochastic event 必須能取得 outcomes/probabilities，或標為 unresolved。

以下可以為空：

- `participants`：零玩家 simulation。
- `entities`：掃雷式 direct-space interaction。
- `outcomes`：無限 sandbox 或持續收集。
- `randomness.events`：僅 deterministic 時。
- `setup.placements`：空盤開始。

## 表達式 MVP

Preconditions：

- `cell_equals`
- `in_bounds`

Effects：

- `set_cell`
- `toggle_cell`

Outcome conditions：

- `line`
- `all_cells_not_equal`
- `count_state_at_least`
- `state_at_coordinate`

未來擴展 expression 必須新增明確 `op`、輸入型別、輸出型別和版本；不可把任意 Python code 字串塞入 Schema 執行。

## Provenance

`source.provenance` 以 canonical path 為 key：

```json
{
  "space.dimensions.x": [
    {
      "method": "array_shape",
      "source": "level.json",
      "confidence": 0.92,
      "detail": "level"
    }
  ]
}
```

允許的方法包括：

- `canonical_explicit`
- `explicit_dimensions`
- `array_shape`
- `derived_pixel_ratio`
- `python_static`
- `python_function_signatures`
- `designer_override`
- 未來的 `llm_completion`

後一個來源不覆蓋前一個證據；provenance 保留整條決策鏈。

## 版本策略

- v1 新增 optional field：保留同一版本並讓 validator 接受。
- 更改 action/state/outcome 的既有語義：升 schema major version。
- Function 4 產生的是另一份 `3D Rule Schema`，不得原地覆寫 source Rule Schema；兩者透過 mapping manifest 關聯。
- AI adapter 生成的九個 AlphaZero API 也應保存 source schema hash 和 action encoding version。
