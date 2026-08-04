"""CubeEngine Spatial Rule Transformation Protocol."""

from .parser import RuleFileParser, parse_rule_file
from .report import Diagnostic, ParseReport, SourceEvidence
from .schema import RULE_SCHEMA_VERSION, classify_schema, normalize_rule_schema, validate_rule_schema
from .source_game import SourceGamePackage
from .source_importer import SourceGameImporter

__all__ = [
    "Diagnostic",
    "ParseReport",
    "RULE_SCHEMA_VERSION",
    "RuleFileParser",
    "SourceEvidence",
    "SourceGameImporter",
    "SourceGamePackage",
    "classify_schema",
    "normalize_rule_schema",
    "parse_rule_file",
    "validate_rule_schema",
]
