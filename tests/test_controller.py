import asyncio

from tjc_hmi.controller import Controller
from tjc_hmi.model import Entity, Item, Page
from tjc_hmi.protocol import Frame
from tjc_hmi.render import render


class Panel:
    connected = True

    def __init__(self):
        self.draws = []

    async def draw(self, commands):
        self.draws.append(commands)


def touch(x, y, pressed):
    return Frame(bytes([0x67]) + x.to_bytes(2, "big") + y.to_bytes(2, "big") + bytes([pressed]))


async def test_release_only_same_region_and_generation_guards():
    calls = []

    async def service(action):
        calls.append(action)

    state = Entity("switch.room", "off")
    panel = Panel()
    controller = Controller(panel, service, lambda eid: state)
    controller.update_pages(
        [Page("tjc-1/home", "TJC1", (Item("switch.room"),)), Page("tjc-2/home", "TJC2", ())]
    )
    controller.start()
    try:
        await asyncio.sleep(0.12)
        controller.frame(touch(30, 60, 0))  # release without press
        controller.frame(touch(30, 60, 1))
        await asyncio.sleep(0.01)
        assert not calls
        controller.frame(touch(30, 60, 0))
        await asyncio.sleep(0.12)
        assert len(calls) == 1 and calls[0].service == "toggle"
        controller.frame(touch(30, 60, 1))
        controller.frame(touch(300, 60, 0))  # drag off tile
        await asyncio.sleep(0.02)
        assert len(calls) == 1
        controller.frame(touch(410, 245, 1))
        controller.frame(touch(410, 245, 0))
        await asyncio.sleep(0.12)
        assert controller.page == 1
        assert len(calls) == 1
    finally:
        await controller.stop()


async def test_confirmation_rechecks_entity_and_state_updates_redraw():
    state = Entity("script.evening", "off")
    calls = []

    async def service(action):
        calls.append(action)

    panel = Panel()
    controller = Controller(panel, service, lambda eid: state)
    item = Item(
        "script.evening",
        tap_action={
            "action": "perform-action",
            "perform_action": "script.turn_on",
            "confirmation": True,
        },
    )
    controller.update_pages([Page("tjc-1/home", "TJC1", (item,))])
    region = render(controller.pages, {state.entity_id: state}).regions[0]
    await controller.activate(region)
    assert controller.confirm and not calls
    screen = render(controller.pages, {state.entity_id: state}, confirm=controller.confirm)
    state.state = "unavailable"
    await controller.activate(next(r for r in screen.regions if r.event == "confirmed"))
    assert not calls and controller.confirm is None
    assert controller.message == "Entity is unavailable"


async def test_redraw_coalescing_reconnect_and_page_deletion():
    state = Entity("sensor.temp", "21")

    async def service(action):
        pass

    panel = Panel()
    controller = Controller(panel, service, lambda eid: state)
    page = Page("tjc-1/home", "TJC1", (Item(state.entity_id),))
    controller.update_pages([page])
    controller.start()
    try:
        await asyncio.sleep(0.12)
        for _ in range(10):
            controller.invalidate()
        await asyncio.sleep(0.12)
        assert len(panel.draws) == 1
        state.state = "22"
        controller.invalidate()
        await asyncio.sleep(0.12)
        assert len(panel.draws) == 2
        controller.connection(True)
        await asyncio.sleep(0.12)
        assert len(panel.draws) == 3
        controller.update_pages([])
        await asyncio.sleep(0.12)
        assert "Select dashboards" in " ".join(panel.draws[-1])
    finally:
        await controller.stop()
