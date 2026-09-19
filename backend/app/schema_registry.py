from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator


class QueryValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ReferenceField:
    field: str
    target: str
    cardinality: str


class SchemaRegistry:
    ALLOWED_QUERY_KEYS = {
        "resource",
        "where",
        "expand",
        "unwind",
        "filter",
        "over",
        "select",
        "sort",
        "pagination",
    }
    ALLOWED_FILTER_OPERATORS = {
        "$eq",
        "$ne",
        "$exists",
        "$gt",
        "$gte",
        "$lt",
        "$lte",
        "$in",
        "$nin",
        "$contains",
        "$elemMatch",
        "$and",
        "$or",
        "$not",
    }
    def __init__(self, raw_schema: Any) -> None:
        self.raw_schema = raw_schema
        self.resources = self._resource_map(raw_schema)
        if not self.resources:
            raise QueryValidationError("Schema contains no resources.")

    @staticmethod
    def _resource_map(raw_schema: Any) -> dict[str, dict[str, Any]]:
        current = raw_schema
        while isinstance(current, dict):
            next_value = None
            for key in ("resources", "schema", "data", "output"):
                candidate = current.get(key)
                if isinstance(candidate, dict):
                    next_value = candidate
                    break
            if next_value is None:
                break
            current = next_value
        if not isinstance(current, dict):
            return {}
        return {
            str(name): definition
            for name, definition in current.items()
            if isinstance(definition, dict)
            and ("fields" in definition or definition.get("type") == "object")
        }

    def resource_names(self) -> list[str]:
        return sorted(self.resources)

    def find_resource(self, semantic_name: str) -> str | None:
        wanted = semantic_name.casefold()
        for name in self.resources:
            if name.casefold() == wanted:
                return name
        for name in self.resources:
            if wanted in name.casefold():
                return name
        return None

    def fields_for(self, resource: str) -> dict[str, Any]:
        definition = self.resources.get(resource)
        if definition is None:
            raise QueryValidationError(f'Unknown resource "{resource}".')
        fields = definition.get("fields", {})
        if not isinstance(fields, dict):
            return {}
        return fields

    @staticmethod
    def _descend(definition: dict[str, Any]) -> dict[str, Any]:
        if definition.get("type") == "array":
            item = definition.get("itemSchema") or definition.get("items") or {}
            return item if isinstance(item, dict) else {}
        return definition

    def field_definition(self, resource: str, path: str) -> dict[str, Any] | None:
        fields = self.fields_for(resource)
        definition: dict[str, Any] | None = None
        for index, segment in enumerate(path.split(".")):
            definition = fields.get(segment)
            if not isinstance(definition, dict):
                return None
            if index == len(path.split(".")) - 1:
                return definition
            nested = self._descend(definition)
            if nested.get("type") == "reference":
                target = nested.get("resource") or nested.get("target")
                fields = self.fields_for(target) if isinstance(target, str) else {}
            else:
                fields = nested.get("fields", {}) if isinstance(nested, dict) else {}
            if not isinstance(fields, dict):
                return None
        return definition

    def field_exists(self, resource: str, path: str) -> bool:
        return self.field_definition(resource, path) is not None

    def references_for(self, resource: str) -> list[ReferenceField]:
        references: list[ReferenceField] = []
        for name, definition in self.fields_for(resource).items():
            if not isinstance(definition, dict):
                continue
            target = definition.get("resource") or definition.get("target")
            if definition.get("type") == "reference" and isinstance(target, str):
                references.append(
                    ReferenceField(
                        field=name,
                        target=target,
                        cardinality=str(definition.get("cardinality", "one")),
                    )
                )
        return references

    def _validate_filter(self, resource: str, clause: Any, prefix: str = "") -> None:
        if isinstance(clause, list):
            for item in clause:
                self._validate_filter(resource, item, prefix)
            return
        if not isinstance(clause, dict):
            return
        for key, value in clause.items():
            if key.startswith("$"):
                if key not in self.ALLOWED_FILTER_OPERATORS:
                    raise QueryValidationError(f'Unknown filter operator "{key}".')
                self._validate_filter(resource, value, prefix)
                continue
            path = f"{prefix}.{key}" if prefix else key
            if not self.field_exists(resource, path):
                raise QueryValidationError(
                    f'Unknown field "{path}" on resource "{resource}".'
                )
            definition = self.field_definition(resource, path) or {}
            if isinstance(value, dict):
                for operator in (str(item) for item in value if str(item).startswith("$")):
                    if operator not in self.ALLOWED_FILTER_OPERATORS:
                        raise QueryValidationError(
                            f'Unknown filter operator "{operator}".'
                        )
            if isinstance(value, dict) and "$elemMatch" in value:
                nested = self._descend(definition)
                item_fields = nested.get("fields", {}) if isinstance(nested, dict) else {}
                if not item_fields:
                    raise QueryValidationError(
                        f'Field "{path}" does not support $elemMatch object clauses.'
                    )
                self._validate_filter_fields(value["$elemMatch"], item_fields, path)
            elif isinstance(value, dict) and not any(str(k).startswith("$") for k in value):
                nested = self._descend(definition)
                if nested.get("type") == "object" or "fields" in nested:
                    self._validate_filter(resource, value, path)

    def _validate_filter_fields(
        self, clause: Any, fields: dict[str, Any], prefix: str
    ) -> None:
        if isinstance(clause, list):
            for item in clause:
                self._validate_filter_fields(item, fields, prefix)
            return
        if not isinstance(clause, dict):
            return
        for key, value in clause.items():
            if key.startswith("$"):
                if key not in self.ALLOWED_FILTER_OPERATORS:
                    raise QueryValidationError(f'Unknown filter operator "{key}".')
                self._validate_filter_fields(value, fields, prefix)
                continue
            if key not in fields:
                raise QueryValidationError(f'Unknown field "{prefix}.{key}".')
            definition = fields[key] if isinstance(fields[key], dict) else {}
            if isinstance(value, dict) and not any(str(k).startswith("$") for k in value):
                nested = self._descend(definition)
                nested_fields = nested.get("fields", {}) if isinstance(nested, dict) else {}
                if nested_fields:
                    self._validate_filter_fields(value, nested_fields, f"{prefix}.{key}")

    def _validate_select(self, resource: str, select: Any, prefix: str = "") -> None:
        if isinstance(select, list):
            for item in select:
                if isinstance(item, str):
                    path = f"{prefix}.{item}" if prefix else item
                    if not self.field_exists(resource, path):
                        raise QueryValidationError(
                            f'Unknown selected field "{path}" on "{resource}".'
                        )
                elif isinstance(item, dict):
                    self._validate_select(resource, item, prefix)
            return
        if not isinstance(select, dict):
            return
        for key, value in select.items():
            if key.startswith("$"):
                continue
            path = f"{prefix}.{key}" if prefix else key
            if not self.field_exists(resource, path) and not (
                isinstance(value, dict)
                and any(str(operator).startswith("$") for operator in value)
            ):
                raise QueryValidationError(
                    f'Unknown selected field "{path}" on "{resource}".'
                )
            if isinstance(value, dict) and not any(
                str(operator).startswith("$") for operator in value
            ):
                self._validate_select(resource, value, path)

    def _validate_expand(self, resource: str, expand: Any) -> None:
        if not isinstance(expand, dict):
            return
        refs = {item.field: item for item in self.references_for(resource)}
        for field, nested in expand.items():
            ref = refs.get(field)
            if ref is None:
                raise QueryValidationError(
                    f'Field "{field}" on "{resource}" is not an expandable reference.'
                )
            if isinstance(nested, dict) and nested:
                self._validate_expand(ref.target, nested)

    def validate_query(self, query: dict[str, Any]) -> None:
        unknown_keys = set(query) - self.ALLOWED_QUERY_KEYS
        if unknown_keys:
            raise QueryValidationError(
                f"Unknown query keys: {', '.join(sorted(unknown_keys))}."
            )
        resource = query.get("resource")
        if not isinstance(resource, str) or resource not in self.resources:
            raise QueryValidationError(f'Unknown resource "{resource}".')
        if "where" in query:
            self._validate_filter(resource, query["where"])
        if "filter" in query:
            self._validate_filter(resource, query["filter"])
        if "select" in query:
            self._validate_select(resource, query["select"])
        if "expand" in query:
            if not isinstance(query["expand"], dict):
                raise QueryValidationError("Expand must be an object reference tree.")
            self._validate_expand(resource, query["expand"])
        unwind = query.get("unwind", [])
        if not isinstance(unwind, list):
            raise QueryValidationError("Unwind must be a list of paths or path objects.")
        for item in unwind:
            path = item.get("path") if isinstance(item, dict) else item
            if not isinstance(path, str) or not self.field_exists(resource, path):
                raise QueryValidationError(f'Unknown unwind path "{path}" on "{resource}".')
            if isinstance(item, dict) and item.get("type", "inner") not in {"inner", "left"}:
                raise QueryValidationError("Unwind type must be inner or left.")
        over = query.get("over", [])
        if over and (
            not isinstance(over, list)
            or not all(isinstance(path, str) for path in over)
        ):
            raise QueryValidationError("Over must be a list of field paths.")
        for path in over:
            if not self.field_exists(resource, path):
                raise QueryValidationError(
                    f'Unknown grouping field "{path}" on "{resource}".'
                )
        sort = query.get("sort", [])
        if not isinstance(sort, list):
            raise QueryValidationError("Sort must be a list of field/direction objects.")
        for item in sort:
            if not isinstance(item, dict) or not isinstance(item.get("field"), str):
                raise QueryValidationError(
                    'Every sort entry must contain a string "field".'
                )
            field = item["field"]
            if not self.field_exists(resource, field):
                select = query.get("select")
                if not isinstance(select, dict) or field not in select:
                    raise QueryValidationError(
                        f'Unknown sort field "{field}" on "{resource}".'
                    )
            if item.get("direction", "asc") not in {"asc", "desc"}:
                raise QueryValidationError("Sort direction must be asc or desc.")
        pagination = query.get("pagination", {})
        if pagination:
            if not isinstance(pagination, dict):
                raise QueryValidationError("Pagination must be an object.")
            limit = pagination.get("limit", 100)
            offset = pagination.get("offset", 0)
            if not isinstance(limit, int) or not 1 <= limit <= 100:
                raise QueryValidationError("Pagination limit must be between 1 and 100.")
            if not isinstance(offset, int) or offset < 0:
                raise QueryValidationError("Pagination offset must be a non-negative integer.")

    def compact_digest(self, *, max_fields_per_resource: int = 80) -> dict[str, Any]:
        resources: dict[str, Any] = {}
        for resource in self.resource_names():
            fields = self.fields_for(resource)
            rendered: list[dict[str, Any]] = []
            for name, definition in list(fields.items())[:max_fields_per_resource]:
                item: dict[str, Any] = {
                    "name": name,
                    "type": definition.get("type", "unknown")
                    if isinstance(definition, dict)
                    else "unknown",
                }
                if isinstance(definition, dict) and definition.get("type") == "reference":
                    item["resource"] = definition.get("resource") or definition.get("target")
                    item["cardinality"] = definition.get("cardinality", "one")
                rendered.append(item)
            resources[resource] = rendered
        return {
            "resources": resources,
            "query_syntax": {
                "pipeline": [
                    "where",
                    "expand",
                    "unwind",
                    "filter",
                    "over",
                    "select",
                    "sort",
                    "pagination",
                ],
                "expand_example": {
                    "expand": {"reference_field": {"nested_reference": True}}
                },
                "sort_example": [
                    {"field": "declared_or_derived_field", "direction": "desc"}
                ],
                "pagination_bounds": {"limit": "1..100", "offset": ">=0"},
            },
        }

    def iter_reference_values(
        self, resource: str, record: dict[str, Any]
    ) -> Iterator[tuple[str, str]]:
        for reference in self.references_for(resource):
            value = record.get(reference.field)
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, dict):
                    identifier = item.get("id")
                else:
                    identifier = item
                if identifier is not None:
                    yield reference.target, str(identifier)
