"""Database guard that makes `audit_log` append-only (UPDATE and DELETE raise). Used by the migration and by its test.

Honest scope: this stops tampering through the application or an injected query. Whoever OWNS the table (the migration role) can still
drop the trigger: full protection needs a separate migration role and off-host log shipping (the JSON logs are shipped, see infra/monitoring).
"""

# One statement per item (asyncpg runs a single statement per call).
CREATE = [
    """CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only' USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log",
    "CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()",
]

DROP = [
    "DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log",
    "DROP FUNCTION IF EXISTS audit_log_append_only()",
]
