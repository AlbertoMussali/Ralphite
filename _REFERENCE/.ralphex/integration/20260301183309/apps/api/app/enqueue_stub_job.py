from __future__ import annotations

from app.jobs import enqueue_registered_job


def main() -> None:
    job = enqueue_registered_job("stub.echo", "compose-smoke")
    print(job.id)


if __name__ == "__main__":
    main()
