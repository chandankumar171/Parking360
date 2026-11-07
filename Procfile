release: python manage.py migrate --noinput && python manage.py createcachetable
web: gunicorn config.wsgi:application -c gunicorn.conf.py
