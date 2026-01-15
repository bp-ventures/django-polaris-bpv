# Django 6.0 Tasks Framework Proposal

**Status:** Proposal
**Created:** January 2026
**Django Version:** 6.0+ (or 5.2+ with `django-tasks` backport)

## Summary

Django 6.0 introduces a native Tasks framework (`django.tasks`) for running background work. This proposal explores how it could make Polaris more scalable by replacing the current single-instance architecture with a cron + tasks pattern.

---

## Current Architecture

Polaris uses **4 long-running Django management commands** for background processing:

| Command | Purpose | Pattern |
|---------|---------|---------|
| `process_pending_deposits` | Submit deposits to Stellar | Async polling + in-memory `asyncio.Queue` |
| `watch_transactions` | Stream incoming Stellar transactions | SSE streaming from Stellar Horizon |
| `execute_outgoing_transactions` | Execute off-chain withdrawals | Sync polling loop |
| `poll_outgoing_transactions` | Poll external transfer status | Sync polling loop |

### Key Characteristics

- **No Celery/Redis** - Uses in-memory asyncio queues + database for state
- **Database is source of truth** - All transaction state in PostgreSQL/SQLite
- **Distributed locking** - `PolarisHeartbeat` model ensures single instance for `process_pending_deposits`
- **Long-lived processes** - Each command runs indefinitely
- **Per-process deployment** - Typically 4 containers/processes in production

---

## The Scalability Problem

`process_pending_deposits` uses distributed locking to ensure only **ONE instance runs globally**:

```
┌────────────────────────────────────────┐
│  process_pending_deposits              │
│  (ONE instance, heartbeat lock)        │
│                                        │
│  polls → checks → submits (serial)     │
└────────────────────────────────────────┘
         ⚠️ Single point of failure
         ⚠️ Can't scale horizontally
         ⚠️ In-memory queues lost on crash
```

### Current Internal Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  process_pending_deposits (single long-running process)         │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │ poll_rails   │───▶│ check_accts  │───▶│ submit_tx    │      │
│  │ (continuous) │    │ (continuous) │    │ (continuous) │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│         │                   │                   │               │
│         └───────── asyncio.Queue ──────────────┘               │
└─────────────────────────────────────────────────────────────────┘
```

**Limitations:**
- All queues are in-memory (lost on process restart)
- Single-instance deployment required (heartbeat lock)
- In-memory locks don't scale across processes
- Limited observability (manual log inspection)

---

## Proposed Architecture: Cron + Tasks

Decouple **detection** from **execution** using Django 6.0 Tasks:

```
┌─────────────────┐      ┌─────────────────────────────────┐
│  Cron (every 10s)│      │  Task Workers (N instances)     │
│                 │      │                                 │
│  SELECT pending │      │  ┌─────────┐ ┌─────────┐       │
│  transactions   │─────▶│  │ task 1  │ │ task 2  │ ...   │
│                 │      │  │ tx:abc  │ │ tx:def  │       │
│  enqueue each   │      │  └─────────┘ └─────────┘       │
└─────────────────┘      └─────────────────────────────────┘
   ✅ Lightweight           ✅ Horizontally scalable
   ✅ Stateless             ✅ Parallel processing
                            ✅ Auto-retry on failure
```

### Benefits

| Aspect | Current | Cron + Tasks |
|--------|---------|--------------|
| **Scalability** | 1 instance max | N workers |
| **Failure handling** | Manual restart | Auto-retry |
| **Observability** | Log parsing | Task status/history |
| **Queue persistence** | In-memory (lost on crash) | Database-backed |
| **Heartbeat locking** | Required | Not needed |
| **Complexity** | Asyncio queues | Simpler per-task logic |

---

## Implementation Sketch

### Task Definitions

```python
# polaris/tasks.py
from django.tasks import task
from django.db import transaction as db_transaction
from polaris.models import Transaction

@task(retries=3, queue_name="deposits")
def process_deposit(transaction_id):
    """Process a single deposit - runs in parallel workers."""
    with db_transaction.atomic():
        tx = Transaction.objects.select_for_update().get(id=transaction_id)
        if tx.submission_status != "ready":
            return  # Already processed or not ready

        # Check account exists on Stellar
        # Check trustline established
        # Submit transaction to Stellar
        ...

@task(retries=3, queue_name="withdrawals")
def execute_withdrawal(transaction_id):
    """Execute a single withdrawal off-chain."""
    tx = Transaction.objects.select_for_update().get(id=transaction_id)
    if tx.status != "pending_anchor":
        return

    # Call RailsIntegration.execute_outgoing_transaction()
    ...

@task(retries=2, queue_name="polling")
def poll_withdrawal_status(transaction_id):
    """Poll external status for a single withdrawal."""
    tx = Transaction.objects.get(id=transaction_id)
    # Call RailsIntegration.poll_outgoing_transactions()
    ...
```

### Cron Job (Enqueuer)

```python
# polaris/management/commands/enqueue_pending_tasks.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from polaris.models import Transaction
from polaris.tasks import process_deposit, execute_withdrawal

