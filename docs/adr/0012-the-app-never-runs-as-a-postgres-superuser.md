# The app never runs as a Postgres superuser

The app connects with its own login, `groundline`, which owns the `groundline` database but can't create logins or databases or change server settings; only the setup step (`scripts/db-setup`, or the `setup` service in `compose.yaml`) logs in as the admin, and the app container never gets the admin password. Connecting as the admin would let us skip the setup step, but a bug or a leaked `DATABASE_URL` could then control the whole server. Whether migrations should have a login of their own is still open.
