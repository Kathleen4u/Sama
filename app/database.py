from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

def init_db():
    db.create_all()
    print("Database tables created!")

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