"""add clinic_id to appointment_types

Revision ID: 9636dad05900
Revises: 8bcd500f9c65
Create Date: 2026-02-07 18:22:51.582971

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9636dad05900'
down_revision: Union[str, Sequence[str], None] = '8bcd500f9c65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # 1) crear la columna permitiendo NULL temporalmente
    op.add_column("appointment_types", sa.Column("clinic_id", sa.Integer(), nullable=True))

    # 2) setear clinic_id a los registros existentes (usa el clinic 1 para salir del paso)
    op.execute("UPDATE appointment_types SET clinic_id = 1 WHERE clinic_id IS NULL;")

    # 3) ahora sí hacerla NOT NULL
    op.alter_column("appointment_types", "clinic_id", nullable=False)

    # 4) FK + index (si no te los puso ya)
    op.create_foreign_key(None, "appointment_types", "clinics", ["clinic_id"], ["id"])
    op.create_index(op.f("ix_appointment_types_clinic_id"), "appointment_types", ["clinic_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_appointment_types_clinic_id"), table_name="appointment_types")
    op.drop_constraint(None, "appointment_types", type_="foreignkey")
    op.drop_column("appointment_types", "clinic_id")

