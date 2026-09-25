from django.db import migrations, models


def preencher_data_fim(apps, schema_editor):
    FolgaFeriado = apps.get_model('horas', 'FolgaFeriado')
    FolgaFeriado.objects.filter(data_fim__isnull=True).update(data_fim=models.F('data_inicio'))


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0032_logrotina'),
    ]

    operations = [
        migrations.RenameField(
            model_name='folgaferiado',
            old_name='data',
            new_name='data_inicio',
        ),
        migrations.AddField(
            model_name='folgaferiado',
            name='data_fim',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(preencher_data_fim, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='folgaferiado',
            name='data_fim',
            field=models.DateField(),
        ),
        migrations.AlterModelOptions(
            name='folgaferiado',
            options={'ordering': ['-data_inicio', '-data_fim', '-abrangencia_todos', 'user__username', 'pk']},
        ),
    ]
