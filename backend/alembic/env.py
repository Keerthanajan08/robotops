from alembic import context
from app.db import Base, engine
import app.db  # noqa: registers models

with engine.connect() as conn:
    context.configure(
        connection=conn, target_metadata=Base.metadata, render_as_batch=True
    )
    with context.begin_transaction():
        context.run_migrations()
