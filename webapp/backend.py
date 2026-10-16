"""Small adapter around the unmodified, pinned upstream sources."""

import asyncio
import ipaddress
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

import aiohttp

UPSTREAM_PATH = Path(__file__).resolve().parent.parent / "vendor" / "upstream"
sys.path.insert(0, str(UPSTREAM_PATH))

from iSponsorBlockTV.api_helpers import ApiHelper  # noqa: E402


class RoutedSession:
    """Keep local discovery direct even when Internet requests need a proxy."""

    def __init__(self, session, config, panel):
        self.session = session
        self.proxies = getproxies() if config.use_proxy else {}
        self.proxy = panel.proxy_url if config.use_proxy else ""

    def proxy_for(self, url):
        parsed = urlsplit(str(url))
        host = parsed.hostname or ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            pass
        if host == "localhost" or host.endswith(".local") or proxy_bypass(host):
            return None
        return self.proxy or self.proxies.get(parsed.scheme)

    def get(self, url, **kwargs):
        return self.session.get(url, proxy=self.proxy_for(url), **kwargs)

    def post(self, url, **kwargs):
        return self.session.post(url, proxy=self.proxy_for(url), **kwargs)

    async def close(self):
        await self.session.close()

    @property
    def closed(self):
        return self.session.closed


@asynccontextmanager
async def helper(config, panel):
    async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=20)) as session:
        yield ApiHelper(config, RoutedSession(session, config, panel))


async def pair(config, panel, code):
    async with helper(config, panel) as api:
        return await api.pair_with_code(code)


async def search(config, panel, query):
    async with helper(config, panel) as api:
        return await api.search_channels(query)


async def discover(config, panel, active=False):
    results = []
    async with helper(config, panel) as api:
        iterator = api.discover_youtube_devices_dial(active=active)
        try:
            async with asyncio.timeout(35 if active else 12):
                async for device in iterator:
                    results.append(device)
        except TimeoutError:
            pass
        finally:
            await iterator.aclose()
    return results
