#!/bin/bash

echo "Démarrage du serveur Flask avec Gunicorn"
gunicorn --bind 0.0.0.0:10000 main:app --workers 4 --threads 2
