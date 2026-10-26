"""Developer report: where Degree Navigator and CoursePilot disagree, per requirement node (Phase 7.4).

Encoding review starts here. Aggregates the reconciliation label of every
row of every CONFIRMED, current (not superseded) DN import - counts only, per
(mapping, requirement node, row kind, label). No student id, name, course or
grade is read or printed, so the report can be shared with whoever reviews
requirement encodings.

Usage:  DATABASE_URL_SYNC=... python scripts/dn_disagreement_report.py [--all]
        --all   include agreements and unmapped rows, not only disagreements
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend")]

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models.degree_audit import DegreeAuditImport, DegreeAuditRequirementRow  # noqa: E402

DISAGREEMENTS = ("dn_satisfied_cp_not", "cp_satisfied_dn_not", "allocation_differs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL_SYNC",
                         "postgresql+psycopg://coursepilot:coursepilot@localhost:5432/coursepilot")
    q = (select(DegreeAuditImport.mapping_key, DegreeAuditRequirementRow.requirement_code,
                DegreeAuditRequirementRow.kind, DegreeAuditRequirementRow.reconciliation,
                func.count())
         .join(DegreeAuditImport, DegreeAuditImport.id == DegreeAuditRequirementRow.import_id)
         .where(DegreeAuditImport.state == "confirmed",
                DegreeAuditImport.superseded_at.is_(None),
                DegreeAuditRequirementRow.excluded.is_(False))
         .group_by(DegreeAuditImport.mapping_key, DegreeAuditRequirementRow.requirement_code,
                   DegreeAuditRequirementRow.kind, DegreeAuditRequirementRow.reconciliation)
         .order_by(DegreeAuditImport.mapping_key, DegreeAuditRequirementRow.requirement_code))
    if not args.all:
        q = q.where(DegreeAuditRequirementRow.reconciliation.in_(DISAGREEMENTS))
    with Session(create_engine(url)) as session:
        rows = session.execute(q).all()
    if not rows:
        print("No disagreements recorded." if not args.all else "No confirmed DN imports.")
        return 0
    print(f"{'mapping':<14} {'requirement':<24} {'kind':<12} {'label':<22} count")
    for mapping, code, kind, label, count in rows:
        print(f"{mapping or '-':<14} {code or '(unmapped)':<24} {kind:<12} {label or '-':<22} "
              f"{count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
