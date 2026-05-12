release: python manage.py migrate
web: python manage.py migrate && python manage.py collectstatic --noinput && gunicorn magnusRotinas_django.wsgi --log-file -
