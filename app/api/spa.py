"""Static files for the admin SPA: client-side routes and cache headers.

Starlette's StaticFiles knows files, not routes, so it answered every
screen of the dashboard except «/dashboard/» with 404: a reload on
/dashboard/money, a deep link, a tap on a push notification (the service
worker navigates to the notification's url), and the relaunch of the
installed PWA on the route it was left on. Each of those showed an empty
screen. Unknown paths without a file extension now serve index.html and
let the router decide — exactly what any SPA host does.

A missing *file* stays 404 on purpose. A stale build asks for a chunk
that no longer exists; answering with index.html would hand HTML to a
<script> tag and turn a clear 404 into a puzzling syntax error.

Caching: Vite fingerprints everything under assets/ (index-2hFOOvh5.js),
so those can be kept for a year and never re-fetched. index.html carries
the pointers to them and must always be revalidated, or the browser keeps
running a build whose chunks are gone. The mount used to send no
Cache-Control at all, so a phone re-validated every chunk on every load.
"""
from __future__ import annotations

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"


def _is_client_route(path: str) -> bool:
    """A screen of the SPA, not a file: no extension in the last segment."""
    return "." not in path.rsplit("/", 1)[-1]


class SpaStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        # StaticFiles signals a miss by raising, not by returning a 404.
        try:
            response = await super().get_response(path, scope)
        except HTTPException as miss:
            if miss.status_code != 404 or not _is_client_route(path):
                raise
            response = await super().get_response("index.html", scope)
        if response.status_code < 400:
            fingerprinted = path.startswith("assets/") and not _is_client_route(path)
            response.headers["Cache-Control"] = IMMUTABLE if fingerprinted else REVALIDATE
        return response
