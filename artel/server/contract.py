from typing import Any

TYPE_OBJECT = "object"
TYPE_ARRAY = "array"
TYPE_STRING = "string"
TYPE_NUMBER = "number"
TYPE_INTEGER = "integer"
TYPE_BOOLEAN = "boolean"

KEY_TYPE = "type"
KEY_REQUIRED = "required"
KEY_PROPERTIES = "properties"
KEY_ITEMS = "items"
KEY_ENUM = "enum"
KEY_MIN_ITEMS = "minItems"
KEY_MIN_LENGTH = "minLength"

SUPPORTED_TYPES = (
    TYPE_OBJECT,
    TYPE_ARRAY,
    TYPE_STRING,
    TYPE_NUMBER,
    TYPE_INTEGER,
    TYPE_BOOLEAN,
)

SUPPORTED_KEYS = (
    KEY_TYPE,
    KEY_REQUIRED,
    KEY_PROPERTIES,
    KEY_ITEMS,
    KEY_ENUM,
    KEY_MIN_ITEMS,
    KEY_MIN_LENGTH,
)

_ROOT = "output"


def _matches(expected: str, value: Any) -> bool:
    if expected == TYPE_OBJECT:
        return isinstance(value, dict)
    if expected == TYPE_ARRAY:
        return isinstance(value, list)
    if expected == TYPE_STRING:
        return isinstance(value, str)
    if expected == TYPE_BOOLEAN:
        return isinstance(value, bool)
    if expected == TYPE_INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == TYPE_NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def validate_contract(contract: Any, path: str = _ROOT) -> list[str]:
    errors: list[str] = []
    if not isinstance(contract, dict):
        return [f"{path}: contract must be an object"]
    for key in contract:
        if key not in SUPPORTED_KEYS:
            errors.append(f"{path}: unsupported contract keyword {key!r}")
    expected = contract.get(KEY_TYPE)
    if expected is not None and expected not in SUPPORTED_TYPES:
        errors.append(f"{path}: unsupported type {expected!r}")
    required = contract.get(KEY_REQUIRED)
    if required is not None and not (
        isinstance(required, list) and all(isinstance(r, str) for r in required)
    ):
        errors.append(f"{path}: required must be a list of property names")
    properties = contract.get(KEY_PROPERTIES)
    if properties is not None:
        if not isinstance(properties, dict):
            errors.append(f"{path}: properties must be an object")
        else:
            for name, sub in properties.items():
                errors.extend(validate_contract(sub, f"{path}.{name}"))
    items = contract.get(KEY_ITEMS)
    if items is not None:
        errors.extend(validate_contract(items, f"{path}[]"))
    enum = contract.get(KEY_ENUM)
    if enum is not None and not isinstance(enum, list):
        errors.append(f"{path}: enum must be a list")
    for key in (KEY_MIN_ITEMS, KEY_MIN_LENGTH):
        bound = contract.get(key)
        if bound is not None and not (isinstance(bound, int) and not isinstance(bound, bool)):
            errors.append(f"{path}: {key} must be an integer")
    return errors


def validate_payload(contract: dict, payload: Any, path: str = _ROOT) -> list[str]:
    errors: list[str] = []
    expected = contract.get(KEY_TYPE)
    if expected is not None and not _matches(expected, payload):
        return [f"{path}: expected {expected}, got {type(payload).__name__}"]
    enum = contract.get(KEY_ENUM)
    if isinstance(enum, list) and payload not in enum:
        errors.append(f"{path}: must be one of {enum!r}")
    min_length = contract.get(KEY_MIN_LENGTH)
    if isinstance(min_length, int) and isinstance(payload, str) and len(payload) < min_length:
        errors.append(f"{path}: shorter than minLength {min_length}")
    if isinstance(payload, dict):
        for name in contract.get(KEY_REQUIRED) or []:
            if name not in payload:
                errors.append(f"{path}.{name}: required property missing")
        properties = contract.get(KEY_PROPERTIES) or {}
        for name, sub in properties.items():
            if name in payload:
                errors.extend(validate_payload(sub, payload[name], f"{path}.{name}"))
    if isinstance(payload, list):
        min_items = contract.get(KEY_MIN_ITEMS)
        if isinstance(min_items, int) and len(payload) < min_items:
            errors.append(f"{path}: has {len(payload)} items, minItems is {min_items}")
        items = contract.get(KEY_ITEMS)
        if isinstance(items, dict):
            for index, value in enumerate(payload):
                errors.extend(validate_payload(items, value, f"{path}[{index}]"))
    return errors
