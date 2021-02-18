#!/bin/bash

if [ ! $1 ]; then
	echo "Need to assign database name"
	exit 1
fi
db_name=$1
db_backup=ivyweb_backup
sudo systemctl stop freeradius
sudo systemctl restart postgresql
psql -c "drop database ${db_name};"
psql -c "create database ${db_name} with template ${db_backup};"
sudo systemctl start freeradius
rsync -r --delete ~/.local/share/Odoo/filestore/${db_backup}/ ~/.local/share/Odoo/filestore/${db_name}
