"""Small serial schedule; contains no provider clients or customer data."""
from dataclasses import dataclass, field


@dataclass
class Job:
    name: str
    interval: int
    kwargs: dict = field(default_factory=dict)
    due_at: float = 0
    requires_external_io: bool = True


def jobs(now):
    return [
        Job('refresh_pixel_page_catalog', 60, {}, now, requires_external_io=False),
        Job('retry_inbound_leads', 300, {'provider': 'meta'}, now + 300),
        Job('dispatch_lead_outbox', 60, {}, now + 60),
        # The command itself preserves the known-refusal retry cadence and
        # overdue reminder windows; only fresh queued delivery runs sooner.
        Job('send_lead_reminders', 60, {}, now + 60),
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
            if enabled or not job.requires_external_io:
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
