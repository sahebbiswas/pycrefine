import asyncio


async def fetch(n):
    await asyncio.sleep(0)
    return n * 2


async def gather_all(values):
    results = []
    for value in values:
        results.append(await fetch(value))
    return results


async def agen(limit):
    for i in range(limit):
        yield i


async def collect():
    return [i async for i in agen(3)]


print(asyncio.run(gather_all([1, 2, 3])), asyncio.run(collect()))
RESULT = asyncio.run(fetch(21))
