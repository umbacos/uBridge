"""Footer slots are independent: Items for overflow and Page arrows for multiple pages."""

import asyncio

import pytest
from tjc_display.controller import Controller
from tjc_display.model import Entity, Item, Page
from tjc_display.protocol import Frame
from tjc_display.render import render

NAVIGATION = {"previous", "next", "sheet"}


def has_counter(screen):
    return any(command.startswith("xstr 430,5,42,30,") for command in screen.blocks["header"])


@pytest.mark.parametrize("count", [1, 2, 3])
@pytest.mark.parametrize("idle", [False, True])
def test_footer_depends_on_page_count_and_counter_keeps_configured_count(count, idle):
    pages = [
        Page("home/main", "Home", (Item("sensor.load", kind="gauge"),)),
        Page("home/extra", "Extra", ()),
    ]
    screen = render(
        pages,
        {"sensor.load": Entity("sensor.load", "50")},
        dashboard_count=count,
        idle_dashboard=idle,
    )
    visible = not idle
    events = {region.event for region in screen.regions}
    assert ({"previous", "next"} <= events) == visible
    assert "sheet" not in events
    assert has_counter(screen) == (count > 1 and not idle)
    assert f"fill 0,39,480,{189 if visible else 233},6371" in screen.commands


def test_multiple_views_get_page_arrows_but_unavailable_pages_do_not():
    pages = [Page("home/main", "Home", ()), Page("home/extra", "Extra", ())]
    screen = render(pages, {})
    assert not has_counter(screen)
    assert {"previous", "next"} <= {region.event for region in screen.regions}
    assert not any(region.event == "sheet" for region in screen.regions)
    # The counter retains its configuration rule, but missing pages create no buttons.
    screen = render(pages[:1], {}, dashboard_count=2)
    assert has_counter(screen)
    assert not any(region.event in NAVIGATION for region in screen.regions)


def test_entity_detail_back_remains_available_without_dashboard_navigation():
    pages = [Page("home/main", "Home", (Item("switch.room"),))]
    screen = render(
        pages,
        {"switch.room": Entity("switch.room", "off")},
        detail="switch.room",
        dashboard_count=1,
    )
    assert not has_counter(screen)
    assert any(region.event == "back" for region in screen.regions)
    assert not any(region.event in NAVIGATION for region in screen.regions)
    idle = render(
        pages,
        {"switch.room": Entity("switch.room", "off")},
        detail="switch.room",
        dashboard_count=2,
        idle_dashboard=True,
    )
    assert not any(region.event in NAVIGATION | {"back", "options"} for region in idle.regions)


async def test_idle_transition_hides_navigation_and_wake_restores_it():
    class Panel:
        connected = True

        async def draw(self, commands):
            pass

    async def service(action):
        pass

    controller = Controller(Panel(), service, lambda eid: None)
    controller.dashboard_count = 2
    controller.update_pages([Page("home/main", "Home", ()), Page("climate/main", "Climate", ())])
    controller.configure_idle_pages(
        [Page("idle/main", "Idle", ()), Page("idle/extra", "Idle extra", ())]
    )
    controller.start()
    try:
        await asyncio.sleep(0.12)
        assert has_counter(controller._screen)
        assert {"previous", "next"} <= {region.event for region in controller._screen.regions}
        controller.idle_dashboard(True)
        await asyncio.sleep(0.12)
        assert not has_counter(controller._screen)
        assert not any(region.event in NAVIGATION for region in controller._screen.regions)
        controller.idle_dashboard(False)
        await asyncio.sleep(0.12)
        assert has_counter(controller._screen)
        assert {"previous", "next"} <= {region.event for region in controller._screen.regions}
    finally:
        await controller.stop()


@pytest.mark.parametrize(
    "page_count,entity_count,expected",
    [
        (1, 2, set()),
        (1, 7, {"sheet"}),
        (2, 2, {"previous", "next"}),
        (2, 7, {"previous", "next", "sheet"}),
    ],
)
def test_footer_labels_and_touch_targets_occupy_only_their_needed_slots(
    page_count, entity_count, expected
):
    items = tuple(Item(f"sensor.item{i}") for i in range(entity_count))
    pages = [Page(f"dashboard{i}/home", f"Dashboard {i}", items) for i in range(page_count)]
    screen = render(pages, {})
    regions = {r.event: r for r in screen.regions if r.event in NAVIGATION}
    assert regions.keys() == expected
    slots = {"previous": (8, 106), "sheet": (122, 236), "next": (366, 106)}
    for event, region in regions.items():
        assert (region.x, region.w) == slots[event] and region.y == 228
    labels = " ".join(screen.blocks["navigation"])
    assert ("< Page" in labels) == ("previous" in expected)
    assert ("Page >" in labels) == ("next" in expected)
    assert ("Items " in labels) == ("sheet" in expected)
    idle = render(pages, {}, idle_dashboard=True)
    assert not any(r.event in NAVIGATION for r in idle.regions)


async def test_items_button_reaches_overflow_entities_with_only_one_page():
    class Panel:
        connected = True

        async def draw(self, commands):
            pass

    async def service(action):
        pass

    entities = {f"sensor.item{i}": Entity(f"sensor.item{i}", str(i)) for i in range(7)}
    controller = Controller(Panel(), service, entities.get)
    controller.dashboard_count = 1
    controller.update_pages([Page("home/main", "Home", tuple(Item(eid) for eid in entities))])
    controller.start()
    try:
        await asyncio.sleep(0.12)
        assert {r.event for r in controller._screen.regions} & NAVIGATION == {"sheet"}
        controller.frame(Frame(b"\x67\x00\xe6\x00\xf5\x01"))
        controller.frame(Frame(b"\x67\x00\xe6\x00\xf5\x00"))
        await asyncio.sleep(0.12)
        assert controller.sheet == 1
        assert any('"sensor.item6"' in c for c in controller._screen.commands)
        assert not any(r.event in {"previous", "next"} for r in controller._screen.regions)
    finally:
        await controller.stop()
