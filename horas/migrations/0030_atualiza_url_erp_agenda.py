from django.db import migrations, models


OLD_ERP_URLS = [
    'http://wsadmteste.magnus.com.br',
    'http://wsadmteste.magnus.com.br/',
    'http://wsadmteste.magnus.com.br:8088',
    'http://wsadmteste.magnus.com.br:8088/',
    'http://wsadmteste.magnus.com.br/g5-senior-services/sapiens_Synccom_magnus_rat?wsdl',
    'https://wsadmin.magnus.com.br',
    'https://wsadmin.magnus.com.br/',
    'https://wsadmin.magnus.com.br/g5-senior-services/sapiens_Synccom_magnus_agenda?wsdl',
    'http://wsadmin.magnus.com.br:8080',
    'http://wsadmin.magnus.com.br:8080/',
    'http://wsadmin.magnus.com.br:8080/g5-senior-services/sapiens_Synccom_magnus_agenda?wsdl',
]
NEW_ERP_URL = 'https://wsadmin.magnus.com.br'


def atualizar_url_erp(apps, schema_editor):
    ConfiguracaoSistema = apps.get_model('horas', 'ConfiguracaoSistema')
    ConfiguracaoSistema.objects.filter(url_erp__in=OLD_ERP_URLS).update(url_erp=NEW_ERP_URL)


def reverter_url_erp(apps, schema_editor):
    ConfiguracaoSistema = apps.get_model('horas', 'ConfiguracaoSistema')
    ConfiguracaoSistema.objects.filter(url_erp=NEW_ERP_URL).update(url_erp=OLD_ERP_URLS[0])


class Migration(migrations.Migration):

    dependencies = [
        ('horas', '0029_userprofile_envia_erp'),
    ]

    operations = [
        migrations.AlterField(
            model_name='configuracaosistema',
            name='url_erp',
            field=models.URLField(default=NEW_ERP_URL, max_length=255, verbose_name='URL ERP'),
        ),
        migrations.RunPython(atualizar_url_erp, reverter_url_erp),
    ]
