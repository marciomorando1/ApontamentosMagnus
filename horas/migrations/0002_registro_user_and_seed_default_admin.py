from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations, models
import django.db.models.deletion


DEFAULT_USERNAME = 'marcio.morando'
DEFAULT_PASSWORD = '1234'


def seed_default_user_and_assign_registros(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    Registro = apps.get_model('horas', 'Registro')

    user, created = User.objects.get_or_create(
        username=DEFAULT_USERNAME,
        defaults={
            'is_staff': True,
            'is_superuser': True,
            'is_active': True,
            'password': make_password(DEFAULT_PASSWORD),
        },
    )

    updates = []
    if not created:
        if not user.is_staff:
            user.is_staff = True
            updates.append('is_staff')
        if not user.is_superuser:
            user.is_superuser = True
            updates.append('is_superuser')
        if not user.is_active:
            user.is_active = True
            updates.append('is_active')
        user.password = make_password(DEFAULT_PASSWORD)
        updates.append('password')
        user.save(update_fields=updates)

    Registro.objects.filter(user__isnull=True).update(user=user)


def reverse_seed_default_user(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    User.objects.filter(username=DEFAULT_USERNAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('horas', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='registro',
            name='user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='registros_horas',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(
            seed_default_user_and_assign_registros,
            reverse_seed_default_user,
        ),
        migrations.AlterField(
            model_name='registro',
            name='user',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='registros_horas',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
