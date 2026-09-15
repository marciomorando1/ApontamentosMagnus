from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0030_atualiza_url_erp_agenda'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='is_terceiro',
            field=models.BooleanField(default=False),
        ),
    ]
