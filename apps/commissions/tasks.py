from celery import shared_task
from django.conf import settings
from django.db import transaction


@shared_task(bind=True, max_retries=5, default_retry_delay=30)
def process_order_task(self, order_id):
    from .engine import process_order

    try:
        process_order(order_id)
    except Exception as exc:  # engine is idempotent, so retrying is safe
        raise self.retry(exc=exc)


def dispatch_process_order(order_id):
    """Run the commission engine inline (no broker) or via Celery after the payment commits."""
    if settings.CELERY_TASK_ALWAYS_EAGER:
        from .engine import process_order

        process_order(order_id)
    else:
        transaction.on_commit(lambda: process_order_task.delay(order_id))
