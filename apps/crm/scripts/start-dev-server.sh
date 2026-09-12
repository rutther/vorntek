#!/usr/bin/env bash
set -eu

cd /home/eastlindner/projects/site-stack-v2/apps/admin
. .venv/bin/activate

exec python manage.py runserver 0.0.0.0:26000
