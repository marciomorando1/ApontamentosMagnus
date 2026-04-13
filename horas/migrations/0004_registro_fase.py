from django.db import migrations, models
import django.db.models.deletion


def preencher_fase_padrao(apps, schema_editor):
    Fase = apps.get_model('horas', 'Fase')
    Registro = apps.get_model('horas', 'Registro')

    fase_padrao, _ = Fase.objects.get_or_create(
        codigo='000',
        defaults={'descricao': 'Nao informada'},
    )
    Registro.objects.filter(fase__isnull=True).update(fase=fase_padrao)


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0003_fase'),
    ]

    operations = [
        migrations.AddField(
            model_name='registro',
            name='fase',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='registros',
                to='horas.fase',
            ),
        ),
        migrations.RunPython(preencher_fase_padrao, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='registro',
            name='fase',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='registros',
                to='horas.fase',
            ),
        ),
    ]
