import json
from unittest.mock import ANY

import jsonschema.validators
import pytest

from schemathesis.generation import GenerationMode
from schemathesis.generation.coverage import (
    CoverageContext,
    GeneratedValue,
    _positive_number,
    _positive_string,
    cover_schema_iter,
)
from schemathesis.specs.openapi.formats import get_default_format_strategies

PATTERN = "^\\d+$"


def cover_schema(ctx: CoverageContext, schema: dict) -> list:
    return [value.value for value in cover_schema_iter(ctx, schema)]


def assert_unique(values: list):
    seen = set()
    for value in values:
        if isinstance(value, GeneratedValue):
            value = value.value
        if isinstance(value, (dict, list)):
            serialized = json.dumps(value, sort_keys=True)
            key = (type(value), serialized)
        else:
            key = (type(value), value)
        assert key not in seen
        seen.add(key)


def assert_conform(values: list, schema: dict):
    for value in values:
        if isinstance(value, GeneratedValue):
            value = value.value
        jsonschema.validate(
            value,
            schema,
            cls=jsonschema.Draft7Validator,
            format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
        )


def assert_not_conform(values: list, schema: dict):
    for entry in values:
        if schema.get("format") == "unknown":
            # Can't validate the format
            continue
        try:
            jsonschema.validate(
                entry,
                schema,
                cls=jsonschema.Draft7Validator,
                format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
            )
            raise ValueError(f"Value {entry} conforms to {schema}")
        except (jsonschema.ValidationError, ValueError):
            pass


@pytest.fixture
def ctx():
    return CoverageContext(
        location="query",
        is_required=True,
        custom_formats=get_default_format_strategies(),
        validator_cls=jsonschema.validators.Draft202012Validator,
    )


@pytest.fixture
def pctx():
    return CoverageContext(
        location="query",
        generation_modes=[GenerationMode.POSITIVE],
        is_required=True,
        custom_formats=get_default_format_strategies(),
        validator_cls=jsonschema.validators.Draft202012Validator,
    )


