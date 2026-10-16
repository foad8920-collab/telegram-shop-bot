import asyncio
from bot.database.main import Database
from sqlalchemy import text

async def main():
    async with Database().session() as s:
        r = await s.execute(text("SELECT id, name, api_url, api_key FROM suppliers ORDER BY id"))
        for row in r:
            key = (row[3] or "")[:20]
            print(f"ID={row[0]} | name={row[1]} | url={row[2]} | key={key}...")

asyncio.run(main())
