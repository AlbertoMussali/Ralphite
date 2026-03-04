from __future__ import annotations

from rq import Worker

from app.jobs import DEFAULT_QUEUE_NAME, get_redis_connection


def run_worker() -> None:
    """Start the RQ worker process for configured queues."""
    worker = Worker([DEFAULT_QUEUE_NAME], connection=get_redis_connection())
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    run_worker()
