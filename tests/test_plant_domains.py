import copy

import pytest

from app.plant.loader import DEFAULT_DOMAIN, PlantConfigError, load_plant


def two_domain_config():
    # Nodes deliberately interleave the domains: config order is not domain order.
    return {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 50.0, "domain": "liquid"},
            {"id": "N-03", "boundary": True, "pressure": 50.0, "domain": "gas"},
            {"id": "N-02", "boundary": True, "pressure": 60.0, "domain": "liquid"},
            {"id": "N-04", "boundary": True, "pressure": 200.0, "domain": "gas"},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-01",
                "node_out": "N-02",
                "design": {"shutoff_pressure_rise": 90.0, "max_flow": 1000.0},
            },
            {
                "tag": "K-101",
                "type": "compressor",
                "node_in": "N-03",
                "node_out": "N-04",
                "design": {},
            },
        ],
    }


def legacy_config():
    return {
        "nodes": [
            {"id": "N-01", "boundary": True, "pressure": 50.0},
            {"id": "N-02", "boundary": False, "pressure": 60.0},
            {"id": "N-03", "boundary": True, "pressure": 875.0},
        ],
        "equipment": [
            {
                "tag": "P-101",
                "type": "pump",
                "node_in": "N-01",
                "node_out": "N-02",
                "design": {},
            },
            {
                "tag": "K-101",
                "type": "compressor",
                "node_in": "N-02",
                "node_out": "N-03",
                "design": {},
            },
        ],
    }


def rejected(config):
    with pytest.raises(PlantConfigError) as raised:
        load_plant(config)

    return raised.value.errors


# Partition


def test_two_domains_load_into_two_topologies():
    plant = load_plant(two_domain_config())

    assert list(plant.topologies) == ["liquid", "gas"]
    assert set(plant.topologies["liquid"].nodes) == {"N-01", "N-02"}
    assert set(plant.topologies["gas"].nodes) == {"N-03", "N-04"}
    assert list(plant.topologies["liquid"].branches) == ["B-P-101"]
    assert list(plant.topologies["gas"].branches) == ["B-K-101"]


def test_plant_nodes_are_flat_and_in_config_order():
    plant = load_plant(two_domain_config())

    assert list(plant.nodes) == ["N-01", "N-03", "N-02", "N-04"]
    assert plant.nodes["N-03"] is plant.topologies["gas"].node("N-03")


def test_topologies_order_follows_first_appearance_across_loads():
    config = two_domain_config()

    orders = [list(load_plant(copy.deepcopy(config)).topologies) for _ in range(3)]

    assert orders == [["liquid", "gas"]] * 3

    config["nodes"].reverse()

    assert list(load_plant(config).topologies) == ["gas", "liquid"]


def test_undeclared_nodes_are_one_default_domain():
    plant = load_plant(legacy_config())

    assert list(plant.topologies) == [DEFAULT_DOMAIN]
    assert plant.topology is plant.topologies[DEFAULT_DOMAIN]


# Plant.topology


def test_topology_property_raises_on_a_multi_domain_plant():
    plant = load_plant(two_domain_config())

    with pytest.raises(ValueError, match="Plant.topologies"):
        plant.topology


def test_topology_property_names_the_domains_when_it_raises():
    plant = load_plant(two_domain_config())

    with pytest.raises(ValueError, match="liquid.*gas"):
        plant.topology


def test_single_declared_domain_still_has_a_topology():
    config = legacy_config()

    for node in config["nodes"]:
        node["domain"] = "liquid"

    plant = load_plant(config)

    assert list(plant.topologies) == ["liquid"]
    assert plant.topology is plant.topologies["liquid"]


# Rejections


def test_branch_across_domains_is_rejected_naming_the_equipment():
    config = two_domain_config()
    config["equipment"][0]["node_out"] = "N-04"

    errors = rejected(config)

    assert len(errors) == 1
    assert errors[0].startswith("$.equipment[0]")
    assert "P-101" in errors[0]
    assert "'N-01'" in errors[0] and "'liquid'" in errors[0]
    assert "'N-04'" in errors[0] and "'gas'" in errors[0]


