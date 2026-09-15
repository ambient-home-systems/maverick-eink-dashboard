"""An identical frame is not worth an e-ink refresh — unless nobody can serve it.

Most renders of most dashboards change nothing, and a refresh costs orders of
magnitude more than the render that produced it, so `Engine.render` compares the
new frame's checksum against `last_checksum` and skips delivery when they match.
The checksum is taken over the palette indices, not the screenshot, so sub-pixel
noise upstream does not defeat it.

The guard is the interesting half. A pull transport delivers nothing: it puts
the frame where a sleeping device will fetch it. Skipping on an empty frame
store would therefore leave that device fetching 404s, so the skip only applies
when the frame is *servable* — the transport pushes, or the store has it.
"""

from __future__ import annotations


async def test_an_unchanged_frame_is_not_pushed_twice(make_engine, legible_image) -> None:
    harness = await make_engine(legible_image, pushes=True)

    first = await harness.engine.render("kitchen", trigger="schedule")
    second = await harness.engine.render("kitchen", trigger="schedule")

    assert first.skipped is False
    assert first.frame is not None and second.frame is not None
    assert first.frame.checksum == second.frame.checksum, "the fixture is not deterministic"

    assert second.skipped is True
    assert second.ok is True, "a skip is a success, not a failure"
    assert second.reason == "frame unchanged"
    assert harness.delivered == 1
    assert harness.state.skip_count == 1
    assert harness.renderer.calls == 2, "the render still happens; only delivery is skipped"


async def test_an_unchanged_frame_is_still_published_for_a_pull_transport(
    make_engine, legible_image
) -> None:
    """With an empty store there is nothing for a waking device to fetch."""
    harness = await make_engine(legible_image, pushes=False)

    await harness.engine.render("kitchen", trigger="schedule")
    second = await harness.engine.render("kitchen", trigger="schedule")

    assert "kitchen" not in harness.engine.frames, "the double must not fill the store"
    assert second.skipped is False
    assert harness.delivered == 2
    assert harness.state.skip_count == 0


async def test_a_pull_transport_skips_once_the_frame_is_servable(
    make_engine, legible_image
) -> None:
    """The guard is about the store's contents, not about the transport kind."""
    harness = await make_engine(legible_image, pushes=False)

    first = await harness.engine.render("kitchen", trigger="schedule")
    assert first.frame is not None
    harness.engine.frames.put("kitchen", first.frame)

    second = await harness.engine.render("kitchen", trigger="schedule")

    assert second.skipped is True
    assert second.reason == "frame unchanged"
    assert harness.delivered == 1


async def test_force_delivers_an_unchanged_frame(make_engine, legible_image) -> None:
    harness = await make_engine(legible_image, pushes=True)

    await harness.engine.render("kitchen", trigger="schedule")
    forced = await harness.engine.render("kitchen", trigger="manual", force=True)

    assert forced.skipped is False
    assert forced.full_refresh is True, "a forced render is also a ghost-clearing one"
    assert harness.delivered == 2


async def test_skip_unchanged_can_be_turned_off_per_display(
    make_engine, legible_image
) -> None:
    harness = await make_engine(legible_image, pushes=True)
    harness.engine.config.display("kitchen").schedule.skip_unchanged = False

    await harness.engine.render("kitchen", trigger="schedule")
    second = await harness.engine.render("kitchen", trigger="schedule")

    assert second.skipped is False
    assert harness.delivered == 2
