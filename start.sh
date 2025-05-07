#!/bin/bash

echo "Démarrage du serveur Flask avec Waitress"
waitress-serve --listen=0.0.0.0:10000 main:app
