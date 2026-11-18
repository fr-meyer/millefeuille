"""Optional Decisions transport: one POST, no OAuth, retries, proxies or redirects."""

import asyncio

ENDPOINT = "https://api.openai.com/v1/decisions"
MODEL = "gpt-6-luna"
REQUEST_CAP = 500_000
RESPONSE_CAP = 1_000_000


async def post_decision(payload: bytes, api_key: str) -> tuple[int, bytes]:
    """Return bounded exact response bytes; caller journals before entering."""
    import httpx

    if not api_key or len(payload) > REQUEST_CAP:
        raise ValueError("Decisions preflight failed")
    async with asyncio.timeout(60):
        async with (
            httpx.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
                timeout=30,
            ) as client,
            client.stream(
                "POST",
                ENDPOINT,
                content=payload,
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                    "Accept-Encoding": "identity",
                },
            ) as response,
        ):
            body = bytearray()
            async for chunk in response.aiter_raw():
                if len(body) + len(chunk) > RESPONSE_CAP:
                    raise ValueError("Decisions response exceeds limit")
                body.extend(chunk)
            return response.status_code, bytes(body)