class Command(BaseCommand):
    help = "Enqueue pending transactions as tasks (run via cron)"

    def handle(self, *args, **options):
        # Enqueue pending deposits
        deposits = Transaction.objects.filter(
            kind=Transaction.KIND.deposit,
            submission_status="ready",
            queued_at__isnull=True,
        )
        for tx in deposits:
            tx.queued_at = timezone.now()
            tx.save(update_fields=["queued_at"])
            process_deposit.enqueue(transaction_id=str(tx.id))

        # Enqueue pending withdrawals
        withdrawals = Transaction.objects.filter(
            kind=Transaction.KIND.withdrawal,
            status=Transaction.STATUS.pending_anchor,
            queued_at__isnull=True,
        )
        for tx in withdrawals:
            tx.queued_at = timezone.now()
            tx.save(update_fields=["queued_at"])
            execute_withdrawal.enqueue(transaction_id=str(tx.id))

        self.stdout.write(f"Enqueued {deposits.count()} deposits, {withdrawals.count()} withdrawals")
```

### Django Settings

```python
# settings.py
TASKS = {
    "default": {
        # Option 1: Database backend (no external deps)
        "BACKEND": "django_tasks.backends.database.DatabaseBackend",

        # Option 2: Immediate (dev only - runs synchronously)
        # "BACKEND": "django.tasks.backends.immediate.ImmediateBackend",
    }
}
```

### Deployment

```bash
# Cron job (every 10 seconds)
*/10 * * * * python manage.py enqueue_pending_tasks

# Task workers (scale as needed)
python manage.py db_worker --queue deposits --concurrency 4
python manage.py db_worker --queue withdrawals --concurrency 2
python manage.py db_worker --queue polling --concurrency 2

# Keep existing streaming command (can't be task-based)
python manage.py watch_transactions
```

---

## Migration Path by Command

| Command | Recommendation | Notes |
|---------|----------------|-------|
| `process_pending_deposits` | ✅ **Replace** with cron + tasks | Best scalability gain |
| `execute_outgoing_transactions` | ✅ **Replace** with cron + tasks | Simple 1:1 mapping |
| `poll_outgoing_transactions` | ✅ **Replace** with cron + tasks | Simple 1:1 mapping |
| `watch_transactions` | ❌ **Keep as-is** | SSE streaming doesn't fit task model |

---

## Task Backend Options

Django 6.0 provides the API but not workers. Backend options:

### 1. `django-tasks` DatabaseBackend (Recommended)

- **Pros:** No external dependencies, uses existing database
- **Cons:** Lower throughput than Redis
- **Install:** `pip install django-tasks`
- **Worker:** `python manage.py db_worker`

### 2. Redis Backend

- **Pros:** High throughput, battle-tested
- **Cons:** Requires Redis infrastructure
- **Options:** `django-tasks-redis`, custom implementation

### 3. Custom Backend

- Implement `BaseTaskBackend` for specific infrastructure (SQS, RabbitMQ, etc.)

---

## Scheduler Options

For running the enqueuer periodically:

| Option | Pros | Cons |
|--------|------|------|
| **Kubernetes CronJob** | Native K8s, no code | K8s only, 1-min minimum |
| **systemd timer** | Simple, reliable | Linux only |
| **Supervisor + loop** | Cross-platform | Custom code |
| **APScheduler** | Python native | Another dependency |
| **Management command + sleep** | Simplest | Less precise timing |

---

## Phased Migration Plan

### Phase 1: Add Optional Task Support

- Add `polaris/tasks.py` with task definitions
- Add `enqueue_pending_tasks` command
- Document as alternative to existing commands
- No breaking changes

### Phase 2: Parallel Support

- Both architectures work side-by-side
- Users choose based on their infrastructure
- Gather feedback on task-based approach

### Phase 3: Default to Tasks (Major Version)

- Make task-based processing the default
- Deprecate single-instance commands
- Provide migration guide

---

## Open Questions

- [ ] Which task backend should Polaris officially recommend?
- [ ] Should this be opt-in or become the new default?
- [ ] Minimum Django version: 6.0 only, or support 5.2 with backport?
- [ ] How to handle idempotency for re-enqueued tasks?
- [ ] Should we provide a combined worker command?

---

## References

- [Django 6.0 Tasks Documentation](https://docs.djangoproject.com/en/6.0/topics/tasks/)
- [Django Tasks API Reference](https://docs.djangoproject.com/en/6.0/ref/tasks/)
- [django-tasks PyPI](https://pypi.org/project/django-tasks/) - Backport for Django 5.2+
- [Django Forum: django-tasks DEP](https://forum.djangoproject.com/t/django-tasks-bringing-background-workers-in-to-django-core/32967)
- [A First Look at Django's Background Tasks](https://roam.be/notes/2025/a-first-look-at-djangos-new-background-tasks/)

---

## Current Implementation Reference

- `polaris/management/commands/process_pending_deposits.py`
- `polaris/management/commands/watch_transactions.py`
- `polaris/management/commands/execute_outgoing_transactions.py`
- `polaris/management/commands/poll_outgoing_transactions.py`
- `polaris/models.py` - `PolarisHeartbeat` model for distributed locking
