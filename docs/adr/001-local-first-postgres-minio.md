# ADR-001: Use PostgreSQL and S3-compatible storage for the local-first platform

- Status: Accepted
- Date: 2026-07-28

## Context

The portfolio needs to demonstrate production data-engineering properties on a recruiter
laptop: replayable raw data, relational analytics, orchestration, quality gates, lineage,
least privilege, and a credible cloud migration path. A cloud-only demo would introduce
cost and credentials; a single embedded database would hide operational boundaries.

## Decision

Use MinIO for immutable raw objects, PostgreSQL 16 for staging/core/marts, dbt for
transformations, Airflow for orchestration, and Metabase for consumption. All components
run in OCI containers through Docker Compose. Python talks to object storage through the
S3 API and to PostgreSQL through standard SQL.

## Consequences

The demo is self-contained and maps directly to S3 plus managed PostgreSQL on a cloud.
Raw and warehouse lifecycles remain separate. The cost is a heavier local stack and
PostgreSQL-specific SQL such as `generate_series`, `COPY`, and `distinct` date handling.
At high scale the fact workload would move to a distributed warehouse while the source
contract and object layout remain stable.
