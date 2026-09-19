import pytest

from backend.app.schema_registry import QueryValidationError, SchemaRegistry
from backend.app.demo_federato import DEMO_SCHEMA


SCHEMA = {
    "Submission": {
        "type": "object",
        "fields": {
            "id": {"type": "number"},
            "status": {"type": "string"},
            "policy": {
                "type": "reference",
                "resource": "Policy",
                "cardinality": "one",
            },
        },
    },
    "Policy": {
        "type": "object",
        "fields": {
            "id": {"type": "number"},
            "premium": {"type": "number"},
        },
    },
}


def test_registry_finds_resources_and_references():
    registry = SchemaRegistry(SCHEMA)

    assert registry.find_resource("submission") == "Submission"
    assert registry.field_exists("Submission", "status")
    assert registry.references_for("Submission")[0].target == "Policy"


def test_query_validation_accepts_known_fields():
    registry = SchemaRegistry(SCHEMA)
    registry.validate_query(
        {
            "resource": "Submission",
            "where": {"status": "open"},
            "select": ["id", "status"],
            "expand": {"policy": True},
        }
    )


def test_query_validation_rejects_hallucinated_field():
    registry = SchemaRegistry(SCHEMA)

    with pytest.raises(QueryValidationError, match="made_up"):
        registry.validate_query(
            {"resource": "Submission", "where": {"made_up": "value"}}
        )


def test_agent_query_rejects_unknown_operators_and_unbounded_pages():
    registry = SchemaRegistry(DEMO_SCHEMA)

    with pytest.raises(QueryValidationError, match="Unknown filter operator"):
        registry.validate_query(
            {"resource": "Policy", "where": {"premium": {"$grt": 50_000}}}
        )

    with pytest.raises(QueryValidationError, match="between 1 and 100"):
        registry.validate_query(
            {"resource": "Policy", "pagination": {"limit": 500, "offset": 0}}
        )


def test_query_validation_rejects_malformed_sort_before_execution():
    registry = SchemaRegistry(DEMO_SCHEMA)

    with pytest.raises(QueryValidationError, match="sort entry"):
        registry.validate_query(
            {"resource": "Policy", "sort": [{"premium": "desc"}]}
        )
