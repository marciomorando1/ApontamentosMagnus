from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0020_userprofile_exportacsv'),
    ]

    operations = [
        migrations.AddField(
            model_name='orcamento',
            name='nome_cliente',
            field=models.CharField(blank=True, max_length=200),
        ),
    ]