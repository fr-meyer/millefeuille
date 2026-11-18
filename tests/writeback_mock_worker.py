"""TEST ONLY. MockTransport inside the actual isolated worker, never production."""

import json
from pathlib import Path

import httpx


def run(state_path):
    from millefeuille.domain import writeback_http as h
    from millefeuille.domain.writeback_http_worker import _main

    path = Path(state_path)

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield self.raw

        def __init__(self, raw):
            self.raw = raw

    async def handler(request):
        assert path.stat().st_size < 65536
        fixture = json.loads(path.read_text())
        state, collection = fixture["state"], fixture["collection"]
        prefix = "users" if state["library_type"] == "user" else "groups"
        base = "https://api.zotero.org/" + prefix + "/" + state["library_id"]
        assert request.headers["zotero-api-key"] == "SYNTHETICSECRET"
        assert request.headers["cache-control"] == "no-cache, no-store"
        url = str(request.url)
        fixture["calls"].append([request.method, url])
        if url == base + "/collections/" + collection["key"]:
            assert request.method == "GET"
            version = collection["version"]
            data = {
                k: collection[k] for k in ("key", "version", "name", "parentCollection")
            }
            if fixture.get("collection_drift"):
                data["name"] = "Changed topic"
            raw = h._wire(
                {
                    "key": data["key"],
                    "version": version,
                    "library": {
                        "type": state["library_type"],
                        "id": int(state["library_id"]),
                    },
                    "data": data,
                }
            )
            status = 200
        else:
            assert url == base + "/items/" + state["key"]
            if request.method == "PATCH":
                assert request.headers["if-unmodified-since-version"] == str(
                    state["version"]
                )
                delta = json.loads(request.content)
                assert set(delta) == {"tags", "collections"}
                state["data"].update(delta)
                state["version"] += 1
                raw, status = b"", 204
            else:
                assert request.method == "GET"
                raw = h._wire(
                    {
                        "key": state["key"],
                        "version": state["version"],
                        "library": {
                            "type": state["library_type"],
                            "id": int(state["library_id"]),
                        },
                        "data": {
                            **state["data"],
                            "key": state["key"],
                            "version": state["version"],
                            "dateModified": "2026-11-16T00:00:00Z",
                        },
                    }
                )
                status = 200
            version = state["version"]
        path.write_text(json.dumps(fixture))
        return httpx.Response(
            status,
            headers={
                "content-type": "application/json",
                "last-modified-version": str(version),
            },
            stream=Stream(raw),
        )

    original = h._HTTPBoundary.__post_init__

    def synthetic_init(self):
        original(self)
        self.transport = httpx.MockTransport(handler)

    h._HTTPBoundary.__post_init__ = synthetic_init
    _main()