@pytest.fixture
def nctx():
    return CoverageContext(
        location="query",
        generation_modes=[GenerationMode.NEGATIVE],
        is_required=True,
        custom_formats=get_default_format_strategies(),
        validator_cls=jsonschema.validators.Draft202012Validator,
    )


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "null"}, [None]),
        ({"type": "boolean"}, [True, False]),
        ({"type": ["boolean", "null"]}, [True, False, None]),
        ({"enum": [1, 2]}, [1, 2]),
        ({"const": 42}, [42]),
    ],
)
def test_positive_primitive_schemas(pctx, schema, expected):
    covered = cover_schema(pctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_conform(covered, schema)


class AnyString:
    def __eq__(self, value: object, /) -> bool:
        return isinstance(value, str)


class AnyNumber:
    def __eq__(self, value: object, /) -> bool:
        return not isinstance(value, bool) and isinstance(value, (int, float))


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "null"}, [0, "false", "", ["null", "null"]]),
        ({"type": "boolean"}, [0, "null", "", ["null", "null"]]),
        ({"type": ["boolean", "null"]}, [0, "", ["null", "null"]]),
        ({"enum": [1, 2]}, ["AAA"]),
        ({"enum": [1, 2, {}]}, ["AAA"]),
        ({"const": 42}, ["AAA"]),
        ({"multipleOf": 2}, lambda x: x % 2 != 0),
        ({"format": "date-time"}, [AnyString()]),
        ({"format": "hostname"}, [AnyString()]),
        ({"format": "unknown"}, [AnyString()]),
        ({"uniqueItems": True}, [["null", "null"]]),
        ({"maximum": 5}, [6]),
        ({"minimum": 5}, [4]),
        ({"exclusiveMinimum": 5}, [5]),
        ({"exclusiveMaximum": 5}, [5]),
        ({"required": ["a"]}, [{}]),
    ],
)
def test_negative_primitive_schemas(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    if callable(expected):
        assert len(covered) == 1
        assert expected(covered[0])
    else:
        assert covered == expected
    assert_unique(covered)
    assert_not_conform(covered, schema)


@pytest.mark.parametrize(
    ("schema", "lengths"),
    [
        ({"type": "string"}, {0}),
        ({"type": "string", "example": "test"}, {4}),
        ({"type": "string", "example": "test", "default": "test"}, {4}),
        ({"type": "string", "example": "test", "default": "another"}, {4, 7}),
        ({"type": "string", "default": "test"}, {4}),
        ({"type": "string", "examples": ["A", "BB"]}, {1, 2}),
        ({"type": "string", "minLength": 0}, {0}),
        ({"type": "string", "pattern": "^[\\w\\W]+$"}, {1}),
        ({"type": "string", "minLength": 5}, {5, 6}),
        ({"type": "string", "maxLength": 10}, {9, 10}),
        ({"type": "string", "minLength": 5, "maxLength": 10}, {5, 6, 9, 10}),
        ({"type": "string", "minLength": 5, "maxLength": 6}, {5, 6}),
        ({"type": "string", "minLength": 5, "maxLength": 5}, {5}),
        ({"type": "string", "minLength": 0, "maxLength": 512, "pattern": r"^[\w\W]+$"}, {1}),
    ],
)
def test_positive_string(ctx, schema, lengths):
    covered = list(_positive_string(ctx, schema))
    assert_unique(covered)
    for length in lengths:
        assert len([x for x in covered if len(x.value) == length]) == 1
    assert_conform(covered, schema)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        # Too permissing - all values will be stringified anyway
        ({"type": "string"}, []),
        ({"type": "string", "minLength": 5}, [0, "true", "null", "0000"]),
        ({"type": "string", "maxLength": 10}, [ANY, ANY, "00000000000"]),
        (
            {"type": "string", "minLength": 5, "maxLength": 10},
            [ANY, "true", "null", ["null", "null"], "0000", "00000000000"],
        ),
        (
            {"type": "string", "pattern": "^[0-9]", "minLength": 1},
            [ANY, ANY, "false", "null", ["null", "null"], AnyString(), ""],
        ),
        ({"type": "string", "pattern": "^[0-9]"}, [ANY, ANY, "false", "null", ["null", "null"], AnyString()]),
        ({"type": "string", "format": "date-time"}, [0, "false", "null", ["null", "null"], ""]),
    ],
)
def test_negative_string(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_not_conform(covered, schema)


def test_negative_string_with_pattern(nctx):
    schema = {
        "type": "string",
        "minLength": 5,
        "maxLength": 8,
        "pattern": r"^[\da-z]+$",
    }
    covered = cover_schema(nctx, schema)
    assert covered in [
        [0, "false", "null", ["null", "null"], "0000", "000000000", AnyString()],
        [0, "true", "null", ["null", "null"], "0000", "000000000", AnyString()],
    ]
    assert_unique(covered)
    assert_not_conform(covered, schema)


@pytest.mark.parametrize("multiple_of", [None, 2])
@pytest.mark.parametrize(
    ("schema", "values", "with_multiple_of"),
    [
        ({"type": "integer"}, [0], [0]),
        ({"type": "integer", "example": 2}, [2], [2]),
        ({"type": "integer", "example": 2, "default": 2}, [2], [2]),
        ({"type": "integer", "example": 2, "default": 4}, [2, 4], [2, 4]),
        ({"type": "integer", "default": 2}, [2], [2]),
        ({"type": "integer", "examples": [42, 44]}, [42, 44], [42, 44]),
        ({"type": "number"}, [0], [0]),
        ({"type": "integer", "minimum": 5}, [5, 6], [6, 8]),
        ({"type": "number", "minimum": 5.5}, [5.5, 6.5], [6, 8]),
        ({"type": "integer", "maximum": 10}, [10, 9], [10, 8]),
        ({"type": "number", "maximum": 11.5}, [11.5, 10.5], [10, 8]),
        ({"type": "integer", "minimum": 5, "maximum": 10}, [5, 6, 10, 9], [6, 8, 10]),
        ({"type": "integer", "minimum": 5, "maximum": 6}, [5, 6], [6]),
        ({"type": "integer", "minimum": 5, "maximum": 5}, [5], None),
        ({"type": "integer", "exclusiveMinimum": 5}, [6, 7], [6, 8]),
        ({"type": "integer", "exclusiveMaximum": 10}, [9, 8], [8, 6]),
        ({"type": "integer", "exclusiveMinimum": 5, "exclusiveMaximum": 10}, [6, 7, 9, 8], [6, 8]),
    ],
)
def test_positive_number(ctx, schema, multiple_of, values, with_multiple_of):
    if with_multiple_of is None and multiple_of is not None:
        pytest.skip("This test is not applicable for multiple_of=None")
    if multiple_of is not None:
        schema["multipleOf"] = multiple_of
        values = with_multiple_of
    covered = [value.value for value in _positive_number(ctx, schema)]
    assert_unique(covered)
    assert covered == values
    assert_conform(covered, schema)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "object"}, [{}]),
        ({"type": "object", "example": {"A": 42}}, [{"A": 42}]),
        ({"type": "object", "example": {"A": 42}, "default": {"A": 42}}, [{"A": 42}]),
        ({"type": "object", "example": {"A": 42}, "default": {"A": 43}}, [{"A": 42}, {"A": 43}]),
        ({"type": "object", "default": {"A": 42}}, [{"A": 42}]),
        ({"type": "object", "examples": [{"A": 42}, {"B": 43}]}, [{"A": 42}, {"B": 43}]),
        (
            {
                "type": "object",
                "properties": {"foo": True},
                "required": ["foo"],
            },
            [
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
                {"foo": ANY},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {"foo": {"type": "integer", "example": 42}},
                "required": ["foo"],
            },
            [
                {"foo": 42},
            ],
        ),
        (
            {
                # No `type`
                "properties": {"foo": {"type": "integer", "example": 42}},
                "required": ["foo"],
            },
            [
                {"foo": 42},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {"foo": {"type": "integer", "default": 42}},
                "required": ["foo"],
            },
            [
                {"foo": 42},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {"foo": {"type": "integer", "examples": [42, 43]}},
                "required": ["foo"],
            },
            [
                {"foo": 42},
                {"foo": 43},
            ],
        ),
        (
            {
                "type": "object",
                "required": ["foo"],
                "properties": {
                    "foo": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["bar"],
                            "properties": {
                                "bar": {
                                    "allOf": [
                                        {
                                            "type": "string",
                                        },
                                        {
                                            "minLength": 1,
                                            "maxLength": 100,
                                        },
                                    ]
                                },
                            },
                        },
                    }
                },
            },
            [
                {"foo": []},
                {
                    "foo": [
                        {
                            "bar": "0",
                        },
                    ],
                },
                {
                    "foo": [
                        {
                            "bar": "00",
                        },
                    ],
                },
                {
                    "foo": [
                        {
                            "bar": "0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
                        },
                    ],
                },
                {
                    "foo": [
                        {
                            "bar": "000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
                        },
                    ],
                },
            ],
        ),
        (
            {
                "type": "object",
                "required": ["foo"],
                "properties": {
                    "foo": {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
            },
            [
                {"foo": []},
                {
                    "foo": [
                        "",
                    ],
                },
            ],
        ),
        (
            {
                "type": "object",
                "properties": {"foo": {"type": "integer"}, "bar": {"type": "string"}},
                "required": ["foo"],
            },
            [
                {"bar": "", "foo": 0},
                {"foo": 0},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {
                    "foo-1": {
                        "type": "object",
                        "properties": {"foo-2": {"type": "integer"}},
                    }
                },
                "required": ["foo-1"],
            },
            [
                {"foo-1": {"foo-2": 0}},
                {"foo-1": {}},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {
                    "foo-1": {
                        "type": "object",
                        "properties": {
                            "foo-2": {
                                "type": "object",
                                "properties": {
                                    "foo-3": {"type": "integer"},
                                },
                            },
                        },
                    }
                },
                "required": ["foo-1"],
            },
            [
                {"foo-1": {"foo-2": {"foo-3": 0}}},
                {"foo-1": {}},
                {"foo-1": {"foo-2": {}}},
            ],
        ),
        (
            {
                "type": "object",
                "properties": {
                    "foo-1": {"type": "integer", "minimum": 2},
                    "foo-2": {"type": "string", "minLength": 2},
                },
                "required": ["foo-1"],
            },
            [
                {"foo-1": 2, "foo-2": "00"},
                {"foo-1": 2},
                {"foo-1": 3, "foo-2": "00"},
                {"foo-1": 2, "foo-2": "000"},
            ],
        ),
        # 3 properties, 2 required
        (
            {
                "type": "object",
                "properties": {
                    "req1": {"type": "string"},
                    "req2": {"type": "integer"},
                    "opt1": {"type": "string"},
                },
                "required": ["req1", "req2"],
            },
            [
                {"req1": "", "req2": 0, "opt1": ""},
                {"req1": "", "req2": 0},
            ],
        ),
        # 6 properties, 2 required
        (
            {
                "type": "object",
                "properties": {
                    "req1": {"type": "string"},
                    "req2": {"type": "integer"},
                    "opt1": {"type": "string"},
                    "opt2": {"type": "number"},
                    "opt3": {"type": "array"},
                    "opt4": {"type": "boolean"},
                },
                "required": ["req1", "req2"],
            },
            [
                {"req1": "", "req2": 0, "opt1": "", "opt2": 0.0, "opt3": [None, None], "opt4": False},
                {"req1": "", "req2": 0, "opt1": ""},
                {"req1": "", "req2": 0, "opt2": 0.0},
                {"req1": "", "req2": 0, "opt3": [None, None]},
                {"req1": "", "req2": 0, "opt4": False},
                {"req1": "", "req2": 0, "opt1": "", "opt2": 0.0},
                {"req1": "", "req2": 0, "opt1": "", "opt2": 0.0, "opt3": [None, None]},
                {"req1": "", "req2": 0},
                {"opt1": "", "opt2": 0.0, "opt3": [None, None], "opt4": True, "req1": "", "req2": 0},
            ],
        ),
        # Nested object with optional properties
        (
            {
                "type": "object",
                "properties": {
                    "req1": {"type": "string"},
                    "opt1": {
                        "type": "object",
                        "properties": {
                            "nested_req": {"type": "integer"},
                            "nested_opt": {"type": "boolean"},
                        },
                        "required": ["nested_req"],
                    },
                },
                "required": ["req1"],
            },
            [
                {"req1": "", "opt1": {"nested_req": 0, "nested_opt": False}},
                {"req1": ""},
                {"req1": "", "opt1": {"nested_req": 0}},
                {"req1": "", "opt1": {"nested_req": 0, "nested_opt": True}},
            ],
        ),
        # Object with all optional properties
        (
            {
                "type": "object",
                "properties": {
                    "opt1": {"type": "string"},
                    "opt2": {"type": "integer"},
                    "opt3": {"type": "boolean"},
                },
            },
            [
                {"opt1": "", "opt2": 0, "opt3": False},
                {"opt1": ""},
                {"opt2": 0},
                {"opt3": False},
                {"opt1": "", "opt2": 0},
                {},
                {"opt1": "", "opt2": 0, "opt3": True},
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "maxItems": 5},
            [
                [],
                [0, 0, 0, 0, 0],
                [0, 0, 0, 0],
            ],
        ),
        (
            {"type": "array", "items": {"enum": ["FOO"]}},
            [[], ["FOO"]],
        ),
        (
            {"type": "array", "items": {"enum": ["FOO"]}, "minItems": 1},
            [
                ["FOO"],
                ["FOO", "FOO"],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "example": [1, 2, 3]},
            [
                [1, 2, 3],
                [0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "example": [1, 2, 3], "default": [1, 2, 3]},
            [
                [1, 2, 3],
                [0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "example": [1, 2, 3], "default": [4, 5, 6]},
            [
                [1, 2, 3],
                [4, 5, 6],
                [0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "default": [1, 2, 3]},
            [
                [1, 2, 3],
                [0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "examples": [[1, 2, 3], [4, 5, 6]]},
            [
                [1, 2, 3],
                [4, 5, 6],
                [0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "minItems": 2},
            [
                [0, 0],
                [0, 0, 0],
            ],
        ),
        (
            {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 5},
            [
                [0, 0],
                [0, 0, 0],
                [0, 0, 0, 0, 0],
                [0, 0, 0, 0],
            ],
        ),
        (
            {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"foo": {"type": "integer", "minimum": 5}},
                    "required": ["foo"],
                },
                "minItems": 1,
                "maxItems": 2,
            },
            [
                [{"foo": 5}],
                [{"foo": 5}, {"foo": 5}],
            ],
        ),
        (
            {"type": "array", "items": [{"type": "integer"}, {"type": "string"}], "minItems": 2, "maxItems": 5},
            [
                [0, ""],
                [0, "", None],
                [0, "", None, None, None],
                [0, "", None, None],
            ],
        ),
        # Single anyOf subschema
        ({"anyOf": [{"type": "integer"}]}, [0]),
        ({"anyOf": [{"type": "boolean"}]}, [True, False]),
        # Multiple anyOf subschemas
        ({"anyOf": [{"type": "integer", "minimum": 2}, {"type": "boolean"}]}, [2, 3, True, False]),
        ({"anyOf": [{"type": "integer"}, {"type": "string"}]}, [0, ""]),
        ({"anyOf": [{"type": "boolean"}, {"type": "string"}]}, [True, False, ""]),
        # Nested anyOf
        (
            {
                "anyOf": [
                    {"type": "integer", "minimum": 2},
                    {
                        "anyOf": [
                            {"type": "boolean"},
                            {"type": "string"},
                        ]
                    },
                ]
            },
            [2, 3, True, False, ""],
        ),
        (
            {
                "anyOf": [
                    {"type": "integer", "minimum": 2},
                    {
                        "anyOf": [
                            {"type": "boolean"},
                            {"type": "string"},
                            {"type": "null"},
                        ]
                    },
                ]
            },
            [2, 3, True, False, "", None],
        ),
        # anyOf with other keywords
        (
            {
                "anyOf": [
                    {"type": "integer", "minimum": 5},
                    {"type": "integer", "maximum": 10},
                ]
            },
            [5, 6, 10, 9],
        ),
        # Single allOf subschema
        ({"allOf": [{"type": "integer"}]}, [0]),
        ({"allOf": [{"type": "boolean"}]}, [True, False]),
        # Multiple allOf subschemas
        (
            {
                "allOf": [
                    {"type": "integer"},
                    {"minimum": 5},
                ]
            },
            [5, 6],
        ),
        (
            {
                "allOf": [
                    {"type": "string"},
                    {"minLength": 3},
                ]
            },
            ["000", "0000"],
        ),
        (
            {
                "allOf": [
                    {"type": "integer"},
                    {"minimum": 5},
                    {"maximum": 10},
                ]
            },
            [5, 6, 10, 9],
        ),
        # Nested allOf
        (
            {
                "allOf": [
                    {"type": "integer"},
                    {
                        "allOf": [
                            {"minimum": 5},
                            {"maximum": 10},
                        ]
                    },
                ]
            },
            [5, 6, 10, 9],
        ),
        (
            {
                "allOf": [
                    {"type": "string"},
                    {
                        "allOf": [
                            {"minLength": 3},
                            {"maxLength": 5},
                        ]
                    },
                ]
            },
            ["000", "0000", "00000"],
        ),
        # allOf with other keywords
        (
            {
                "allOf": [
                    {"type": "integer"},
                    {"minimum": 5},
                    {"maximum": 10},
                ],
                "exclusiveMinimum": 5,
            },
            [6, 7, 10, 9],
        ),
        # Unsatisfiable allOf
        (
            {
                "allOf": [
                    {"type": "string", "pattern": "^\\p{Alnum}$"},
                    {"maxLength": 160},
                ]
            },
            [],
        ),
        (
            {
                "allOf": [
                    {"type": "string", "pattern": 0.0},
                    {"maxLength": 160},
                ]
            },
            [],
        ),
    ],
)
def test_positive_other(pctx, schema, expected):
    covered = cover_schema(pctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_conform(covered, schema)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            # These are query parameters and strings are not possible to negate
            {
                "properties": {
                    "foo": {"type": "string"},
                    "bar": {"type": "string"},
                },
                "required": ["foo", "bar"],
            },
            [
                {
                    "bar": "",
                },
                {
                    "foo": "",
                },
            ],
        ),
        (
            {
                "properties": {
                    "foo": {"type": "string", "maxLength": 3},
                    "bar": {"type": "string", "maxLength": 3},
                },
                "required": ["foo", "bar"],
            },
            [
                {
                    "foo": AnyNumber(),
                    "bar": "",
                },
                {
                    "foo": AnyNumber(),
                    "bar": "",
                },
                {
                    "bar": "",
                    "foo": "false",
                },
                {
                    "bar": "",
                    "foo": "null",
                },
                {
                    "bar": "",
                    "foo": ["null", "null"],
                },
                {
                    "bar": "",
                    "foo": "0000",
                },
                {
                    "bar": AnyNumber(),
                    "foo": "",
                },
                {
                    "bar": AnyNumber(),
                    "foo": "",
                },
                {
                    "bar": "false",
                    "foo": "",
                },
                {
                    "bar": "null",
                    "foo": "",
                },
                {
                    "bar": ["null", "null"],
                    "foo": "",
                },
                {
                    "bar": "0000",
                    "foo": "",
                },
                {
                    "bar": "",
                },
                {
                    "foo": "",
                },
            ],
        ),
        (
            {
                "properties": {
                    "foo": {"type": "string", "maxLength": 3},
                    "bar": {"type": "string", "maxLength": 3},
                },
            },
            [
                {
                    "bar": "",
                    "foo": AnyNumber(),
                },
                {
                    "bar": "",
                    "foo": AnyNumber(),
                },
                {
                    "bar": "",
                    "foo": "false",
                },
                {
                    "bar": "",
                    "foo": "null",
                },
                {
                    "bar": "",
                    "foo": ["null", "null"],
                },
                {
                    "bar": "",
                    "foo": "0000",
                },
                {
                    "bar": AnyNumber(),
                    "foo": "",
                },
                {
                    "bar": AnyNumber(),
                    "foo": "",
                },
                {
                    "bar": "false",
                    "foo": "",
                },
                {
                    "bar": "null",
                    "foo": "",
                },
                {
                    "bar": ["null", "null"],
                    "foo": "",
                },
                {
                    "bar": "0000",
                    "foo": "",
                },
            ],
        ),
        (
            {
                "properties": {
                    "foo": {"type": "string", "maxLength": 3},
                },
                "additionalProperties": False,
            },
            [
                {
                    "foo": AnyNumber(),
                },
                {
                    "foo": AnyNumber(),
                },
                {
                    "foo": "false",
                },
                {
                    "foo": "null",
                },
                {
                    "foo": ["null", "null"],
                },
                {
                    "foo": "0000",
                },
                {
                    "foo": "",
                    "x-schemathesis-unknown-property": 42,
                },
            ],
        ),
    ],
)
def test_negative_objects(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_not_conform(covered, schema)


SCHEMA_WITH_PATTERN = {"minLength": 2, "pattern": "^A{2}$"}


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        # Top-level pattern
        (SCHEMA_WITH_PATTERN, ["A", "00"]),
        # Pattern inside properties
        ({"properties": {"username": SCHEMA_WITH_PATTERN}}, [{"username": "A"}, {"username": "00"}]),
        # Pattern inside items
        ({"items": SCHEMA_WITH_PATTERN}, [["A"], ["00"]]),
        # Pattern inside nested properties
        (
            {
                "properties": {"user": {"properties": {"id": SCHEMA_WITH_PATTERN}}},
            },
            [{"user": {"id": "A"}}, {"user": {"id": "00"}}],
        ),
        # Pattern inside items of an array property
        (
            {
                "properties": {"tags": {"items": SCHEMA_WITH_PATTERN}},
            },
            [{"tags": ["A"]}, {"tags": ["00"]}],
        ),
        # Multiple patterns in different locations
        (
            {
                "properties": {
                    "id": SCHEMA_WITH_PATTERN,
                    "items": {"items": SCHEMA_WITH_PATTERN},
                },
                "patternProperties": {"^meta_": SCHEMA_WITH_PATTERN},
            },
            [
                {"id": "A", "items": None},
                {"id": "00", "items": None},
                {"id": None, "items": ["A"]},
                {"id": None, "items": ["00"]},
                {"id": None, "items": None, "meta_": "A"},
                {"id": None, "items": None, "meta_": "00"},
            ],
        ),
        # Pattern in combination with other keywords
        ({"pattern": "^A{2}$", "minLength": 3, "maxLength": 20}, ["000", "AA", "AA0000000000000000000"]),
        # Pattern inside allOf
        ({"allOf": [SCHEMA_WITH_PATTERN, {"minLength": 5}]}, ["AA", "00000"]),
    ],
)
def test_negative_pattern(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    assert covered == expected
    assert_unique(covered)
    assert_not_conform(covered, schema)


def test_positive_pattern(pctx):
    schema = {"pattern": r"^[a-zA-Z0-9]{2,4}-\d{4,15}$", "minLength": 7, "maxLength": 20, "type": "string"}
    covered = cover_schema(pctx, schema)
    assert covered == ["0000-0000", "00-0000", "00-00000", "0000-000000000000000", "000-000000000000000"]
    assert_unique(covered)
    assert_not_conform(covered, schema)


def test_negative_pattern_with_incompatible_length(nctx):
    schema = {
        "minLength": 6,
        "maxLength": 20,
        "pattern": "^[a-zA-Z]{4}-\\d{4,15}$",
    }
    covered = cover_schema(nctx, schema)
    assert covered == ["AAAA-", "AAAA-0000000000000000", "000000"]
    assert_unique(covered)
    assert_not_conform(covered, schema)


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        (
            {
                "allOf": [
                    {"minimum": 5},
                ],
            },
            [4],
        ),
        (
            {
                "allOf": [
                    {"type": "integer"},
                    {"minimum": 5},
                ],
            },
            [4, AnyNumber(), "false", "null", "", ["null", "null"]],
        ),
        (
            {
                "anyOf": [
                    {"minimum": 5},
                    {"type": "string"},
                ],
            },
            [4],
        ),
        (
            {
                "anyOf": [
                    {"minimum": 5},
                    {"type": "string", "maxLength": 5},
                ],
            },
            [4, AnyNumber(), AnyNumber(), "000000"],
        ),
        (
            {
                "allOf": [
                    {
                        "maxLength": 10,
                        "type": "string",
                    },
                    {
                        "anyOf": [
                            {"maxLength": 10},
                            {"type": "null"},
                        ]
                    },
                ]
            },
            [ANY, "00000000000", AnyNumber(), AnyNumber()],
        ),
        (
            {
                "allOf": [
                    {"type": "string", "pattern": 0.0},
                    {"maxLength": 160},
                ]
            },
            [],
        ),
    ],
)
def test_negative_combinators(nctx, schema, expected):
    covered = cover_schema(nctx, schema)
    for exp in expected if isinstance(expected, tuple) else (expected,):
        if covered == exp:
            assert_unique(covered)
            assert_not_conform(covered, schema)
            break
    else:
        pytest.fail("Expected value didn't match")


