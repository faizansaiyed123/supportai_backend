"""
Procrastinate app instance (D008). Single shared instance imported by both the API process
(to enqueue jobs) and the worker process (to run them). Uses the same Postgres database as
the rest of the app - see docker-compose.yml's `worker` service command.
"""
from procrastinate import App, PsycopgConnector

from app.core.config import get_settings

settings = get_settings()

procrastinate_app = App(
    connector=PsycopgConnector(conninfo=settings.database_url_sync, kwargs={})
)

# Import task modules so they register on procrastinate_app when it's loaded.
# Without this, the worker process won't discover the tasks.
import app.workers.ingestion  # noqa: F401,E402
