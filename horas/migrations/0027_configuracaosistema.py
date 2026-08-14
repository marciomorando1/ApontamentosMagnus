# Generated manually for ERP configuration.

from django.db import migrations, models


def seed_configuracao(apps, schema_editor):
    ConfiguracaoSistema = apps.get_model('horas', 'ConfiguracaoSistema')
    ConfiguracaoSistema.objects.get_or_create(
        pk=1,
        defaults={
            'url_erp': 'http://wsadmteste.magnus.com.br',
            'usuario_erp': '',
            'senha_erp': '',
            'encryption_erp': 0,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0026_cliente'),
    ]

    operations = [
        migrations.CreateModel(
            name='ConfiguracaoSistema',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url_erp', models.URLField(default='http://wsadmteste.magnus.com.br', max_length=255, verbose_name='URL ERP')),
                ('usuario_erp', models.CharField(max_length=100, verbose_name='Usuario ERP')),
                ('senha_erp', models.CharField(max_length=200, verbose_name='Senha ERP')),
                ('encryption_erp', models.PositiveSmallIntegerField(default=0, verbose_name='Encryption ERP')),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Configuracao do Sistema',
                'verbose_name_plural': 'Configuracoes do Sistema',
            },
        ),
        migrations.RunPython(seed_configuracao, migrations.RunPython.noop),
    ]
