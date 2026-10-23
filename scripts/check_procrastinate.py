import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url)

async def check():
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT typname FROM pg_type WHERE typname LIKE 'procrastinate%' ORDER BY typname"
        ))
        types = [r[0] for r in result]
        print('Procrastinate types:', types)
        result2 = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE tablename LIKE 'procrastinate%' OR tablename LIKE 'psts_%' "
                "ORDER BY tablename"
            )
        )
        tables = [r[0] for r in result2]
        print('Procrastinate/psts tables:', tables)
    await engine.dispose()

asyncio.run(check())
