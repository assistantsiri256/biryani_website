import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("binarypro")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Run a worker:  celery -A config worker -l info   (Windows dev: add  --pool=solo)