@pytest.mark.parametrize(
    "pattern",
    [
        "^[A-Za-z0-9]$|^[A-Za-z0-9][\\w-\\.]*[A-Za-z0-9]$",
        "^[-._\\p{L}\\p{N}]+$",
    ],
)
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_unsupported_patterns(nctx, pattern):
    covered = cover_schema(nctx, {"type": "string", "pattern": pattern})
    assert covered == []
    assert not cover_schema(nctx, {"patternProperties": {pattern: {"type": "string"}}})


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "integer", "format": "int32"}, [0]),
        ({"type": "string", "format": "unknown"}, [""]),
    ],
)
def test_ignoring_unknown_formats(pctx, schema, expected):
    covered = cover_schema(pctx, schema)
    assert covered == expected


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string", "minLength": 5, "maxLength": 10}, {"/minLength", "/maxLength", "/type"}),
        ({"type": "number", "minimum": 0, "maximum": 100}, {"/minimum", "/maximum", "/type"}),
        (
            {"type": "array", "items": {"type": "string", "pattern": "^[a-z]+$"}},
            {"/items/pattern", "/items/type", "/type"},
        ),
        (
            {"type": "object", "properties": {"name": {"type": "string", "minLength": 3}}},
            {"/properties/name/minLength", "/type", "/properties/name/type"},
        ),
        ({"type": "string", "enum": ["red", "green", "blue"]}, {"/enum", "/type"}),
        (
            {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}},
            {"/required", "/type", "/properties/id/type"},
        ),
        ({"type": "string", "format": "email"}, {"/format", "/type"}),
        ({"anyOf": [{"type": "string"}, {"type": "number"}]}, {"/anyOf/1/type"}),
        (
            {"type": "object", "additionalProperties": False},
            {"/additionalProperties", "/type"},
        ),
        (
            {"type": "object", "patternProperties": {"^meta": {"type": "string"}}},
            {"/type"},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "object",
                        "properties": {
                            "address": {"type": "object", "properties": {"street": {"type": "string", "minLength": 5}}}
                        },
                    }
                },
            },
            {
                "/properties/user/properties/address/properties/street/minLength",
                "/properties/user/properties/address/properties/street/type",
                "/properties/user/properties/address/type",
                "/properties/user/type",
                "/type",
            },
        ),
    ],
)
def test_negative_value_locations(nctx, schema, expected):
    assert {v.location for v in cover_schema_iter(nctx, schema)} == expected


