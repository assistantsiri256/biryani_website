import json

import apps.core.fields
from django.db import migrations


def copy_to_encrypted(apps, schema_editor):
    WithdrawalRequest = apps.get_model("withdrawals", "WithdrawalRequest")
    for wr in WithdrawalRequest.objects.all():
        wr.payout_snapshot = json.dumps(wr.payout_details or {})
        wr.save(update_fields=["payout_snapshot"])


class Migration(migrations.Migration):

    dependencies = [
        ("withdrawals", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="withdrawalrequest",
            name="payout_snapshot",
            field=apps.core.fields.EncryptedCharField(blank=True, default=""),
        ),
        migrations.RunPython(copy_to_encrypted, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="withdrawalrequest",
            name="payout_details",
        ),
    ]
