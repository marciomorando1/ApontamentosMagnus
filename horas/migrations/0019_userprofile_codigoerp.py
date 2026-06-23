from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0018_agendaatividade_servico'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='codigoerp',
            field=models.PositiveIntegerField(default=0, verbose_name='Código ERP'),
        ),
    ]