@pytest.mark.parametrize(
    "ctx, expected",
    (
        (
            "pctx",
            [
                {"name": "0"},
                {"name": "00"},
                {"name": "0" * 4000},
                {"name": "0" * 3999},
            ],
        ),
        (
            "nctx",
            [
                {"name": "0" * 4001},
                {
                    "name": "",
                },
                {},
                0,
                "false",
                "null",
                "",
                [
                    "null",
                    "null",
                ],
            ],
        ),
    ),
)
def test_generate_large_string(request, ctx, expected):
    ctx = request.getfixturevalue(ctx)
    schema = {
        "properties": {
            "name": {"maxLength": 4000, "minLength": 1, "pattern": "^[\\w\\W]+$", "type": "string"},
        },
        "required": ["name"],
        "type": "object",
    }
    assert cover_schema(ctx, schema) == expected


def test_generate_very_large_string(nctx):
    schema = {
        "properties": {
            "name": {"maxLength": 10000, "minLength": 1, "pattern": "^[\\w\\W]*$", "type": "string"},
        },
        "required": ["name"],
        "type": "object",
    }

    assert 10001 in {
        len(item["name"])
        for item in cover_schema(nctx, schema)
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def test_large_string_with_complex_pattern(nctx):
    schema = {
        "maxLength": 4000,
        "minLength": 1,
        "pattern": "^question\\.custom\\.[^,]+(?:,question\\.custom\\.[^,]+)*$",
        "type": "string",
    }
    assert cover_schema(nctx, schema) == [
        "0" * 4001,
        "",
        "0",
        0,
        "false",
        "null",
        [
            "null",
            "null",
        ],
    ]


def test_deeply_nested_values(pctx):
    schema = {
        "properties": {
            "customer": {
                "properties": {
                    "contacts": {
                        "properties": {
                            "contact": {
                                "items": {
                                    "properties": {
                                        "name": {
                                            "maxLength": 10,
                                            "minLength": 1,
                                            "type": "string",
                                        },
                                        "phone": {
                                            "items": {
                                                "properties": {
                                                    "phoneNumber": {
                                                        "maxLength": 15,
                                                        "minLength": 1,
                                                        "type": "string",
                                                    }
                                                },
                                                "type": "object",
                                            },
                                            "type": "array",
                                        },
                                    },
                                    "required": ["name"],
                                    "type": "object",
                                },
                                "type": "array",
                            }
                        },
                        "type": "object",
                    }
                },
                "type": "object",
            },
        },
        "type": "object",
    }
    assert cover_schema(pctx, schema) == [
        {
            "customer": {
                "contacts": {
                    "contact": [],
                },
            },
        },
        {},
        {
            "customer": {},
        },
        {
            "customer": {
                "contacts": {},
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "00",
                            "phone": [],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0000000000",
                            "phone": [],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "000000000",
                            "phone": [],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [
                                {
                                    "phoneNumber": "0",
                                },
                            ],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [
                                {},
                            ],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [
                                {
                                    "phoneNumber": "00",
                                },
                            ],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [
                                {
                                    "phoneNumber": "000000000000000",
                                },
                            ],
                        },
                    ],
                },
            },
        },
        {
            "customer": {
                "contacts": {
                    "contact": [
                        {
                            "name": "0",
                            "phone": [
                                {
                                    "phoneNumber": "00000000000000",
                                },
                            ],
                        },
                    ],
                },
            },
        },
    ]


