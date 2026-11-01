"""Migrations run up, down, up against a real Postgres (sentinel-project.md §8).

The test downgrades to base, so TEST_DATABASE_URL must be a disposable
database, never one holding data you want.
"""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = requires_db


def _tables_with_rows(engine) -> list[str]:
    insp = inspect(engine)
    with engine.connect() as conn:
        return [
            t for t in insp.get_table_names()
            if t != "alembic_version"
            and conn.execute(text(f'SELECT EXISTS (SELECT 1 FROM "{t}")')).scalar()
        ]


def test_up_down_up(alembic_cfg):
    command.upgrade(alembic_cfg, "head")
    guard = create_engine(TEST_DATABASE_URL)
    try:
        if occupied := _tables_with_rows(guard):
            pytest.skip(f"refusing to downgrade a database with data in {occupied}")
    finally:
        guard.dispose()
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(TEST_DATABASE_URL)
    try:
        insp = inspect(engine)
        assert "paper_title_trgm" in {i["name"] for i in insp.get_indexes("paper")}
        assert "stage_run_corpus" in {i["name"] for i in insp.get_indexes("stage_run")}

        cols = {c["name"]: c for c in insp.get_columns("finding")}
        assert cols["paper_id"]["nullable"] is True
        assert cols["nct_id"]["nullable"] is True
        assert "finding_nct" in {i["name"] for i in insp.get_indexes("finding")}
        checks = {c["name"] for c in insp.get_check_constraints("finding")}
        assert "finding_one_subject" in checks

        uniques = {u["name"] for u in insp.get_unique_constraints("retraction_notice")}
        assert "retraction_notice_source_record_key" in uniques

        # Phase 2 (§10.2, ADR-044).
        assert "passage_embedding_hnsw" in {i["name"] for i in insp.get_indexes("passage_embedding")}
        assert {c["name"] for c in insp.get_columns("passage_embedding")} >= {"passage_id", "model", "embedding"}
        assert insp.get_pk_constraint("passage_embedding")["constrained_columns"] == ["passage_id", "model"]
        assert "passage_paper_ver_ordinal_key" in {
            u["name"] for u in insp.get_unique_constraints("passage")}
        assert "citation_context_paper_ver_ordinal_key" in {
            u["name"] for u in insp.get_unique_constraints("citation_context")}
        assert {c["name"]: c for c in insp.get_columns("eval_item")}["license"]["nullable"] is False
        gold_fk = insp.get_foreign_keys("eval_item_gold_passage")
        assert {fk["referred_table"]: fk["options"].get("ondelete") for fk in gold_fk} == {
            "eval_item": "CASCADE", "passage": "RESTRICT"}

        # A finding must have exactly one subject. Rolled back, so nothing persists.
        with engine.connect() as conn:
            run_id = conn.execute(
                text(
                    "INSERT INTO check_run (config_hash, checks) "
                    "VALUES ('x', ARRAY['C05']) RETURNING id"
                )
            ).scalar_one()
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO finding (run_id, check_id, check_ver, "
                        "severity, status, summary, evidence) VALUES "
                        "(:r, 'C05', '1.0.0', 'info', 'clean', 's', '{}')"
                    ),
                    {"r": run_id},
                )
            conn.rollback()
    finally:
        engine.dispose()


def test_phase2_tables_behave(alembic_cfg):
    """Vectors round-trip and search by cosine distance; a context can cite
    several references; a gold passage can't be deleted; re-runs can't
    duplicate. Everything is rolled back."""
    command.upgrade(alembic_cfg, "head")
    engine = create_engine(TEST_DATABASE_URL)
    try:
        with engine.connect() as conn:
            paper_id = conn.execute(text(
                "INSERT INTO paper (title, title_norm, source) "
                "VALUES ('t', 't', 'test') RETURNING id")).scalar_one()
            refs = [conn.execute(text(
                "INSERT INTO reference (citing_paper_id, ordinal) VALUES (:p, :o) RETURNING id"),
                {"p": paper_id, "o": o}).scalar_one() for o in (3, 4, 5)]

            passages = [conn.execute(text(
                "INSERT INTO passage (paper_id, splitter_ver, ordinal, section, text) "
                "VALUES (:p, '1.0.0', :o, 'results', :t) RETURNING id"),
                {"p": paper_id, "o": o, "t": f"passage {o}"}).scalar_one() for o in (0, 1)]
            near = [1.0] + [0.0] * 767
            far = [0.0, 1.0] + [0.0] * 766
            for pid, vec in zip(passages, (near, far)):
                conn.execute(text(
                    "INSERT INTO passage_embedding (passage_id, model, embedding) "
                    "VALUES (:p, 'test-model', :v)"), {"p": pid, "v": str(vec)})
            # A second model's vector for the same passage is a separate row.
            conn.execute(text(
                "INSERT INTO passage_embedding (passage_id, model, embedding) "
                "VALUES (:p, 'other-model', :v)"), {"p": passages[0], "v": str(far)})
            nearest = conn.execute(text(
                "SELECT passage_id FROM passage_embedding WHERE model = 'test-model' "
                "ORDER BY embedding <=> :q LIMIT 1"), {"q": str(near)}).scalar_one()
            assert nearest == passages[0]

            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(text(
                    "INSERT INTO passage_embedding (passage_id, model, embedding) "
                    "VALUES (:p, 'test-model', :v)"), {"p": passages[0], "v": str(near)})

            # "[3-5]": one context, three references.
            ctx = conn.execute(text(
                "INSERT INTO citation_context (citing_paper_id, extractor_ver, ordinal, "
                "marker, sentence, context_text) VALUES (:p, '1.0.0', 0, '[3-5]', 's', 'c s c') "
                "RETURNING id"), {"p": paper_id}).scalar_one()
            for r in refs:
                conn.execute(text(
                    "INSERT INTO citation_context_reference VALUES (:c, :r)"), {"c": ctx, "r": r})
            assert conn.execute(text(
                "SELECT count(*) FROM citation_context_reference WHERE citation_context_id = :c"),
                {"c": ctx}).scalar_one() == 3
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(text(
                    "INSERT INTO citation_context (citing_paper_id, extractor_ver, ordinal, "
                    "sentence, context_text) VALUES (:p, '1.0.0', 0, 's', 'c')"), {"p": paper_id})

            item = conn.execute(text(
                "INSERT INTO eval_item (set_name, split, claim_text, source_paper_id, label, license) "
                "VALUES ('external', 'test', 'claim', :p, 'supported', 'CC-BY-4.0') RETURNING id"),
                {"p": paper_id}).scalar_one()
            conn.execute(text("INSERT INTO eval_item_gold_passage VALUES (:i, :p)"),
                         {"i": item, "p": passages[0]})
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(text("DELETE FROM passage WHERE id = :p"), {"p": passages[0]})
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(text(
                    "INSERT INTO eval_item (set_name, split, claim_text) "
                    "VALUES ('external', 'test', 'no licence')"))

            # A passage nothing points at still cascades from its paper's embeddings.
            conn.execute(text("DELETE FROM passage WHERE id = :p"), {"p": passages[1]})
            assert conn.execute(text(
                "SELECT count(*) FROM passage_embedding WHERE passage_id = :p"),
                {"p": passages[1]}).scalar_one() == 0
            conn.rollback()
    finally:
        engine.dispose()

