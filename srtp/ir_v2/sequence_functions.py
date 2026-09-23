"""Deterministic collection operations for generated grid algorithms.

These are pure computations. Foreach evaluates its query once, allowing a
compiled action to compute a whole line/region before committing cell writes.
"""
from collections import deque
from .expression import ExpressionError, FunctionSpec


def merge_equal(values, empty, multiplier):
    items = [v for v in values if v != empty]
    merged, score, index = [], 0, 0
    while index < len(items):
        value = items[index]
        if index + 1 < len(items) and items[index + 1] == value:
            value *= multiplier
            score += value
            index += 2
        else:
            index += 1
        merged.append(value)
    return tuple(merged + [empty] * (len(values) - len(merged))), score


def sequence_function_specs(runtime):
    def get(args, context):
        values, index = args
        if type(index) is not int or not 0 <= index < len(values):
            raise ExpressionError('sequence.get index is outside its collection')
        return values[index]

    def zip_values(args, context):
        if len(args[0]) != len(args[1]):
            raise ExpressionError('sequence.zip requires equal lengths')
        return tuple(zip(*args))

    def vector_add(args, context):
        if len(args[0]) != len(args[1]):
            raise ExpressionError('vector.add requires equal ranks')
        return tuple(a + b for a, b in zip(*args))

    def grid_values(args, context):
        state = context.runtime if context.runtime is not None else runtime.state
        grid = state.grids[str(args[0])]
        result = []
        for coord in args[1]:
            if len(coord) != len(grid.shape) or any(type(c) is not int or c < 0 or c >= grid.shape[i] for i,c in enumerate(coord)):
                raise ExpressionError('grid.values coordinate is outside the grid')
            result.append(grid[tuple(coord)])
        return tuple(result)

    def flood(args, context):
        state_id, start, through_values, blocked_values, diagonal, include_boundary, blocked_coords = args
        state = context.runtime if context.runtime is not None else runtime.state
        grid = state.grids[str(state_id)]
        start = tuple(start)
        forbidden = {tuple(c) for c in blocked_coords}
        if len(start) != len(grid.shape) or any(type(c) is not int or c < 0 or c >= grid.shape[i] for i, c in enumerate(start)):
            raise ExpressionError('grid.flood_region start is outside the grid')
        from itertools import product
        directions = [d for d in product((-1, 0, 1), repeat=len(start))
                      if any(d) and (diagonal or sum(abs(v) for v in d) == 1)]
        queue, visited, result = deque([start]), set(), []
        while queue:
            coord = queue.popleft()
            if coord in visited or coord in forbidden:
                continue
            visited.add(coord)
            value = grid[coord]
            if value in blocked_values:
                continue
            expands = value in through_values
            if expands or include_boundary or coord == start:
                result.append(coord)
            if expands:
                for direction in directions:
                    other = tuple(a + b for a, b in zip(coord, direction))
                    if all(0 <= c < grid.shape[i] for i, c in enumerate(other)) and other not in visited:
                        queue.append(other)
        return tuple(sorted(result))

    definitions = [
        ('sequence.get', get, 'core:any', ('core:any', 'core:int')),
        ('sequence.zip', zip_values, 'core:any', ('core:any', 'core:any')),
        ('sequence.slice', lambda a,c: tuple(a[0][a[1]:a[2]]), 'core:any', ('core:any','core:int','core:int')),
        ('sequence.concat', lambda a,c: tuple(a[0]) + tuple(a[1]), 'core:any', ('core:any','core:any')),
        ('sequence.merge_equal', lambda a,c: merge_equal(*a)[0], 'core:any', ('core:any','core:int','core:int')),
        ('sequence.merge_score', lambda a,c: merge_equal(*a)[1], 'core:int', ('core:any','core:int','core:int')),
        ('vector.add', vector_add, 'core:coord', ('core:coord','core:coord')),
        ('grid.values', grid_values, 'core:any', ('core:string','core:any')),
        ('grid.flood_region', flood, 'core:any', ('core:string','core:coord','core:any','core:any','core:bool','core:bool','core:any')),
    ]
    return {'core:' + name: FunctionSpec('core:' + name, fn, result, args) for name, fn, result, args in definitions}
