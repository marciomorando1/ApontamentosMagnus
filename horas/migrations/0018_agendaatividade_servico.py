from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0017_servico_registro_servico'),
    ]

    operations = [
        migrations.AddField(
            model_name='agendaatividade',
            name='servico',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='agenda_atividades',
                to='horas.servico',
            ),
        ),
    ]
