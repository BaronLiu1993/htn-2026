from backend.app.demo_federato import query_demo


def test_demo_query_executes_nested_expansion_and_projection():
    result = query_demo(
        {
            "resource": "Submission",
            "where": {"id": "101"},
            "expand": {"policy": {"buildings": True}},
            "select": {
                "id": True,
                "policy": {
                    "premium": True,
                    "buildings": {"id": True, "year_built": True},
                },
            },
            "pagination": {"limit": 10, "offset": 0},
        }
    )

    assert result["total"] == 1
    assert result["records"][0]["policy"]["premium"] == 92_000
    assert len(result["records"][0]["policy"]["buildings"]) == 2


def test_demo_query_executes_unwind_filter_grouping_and_reductions():
    result = query_demo(
        {
            "resource": "Policy",
            "where": {"id": "101"},
            "expand": {"buildings": True},
            "unwind": ["buildings"],
            "filter": {"buildings.year_built": {"$gte": 2010}},
            "over": ["id"],
            "select": {
                "id": True,
                "building_count": {"$count": True},
                "building_tiv": {"$sum": "buildings.tiv"},
            },
            "pagination": {"limit": 10, "offset": 0},
        }
    )

    assert result["total"] == 1
    assert result["groups"] == [
        {"id": "101", "building_count": 2, "building_tiv": 78_000_000}
    ]
