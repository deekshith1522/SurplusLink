# SurplusLink deployment (PythonAnywhere free tier)

This is a synthetic-data demo. Do not add real addresses or personal details.

## Who must do what

Steps marked **[YOU]** need your own account, credentials or judgement. The assistant cannot do these: creating the PythonAnywhere account, choosing and storing the secret key, running Bash console commands, uploading or cloning code, pushing to GitHub (if used), and clicking renewal.

## Steps

1. **[YOU]** Create a PythonAnywhere account (free tier). Check the current free-tier terms on the PythonAnywhere website yourself, including any renewal requirement for free web apps. Do not rely on any date written in this file.

2. **[YOU]** Get the code onto PythonAnywhere: upload a zip, or `git clone` from your repository. Do not commit or upload `.env`, `instance/` or `*.db` files.

3. **[YOU]** In a Bash console:
   ```
   cd ~/surpluslink            # the project folder
   python3.11 -m venv ~/.virtualenvs/surpluslink
   source ~/.virtualenvs/surpluslink/bin/activate
   pip install -r requirements.txt
   ```
   (`mkvirtualenv` from virtualenvwrapper also works if you prefer it.)

4. **[YOU]** Create the persistent data folder outside the code folder:
   ```
   mkdir -p ~/surplus_data
   ```

5. **[YOU]** Web tab:
   - Add a new web app, choose manual configuration, and select Python 3.11.
   - Set the virtualenv path to `/home/<your-username>/.virtualenvs/surpluslink`.
   - Set the source code directory to the project folder (for example `/home/<your-username>/surpluslink`).
   - Edit the WSGI configuration file. Replace its contents with the lines below, after generating your own secret key. Replace `<your-username>` and the secret value.

   ```python
   import os
   import sys

   os.environ["SURPLUSLINK_SECRET_KEY"] = "REPLACE-WITH-YOUR-OWN-LONG-RANDOM-VALUE"
   os.environ["SURPLUSLINK_ENV"] = "production"
   os.environ["SURPLUSLINK_DB_PATH"] = "/home/<your-username>/surplus_data/surpluslink.db"

   project_home = "/home/<your-username>/surpluslink"
   if project_home not in sys.path:
       sys.path.insert(0, project_home)

   from surplus_link import create_app
   application = create_app()
   ```

   The three `os.environ` lines must be set before the import, and `SURPLUSLINK_DB_PATH` here must be the same path you put in `~/.surplus_env`. Do not add `--debug` anywhere. The production app never runs with debug enabled; `flask run --debug` is for local development only.

   Generate the secret key yourself in a Bash console (for example with `python3.11 -c "import secrets; print(secrets.token_urlsafe(48))"`). Never commit it or paste it into the repository. It lives only in the WSGI file on PythonAnywhere.

6. **[YOU]** Create `~/.surplus_env` (outside the project folder, never committed) containing exactly three lines:
   ```
   export SURPLUSLINK_SECRET_KEY="REPLACE-WITH-SAME-VALUE-AS-WSGI-FILE"
   export SURPLUSLINK_ENV=production
   export SURPLUSLINK_DB_PATH=/home/<your-username>/surplus_data/surpluslink.db
   ```
   Lock it down with `chmod 600 ~/.surplus_env`.

   Then, in a Bash console:
   ```
   cd ~/surpluslink ; source ~/.virtualenvs/surpluslink/bin/activate ; source ~/.surplus_env ; flask --app surplus_link init-db ; flask --app surplus_link seed
   ```

   The console and the web app must use the same `SURPLUSLINK_DB_PATH` and the same secret value, otherwise the console would write to a different database.

   Verify the database path without printing the secret:
   ```
   python -c "from surplus_link import create_app; print(create_app().config['DATABASE'])"
   ```
   The output must equal `/home/<your-username>/surplus_data/surpluslink.db`, the same path as in the WSGI file.

7. **[YOU]** Web tab: click Reload. Open `https://<your-username>.pythonanywhere.com/` and check the home page loads and shows the synthetic-data banner.

8. **[YOU]** Persistence check: note the persona count shown on the home page. Click Reload on the Web tab and check the same count is shown. Run `flask --app surplus_link seed` again and confirm it reports the data is already seeded, not duplicated.

9. **[YOU]** Debug check: open `https://<your-username>.pythonanywhere.com/does-not-exist`. You should see a plain 404 page, not an interactive traceback page. If you see a traceback, stop and check that `SURPLUSLINK_ENV=production` is set in the WSGI file.

10. **[YOU]** Renewal: free web apps need a periodic renewal action on the Web tab. Check the current rule on PythonAnywhere yourself. This project does not assume any schedule.

## Keep secrets out of Git

- The secret key lives only in the WSGI file on PythonAnywhere.
- `.env`, `instance/` and `*.db` are git-ignored. Keep them out of any upload.

## Missing-secret behaviour

If `SURPLUSLINK_ENV=production` and `SURPLUSLINK_SECRET_KEY` is not set, the app refuses to start with a `RuntimeError`. This is intentional. The development default key is never used in production.

## Rollback

Data lives in the SQLite file at `~/surplus_data/surpluslink.db`. Copy that file before any reseed or `seed --reset`, so you can restore it if needed.
