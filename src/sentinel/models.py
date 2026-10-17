"""SQLAlchemy Core Table definitions for the brief §5 schema (ADR-004: Core,
not the ORM). Alembic owns schema changes against this metadata; `create_all`
is never called.

Tables are declared in dependency order for a human reader. SQLAlchemy
resolves `ForeignKey` by name against `metadata` regardless of declaration
order, and sorts `CREATE TABLE` statements topologically — unlike the literal
SQL in brief §5, where `finding` forward-references `check_run` before it is
declared, this does not matter here. `check_run` is still listed before
`finding` to keep the file readable.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    REAL,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

metadata = MetaData()

# A work we know about, from any source.
paper = Table(
    "paper",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("doi", Text, unique=True),
    Column("pmid", Text, unique=True),
    Column("pmcid", Text, unique=True),
    Column("title", Text, nullable=False),
    Column("title_norm", Text, nullable=False),
    Column("container", Text),
    Column("year", SmallInteger),
    Column("first_author", Text),
    Column("authors", JSONB),
    Column("source", Text, nullable=False),
    Column("is_oa", Boolean),
    Column("license", Text),
    Column(
        "fulltext_status", Text, nullable=False, server_default=text("'none'")
    ),
    Column("raw", JSONB),
    Column(
        "ingested_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)
Index(
    "paper_title_trgm",
    paper.c.title_norm,
    postgresql_using="gin",
    postgresql_ops={"title_norm": "gin_trgm_ops"},
)
Index("paper_author_year", paper.c.first_author, paper.c.year)

# One row per reference in a citing paper's bibliography.
reference = Table(
    "reference",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "citing_paper_id",
        BigInteger,
        ForeignKey("paper.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("ordinal", Integer),
    Column("raw_string", Text),
    Column("parsed_doi", Text),
    Column("parsed_title", Text),
    Column("parsed_first_author", Text),
    Column("parsed_year", SmallInteger),
    Column("parsed_container", Text),
    Column("resolved_paper_id", BigInteger, ForeignKey("paper.id")),
    Column("resolution_method", Text),
    Column("resolution_score", REAL),
    Column(
        "resolution_status",
        Text,
        nullable=False,
        server_default=text("'pending'"),
    ),
    Column("resolved_at", DateTime(timezone=True)),
)
Index("reference_citing", reference.c.citing_paper_id)
Index("reference_status", reference.c.resolution_status)

# Retraction and concern notices, keyed loosely because sources disagree.
retraction_notice = Table(
    "retraction_notice",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("retracted_doi", Text),
    Column("retracted_pmid", Text),
    Column("notice_doi", Text),
    Column("notice_type", Text, nullable=False),
    Column("notice_date", Date),
    Column("reason", Text),
    Column("source", Text, nullable=False),
    Column("raw", JSONB),
)
Index("retraction_doi", retraction_notice.c.retracted_doi)
Index("retraction_pmid", retraction_notice.c.retracted_pmid)

# Registry record for a clinical trial. Ingested now; compared in Phase 2.
trial = Table(
    "trial",
    metadata,
    Column("nct_id", Text, primary_key=True),
    Column("brief_title", Text),
    Column("overall_status", Text),
    Column("study_type", Text),
    Column("phase", Text),
    Column("enrollment", Integer),
    Column("start_date", Date),
    Column("primary_completion_date", Date),
    Column("completion_date", Date),
    Column("results_first_posted", Date),
    Column("lead_sponsor", Text),
    Column("sponsor_class", Text),
    Column("raw", JSONB),
    Column(
        "ingested_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

# Outcome definitions, versioned. ClinicalTrials.gov keeps a change history;
# capture every version, because outcome switching is detected by diffing them.
trial_outcome = Table(
    "trial_outcome",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "nct_id",
        Text,
        ForeignKey("trial.nct_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("version_no", Integer, nullable=False),
    Column("version_date", Date),
    Column("outcome_type", Text, nullable=False),
    Column("measure", Text, nullable=False),
    Column("description", Text),
    Column("time_frame", Text),
    UniqueConstraint("nct_id", "version_no", "outcome_type", "measure"),
)
Index("trial_outcome_nct", trial_outcome.c.nct_id)

paper_trial_link = Table(
    "paper_trial_link",
    metadata,
    Column(
        "paper_id",
        BigInteger,
        ForeignKey("paper.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "nct_id",
        Text,
        ForeignKey("trial.nct_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("link_source", Text, nullable=False),
    Column("confidence", REAL),
)

# Every statistical test string found in a paper, with recomputation.
stat_test = Table(
    "stat_test",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "paper_id",
        BigInteger,
        ForeignKey("paper.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("location", Text),
    Column("raw_string", Text, nullable=False),
    Column("test_type", Text, nullable=False),
    Column("statistic", Double),
    Column("df1", Double),
    Column("df2", Double),
    Column("reported_p", Double),
    Column("reported_p_op", Text),
    Column("computed_p", Double),
    Column("one_tailed_ok", Boolean),
    Column("inconsistent", Boolean),
    Column("gross", Boolean),
)
Index("stat_test_paper", stat_test.c.paper_id)

check_run = Table(
    "check_run",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "started_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column("finished_at", DateTime(timezone=True)),
    Column("corpus_label", Text),
    Column("config_hash", Text, nullable=False),
    Column("checks", ARRAY(Text), nullable=False),
    Column("counts", JSONB),
    Column("status", Text, nullable=False, server_default=text("'running'")),
)

# The report card, one row per finding. `evidence` is populated even for
# `clean` results (ADR-015) — a check reporting clean without showing what it
# examined is unauditable. `check_ver` lets an improved check produce
# comparable rows instead of silently overwriting history.
finding = Table(
    "finding",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "paper_id",
        BigInteger,
        ForeignKey("paper.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "run_id",
        BigInteger,
        ForeignKey("check_run.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("check_id", Text, nullable=False),
    Column("check_ver", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("summary", Text, nullable=False),
    Column("evidence", JSONB, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)
Index("finding_paper", finding.c.paper_id)
Index("finding_check", finding.c.check_id, finding.c.status)

ingest_watermark = Table(
    "ingest_watermark",
    metadata,
    Column("source", Text, primary_key=True),
    Column("cursor", Text),
    Column("last_run_at", DateTime(timezone=True)),
    Column("rows_seen", BigInteger, nullable=False, server_default=text("0")),
)
