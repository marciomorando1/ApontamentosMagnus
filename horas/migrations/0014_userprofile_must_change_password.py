from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0013_orcamento_horas_adicionais_solicitacaohoras'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='must_change_password',
            field=models.BooleanField(default=False, verbose_name='Exigir troca de senha no proximo login'),
        ),
    ]
