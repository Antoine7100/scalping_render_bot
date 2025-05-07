#!/bin/bash

echo "Démarrage du serveur NGINX"
nginx -c /path/to/nginx.conf

echo "Démarrage du serveur Flask avec Gunicorn"
gunicorn --bind 0.0.0.0:5000 main:app --workers 4 --threads 2
