# Database Outage

1. Remove API/worker readiness and stop mutations; do not run migrations.
2. Check database availability, storage, connections, replication, and recent changes.
3. Fail over or restore from the latest verified backup under the database owner.
4. Run integrity checks, Alembic version inspection, and a replay smoke test.
5. Reintroduce worker then API traffic; document recovery point and recovery time.

