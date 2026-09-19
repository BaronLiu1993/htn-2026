# Query Request Body

## Overview

Every key is optional except `resource`.

Supported query keys are `resource`, `where`, `expand`, `unwind`, `filter`, `over`, `select`, `sort`, and `pagination`.

The query language is Mongo-flavored. If you have written MongoDB filters before, most of this will feel familiar.

## Query pipeline

```text
where (filter raw records)
  ↓
expand (hydrate references)
  ↓
unwind (fan arrays into rows)
  ↓
filter (filter hydrated rows)
  ↓
over (partition into groups)
  ↓
select (paths + expand + reductions)
  ↓
sort (order results)
  ↓
paginate (limit/offset)
```

Omitted steps are no-ops.

## Filtering

A `where` value mirrors the shape of the records. Keys are either field names or operators (starting with `$`).

```json
{
  "resource": "Policy",
  "where": {
    "status": "active"
  }
}
```

Equivalent to `Policy.status == 'active'`.

```json
{
  "resource": "Location",
  "where": {
    "hazard_tags": ["hail", "wildfire"]
  }
}
```

Equivalent to `Location.hazard_tags === ['hail', 'wildfire']`.

Operators can be used in a field clause:

```json
{
  "where": {
    "premium": {
      "$gte": 5000,
      "$lt": 20000
    }
  }
}
```

Equivalent to `5000 <= $.premium < 20000`.

### Operators

| Operator | Meaning | Works on |
| --- | --- | --- |
| `$eq` | Deep equality | Any type |
| `$ne` | Not equal | Any type |
| `$exists` | Field exists | Any type |
| `$gt`, `$gte`, `$lt`, `$lte` | Comparisons | Scalars |
| `$in` | In list | Scalars/arrays |
| `$nin` | Not in list | Scalars/arrays |
| `$contains` | Contains substring/element | Scalars/arrays |
| `$elemMatch` | At least one array element matches | Arrays only |

Multiple operators in one clause implicitly form `$and`.

## Nested objects and arrays

For nested objects, nesting and dot-paths are equivalent:

```json
{
  "where": {
    "dates": {
      "effective": "2026-01-01"
    }
  }
}
```

```json
{
  "where": {
    "dates.effective": "2026-01-01"
  }
}
```

Be careful with arrays: use `$elemMatch` to break paths at array boundaries.

```json
{
  "expand": {
    "exposure_units": {
      "location": true
    }
  },
  "filter": {
    "exposure_units": {
      "$elemMatch": {
        "kind": "location",
        "location": {
          "hazard_tags": {
            "$in": ["earthquake", "wildfire"]
          }
        }
      }
    }
  }
}
```

`$and`, `$or`, and `$not` work as expected. For example:

```json
{
  "where": {
    "status": "active",
    "$or": [
      { "business_type": "renewal" },
      { "premium": { "$gte": 100000 } }
    ],
    "$not": {
      "producer.broker": 2
    }
  }
}
```

## References

Some fields are references: they store IDs pointing to records in another resource. There are two ways to resolve them.

Use `expand` when something downstream needs the reference:

```json
{
  "expand": {
    "producer": "broker"
  }
}
```

This hydrates the record so `filter`, `over`, `sort`, and `select` can reach through `producer.broker`.

Use `$expand` when you only want the reference resolved in the reply:

```json
{
  "select": {
    "producer": {
      "broker": {
        "$expand": true
      }
    }
  }
}
```

The `expand` stage also accepts these equivalent forms:

```json
{ "expand": { "producer": { "broker": true } } }
```

```json
{ "expand": { "producer": { "broker": {} } } }
```

Chain by nesting:

```json
{
  "expand": {
    "exposure_units": {
      "location": {
        "buildings": true
      }
    }
  }
}
```

## Projection (`select`)

Omit `select` to return the entire record shape. There are two forms:

```json
{
  "select": ["id", "status", "dates.effective"]
}
```

```json
{
  "select": {
    "id": true,
    "status": true,
    "dates": {
      "effective": true
    }
  }
}
```

A select leaf can be a field path, a reference, or an aggregation:

```json
{
  "select": {
    "policy_number": true,
    "broker": {
      "$expand": {
        "select": ["name"]
      }
    },
    "totalTiv": {
      "$sum": "exposure_units.basis_amount"
    }
  }
}
```

## Aggregations

| Function | Argument | Notes |
| --- | --- | --- |
| `$sum` | path | Non-numeric/null skipped. Sum of nothing = `0`. |
| `$avg` | path | Nulls skipped. Average of nothing = `null`. |
| `$min` / `$max` | path | Nulls skipped. Result over nothing = `null`. |
| `$count` | `true` | Counts every row, including nulls. |
| `$countDistinct` | path or array | Distinct over non-null only. |

