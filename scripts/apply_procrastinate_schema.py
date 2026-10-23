import asyncio

from app.workers.app import procrastinate_app


async def main():
    async with procrastinate_app.open_async():
        await procrastinate_app.schema_manager.apply_schema_async()
        print("Procrastinate schema applied successfully")

asyncio.run(main())
