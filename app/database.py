from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

def init_db(app):
    """
    Bootstrap an empty database, and warn when an existing one is behind
    the migrations. Runs at startup inside an app context.

    create_all() runs ONLY on an empty database: the migration chain starts
    with column changes and never creates the core tables (users, wallets,
    transactions...), so a fresh setup still needs it. The new database is
    then stamped at the latest migration.

    An existing database is never touched — create_all() can't add columns
    to tables that already exist, so schema changes there must come from
    `flask db upgrade`. If the database is behind, log it loudly instead of
    failing later on a missing column.
    """
    from sqlalchemy import inspect, text

    existing = set(inspect(db.engine).get_table_names()) - {"alembic_version"}
    if not existing:
        db.create_all()
        app.logger.warning("Empty database: created all tables from the models.")
        try:
            from flask_migrate import stamp
            stamp()
        except Exception as e:
            app.logger.error(
                f"Tables created but could not stamp the migration version ({e}). "
                f"Run `flask db stamp head` before the next `flask db upgrade`."
            )
        return

    try:
        from alembic.script import ScriptDirectory
        config = app.extensions["migrate"].migrate.get_config()
        heads = set(ScriptDirectory.from_config(config).get_heads())
    except Exception as e:
        # e.g. the migrations folder isn't deployed — nothing to compare with
        app.logger.info(f"Skipping migration version check: {e}")
        return

    if "alembic_version" not in inspect(db.engine).get_table_names():
        app.logger.error(
            "Database has tables but no alembic_version — it isn't under migration "
            "control. Stamp it at the revision its schema matches (`flask db stamp <rev>`), "
            "then run `flask db upgrade`."
        )
        return

    current = set(db.session.execute(text("SELECT version_num FROM alembic_version")).scalars())
    db.session.rollback()  # don't hold the read transaction open
    if current != heads:
        app.logger.error(
            f"Database schema is behind the code: at {sorted(current) or 'nothing'}, "
            f"latest is {sorted(heads)}. Run `flask db upgrade` against this database."
        )

"""
USAGE IN YOUR APP:

1. Import db:
    from app.database import db

2. Query in routes:
    from app.models.user import User

    user = db.session.execute(
        db.select(User).where(User.email == email)
    ).scalar_one_or_none()

3. Add/Commit:
    db.session.add(user)
    db.session.commit()

4. Rollback on error:
    db.session.rollback()
"""
