from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('horas', '0014_userprofile_must_change_password'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='is_pmo',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='orcamento',
            name='pmo',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='orcamentos_pmo',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