def test_large_arrays(nctx):
    schema = {
        "properties": {
            "questions": {
                "items": {
                    "properties": {
                        "id": {"minLength": 6, "pattern": "^[0-9]+$", "type": "string"},
                    },
                    "required": ["id"],
                    "type": "object",
                    "additionalProperties": False,
                },
                "maxItems": 500,
                "minItems": 0,
                "type": "array",
            },
        },
        "type": "object",
    }

    assert 501 in {
        len(item["questions"])
        for item in cover_schema(nctx, schema)
        if isinstance(item, dict) and isinstance(item["questions"], list)
    }


def test_large_arrays_nested(nctx):
    schema = {
        "properties": {
            "questions": {
                "items": {
                    "properties": {
                        "answers": {
                            "items": {
                                "type": "null",
                            },
                            "maxItems": 100,
                            "type": "array",
                        },
                        "id": {"minLength": 6, "pattern": "^[0-9]+$", "type": "string"},
                    },
                    "required": ["id"],
                    "type": "object",
                },
                "maxItems": 500,
                "minItems": 1,
                "type": "array",
            },
        },
        "required": ["questions"],
        "type": "object",
    }

    assert 501 in {
        len(item["questions"])
        for item in cover_schema(nctx, schema)
        if isinstance(item, dict) and isinstance(item.get("questions"), list)
    }
