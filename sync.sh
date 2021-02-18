#!/bin/bash

#This scripts makes a copy of the live database and changes the cron so that
#the email tasks do not inadvertently run

db_backup=ivyweb_backup
dropdb ${db_backup}
createdb ${db_backup}
echo "Starting synchonization of ivyweb database"
ssh odoo@www.ivyweb.co.za "pg_dump ivyweb -Fc" | pg_restore -d ${db_backup} -Fc
echo "Sync finished"
echo "Disabling client facing cron jobs"
psql ${db_backup} -c "update ir_cron set active=False"
echo "Starting synchornization of filestore"
rsync -r --progress -u --delete-during odoo@www.ivyweb.co.za:.local/share/Odoo/filestore/ivyweb/ ~/.local/share/Odoo/filestore/${db_backup}
echo "Done."
