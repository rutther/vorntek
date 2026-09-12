"""Small serial schedule; contains no provider clients or customer data."""
from dataclasses import dataclass, field


@dataclass
class Job:
    name: str
    interval: int
    kwargs: dict = field(default_factory=dict)
    due_at: float = 0


def jobs(now):
    return [
        Job('retry_inbound_leads', 300, {'provider': 'meta'}, now + 300),
        Job('dispatch_lead_outbox', 300, {}, now + 300),
        Job('send_lead_reminders', 900, {}, now + 900),
    ]


def tick(schedule, *, now, enabled, execute, clock, force=False, stop_requested=None):
    results = []
    for job in schedule:
        if stop_requested is not None and stop_requested():
            break
        if not force and now < job.due_at:
            continue
        result = {'job': job.name, 'status': 'paused'}
        try:
            if enabled:
                execute(job)
                # Completed command != proof of successful external delivery.
                result['status'] = 'completed'
        except Exception as exc:
            result.update(status='failed', error_type=type(exc).__name__)
        finally:
            # No catch-up burst after downtime; next run is measured from finish.
            job.due_at = clock() + job.interval
        results.append(result)
    return results
