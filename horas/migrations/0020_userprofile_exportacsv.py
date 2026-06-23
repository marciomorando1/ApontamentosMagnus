from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0019_userprofile_codigoerp'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='exportacsv',
            field=models.BooleanField(default=False, verbose_name='Exporta CSV'),
        ),
    ]
