# Local installation

Requires Python 3.11+ (3.12 recommended). SQLite is used locally; MySQL is optional locally and required in production.

## Linux (Debian/Ubuntu)
```bash
sudo apt install python3-venv python3-dev build-essential pkg-config default-libmysqlclient-dev libjpeg-dev
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
## macOS
```bash
brew install mysql-client pkg-config
export PKG_CONFIG_PATH="$(brew --prefix mysql-client)/lib/pkgconfig"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
## Windows
Install Python from python.org (tick "Add to PATH"). In PowerShell:
```powershell
py -m venv .venv ; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
`mysqlclient` ships wheels for most Windows Python versions. If the install fails and you only want to try the system locally, remove the `mysqlclient` line from requirements.txt, install the rest, and use SQLite (`DB_ENGINE=sqlite`). For MySQL on a machine where mysqlclient will not build, install `PyMySQL` and add
`import pymysql; pymysql.install_as_MySQLdb()` at the top of `sacco_project/__init__.py`.

## Configure
Copy `.env.example` to `.env` and edit, or export variables. For local work only:
```
DJANGO_DEBUG=1
DB_ENGINE=sqlite
```
For local MySQL set `DB_ENGINE=mysql`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, and create the database with `CREATE DATABASE sacco CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;`.

## Run
```bash
python manage.py migrate
python manage.py seed_data --no-force-change     # demo data (refuses to run on a database that already has members)
python manage.py runserver
```
A real installation does not use demo data. Instead set `BOOTSTRAP_ADMIN_PASSWORD` and run `python manage.py bootstrap_admin`.

## Test
```bash
SACCO_TESTING=1 DB_ENGINE=sqlite SQLITE_PATH=:memory: python manage.py test tests
```
