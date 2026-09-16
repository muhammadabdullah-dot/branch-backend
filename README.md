# D.Marina — Branch Server

The server that runs inside the shop. It owns the branch's own trading: billing and the till, the
counters and who is on them, stock and receiving, the branch's books, its staff and their access —
and it keeps working whether or not the internet is up. It also serves the built
[branch app](https://github.com/raza722/branch-app) on the same port, so a till opens one address and
gets everything.

FastAPI + Tortoise ORM + SQLite, Python 3.14. Runs on port 4174.

```bash
.venv/Scripts/python.exe -m app.main     # settings come from .env
.venv/Scripts/aerich.exe upgrade         # apply schema migrations (back the database up first)
```

**[PROJECT_STATUS.md](PROJECT_STATUS.md) describes the whole system** — this server, head office, both
apps, how they talk to each other, what is live today and what is not built yet. Read that first.

The database (`branch.db`), its backups, logs and `.env` are not in git and never should be.
