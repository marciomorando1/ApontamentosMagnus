# Generated manually for cliente cadastro.

from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


def cleanup_old_cliente_artifacts(apps, schema_editor):
    table_names = schema_editor.connection.introspection.table_names()
    quote = schema_editor.quote_name

    with schema_editor.connection.cursor() as cursor:
        if 'horas_orcamento' in table_names:
            orcamento_columns = {
                column.name for column in schema_editor.connection.introspection.get_table_description(cursor, 'horas_orcamento')
            }
            if 'cliente_id' in orcamento_columns:
                if schema_editor.connection.vendor == 'sqlite':
                    cursor.execute('PRAGMA index_list(horas_orcamento)')
                    for _, index_name, *_ in cursor.fetchall():
                        cursor.execute(f'PRAGMA index_info({index_name})')
                        if any(row[2] == 'cliente_id' for row in cursor.fetchall()):
                            schema_editor.execute(f'DROP INDEX IF EXISTS {quote(index_name)}')
                schema_editor.execute(
                    f'ALTER TABLE {quote("horas_orcamento")} DROP COLUMN {quote("cliente_id")} '
                )

        if 'horas_cliente' in table_names:
            cliente_columns = {
                column.name for column in schema_editor.connection.introspection.get_table_description(cursor, 'horas_cliente')
            }
            is_old_cliente_table = {'codigo', 'nome', 'ativo'}.issubset(cliente_columns) and 'Codigo_Cliente' not in cliente_columns
            if is_old_cliente_table:
                schema_editor.execute(f'DROP TABLE {quote("horas_cliente")}')

        cursor.execute(
            "DELETE FROM django_migrations WHERE app = 'horas' AND name = '0026_cliente_orcamento_cliente'"
        )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('horas', '0025_agendaatividade_destino_para_and_more'),
    ]

    operations = [
        migrations.RunPython(cleanup_old_cliente_artifacts, migrations.RunPython.noop),
        migrations.CreateModel(
            name='Cliente',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('Codigo_Cliente', models.CharField(max_length=50, unique=True, validators=[django.core.validators.RegexValidator(message='Informe somente n\u00fameros.', regex=r'^\d+$')])),
                ('Nome_Cliente', models.CharField(max_length=200)),
                ('Situacao', models.CharField(choices=[('ATIVO', 'Ativo'), ('INATIVO', 'Inativo')], default='ATIVO', max_length=10)),
                ('Data_Cadastro', models.DateTimeField(auto_now_add=True)),
                ('Data_Alteracao', models.DateTimeField(auto_now=True)),
                ('Usuario_Alteracao', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='clientes_alterados', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['Codigo_Cliente'],
            },
        ),
    ]
