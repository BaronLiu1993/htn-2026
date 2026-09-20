from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterator


class QueryValidationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        field_path: str | None = None,
        stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.field_path = field_path
        self.stage = stage


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
    REDUCTION_OPERATORS = {"$sum", "$avg", "$min", "$max", "$count", "$countDistinct"}
    VALUE_OPERATORS = {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte"}
    LIST_OPERATORS = {"$in", "$nin"}

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

    def identifier_field(self, resource: str) -> str:
        fields = self.fields_for(resource)
        for candidate in ("id", "_id"):
            if candidate in fields:
                return candidate
        raise QueryValidationError(
            f'Resource "{resource}" has no declared identifier field.',
            field_path="id",
        )

    @staticmethod
    def canonical_identifier(value: Any) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, dict):
            return SchemaRegistry.canonical_identifier(value.get("id") or value.get("_id"))
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        text = str(value).strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return text
        if number.is_integer() and abs(number) < 1e15:
            return str(int(number))
        return text

    def field_value_type(self, resource: str, path: str) -> str | None:
        definition = self.field_definition(resource, path)
        if not isinstance(definition, dict):
            return None
        nested = self._descend(definition)
        reference = nested if nested.get("type") == "reference" else definition
        if reference.get("type") == "reference":
            target = reference.get("resource") or reference.get("target")
            if isinstance(target, str):
                try:
                    identifier = self.identifier_field(target)
                except QueryValidationError:
                    return "number"
                identifier_definition = self.field_definition(target, identifier)
                if isinstance(identifier_definition, dict):
                    return str(identifier_definition.get("type") or "number")
            return "number"
        return str(definition.get("type") or nested.get("type") or "unknown")

    @staticmethod
    def coerce_value(value: Any, field_type: str | None) -> Any:
        if value is None or not field_type:
            return value
        normalized = field_type.casefold()
        if normalized in {"number", "integer", "int", "float"}:
            if isinstance(value, bool):
                return value
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                if normalized != "float" and value.is_integer():
                    return int(value)
                return value
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return value
                try:
                    if normalized == "float" or any(marker in text.lower() for marker in (".", "e")):
                        number = float(text)
                        if normalized != "float" and number.is_integer():
                            return int(number)
                        return number
                    return int(text)
                except ValueError:
                    return value
            return value
        if normalized in {"string", "str"}:
            return str(value)
        return value

    def coerce_identifier(self, resource: str, value: Any) -> Any:
        try:
            field = self.identifier_field(resource)
        except QueryValidationError:
            return value
        return self.coerce_value(value, self.field_value_type(resource, field))

    def _coerce_value_for_path(self, resource: str, path: str, value: Any) -> Any:
        if not path:
            return value
        return self.coerce_value(value, self.field_value_type(resource, path))

    def _coerce_filter(self, resource: str, clause: Any, *, prefix: str = "") -> Any:
        if isinstance(clause, list):
            return [self._coerce_filter(resource, item, prefix=prefix) for item in clause]
        if not isinstance(clause, dict):
            return self._coerce_value_for_path(resource, prefix, clause) if prefix else clause
        output: dict[str, Any] = {}
        for key, value in clause.items():
            if key.startswith("$"):
                if key in self.LIST_OPERATORS and isinstance(value, list):
                    output[key] = [
                        self._coerce_value_for_path(resource, prefix, item) for item in value
                    ]
                elif key in self.VALUE_OPERATORS:
                    output[key] = self._coerce_value_for_path(resource, prefix, value)
                else:
                    output[key] = self._coerce_filter(resource, value, prefix=prefix)
                continue
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                output[key] = self._coerce_filter(resource, value, prefix=path)
            elif isinstance(value, list):
                output[key] = [
                    self._coerce_filter(resource, item, prefix=path)
                    if isinstance(item, dict)
                    else self._coerce_value_for_path(resource, path, item)
                    for item in value
                ]
            else:
                output[key] = self._coerce_value_for_path(resource, path, value)
        return output

    def coerce_query(self, query: dict[str, Any]) -> dict[str, Any]:
        resource = query.get("resource")
        if not isinstance(resource, str) or resource not in self.resources:
            return query
        if "where" in query:
            query["where"] = self._coerce_filter(resource, query["where"])
        if "filter" in query:
            query["filter"] = self._coerce_filter(resource, query["filter"])
        return query

    def identifier_types(self) -> dict[str, dict[str, str]]:
        output: dict[str, dict[str, str]] = {}
        for resource in self.resource_names():
            try:
                field = self.identifier_field(resource)
            except QueryValidationError:
                continue
            output[resource] = {
                "field": field,
                "type": self.field_value_type(resource, field) or "unknown",
            }
        return output

    def schema_digest(self) -> str:
        encoded = json.dumps(
            self.raw_schema,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

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
            nested = self._descend(definition)
            reference = (
                nested
                if nested.get("type") == "reference"
                else definition
            )
            target = reference.get("resource") or reference.get("target")
            if reference.get("type") == "reference" and isinstance(target, str):
                references.append(
                    ReferenceField(
                        field=name,
                        target=target,
                        cardinality=(
                            "many"
                            if definition.get("type") == "array"
                            else str(reference.get("cardinality", "one"))
                        ),
                    )
                )
        return references

    @staticmethod
    def _is_many(definition: dict[str, Any]) -> bool:
        return definition.get("type") == "array" or (
            definition.get("type") == "reference"
            and definition.get("cardinality") == "many"
        )

    @staticmethod
    def _expanded_child(expand: Any, field: str) -> Any:
        if not isinstance(expand, dict) or field not in expand:
            return None
        value = expand[field]
        if value is True or value == {}:
            return {}
        if isinstance(value, str):
            return {value: True}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _select_includes(select: Any, field: str) -> bool:
        if isinstance(select, list):
            return field in select
        return isinstance(select, dict) and select.get(field) is True

    @classmethod
    def _select_has_reduction(cls, select: Any) -> bool:
        if isinstance(select, list):
            return any(cls._select_has_reduction(item) for item in select)
        if not isinstance(select, dict):
            return False
        return any(
            key in cls.REDUCTION_OPERATORS or cls._select_has_reduction(value)
            for key, value in select.items()
        )

    def _require_identifier_projection(
        self,
        resource: str,
        select: Any,
        *,
        path: str,
    ) -> None:
        identifier = self.identifier_field(resource)
        if not self._select_includes(select, identifier):
            raise QueryValidationError(
                f'Select for "{path}" must retain declared identifier "{identifier}".',
                field_path=f"{path}.{identifier}" if path else identifier,
                stage="select",
            )

    def _validate_stage_path(
        self,
        resource: str,
        path: str,
        *,
        stage: str,
        expand: Any = None,
        unwound: set[str] | None = None,
        allow_array_path: bool = False,
    ) -> dict[str, Any]:
        fields = self.fields_for(resource)
        current_resource = resource
        current_expand = expand
        consumed: list[str] = []
        segments = path.split(".")
        definition: dict[str, Any] = {}
        for index, segment in enumerate(segments):
            definition = fields.get(segment) if isinstance(fields, dict) else None
            rendered = ".".join([*consumed, segment])
            if not isinstance(definition, dict):
                raise QueryValidationError(
                    f'Unknown field "{rendered}" on resource "{current_resource}".',
                    field_path=rendered,
                    stage=stage,
                )
            consumed.append(segment)
            if index == len(segments) - 1:
                return definition

            nested = self._descend(definition)
            reference = (
                nested
                if nested.get("type") == "reference"
                else definition
            )
            if reference.get("type") == "reference":
                if stage == "where":
                    raise QueryValidationError(
                        f'Pre-expansion where cannot traverse reference "{rendered}".',
                        field_path=rendered,
                        stage=stage,
                    )
                child_expand = self._expanded_child(current_expand, segment)
                if child_expand is None:
                    raise QueryValidationError(
                        f'Field path "{path}" traverses unexpanded reference "{rendered}".',
                        field_path=rendered,
                        stage=stage,
                    )
                target = reference.get("resource") or reference.get("target")
                if not isinstance(target, str):
                    raise QueryValidationError(
                        f'Reference "{rendered}" has no target resource.',
                        field_path=rendered,
                        stage=stage,
                    )
                if (
                    self._is_many(definition)
                    and not allow_array_path
                    and rendered not in (unwound or set())
                ):
                    raise QueryValidationError(
                        f'Array boundary "{rendered}" requires $elemMatch or unwind.',
                        field_path=rendered,
                        stage=stage,
                    )
                current_resource = target
                fields = self.fields_for(target)
                current_expand = child_expand
                continue

            if self._is_many(definition) and not allow_array_path and rendered not in (unwound or set()):
                raise QueryValidationError(
                    f'Array boundary "{rendered}" requires $elemMatch or unwind.',
                    field_path=rendered,
                    stage=stage,
                )
            fields = nested.get("fields", {}) if isinstance(nested, dict) else {}
            if not isinstance(fields, dict):
                fields = {}
        return definition

    def _validate_filter(
        self,
        resource: str,
        clause: Any,
        *,
        stage: str,
        expand: Any = None,
        unwound: set[str] | None = None,
        prefix: str = "",
        allow_array_path: bool = False,
    ) -> None:
        if isinstance(clause, list):
            for item in clause:
                self._validate_filter(
                    resource,
                    item,
                    stage=stage,
                    expand=expand,
                    unwound=unwound,
                    prefix=prefix,
                    allow_array_path=allow_array_path,
                )
            return
        if not isinstance(clause, dict):
            return
        for key, value in clause.items():
            if key.startswith("$"):
                if key not in self.ALLOWED_FILTER_OPERATORS:
                    raise QueryValidationError(
                        f'Unknown filter operator "{key}".',
                        field_path=prefix or None,
                        stage=stage,
                    )
                self._validate_filter(
                    resource,
                    value,
                    stage=stage,
                    expand=expand,
                    unwound=unwound,
                    prefix=prefix,
                    allow_array_path=allow_array_path,
                )
                continue
            path = f"{prefix}.{key}" if prefix else key
            definition = self._validate_stage_path(
                resource,
                path,
                stage=stage,
                expand=expand,
                unwound=unwound,
                allow_array_path=allow_array_path,
            )
            if isinstance(value, dict):
                operators = [str(item) for item in value if str(item).startswith("$")]
                for operator in operators:
                    if operator not in self.ALLOWED_FILTER_OPERATORS:
                        raise QueryValidationError(
                            f'Unknown filter operator "{operator}".',
                            field_path=path,
                            stage=stage,
                        )
                if "$elemMatch" in value:
                    if not self._is_many(definition):
                        raise QueryValidationError(
                            f'Field "{path}" is not an array and cannot use $elemMatch.',
                            field_path=path,
                            stage=stage,
                        )
                    self._validate_filter(
                        resource,
                        value["$elemMatch"],
                        stage=stage,
                        expand=expand,
                        unwound=unwound,
                        prefix=path,
                        allow_array_path=True,
                    )
                elif not operators:
                    self._validate_filter(
                        resource,
                        value,
                        stage=stage,
                        expand=expand,
                        unwound=unwound,
                        prefix=path,
                        allow_array_path=allow_array_path,
                    )

    def _validate_select(
        self,
        resource: str,
        select: Any,
        *,
        expand: Any = None,
        unwound: set[str] | None = None,
        prefix: str = "",
        allow_reference_projection: bool = False,
    ) -> None:
        if isinstance(select, list):
            for item in select:
                if isinstance(item, str):
                    path = f"{prefix}.{item}" if prefix else item
                    self._validate_stage_path(
                        resource,
                        path,
                        stage="select",
                        expand=expand,
                        unwound=unwound,
                        allow_array_path=True,
                    )
                elif isinstance(item, dict):
                    self._validate_select(
                        resource,
                        item,
                        expand=expand,
                        unwound=unwound,
                        prefix=prefix,
                        allow_reference_projection=allow_reference_projection,
                    )
            return
        if not isinstance(select, dict):
            raise QueryValidationError("Select must be a list or object.", stage="select")
        for key, value in select.items():
            path = f"{prefix}.{key}" if prefix else key
            reductions = (
                [operator for operator in value if operator in self.REDUCTION_OPERATORS]
                if isinstance(value, dict)
                else []
            )
            if reductions:
                for operator in reductions:
                    operand = value[operator]
                    if operator != "$count":
                        for operand_path in operand if isinstance(operand, list) else [operand]:
                            if not isinstance(operand_path, str):
                                raise QueryValidationError(
                                    f'Reduction "{operator}" requires field paths.',
                                    field_path=path,
                                    stage="select",
                                )
                            self._validate_stage_path(
                                resource,
                                operand_path,
                                stage="select",
                                expand=expand,
                                unwound=unwound,
                                allow_array_path=True,
                            )
                continue

            definition = self._validate_stage_path(
                resource,
                path,
                stage="select",
                expand=expand,
                unwound=unwound,
                allow_array_path=True,
            )
            if not isinstance(value, (dict, list)):
                continue
            if isinstance(value, dict) and "$expand" in value:
                nested_definition = self._descend(definition)
                reference = (
                    nested_definition
                    if nested_definition.get("type") == "reference"
                    else definition
                )
                if reference.get("type") != "reference":
                    raise QueryValidationError(
                        f'Select $expand requires reference field "{path}".',
                        field_path=path,
                        stage="select",
                    )
                spec = value["$expand"]
                target = reference.get("resource") or reference.get("target")
                if isinstance(spec, dict) and "select" in spec and isinstance(target, str):
                    self._require_identifier_projection(
                        target,
                        spec["select"],
                        path=path,
                    )
                    self._validate_select(
                        target,
                        spec["select"],
                        expand=None,
                        unwound=set(),
                        allow_reference_projection=True,
                    )
                elif spec is not True:
                    raise QueryValidationError(
                        "Select $expand must be true or contain a select projection.",
                        field_path=path,
                        stage="select",
                    )
                continue
            nested_definition = self._descend(definition)
            reference = (
                nested_definition
                if nested_definition.get("type") == "reference"
                else definition
            )
            if reference.get("type") == "reference":
                first_segment = path.split(".")[0]
                if (
                    self._expanded_child(expand, first_segment) is None
                ):
                    raise QueryValidationError(
                        f'Nested select for "{path}" requires expand or select-leaf $expand.',
                        field_path=path,
                        stage="select",
                    )
                target = reference.get("resource") or reference.get("target")
                if isinstance(target, str):
                    self._require_identifier_projection(target, value, path=path)
                    self._validate_select(
                        target,
                        value,
                        expand=self._expanded_child(expand, first_segment),
                        unwound=set(),
                        allow_reference_projection=True,
                    )
            else:
                self._validate_select(
                    resource,
                    value,
                    expand=expand,
                    unwound=unwound,
                    prefix=path,
                    allow_reference_projection=allow_reference_projection,
                )

    def _validate_expand(self, resource: str, expand: Any, prefix: str = "") -> None:
        if not isinstance(expand, dict):
            raise QueryValidationError("Expand must be an object reference tree.", stage="expand")
        refs = {item.field: item for item in self.references_for(resource)}
        for field, nested in expand.items():
            path = f"{prefix}.{field}" if prefix else field
            ref = refs.get(field)
            if ref is None:
                raise QueryValidationError(
                    f'Field "{path}" on "{resource}" is not an expandable reference.',
                    field_path=path,
                    stage="expand",
                )
            if isinstance(nested, str):
                self._validate_expand(ref.target, {nested: True}, path)
            elif isinstance(nested, dict) and nested:
                self._validate_expand(ref.target, nested, path)
            elif nested not in (True, {}):
                raise QueryValidationError(
                    f'Expand value for "{path}" must be true, a field name, or an object.',
                    field_path=path,
                    stage="expand",
                )

    def validate_query(self, query: dict[str, Any]) -> None:
        unknown_keys = set(query) - self.ALLOWED_QUERY_KEYS
        if unknown_keys:
            raise QueryValidationError(
                f"Unknown query keys: {', '.join(sorted(unknown_keys))}."
            )
        resource = query.get("resource")
        if not isinstance(resource, str) or resource not in self.resources:
            raise QueryValidationError(f'Unknown resource "{resource}".')
        expand = query.get("expand", {})
        if "expand" in query:
            self._validate_expand(resource, expand)
        if "where" in query:
            self._validate_filter(resource, query["where"], stage="where")
        unwind = query.get("unwind", [])
        if not isinstance(unwind, list):
            raise QueryValidationError("Unwind must be a list of paths or path objects.")
        unwound: set[str] = set()
        for item in unwind:
            path = item.get("path") if isinstance(item, dict) else item
            if not isinstance(path, str):
                raise QueryValidationError("Every unwind entry requires a string path.")
            definition = self._validate_stage_path(
                resource,
                path,
                stage="unwind",
                expand=expand,
                unwound=unwound,
                allow_array_path=True,
            )
            if not self._is_many(definition):
                raise QueryValidationError(
                    f'Unwind path "{path}" is not an array.',
                    field_path=path,
                    stage="unwind",
                )
            unwound.add(path)
            if isinstance(item, dict) and item.get("type", "inner") not in {"inner", "left"}:
                raise QueryValidationError("Unwind type must be inner or left.")
        if "filter" in query:
            self._validate_filter(
                resource,
                query["filter"],
                stage="filter",
                expand=expand,
                unwound=unwound,
            )
        over = query.get("over", [])
        if over and (
            not isinstance(over, list)
            or not all(isinstance(path, str) for path in over)
        ):
            raise QueryValidationError("Over must be a list of field paths.")
        for path in over:
            self._validate_stage_path(
                resource,
                path,
                stage="over",
                expand=expand,
                unwound=unwound,
                allow_array_path=True,
            )
        if "select" in query:
            if not self._select_has_reduction(query["select"]):
                self._require_identifier_projection(
                    resource,
                    query["select"],
                    path="",
                )
            self._validate_select(
                resource,
                query["select"],
                expand=expand,
                unwound=unwound,
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
            try:
                self._validate_stage_path(
                    resource,
                    field,
                    stage="sort",
                    expand=expand,
                    unwound=unwound,
                    allow_array_path=True,
                )
            except QueryValidationError:
                select = query.get("select")
                if not isinstance(select, dict) or field not in select:
                    raise QueryValidationError(
                        f'Unknown sort field "{field}" on "{resource}".',
                        field_path=field,
                        stage="sort",
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
            references = {
                reference.field: reference
                for reference in self.references_for(resource)
            }
            rendered: list[dict[str, Any]] = []
            for name, definition in list(fields.items())[:max_fields_per_resource]:
                item: dict[str, Any] = {
                    "name": name,
                    "type": definition.get("type", "unknown")
                    if isinstance(definition, dict)
                    else "unknown",
                }
                if isinstance(definition, dict) and definition.get("required") is True:
                    item["required"] = True
                if name in references:
                    item["resource"] = references[name].target
                    item["cardinality"] = references[name].cardinality
                rendered.append(item)
            resources[resource] = rendered
        return {
            "sha256": self.schema_digest(),
            "identifiers": self.identifier_types(),
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
                "identifier_values": (
                    "Use the declared identifier type in $in and $eq. "
                    "Number fields must be JSON numbers, not quoted strings."
                ),
                "array_boundaries": (
                    "Use $elemMatch or unwind at arrays such as exposure_units, "
                    "buildings, and claims. Do not traverse an array with a bare dotted path."
                ),
                "expand_vs_dollar_expand": (
                    "Use expand when later filter or select must traverse a reference. "
                    "Use select-leaf $expand when the hydrated object is only needed in the reply."
                ),
                "expand_example": {
                    "expand": {"reference_field": {"nested_reference": True}}
                },
                "elem_match_example": {
                    "filter": {
                        "array_field": {
                            "$elemMatch": {"nested_field": {"$eq": "value"}}
                        }
                    }
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
                    identifier = item.get("id") or item.get("_id")
                else:
                    identifier = item
                canonical = self.canonical_identifier(identifier)
                if canonical is not None:
                    yield reference.target, canonical
