"""Small durable job registry shared by the console and webhook processes."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


class BusyError(ValueError):
    pass


class JobStore:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "jobs.sqlite3"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, created TEXT NOT NULL, state TEXT NOT NULL,
                mode TEXT NOT NULL, scenario TEXT NOT NULL, delivery TEXT UNIQUE,
                incident TEXT, metadata TEXT NOT NULL, error TEXT NOT NULL DEFAULT '')""")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_active ON jobs((1)) WHERE state='running'")
            db.execute("""CREATE TABLE IF NOT EXISTS publications (
                incident TEXT PRIMARY KEY, state TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
                error TEXT NOT NULL DEFAULT '')""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        return result

    def create(self, job_id, mode, scenario, metadata, delivery=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if delivery:
                previous = db.execute("SELECT * FROM jobs WHERE delivery=?", (delivery,)).fetchone()
                if previous:
                    return self.decode(previous), True
            try:
                db.execute("INSERT INTO jobs(id,created,state,mode,scenario,delivery,metadata) VALUES(?,?,?,?,?,?,?)",
                           (job_id, datetime.now(timezone.utc).isoformat(), "running", mode, scenario, delivery,
                            json.dumps(metadata)))
            except sqlite3.IntegrityError as error:
                raise BusyError("An investigation is already running. Retry this delivery later.") from error
        return self.get(job_id), False

    def get(self, job_id):
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def latest(self):
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 1").fetchone())

    def for_incident(self, incident):
        with self.connect() as db:
            return self.decode(db.execute("SELECT * FROM jobs WHERE incident=?", (incident,)).fetchone())

    def update(self, job_id, *, incident=None, state=None, error=None, metadata=None):
        updates = {key: value for key, value in {"incident": incident, "state": state, "error": error,
                   "metadata": json.dumps(metadata) if metadata is not None else None}.items() if value is not None}
        if updates:
            with self.connect() as db:
                db.execute("UPDATE jobs SET " + ",".join(key + "=?" for key in updates) + " WHERE id=?",
                           [*updates.values(), job_id])

    def abandon(self, job_id):
        """Explicit operator recovery after stopping the crashed worker process."""
        self.update(job_id, state="failed", error="Operator marked an interrupted job failed; no automatic replay.")

    def publication(self, incident):
        with self.connect() as db:
            row = db.execute("SELECT * FROM publications WHERE incident=?", (incident,)).fetchone()
            return {**dict(row), "result": json.loads(row["result"])} if row else None

    def claim_publication(self, incident):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM publications WHERE incident=?", (incident,)).fetchone()
            if row and row["state"] == "publishing":
                raise BusyError("This incident is already being published.")
            db.execute("INSERT INTO publications(incident,state) VALUES(?, 'publishing') "
                       "ON CONFLICT(incident) DO UPDATE SET state='publishing',error=''", (incident,))

    def finish_publication(self, incident, result=None, error=""):
        with self.connect() as db:
            db.execute("UPDATE publications SET state=?,result=?,error=? WHERE incident=?",
                       ("failed" if error else "created", json.dumps(result or {}), error, incident))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Recover an interrupted job after stopping its worker.")
    parser.add_argument("--state-dir", type=Path, default=Path(".ui-runs/state"))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--abandon", help="Job ID; stop the original worker first")
    action.add_argument("--reset-publication", help="Incident ID; stop the original publisher first")
    arguments = parser.parse_args()
    store = JobStore(arguments.state_dir)
    if arguments.abandon:
        store.abandon(arguments.abandon)
    else:
        store.finish_publication(arguments.reset_publication, error="Operator reset an interrupted publisher")
