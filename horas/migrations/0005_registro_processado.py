from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0004_registro_fase'),
    ]

    operations = [
        migrations.AddField(
            model_name='registro',
            name='processado',
            field=models.CharField(
                choices=[('S', 'Sim'), ('N', 'Não')],
                default='N',
                max_length=1,
            ),
        ),
    ]
