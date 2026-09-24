from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('horas', '0031_userprofile_is_terceiro'),
    ]

    operations = [
        migrations.CreateModel(
            name='LogRotina',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operacao', models.CharField(max_length=100)),
                ('requisicao_enviada', models.TextField()),
                ('retorno_recebido', models.TextField()),
                ('criado_em', models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    'usuario_requisicao',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='logs_rotina',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'verbose_name': 'Log de Rotina',
                'verbose_name_plural': 'Logs de Rotina',
                'ordering': ['-criado_em', '-pk'],
            },
        ),
    ]
