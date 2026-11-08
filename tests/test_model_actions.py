from tjc_hmi.actions import controls, default_action, explicit_action, step_value
from tjc_hmi.model import Entity, Item, dashboard_number, parse_dashboard
from tjc_hmi.render import render


def test_dashboard_selection_and_natural_sort_keys():
    assert [dashboard_number(s) for s in ("TJC1", "TJC10", "tjc2", "TJC0", "TJC1 test")] == [
        1,
        10,
        2,
        None,
        None,
    ]
    assert dashboard_number("WALL2", "WALL") == 2


def test_nested_sections_views_and_custom_cards_reported():
    config = {
        "views": [
            {
                "path": "home",
                "sections": [
                    {
                        "cards": [
                            {
                                "type": "grid",
                                "cards": [
                                    {"type": "tile", "entity": "light.room"},
                                    {
                                        "type": "entities",
                                        "entities": [
                                            "sensor.temp",
                                            {"entity": "switch.desk", "name": "Desk"},
                                            {"type": "divider"},
                                        ],
                                    },
                                    {
                                        "type": "custom:mushroom-light-card",
                                        "entity": "light.hidden",
                                    },
                                ],
                            }
                        ]
                    }
                ],
            },
            {"title": "Extra", "cards": [{"type": "thermostat", "entity": "climate.house"}]},
        ]
    }
    pages, warnings = parse_dashboard("tjc-1", "TJC1", config)
    assert pages[0].key == "tjc-1/home"
    assert [i.entity_id for i in pages[0].items] == ["light.room", "sensor.temp", "switch.desk"]
    assert pages[1].title == "TJC1 / Extra"
    assert any("custom:" in w for w in warnings)


def test_visibility_and_malformed_entities_are_not_exposed():
    pages, warnings = parse_dashboard(
        "tjc-1",
        "TJC1",
        {
            "views": [
                {
                    "cards": [
                        {"type": "conditional", "card": {"type": "tile", "entity": "light.secret"}},
                        {"type": "tile", "entity": "light.hidden", "visibility": []},
                        {
                            "type": "entities",
                            "entities": [
                                "bad entity",
                                {"entity": "switch.hidden", "visibility": []},
                            ],
                        },
                    ]
                }
            ]
        },
    )
    assert not pages[0].items
    assert len(warnings) == 4


def test_actions_use_exact_domains_targets_and_limits():
    assert default_action(Entity("scene.evening", "scening")).service == "turn_on"
    assert default_action(Entity("input_button.doorbell", "2020")).service == "press"
    number = Entity("input_number.level", "9.9", {"min": 0, "max": 10, "step": 0.2})
    assert step_value(number, None, 1) == 10
    assert step_value(Entity("number.n", "nan"), None, 1) is None
    thermostat = Entity(
        "climate.room",
        "heat",
        {"temperature": 22, "target_temp_step": 0.5, "supported_features": 1},
    )
    assert controls(thermostat)[0].data == {"temperature": 21.5}
    assert not controls(
        Entity("climate.room", "heat", {"target_temp_low": 18, "supported_features": 2})
    )
    assert not controls(Entity("cover.door", "closed", {"supported_features": 0}))
    assert [
        a.service for a in controls(Entity("cover.door", "closed", {"supported_features": 11}))
    ] == ["open_cover", "stop_cover", "close_cover"]


def test_stateless_entities_can_be_used_before_first_activation():
    from tjc_hmi.model import Page

    for domain in ("scene", "button", "input_button"):
        entity = Entity(f"{domain}.new", "unknown")
        assert entity.available and entity.value == "Ready"
        page = Page("tjc-1/home", "TJC1", (Item(entity.entity_id),))
        screen = render([page], {entity.entity_id: entity})
        assert screen.regions[0].action.service in {"turn_on", "press"}
        entity.state = "unavailable"
        assert not entity.available
    assert not Entity("sensor.new", "unknown").available


def test_explicit_service_and_confirmation():
    action = explicit_action(
        Item(
            "script.evening",
            tap_action={
                "action": "perform-action",
                "perform_action": "script.turn_on",
                "confirmation": {"text": "Evening?"},
            },
        )
    )
    assert action.domain == "script" and action.service == "turn_on"
    assert action.target == {"entity_id": "script.evening"}
    assert action.confirmation == "Evening?"
    assert explicit_action(Item("light.a", tap_action={"action": "none"})).service == ""


def test_malformed_actions_and_features_do_not_break_rendering():
    pages, warnings = parse_dashboard(
        "tjc-1",
        "TJC1",
        {
            "views": [
                {
                    "cards": [
                        {"type": "tile", "entity": "light.a", "tap_action": "invalid"},
                        {"type": "tile", "entity": "light.b", "tap_action": {"action": []}},
                    ]
                }
            ]
        },
    )
    assert len(pages[0].items) == 1 and warnings
    screen = render(pages, {"light.b": Entity("light.b", "on")})
    assert not screen.regions[0].action.service
    assert not controls(Entity("cover.test", "closed", {"supported_features": "broken"}))


def test_detail_controls_preserve_configured_confirmation():
    from tjc_hmi.model import Page

    entity = Entity("script.goodnight", "off")
    item = Item(
        entity.entity_id,
        tap_action={
            "action": "perform-action",
            "perform_action": "script.turn_on",
            "confirmation": {"text": "Good night?"},
        },
    )
    screen = render(
        [Page("tjc-1/home", "TJC1", (item,))], {entity.entity_id: entity}, detail=entity.entity_id
    )
    action = next(r.action for r in screen.regions if r.event == "action")
    assert action.service == "turn_on" and action.confirmation == "Good night?"


def test_pagination_bounds_and_unavailable_hit_regions():
    pages, _ = parse_dashboard(
        "tjc-1",
        "TJC1",
        {
            "views": [
                {"cards": [{"type": "entities", "entities": [f"switch.s{i}" for i in range(9)]}]}
            ]
        },
    )
    entities = {f"switch.s{i}": Entity(f"switch.s{i}", "off") for i in range(8)}
    screen = render(pages, entities, sheet=1)
    ids = {r.entity_id for r in screen.regions if r.entity_id}
    assert ids == {"switch.s6", "switch.s7"}
    assert all(
        0 <= r.x < 480 and 0 <= r.y < 272 and r.x + r.w <= 480 and r.y + r.h <= 272
        for r in screen.regions
    )
    assert all(len(c.encode("ascii")) <= 240 for c in screen.commands)
