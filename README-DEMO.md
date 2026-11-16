# SupportAI isolated demo API

Run with `uvicorn app.demo_api:app --host 127.0.0.1 --port 8001` and open `/docs`.

All records are synthetic and kept in process memory. The demo entry point does not import `app.main`, PostgreSQL configuration, SQLAlchemy models, the background worker, local storage, Ollama, or any production service.

Demo organization: `demo`

Password for every demo account: `demo1234`
