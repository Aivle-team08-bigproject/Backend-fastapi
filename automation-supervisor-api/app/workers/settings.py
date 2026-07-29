from arq.connections import RedisSettings

from app.core.config import settings
from app.workers.tasks import run_stage_worker, run_supervisor_worker


class WorkerSettings:
    """One Redis consumer can execute every Worker role."""

    functions = [run_supervisor_worker, run_stage_worker]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 4
    job_timeout = 60 * 30
    keep_result = 60 * 60
