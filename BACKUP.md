# Backup and restore

## What a backup is
A JSON export of all application records (members, accounts, journals, loans, audit log and settings), compressed and **encrypted with Fernet** using `BACKUP_ENCRYPTION_KEY`, with a SHA-256 checksum recorded. Uploaded files (photos, KYC documents) under `MEDIA_ROOT` are not inside the JSON; back up that folder too (Render disk snapshots, or copy `/var/data/media`).

## Taking a backup
* In the app: Maintenance > Backups > **Create backup now**, then **Download**. Keep the file off the server.
* Scheduled: `python manage.py make_backup` (add a daily Render Cron Job).
* Also enable automated backups on the Clever Cloud MySQL add-on (database-level backups).

## Verifying
Maintenance > Backups > **Verify** re-reads the file, checks the checksum, decrypts and counts records. Verify after every backup and do a full test restore into a spare database at least quarterly.

## Restoring
1. Provision an empty database and point the DB_* variables at it.
2. `python manage.py migrate`
3. `python manage.py restore_backup /path/to/file.sfmsbak --yes-i-am-sure` (the command refuses a database that already holds members or journals)
4. Restore the media folder.
5. Run `python manage.py check_audit` to verify the audit chain, then sign in and review Maintenance > System health and the trial balance.

You need the same `BACKUP_ENCRYPTION_KEY` and `AUDIT_HMAC_KEY` as when the backup was taken. Losing the encryption key makes backups unreadable; store it in a password manager and with a second custodian.
