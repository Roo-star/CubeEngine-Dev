"""Typed, side-effect-free expression AST for Rule IR v2."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, MutableMapping, Optional, Sequence, Tuple


class ExpressionError(ValueError):
    pass


ExpressionFunction = Callable[[Tuple[Any, ...], "EvaluationContext"], Any]


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    evaluator: ExpressionFunction
    result_type: str = "core:any"
    argument_types: Tuple[str, ...] = ()
    variadic: bool = False


@dataclass
class EvaluationContext:
    references: Mapping[str, Any] = field(default_factory=dict)
    parameters: Mapping[str, Any] = field(default_factory=dict)
    variables: MutableMapping[str, Any] = field(default_factory=dict)
    functions: Mapping[str, FunctionSpec] = field(default_factory=dict)
    runtime: Any = None


class ExpressionEvaluator:
    """Evaluate and type-check the locked Rule IR expression vocabulary."""

    def __init__(self, functions: Optional[Mapping[str, FunctionSpec]] = None) -> None:
        self.functions: Dict[str, FunctionSpec] = dict(functions or {})

    def register(self, spec: FunctionSpec) -> None:
        if not isinstance(spec, FunctionSpec) or ":" not in spec.name:
            raise ExpressionError("expression functions require a namespaced FunctionSpec")
        self.functions[spec.name] = spec

    def evaluate(self, expression: Mapping[str, Any], context: Optional[EvaluationContext] = None) -> Any:
        if not isinstance(expression, Mapping):
            raise ExpressionError("expression must be an AST object")
        context = context or EvaluationContext(functions=self.functions)
        functions = context.functions or self.functions
        operation = expression.get("op")
        if operation == "literal":
            _literal_type(expression.get("value"))
            return deepcopy(expression.get("value"))
        if operation == "ref":
            return self._resolve_reference(str(expression.get("path", "")), context.references)
        if operation == "param":
            return self._lookup(str(expression.get("name", "")), context.parameters, "parameter")
        if operation == "var":
            return self._lookup(str(expression.get("name", "")), context.variables, "variable")
        if operation == "call":
            name = expression.get("function")
            if name not in functions:
                raise ExpressionError("unregistered expression function: {0}".format(name))
            arguments = tuple(self.evaluate(item, context) for item in self._children(expression, "args"))
            self._check_arity(functions[name], len(arguments))
            return functions[name].evaluator(arguments, context)
        if operation in ("list", "vector"):
            return tuple(self.evaluate(item, context) for item in self._children(expression, "items"))
        if operation == "not":
            return not self._boolean(self._single_argument(expression, context), "not")
        if operation == "and":
            for item in self._children(expression, "args"):
                if not self._boolean(self.evaluate(item, context), "and"):
                    return False
            return True
        if operation == "or":
            for item in self._children(expression, "args"):
                if self._boolean(self.evaluate(item, context), "or"):
                    return True
            return False
        if operation in ("eq", "ne", "lt", "lte", "gt", "gte"):
            left, right = self._pair(expression, context)
            return {
                "eq": lambda: left == right, "ne": lambda: left != right,
                "lt": lambda: left < right, "lte": lambda: left <= right,
                "gt": lambda: left > right, "gte": lambda: left >= right,
            }[operation]()
        if operation in ("add", "sub", "mul", "div", "mod", "min", "max"):
            values = [self._number(self.evaluate(item, context), operation) for item in self._children(expression, "args")]
            if not values:
                raise ExpressionError("{0} requires at least one argument".format(operation))
            if operation == "add":
                return sum(values)
            if operation == "sub":
                return values[0] - sum(values[1:])
            if operation == "mul":
                result = 1
                for value in values:
                    result *= value
                return result
            if operation == "div":
                if len(values) != 2 or values[1] == 0 or values[0] % values[1] != 0:
                    raise ExpressionError("integer div requires two values with an exact non-zero divisor")
                return values[0] // values[1]
            if operation == "mod":
                if len(values) != 2 or values[1] == 0:
                    raise ExpressionError("mod requires two values with a non-zero divisor")
                return values[0] % values[1]
            return min(values) if operation == "min" else max(values)
        if operation in ("neg", "abs"):
            value = self._number(self._single_argument(expression, context), operation)
            return -value if operation == "neg" else abs(value)
        if operation == "if":
            condition = self._boolean(self.evaluate(expression.get("condition"), context), "if")
            return self.evaluate(expression.get("then") if condition else expression.get("else"), context)
        if operation == "coalesce":
            for item in self._children(expression, "args"):
                value = self.evaluate(item, context)
                if value is not None:
                    return value
            return None
        if operation == "contains":
            collection, value = self._pair(expression, context)
            return value in collection
        if operation == "count":
            return len(self._single_argument(expression, context))
        if operation in ("all", "any"):
            values = self._single_argument(expression, context)
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise ExpressionError("{0} requires a sequence".format(operation))
            booleans = [self._boolean(value, operation) for value in values]
            return all(booleans) if operation == "all" else any(booleans)
        raise ExpressionError("unsupported expression operation: {0}".format(operation))

    def infer_type(self, expression: Mapping[str, Any], environment: Optional[Mapping[str, str]] = None) -> str:
        if not isinstance(expression, Mapping):
            raise ExpressionError("expression must be an AST object")
        environment = environment or {}
        operation = expression.get("op")
        if operation == "literal":
            return _literal_type(expression.get("value"))
        if operation == "ref":
            return self._type_lookup(str(expression.get("path", "")), environment, "reference")
        if operation in ("param", "var"):
            return self._type_lookup(str(expression.get("name", "")), environment, str(operation))
        if operation == "call":
            name = expression.get("function")
            if name not in self.functions:
                raise ExpressionError("unregistered expression function: {0}".format(name))
            arguments = self._children(expression, "args")
            spec = self.functions[name]
            self._check_arity(spec, len(arguments))
            for index, item in enumerate(arguments):
                actual = self.infer_type(item, environment)
                expected = spec.argument_types[min(index, len(spec.argument_types) - 1)] if spec.argument_types else "core:any"
                if expected != "core:any" and actual != "core:any" and actual != expected:
                    raise ExpressionError("{0} argument {1} expects {2}, got {3}".format(name, index, expected, actual))
            return spec.result_type
        if operation == "list":
            for item in self._children(expression, "items"):
                self.infer_type(item, environment)
            return "core:any"
        if operation == "vector":
            for item in self._children(expression, "items"):
                if self.infer_type(item, environment) != "core:int":
                    raise ExpressionError("vector items must be core:int")
            return "core:coord"
        if operation in ("not", "and", "or", "eq", "ne", "lt", "lte", "gt", "gte", "contains", "all", "any"):
            self._infer_children(expression, environment)
            return "core:bool"
        if operation in ("add", "sub", "mul", "div", "mod", "min", "max", "neg", "abs", "count"):
            types = self._infer_children(expression, environment)
            if operation != "count" and any(item not in ("core:int", "core:fixed", "core:any") for item in types):
                raise ExpressionError("{0} requires numeric operands".format(operation))
            return "core:int" if all(item != "core:fixed" for item in types) else "core:fixed"
        if operation == "if":
            condition_type = self.infer_type(expression.get("condition"), environment)
            if condition_type not in ("core:bool", "core:any"):
                raise ExpressionError("if condition must be core:bool")
            left = self.infer_type(expression.get("then"), environment)
            right = self.infer_type(expression.get("else"), environment)
            return left if left == right else "core:any"
        if operation == "coalesce":
            types = self._infer_children(expression, environment)
            return types[0] if types and all(item == types[0] for item in types) else "core:any"
        raise ExpressionError("unsupported expression operation: {0}".format(operation))

    def _infer_children(self, expression: Mapping[str, Any], environment: Mapping[str, str]) -> Tuple[str, ...]:
        if "args" in expression:
            children = self._children(expression, "args")
        elif "items" in expression:
            children = self._children(expression, "items")
        elif "value" in expression:
            children = (expression["value"],)
        else:
            children = ()
        return tuple(self.infer_type(item, environment) for item in children)

    def _single_argument(self, expression: Mapping[str, Any], context: EvaluationContext) -> Any:
        children = self._children(expression, "args")
        if len(children) != 1:
            raise ExpressionError("{0} requires exactly one argument".format(expression.get("op")))
        return self.evaluate(children[0], context)

    def _pair(self, expression: Mapping[str, Any], context: EvaluationContext) -> Tuple[Any, Any]:
        children = self._children(expression, "args")
        if len(children) != 2:
            raise ExpressionError("{0} requires exactly two arguments".format(expression.get("op")))
        return self.evaluate(children[0], context), self.evaluate(children[1], context)

    @staticmethod
    def _children(expression: Mapping[str, Any], name: str) -> Tuple[Mapping[str, Any], ...]:
        value = expression.get(name)
        if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
            raise ExpressionError("expression {0} must be an array of AST objects".format(name))
        return tuple(value)

    @staticmethod
    def _lookup(name: str, values: Mapping[str, Any], kind: str) -> Any:
        if name not in values:
            raise ExpressionError("unknown {0}: {1}".format(kind, name))
        return values[name]

    @staticmethod
    def _type_lookup(name: str, values: Mapping[str, str], kind: str) -> str:
        if name not in values:
            raise ExpressionError("unknown {0} type: {1}".format(kind, name))
        return values[name]

    @staticmethod
    def _resolve_reference(path: str, references: Mapping[str, Any]) -> Any:
        if path in references:
            return references[path]
        current: Any = references
        for token in path.split("."):
            if not isinstance(current, Mapping) or token not in current:
                raise ExpressionError("unknown reference: {0}".format(path))
            current = current[token]
        return current

    @staticmethod
    def _check_arity(spec: FunctionSpec, count: int) -> None:
        minimum = len(spec.argument_types)
        if (not spec.variadic and count != minimum) or (spec.variadic and count < minimum):
            relation = "at least" if spec.variadic else "exactly"
            raise ExpressionError("{0} requires {1} {2} argument(s)".format(spec.name, relation, minimum))

    @staticmethod
    def _boolean(value: Any, operation: str) -> bool:
        if not isinstance(value, bool):
            raise ExpressionError("{0} requires boolean values".format(operation))
        return value

    @staticmethod
    def _number(value: Any, operation: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ExpressionError("{0} requires integer/fixed-point values".format(operation))
        return value


def _literal_type(value: Any) -> str:
    if isinstance(value, bool):
        return "core:bool"
    if isinstance(value, int):
        return "core:int"
    if isinstance(value, float):
        raise ExpressionError("binary floating-point literals are not allowed in Rule IR")
    if isinstance(value, str):
        if value.startswith("rule:participant."):
            return "core:participant_id"
        if value.startswith("rule:action."):
            return "core:action_id"
        if value.startswith("entity:"):
            return "core:entity_id"
        return "core:string"
    if value is None or isinstance(value, (list, tuple, dict)):
        return "core:any"
    raise ExpressionError("unsupported Rule IR literal type: {0}".format(type(value).__name__))