## Unwinding

Each entry is a path string or `{ "path", "type" }`.

```json
{
  "unwind": [
    {
      "path": "claims",
      "type": "inner"
    }
  ]
}
```

An inner unwind produces `k` records where `k <= N`; a left unwind produces `N` records.

To return a stream of associated objects with parent information:

```json
{
  "unwind": ["exposure_units"],
  "select": ["id", "policy_number", "exposure_units"]
}
```

To recover all exposure units per policy, include an `over` clause:

```json
{
  "unwind": ["exposure_units"],
  "over": ["id", "exposure_units.id"],
  "select": ["id", "policy_number", "exposure_units"]
}
```

## Grouping (`over`)

Much like SQL `GROUP BY`, `over` partitions rows. It defaults to `["id"]`.

```json
{
  "over": ["line_of_business", "exposure_units.basis"]
}
```

Groups return as flat projections in first-seen order:

```json
{
  "resource": "Policy",
  "total": 2,
  "groups": [
    {
      "line_of_business": "property",
      "policies": 27,
      "premium": 6690900
    },
    {
      "line_of_business": "cgl",
      "policies": 11,
      "premium": 2193000
    }
  ]
}
```

## Ordering (`sort`)

```json
{
  "sort": [
    { "field": "premium", "direction": "desc" },
    { "field": "id" }
  ]
}
```

Rules apply in priority order. `direction` defaults to `"asc"`. Nulls sort last. Sorting runs after `select`, so it can reference derived paths.

## Pagination

```json
{
  "pagination": {
    "limit": 10,
    "offset": 20
  }
}
```

The `total` in results reports total matching records, irrespective of pagination.

## Worked examples

### Non-expired policies with expanded details

```json
{
  "resource": "Policy",
  "where": {
    "status": { "$ne": "expired" }
  },
  "expand": {
    "insured": true
  },
  "filter": {
    "insured.naics_code": "541110"
  },
  "select": [
    "policy_number",
    "line_of_business",
    "premium",
    {
      "insured": ["name", "entity_type", "employee_count"]
    }
  ],
  "sort": [
    { "field": "insured.name", "direction": "asc" }
  ]
}
```

### Auto policies with fleet summarized

```json
{
  "resource": "Policy",
  "where": {
    "line_of_business": "auto"
  },
  "expand": {
    "exposure_units": true
  },
  "unwind": ["exposure_units"],
  "filter": {
    "exposure_units.kind": "vehicle"
  },
  "select": {
    "policy_number": true,
    "premium": true,
    "vehicles": { "$count": true },
    "fleetCost": { "$sum": "exposure_units.vehicle.cost_new" },
    "oldestYear": { "$min": "exposure_units.vehicle.year" }
  },
  "sort": [
    { "field": "fleetCost", "direction": "desc" }
  ],
  "pagination": {
    "limit": 10
  }
}
```

### Pre-1990 buildings at catastrophe-exposed locations

```json
{
  "resource": "Policy",
  "where": {
    "$and": [
      {
        "status": {
          "$in": ["active", "bound"]
        }
      },
      {
        "line_of_business": {
          "$in": ["property", "cgl"]
        }
      }
    ]
  },
  "expand": {
    "insured": true,
    "exposure_units": {
      "location": {
        "buildings": true
      }
    }
  },
  "unwind": [
    "exposure_units",
    "exposure_units.location.buildings"
  ],
  "filter": {
    "$and": [
      {
        "exposure_units.location.hazard_tags": {
          "$in": ["hurricane", "flood", "earthquake", "wildfire"]
        }
      },
      {
        "exposure_units.location.buildings.sprinklered": false
      },
      {
        "exposure_units.location.buildings.year_built": {
          "$lte": 1990
        }
      }
    ]
  },
  "over": ["exposure_units.location.state"],
  "select": {
    "exposure_units": {
      "location": {
        "state": true
      }
    },
    "accounts": {
      "$countDistinct": "insured.id"
    },
    "policies": {
      "$countDistinct": "id"
    },
    "buildings": {
      "$count": true
    },
    "exposed": {
      "tiv": {
        "$sum": "exposure_units.location.buildings.tiv"
      },
      "largest": {
        "$max": "exposure_units.location.buildings.tiv"
      }
    }
  },
  "sort": [
    { "field": "exposed.tiv", "direction": "desc" }
  ]
}
```