def test_partial_declaration_does_not_inherit_a_neighbours_domain():
    config = legacy_config()
    config["nodes"][0]["domain"] = "gas"

    errors = rejected(config)

    assert len(errors) == 1
    assert errors[0].startswith("$.equipment[0]")
    assert f"'{DEFAULT_DOMAIN}'" in errors[0]
    assert "no domain was declared for 'N-02'" in errors[0]
    assert "no domain was declared for 'N-01'" not in errors[0]


def test_explicit_default_domain_agrees_with_undeclared_nodes():
    config = legacy_config()

    for node in config["nodes"]:
        node["domain"] = DEFAULT_DOMAIN

    plant = load_plant(config)

    assert list(plant.topologies) == [DEFAULT_DOMAIN]


def test_domain_without_a_boundary_is_rejected():
    config = two_domain_config()

    for node in config["nodes"]:
        if node["domain"] == "gas":
            node["boundary"] = False

    errors = rejected(config)

    assert len(errors) == 1
    assert "'gas'" in errors[0]
    assert "no boundary node" in errors[0]


def test_single_domain_without_a_boundary_keeps_its_global_wording():
    config = legacy_config()

    for node in config["nodes"]:
        node["boundary"] = False

    assert rejected(config) == [
        "$.nodes: no boundary node — nothing anchors the pressure field",
    ]


def test_disconnected_domain_is_rejected_naming_the_domain():
    config = two_domain_config()
    config["nodes"].append(
        {"id": "N-05", "boundary": True, "pressure": 80.0, "domain": "gas"},
    )

    errors = rejected(config)

    assert len(errors) == 1
    assert "domain 'gas'" in errors[0]
    assert "not one connected piece" in errors[0]
    assert "N-05" in errors[0]


def test_two_domains_are_not_reported_as_disconnected_from_each_other():
    load_plant(two_domain_config())


def test_single_domain_disconnection_keeps_its_wording():
    config = legacy_config()
    config["nodes"].append({"id": "N-09", "boundary": False, "pressure": 1.0})

    errors = rejected(config)

    assert len(errors) == 1
    assert errors[0].startswith("$.nodes: the graph is not one connected piece")


def test_branchless_domain_is_legal_with_a_boundary():
    config = two_domain_config()
    config["nodes"].append(
        {"id": "N-09", "boundary": True, "pressure": 30.0, "domain": "vent"},
    )

    plant = load_plant(config)

    assert list(plant.topologies) == ["liquid", "gas", "vent"]
    assert not plant.topologies["vent"].branches


def test_branchless_domain_without_a_boundary_is_rejected():
    config = two_domain_config()
    config["nodes"].append(
        {"id": "N-09", "boundary": False, "pressure": 30.0, "domain": "vent"},
    )

    errors = rejected(config)

    assert len(errors) == 1
    assert "'vent'" in errors[0]


def test_every_domain_problem_is_reported_together():
    config = two_domain_config()
    config["equipment"][0]["node_out"] = "N-04"

    for node in config["nodes"]:
        if node["id"] == "N-03":
            node["boundary"] = False

    for node in config["nodes"]:
        if node["id"] == "N-04":
            node["boundary"] = False

    assert len(rejected(config)) == 2


def test_empty_domain_string_is_rejected_by_the_schema():
    config = legacy_config()
    config["nodes"][0]["domain"] = ""

    errors = rejected(config)

    assert any("domain" in error for error in errors)


# Round trip


def test_legacy_round_trip_is_exact_and_emits_no_domain():
    config = legacy_config()

    plant = load_plant(config)

    assert plant.to_config() == config
    assert all("domain" not in node for node in plant.to_config()["nodes"])


def test_explicit_domain_round_trip_is_exact():
    config = two_domain_config()

    first = load_plant(config)
    second = load_plant(first.to_config())

    assert first.to_config() == config
    assert second.to_config() == config
    assert list(second.topologies) == list(first.topologies)


def test_partial_declaration_round_trips_without_inventing_domains():
    config = legacy_config()
    config["nodes"][0]["domain"] = DEFAULT_DOMAIN

    plant = load_plant(config)
    out = plant.to_config()

    assert out == config
    assert "domain" not in out["nodes"][1]


def test_equipment_round_trips_in_config_order_across_domains():
    config = two_domain_config()
    config["equipment"].reverse()

    plant = load_plant(config)

    assert [item["tag"] for item in plant.to_config()["equipment"]] == [
        "K-101",
        "P-101",
    ]
