"""Headless browser lifecycle.

One Chromium process is shared by every display. Each render gets its own page
but reuses the browser context, because the context is what holds the Home
Assistant auth in ``localStorage`` and rebuilding it per render means a fresh
frontend bootstrap every time — several seconds on a Raspberry Pi.

Concurrency is capped by a semaphore. Rendering two 1872x1404 dashboards at
supersample 2 simultaneously is a ~250 MB spike, which is enough to get the
add-on OOM-killed on small hardware.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Browser, BrowserContext, Page

log = logging.getLogger(__name__)

#: Flags that matter for deterministic, headless, container-friendly capture.
CHROMIUM_ARGS = [
    "--disable-dev-shm-usage",      # /dev/shm is tiny in containers
    "--disable-gpu",
    "--no-sandbox",                 # required unrooted in most container runtimes
    "--hide-scrollbars",
    "--disable-features=IsolateOrigins,site-per-process",
    "--force-color-profile=srgb",   # otherwise capture colours drift per host
    "--disable-lcd-text",           # subpixel AA is meaningless on e-ink
    "--font-render-hinting=none",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
]


class BrowserPool:
    """Owns the Chromium process and hands out pages."""

    def __init__(
        self,
        max_concurrent: int = 2,
        headless: bool = True,
        executable_path: str | None = None,
    ) -> None:
        """
        ``executable_path`` (or ``$MAVERICK_CHROMIUM_PATH``) points at a Chromium
        binary Playwright did not install itself. Needed wherever Playwright
        ships no matching build — notably arm/aarch64 Home Assistant OS, where
        the distro's ``chromium`` package is the only option.
        """
        self._executable_path = executable_path or os.environ.get("MAVERICK_CHROMIUM_PATH")
        self._playwright: Any = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()
        self._headless = headless

    async def start(self) -> None:
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        launch: dict[str, Any] = {"headless": self._headless, "args": CHROMIUM_ARGS}
        if self._executable_path:
            launch["executable_path"] = self._executable_path
        try:
            self._browser = await self._playwright.chromium.launch(**launch)
        except Exception as exc:  # noqa: BLE001 - re-raised with an actionable message
            raise RuntimeError(
                f"Could not start Chromium: {exc}\n"
                "Run `playwright install chromium`, or point "
                "MAVERICK_CHROMIUM_PATH at an existing Chromium binary."
            ) from exc
        log.info(
            "chromium started (version %s%s)",
            self._browser.version,
            f", {self._executable_path}" if self._executable_path else "",
        )

    async def stop(self) -> None:
        for context in self._contexts.values():
            await context.close()
        self._contexts.clear()
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _context(
        self,
        key: str,
        viewport: tuple[int, int],
        scale: float,
        init_scripts: list[str],
        ignore_https_errors: bool,
    ) -> BrowserContext:
        """Get or build a context.

        Keyed by everything that cannot be changed after creation — viewport
        scale and init scripts among them — so a display whose geometry changes
        transparently gets a new context.
        """
        async with self._lock:
            existing = self._contexts.get(key)
            if existing is not None:
                return existing
            assert self._browser is not None, "BrowserPool.start() was not awaited"
            context = await self._browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]},
                device_scale_factor=scale,
                ignore_https_errors=ignore_https_errors,
                color_scheme="light",
                reduced_motion="reduce",
                # A real-ish UA avoids the frontend's unsupported-browser notice.
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Maverick/0.1"
                ),
            )
            for script in init_scripts:
                await context.add_init_script(script)
            self._contexts[key] = context
            return context

    async def drop_context(self, key: str) -> None:
        """Discard a cached context, e.g. after an auth failure."""
        async with self._lock:
            context = self._contexts.pop(key, None)
        if context is not None:
            await self._close(key, context)

    async def drop_contexts_for(self, display_id: str) -> int:
        """Discard every cached context belonging to one display.

        A context key is built in `src/maverick/render/dashboard.py` as
        ``f"{display.id}:{viewport}:{supersample}:{scripts}"``, so one display
        can hold several — one per geometry it has been rendered at. Removing or
        reconfiguring a display drops all of them, and deliberately leaves the
        browser running: Chromium takes seconds to relaunch, and every other
        panel is waiting on it. Returns how many were closed.
        """
        prefix = f"{display_id}:"
        async with self._lock:
            keys = [key for key in self._contexts if key.startswith(prefix)]
            contexts = [(key, self._contexts.pop(key)) for key in keys]
        for key, context in contexts:
            await self._close(key, context)
        return len(contexts)

    async def _close(self, key: str, context: BrowserContext) -> None:
        """Close one context, surviving a Chromium that has already gone.

        Closing is cleanup, and cleanup that raises would abandon the rest of
        it — the other contexts of a display being removed, the transport still
        to be stopped.
        """
        try:
            await context.close()
        except Exception as exc:  # noqa: BLE001 - a dead context is already gone
            log.warning("could not close the browser context %s: %s", key, exc)

    @asynccontextmanager
    async def page(
        self,
        key: str,
        viewport: tuple[int, int],
        scale: float = 1.0,
        init_scripts: list[str] | None = None,
        ignore_https_errors: bool = False,
    ):
        """Yield a page, holding a concurrency slot for its lifetime."""
        await self.start()
        async with self._semaphore:
            context = await self._context(
                key, viewport, scale, init_scripts or [], ignore_https_errors
            )
            page: Page = await context.new_page()
            # Chromium reuses the context viewport, but an explicit set keeps a
            # recycled context honest when a display is resized at runtime.
            await page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
            try:
                yield page
            finally:
                await page.close()


__all__ = ["BrowserPool", "CHROMIUM_ARGS"]
