from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0015_userprofile_is_pmo_orcamento_pmo'),
    ]

    operations = [
        migrations.AlterField(
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
    ]
