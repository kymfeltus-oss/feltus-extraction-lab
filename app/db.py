from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS lab_documents (
    id TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    source_relative_path TEXT NOT NULL DEFAULT '',
    stored_path TEXT NOT NULL,
    supabase_storage_path TEXT,
    sha256 TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_lab_documents_sha256 ON lab_documents(sha256);

CREATE TABLE IF NOT EXISTS lab_runs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES lab_documents(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('PROCESSING','VALIDATED','NEEDS_REVIEW','FAILED')),
    parser_version TEXT NOT NULL,
    document_type TEXT NOT NULL DEFAULT 'UNKNOWN',
    classification_confidence REAL NOT NULL DEFAULT 0,
    extraction_confidence REAL NOT NULL DEFAULT 0,
    ocr_used INTEGER NOT NULL DEFAULT 0,
    raw_text TEXT NOT NULL DEFAULT '',
    pages_json TEXT NOT NULL DEFAULT '[]',
    tables_json TEXT NOT NULL DEFAULT '[]',
    normalized_json TEXT NOT NULL DEFAULT '{}',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    errors_json TEXT NOT NULL DEFAULT '[]',
    started_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_lab_runs_document ON lab_runs(document_id, started_at DESC);

CREATE TABLE IF NOT EXISTS lab_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES lab_runs(id) ON DELETE CASCADE,
    field_path TEXT NOT NULL,
    value_json TEXT NOT NULL,
    page_number INTEGER,
    source_text TEXT,
    source_bbox_json TEXT,
    confidence REAL NOT NULL,
    UNIQUE(run_id, field_path)
);
CREATE INDEX IF NOT EXISTS ix_lab_fields_run ON lab_fields(run_id);

CREATE TABLE IF NOT EXISTS lab_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES lab_runs(id) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK(decision IN ('APPROVED','REJECTED','NEEDS_CORRECTION')),
    note TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_lab_reviews_run ON lab_reviews(run_id, reviewed_at DESC);

CREATE TABLE IF NOT EXISTS lab_interpretations (
    id TEXT PRIMARY KEY,
    source_run_id TEXT NOT NULL REFERENCES lab_runs(id) ON DELETE CASCADE,
    interpreter_type TEXT NOT NULL,
    interpreter_version TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_lab_interpretations_source ON lab_interpretations(source_run_id, created_at DESC);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            # First run the basic schema without the new columns
            conn.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS lab_documents (
                id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                source_relative_path TEXT NOT NULL DEFAULT '',
                stored_path TEXT NOT NULL,
                supabase_storage_path TEXT,
                sha256 TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                page_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_lab_documents_sha256 ON lab_documents(sha256);

            CREATE TABLE IF NOT EXISTS lab_runs (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES lab_documents(id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('PROCESSING','VALIDATED','NEEDS_REVIEW','FAILED')),
                parser_version TEXT NOT NULL,
                document_type TEXT NOT NULL DEFAULT 'UNKNOWN',
                classification_confidence REAL NOT NULL DEFAULT 0,
                extraction_confidence REAL NOT NULL DEFAULT 0,
                ocr_used INTEGER NOT NULL DEFAULT 0,
                raw_text TEXT NOT NULL DEFAULT '',
                pages_json TEXT NOT NULL DEFAULT '[]',
                tables_json TEXT NOT NULL DEFAULT '[]',
                normalized_json TEXT NOT NULL DEFAULT '{}',
                warnings_json TEXT NOT NULL DEFAULT '[]',
                errors_json TEXT NOT NULL DEFAULT '[]',
                started_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_lab_runs_document ON lab_runs(document_id, started_at DESC);

            CREATE TABLE IF NOT EXISTS lab_fields (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES lab_runs(id) ON DELETE CASCADE,
                field_path TEXT NOT NULL,
                value_json TEXT NOT NULL,
                page_number INTEGER,
                source_text TEXT,
                source_bbox_json TEXT,
                confidence REAL NOT NULL,
                UNIQUE(run_id, field_path)
            );
            CREATE INDEX IF NOT EXISTS ix_lab_fields_run ON lab_fields(run_id);

            CREATE TABLE IF NOT EXISTS lab_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES lab_runs(id) ON DELETE CASCADE,
                decision TEXT NOT NULL CHECK(decision IN ('APPROVED','REJECTED','NEEDS_CORRECTION')),
                note TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_lab_reviews_run ON lab_reviews(run_id, reviewed_at DESC);

            CREATE TABLE IF NOT EXISTS lab_interpretations (
                id TEXT PRIMARY KEY,
                source_run_id TEXT NOT NULL REFERENCES lab_runs(id) ON DELETE CASCADE,
                interpreter_type TEXT NOT NULL,
                interpreter_version TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_lab_interpretations_source ON lab_interpretations(source_run_id, created_at DESC);
            """)
            
            # Legacy migrations
            columns = {row[1] for row in conn.execute("PRAGMA table_info(lab_runs)").fetchall()}
            if "pages_json" not in columns:
                conn.execute("ALTER TABLE lab_runs ADD COLUMN pages_json TEXT NOT NULL DEFAULT '[]'")
            document_columns = {row[1] for row in conn.execute("PRAGMA table_info(lab_documents)").fetchall()}
            if "source_relative_path" not in document_columns:
                conn.execute("ALTER TABLE lab_documents ADD COLUMN source_relative_path TEXT NOT NULL DEFAULT ''")
            
            # Multi-tenant migration
            # Check if organizations table exists
            org_tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='organizations'").fetchall()
            if not org_tables:
                # Create new multi-tenant tables
                conn.executescript("""
                CREATE TABLE IF NOT EXISTS organizations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_organizations_slug ON organizations(slug);

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_users_email ON users(email);

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_sessions_token ON sessions(token);
                CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

                CREATE TABLE IF NOT EXISTS organization_members (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('owner','admin','member')),
                    created_at TEXT NOT NULL,
                    UNIQUE(organization_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS ix_organization_members_org ON organization_members(organization_id);
                CREATE INDEX IF NOT EXISTS ix_organization_members_user ON organization_members(user_id);
                """)
                
                # Create default organization
                import uuid
                default_org_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO organizations (id, name, slug, created_at) VALUES (?, ?, ?, ?)",
                    (default_org_id, "FELTUS Extraction Lab", "feltus", now_iso())
                )
                
                # Add organization_id columns to existing tables
                doc_columns = {row[1] for row in conn.execute("PRAGMA table_info(lab_documents)").fetchall()}
                if "organization_id" not in doc_columns:
                    conn.execute("ALTER TABLE lab_documents ADD COLUMN organization_id TEXT")
                
                runs_columns = {row[1] for row in conn.execute("PRAGMA table_info(lab_runs)").fetchall()}
                if "organization_id" not in runs_columns:
                    conn.execute("ALTER TABLE lab_runs ADD COLUMN organization_id TEXT")
                
                reviews_columns = {row[1] for row in conn.execute("PRAGMA table_info(lab_reviews)").fetchall()}
                if "organization_id" not in reviews_columns:
                    conn.execute("ALTER TABLE lab_reviews ADD COLUMN organization_id TEXT")
                
                # Backfill existing data to default organization
                conn.execute("UPDATE lab_documents SET organization_id = ? WHERE organization_id IS NULL", (default_org_id,))
                conn.execute("UPDATE lab_runs SET organization_id = ? WHERE organization_id IS NULL", (default_org_id,))
                conn.execute("UPDATE lab_reviews SET organization_id = ? WHERE organization_id IS NULL", (default_org_id,))
                
                # Create new indexes
                conn.execute("CREATE INDEX IF NOT EXISTS ix_lab_documents_org_sha256 ON lab_documents(organization_id, sha256)")
                conn.execute("CREATE INDEX IF NOT EXISTS ix_lab_runs_org_status ON lab_runs(organization_id, status)")
                conn.execute("CREATE INDEX IF NOT EXISTS ix_lab_reviews_org_run ON lab_reviews(organization_id, run_id)")
            else:
                # Handle case where organizations table exists but might be empty or migration incomplete
                org_count = conn.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]
                if org_count == 0:
                    # Create default organization
                    import uuid
                    default_org_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO organizations (id, name, slug, created_at) VALUES (?, ?, ?, ?)",
                        (default_org_id, "FELTUS Extraction Lab", "feltus", now_iso())
                    )
                    
                    # Add organization_id columns if missing
                    doc_columns = {row[1] for row in conn.execute("PRAGMA table_info(lab_documents)").fetchall()}
                    if "organization_id" not in doc_columns:
                        conn.execute("ALTER TABLE lab_documents ADD COLUMN organization_id TEXT")
                    
                    runs_columns = {row[1] for row in conn.execute("PRAGMA table_info(lab_runs)").fetchall()}
                    if "organization_id" not in runs_columns:
                        conn.execute("ALTER TABLE lab_runs ADD COLUMN organization_id TEXT")
                    
                    reviews_columns = {row[1] for row in conn.execute("PRAGMA table_info(lab_reviews)").fetchall()}
                    if "organization_id" not in reviews_columns:
                        conn.execute("ALTER TABLE lab_reviews ADD COLUMN organization_id TEXT")
                    
                    # Add password_hash column to users if missing
                    user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
                    if "password_hash" not in user_columns:
                        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
                    
                    # Create sessions table if missing
                    session_tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'").fetchall()
                    if not session_tables:
                        conn.execute("""
                        CREATE TABLE IF NOT EXISTS sessions (
                            id TEXT PRIMARY KEY,
                            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                            token TEXT NOT NULL UNIQUE,
                            created_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS ix_sessions_token ON sessions(token);
                        CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
                        """)
                    
                    # Backfill existing data to default organization
                    conn.execute("UPDATE lab_documents SET organization_id = ? WHERE organization_id IS NULL", (default_org_id,))
                    conn.execute("UPDATE lab_runs SET organization_id = ? WHERE organization_id IS NULL", (default_org_id,))
                    conn.execute("UPDATE lab_reviews SET organization_id = ? WHERE organization_id IS NULL", (default_org_id,))
                    
                    # Create new indexes
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_lab_documents_org_sha256 ON lab_documents(organization_id, sha256)")
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_lab_runs_org_status ON lab_runs(organization_id, status)")
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_lab_reviews_org_run ON lab_reviews(organization_id, run_id)")
                
                # Authentication migrations (always run for existing tables)
                # Add password_hash column to users if missing
                user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
                if "password_hash" not in user_columns:
                    conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT NOT NULL DEFAULT ''")
                
                # Create sessions table if missing
                session_tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'").fetchall()
                if not session_tables:
                    conn.execute("""
                    CREATE TABLE IF NOT EXISTS sessions (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        token TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL
                    );
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_sessions_token ON sessions(token)")
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id)")
                
                # Add domain column to organizations if missing
                org_columns = {row[1] for row in conn.execute("PRAGMA table_info(organizations)").fetchall()}
                if "domain" not in org_columns:
                    conn.execute("ALTER TABLE organizations ADD COLUMN domain TEXT")
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_organizations_domain ON organizations(domain)")
                
                # Supabase Storage path for new uploads
                document_columns = {row[1] for row in conn.execute("PRAGMA table_info(lab_documents)").fetchall()}
                if "supabase_storage_path" not in document_columns:
                    conn.execute("ALTER TABLE lab_documents ADD COLUMN supabase_storage_path TEXT")

                # Tenant branding table
                branding_tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tenant_branding'").fetchall()
                if not branding_tables:
                    conn.execute("""
                    CREATE TABLE IF NOT EXISTS tenant_branding (
                        organization_id TEXT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
                        app_name TEXT NOT NULL DEFAULT '',
                        brand_name TEXT NOT NULL DEFAULT '',
                        logo_url TEXT,
                        favicon_url TEXT,
                        primary_color TEXT,
                        secondary_color TEXT,
                        accent_color TEXT,
                        company_name TEXT,
                        support_email TEXT,
                        footer_text TEXT,
                        privacy_url TEXT,
                        terms_url TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_tenant_branding_org ON tenant_branding(organization_id)")
                    
                    # Insert default FELTUS branding
                    feltus = conn.execute("SELECT * FROM organizations WHERE slug = ?", ("feltus",)).fetchone()
                    if feltus:
                        conn.execute("""
                        INSERT INTO tenant_branding (organization_id, app_name, brand_name, logo_url, favicon_url,
                            primary_color, secondary_color, accent_color, company_name, support_email,
                            footer_text, privacy_url, terms_url, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            feltus["id"], "FELTUS Extraction Lab", "FELTUS", "/images/logo.png", None,
                            "#07172a", "#40bb90", "#50d6a6", "FELTUS", None,
                            "FELTUS Extraction Lab", None, None, now_iso(), now_iso()
                        ))
                else:
                    # Update existing FELTUS branding to set default logo if not set
                    feltus = conn.execute("SELECT * FROM organizations WHERE slug = ?", ("feltus",)).fetchone()
                    if feltus:
                        conn.execute("""
                        UPDATE tenant_branding SET logo_url = ?
                        WHERE organization_id = ? AND (logo_url IS NULL OR logo_url = '')
                        """, ("/images/logo.png", feltus["id"]))

                # Migrate legacy default logo paths to the authoritative public asset
                conn.execute("""
                UPDATE tenant_branding SET logo_url = ?
                WHERE logo_url = ?
                """, ("/images/logo.png", "/static/images/logo.png"))

                # Usage tracking table
                usage_tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='usage_records'").fetchall()
                if not usage_tables:
                    conn.execute("""
                    CREATE TABLE IF NOT EXISTS usage_records (
                        id TEXT PRIMARY KEY,
                        organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        event_type TEXT NOT NULL CHECK(event_type IN ('PDF_EXTRACTION')),
                        document_id TEXT NOT NULL REFERENCES lab_documents(id) ON DELETE CASCADE,
                        page_count INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    );
                    """)
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_usage_records_org ON usage_records(organization_id)")
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_usage_records_user ON usage_records(user_id)")
                    conn.execute("CREATE INDEX IF NOT EXISTS ix_usage_records_created ON usage_records(created_at)")

            # Deterministic fix: ensure organizations.domain exists for every initialization path
            if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='organizations'").fetchone():
                org_columns = {row[1] for row in conn.execute("PRAGMA table_info(organizations)").fetchall()}
                if "domain" not in org_columns:
                    conn.execute("ALTER TABLE organizations ADD COLUMN domain TEXT")
                conn.execute("CREATE INDEX IF NOT EXISTS ix_organizations_domain ON organizations(domain)")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def insert_document(self, row: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO lab_documents
                (id, original_filename, source_relative_path, stored_path, supabase_storage_path, sha256, media_type, size_bytes, page_count, organization_id, created_at)
                VALUES (:id,:original_filename,:source_relative_path,:stored_path,:supabase_storage_path,:sha256,:media_type,:size_bytes,:page_count,:organization_id,:created_at)""",
                row,
            )

    def update_page_count(self, document_id: str, page_count: int) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE lab_documents SET page_count=? WHERE id=?", (page_count, document_id))

    def update_supabase_storage_path(self, document_id: str, organization_id: str, storage_path: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE lab_documents SET supabase_storage_path=? WHERE id=? AND organization_id=?",
                (storage_path, document_id, organization_id),
            )

    def insert_run(self, row: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO lab_runs
                (id, document_id, status, parser_version, organization_id, started_at)
                VALUES (:id,:document_id,'PROCESSING',:parser_version,:organization_id,:started_at)""",
                row,
            )

    def complete_run(self, run_id: str, result: dict[str, Any], fields: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE lab_runs SET status=:status, document_type=:document_type,
                classification_confidence=:classification_confidence,
                extraction_confidence=:extraction_confidence, ocr_used=:ocr_used,
                raw_text=:raw_text, pages_json=:pages_json, tables_json=:tables_json, normalized_json=:normalized_json,
                warnings_json=:warnings_json, errors_json=:errors_json, organization_id=:organization_id, completed_at=:completed_at
                WHERE id=:id""",
                {
                    "id": run_id,
                    "status": result["status"],
                    "document_type": result["document_type"],
                    "classification_confidence": result["classification_confidence"],
                    "extraction_confidence": result["extraction_confidence"],
                    "ocr_used": int(result["ocr_used"]),
                    "raw_text": result["raw_text"],
                    "pages_json": json.dumps(result["pages"]),
                    "tables_json": json.dumps(result["tables"]),
                    "normalized_json": json.dumps(result["normalized"]),
                    "warnings_json": json.dumps(result["warnings"]),
                    "errors_json": json.dumps(result["errors"]),
                    "organization_id": result.get("organization_id"),
                    "completed_at": now_iso(),
                },
            )
            conn.executemany(
                """INSERT INTO lab_fields
                (run_id,field_path,value_json,page_number,source_text,source_bbox_json,confidence)
                VALUES (:run_id,:field_path,:value_json,:page_number,:source_text,:source_bbox_json,:confidence)""",
                [{**field, "run_id": run_id} for field in fields],
            )

    def fail_run(self, run_id: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE lab_runs SET status='FAILED', errors_json=?, completed_at=? WHERE id=?",
                (json.dumps([message]), now_iso(), run_id),
            )

    def list_runs(self, organization_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if organization_id:
                rows = conn.execute(
                    """SELECT r.id, r.status, r.document_type, r.classification_confidence,
                    r.extraction_confidence, r.ocr_used, r.started_at, r.completed_at,
                    d.id document_id, d.original_filename, d.source_relative_path, d.size_bytes, d.page_count,
                    (SELECT decision FROM lab_reviews WHERE run_id = r.id ORDER BY reviewed_at DESC LIMIT 1) AS latest_review
                    FROM lab_runs r JOIN lab_documents d ON d.id=r.document_id
                    WHERE r.organization_id = ?
                    ORDER BY r.started_at DESC""",
                    (organization_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT r.id, r.status, r.document_type, r.classification_confidence,
                    r.extraction_confidence, r.ocr_used, r.started_at, r.completed_at,
                    d.id document_id, d.original_filename, d.source_relative_path, d.size_bytes, d.page_count,
                    (SELECT decision FROM lab_reviews WHERE run_id = r.id ORDER BY reviewed_at DESC LIMIT 1) AS latest_review
                    FROM lab_runs r JOIN lab_documents d ON d.id=r.document_id
                    ORDER BY r.started_at DESC"""
                ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str, organization_id: str | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            if organization_id:
                row = conn.execute(
                    """SELECT r.*, d.original_filename, d.source_relative_path, d.size_bytes, d.page_count, d.sha256
                    FROM lab_runs r JOIN lab_documents d ON d.id=r.document_id WHERE r.id=? AND r.organization_id = ?""",
                    (run_id, organization_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT r.*, d.original_filename, d.source_relative_path, d.size_bytes, d.page_count, d.sha256
                    FROM lab_runs r JOIN lab_documents d ON d.id=r.document_id WHERE r.id=?""",
                    (run_id,),
                ).fetchone()
            if not row:
                return None
            fields = conn.execute(
                "SELECT field_path,value_json,page_number,source_text,source_bbox_json,confidence FROM lab_fields WHERE run_id=? ORDER BY field_path",
                (run_id,),
            ).fetchall()
            reviews = conn.execute(
                "SELECT decision,note,reviewed_at FROM lab_reviews WHERE run_id=? ORDER BY reviewed_at DESC",
                (run_id,),
            ).fetchall()
        result = dict(row)
        for key in ("pages_json", "tables_json", "normalized_json", "warnings_json", "errors_json"):
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
        result["ocr_used"] = bool(result["ocr_used"])
        result["fields"] = []
        for field in fields:
            item = dict(field)
            item["value"] = json.loads(item.pop("value_json"))
            item["source_bbox"] = json.loads(item.pop("source_bbox_json")) if item["source_bbox_json"] else None
            result["fields"].append(item)
        result["reviews"] = [dict(review) for review in reviews]
        return result

    def get_document(self, document_id: str, organization_id: str | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            if organization_id:
                row = conn.execute(
                    "SELECT * FROM lab_documents WHERE id=? AND organization_id = ?",
                    (document_id, organization_id)
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM lab_documents WHERE id=?", (document_id,)).fetchone()
        return dict(row) if row else None

    def add_review(self, run_id: str, decision: str, note: str, organization_id: str | None = None) -> None:
        with self.connect() as conn:
            if organization_id:
                exists = conn.execute(
                    "SELECT 1 FROM lab_runs WHERE id=? AND organization_id=?",
                    (run_id, organization_id)
                ).fetchone()
            else:
                exists = conn.execute("SELECT 1 FROM lab_runs WHERE id=?", (run_id,)).fetchone()
            if not exists:
                raise KeyError(run_id)
            conn.execute(
                "INSERT INTO lab_reviews(run_id,decision,note,organization_id,reviewed_at) VALUES (?,?,?,?,?)",
                (run_id, decision, note, organization_id, now_iso()),
            )

    def insert_interpretation(self, row: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO lab_interpretations
                (id,source_run_id,interpreter_type,interpreter_version,status,result_json,created_at)
                VALUES (:id,:source_run_id,:interpreter_type,:interpreter_version,:status,:result_json,:created_at)""",
                {**row, "result_json": json.dumps(row["result"])},
            )

    def list_interpretations(self, source_run_id: str, organization_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if organization_id:
                # Verify run belongs to organization
                run = conn.execute(
                    "SELECT id FROM lab_runs WHERE id = ? AND organization_id = ?",
                    (source_run_id, organization_id)
                ).fetchone()
                if not run:
                    return []
            rows = conn.execute(
                "SELECT * FROM lab_interpretations WHERE source_run_id=? ORDER BY created_at DESC",
                (source_run_id,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["result"] = json.loads(item.pop("result_json"))
            results.append(item)
        return results

    def get_interpretation(self, interpretation_id: str, organization_id: str | None = None) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM lab_interpretations WHERE id=?", (interpretation_id,)).fetchone()
            if not row:
                return None
            if organization_id:
                # Verify source run belongs to organization
                run = conn.execute(
                    "SELECT id FROM lab_runs WHERE id = ? AND organization_id = ?",
                    (row["source_run_id"], organization_id)
                ).fetchone()
                if not run:
                    return None
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json"))
        return item

    def dashboard(self, organization_id: str | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            # Total uploads = count of lab_documents
            if organization_id:
                total_uploads = conn.execute(
                    "SELECT COUNT(*) FROM lab_documents WHERE organization_id = ?", (organization_id,)
                ).fetchone()[0]
            else:
                total_uploads = conn.execute("SELECT COUNT(*) FROM lab_documents").fetchone()[0]
            
            # Total extracted = documents whose latest run status is VALIDATED
            # Get the latest run for each document
            if organization_id:
                latest_runs = conn.execute("""
                    SELECT lr.document_id, lr.status
                    FROM lab_runs lr
                    WHERE lr.organization_id = ?
                    AND lr.started_at = (
                        SELECT MAX(started_at)
                        FROM lab_runs
                        WHERE document_id = lr.document_id AND organization_id = ?
                    )
                """, (organization_id, organization_id)).fetchall()
            else:
                latest_runs = conn.execute("""
                    SELECT lr.document_id, lr.status
                    FROM lab_runs lr
                    WHERE lr.started_at = (
                        SELECT MAX(started_at)
                        FROM lab_runs
                        WHERE document_id = lr.document_id
                    )
                """).fetchall()
            
            total_extracted = sum(1 for run in latest_runs if run[1] == "VALIDATED")
            
            # Total rejected = documents whose latest run has latest review decision REJECTED
            total_rejected = 0
            for run in latest_runs:
                document_id = run[0]
                # Get the latest run for this document
                if organization_id:
                    latest_run = conn.execute("""
                        SELECT id FROM lab_runs
                        WHERE document_id = ? AND organization_id = ?
                        ORDER BY started_at DESC
                        LIMIT 1
                    """, (document_id, organization_id)).fetchone()
                else:
                    latest_run = conn.execute("""
                        SELECT id FROM lab_runs
                        WHERE document_id = ?
                        ORDER BY started_at DESC
                        LIMIT 1
                    """, (document_id,)).fetchone()
                
                if latest_run:
                    run_id = latest_run[0]
                    # Get the latest review decision for this run
                    latest_review = conn.execute("""
                        SELECT decision FROM lab_reviews
                        WHERE run_id = ?
                        ORDER BY reviewed_at DESC
                        LIMIT 1
                    """, (run_id,)).fetchone()
                    
                    if latest_review and latest_review[0] == "REJECTED":
                        total_rejected += 1
            
            return {
                "total_uploads": total_uploads,
                "total_extracted": total_extracted,
                "total_rejected": total_rejected
            }

    def create_usage_record(self, record: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO usage_records
                (id, organization_id, user_id, event_type, document_id, page_count, created_at)
                VALUES (:id, :organization_id, :user_id, :event_type, :document_id, :page_count, :created_at)""",
                record,
            )

    def get_usage(self, organization_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            current_month = datetime.now(timezone.utc).strftime("%Y-%m")
            totals = conn.execute(
                """SELECT COUNT(*) as total_pdfs, COALESCE(SUM(page_count), 0) as total_pages
                   FROM usage_records WHERE organization_id = ?""",
                (organization_id,),
            ).fetchone()
            month = conn.execute(
                """SELECT COUNT(*) as month_pdfs, COALESCE(SUM(page_count), 0) as month_pages
                   FROM usage_records
                   WHERE organization_id = ? AND substr(created_at, 1, 7) = ?""",
                (organization_id, current_month),
            ).fetchone()
        return {
            "total_pdfs_processed": totals["total_pdfs"],
            "total_pages_processed": totals["total_pages"],
            "current_month_pdfs": month["month_pdfs"],
            "current_month_pages": month["month_pages"],
        }

    def get_default_organization(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM organizations WHERE slug = ?", ("feltus",)).fetchone()
        return dict(row) if row else None

    def get_public_branding(self, slug: str | None = None, hostname: str | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            org_id = None
            if slug:
                row = conn.execute("SELECT id FROM organizations WHERE slug = ?", (slug,)).fetchone()
                if row:
                    org_id = row["id"]
            if not org_id and hostname:
                # Strip port if present
                clean_host = hostname.split(":")[0]
                row = conn.execute(
                    "SELECT id FROM organizations WHERE domain = ? OR slug = ?",
                    (clean_host, clean_host)
                ).fetchone()
                if row:
                    org_id = row["id"]

            if org_id:
                brand = conn.execute("SELECT * FROM tenant_branding WHERE organization_id = ?", (org_id,)).fetchone()
                if brand:
                    return {
                        "app_name": brand["app_name"],
                        "brand_name": brand["brand_name"],
                        "logo_url": brand["logo_url"],
                        "favicon_url": brand["favicon_url"],
                        "primary_color": brand["primary_color"],
                        "secondary_color": brand["secondary_color"],
                        "accent_color": brand["accent_color"],
                        "company_name": brand["company_name"],
                        "support_email": brand["support_email"],
                        "footer_text": brand["footer_text"],
                        "privacy_url": brand["privacy_url"],
                        "terms_url": brand["terms_url"],
                    }

            return self.get_default_branding()

    def get_default_branding(self) -> dict[str, Any]:
        return {
            "app_name": "FELTUS Extraction Lab",
            "brand_name": "FELTUS",
            "logo_url": "/images/logo.png",
            "favicon_url": None,
            "primary_color": "#07172a",
            "secondary_color": "#40bb90",
            "accent_color": "#50d6a6",
            "company_name": "FELTUS",
            "support_email": None,
            "footer_text": "FELTUS Extraction Lab",
            "privacy_url": None,
            "terms_url": None,
        }

    def get_branding(self, organization_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tenant_branding WHERE organization_id = ?",
                (organization_id,)
            ).fetchone()
        if not row:
            return self.get_default_branding()
        return {
            "app_name": row["app_name"],
            "brand_name": row["brand_name"],
            "logo_url": row["logo_url"],
            "favicon_url": row["favicon_url"],
            "primary_color": row["primary_color"],
            "secondary_color": row["secondary_color"],
            "accent_color": row["accent_color"],
            "company_name": row["company_name"],
            "support_email": row["support_email"],
            "footer_text": row["footer_text"],
            "privacy_url": row["privacy_url"],
            "terms_url": row["terms_url"],
        }

    def create_session(self, user_id: str, token: str, expires_at: str) -> None:
        import uuid
        session_id = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, user_id, token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, user_id, token, now_iso(), expires_at)
            )

    def delete_session(self, token: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def cleanup_expired_sessions(self) -> int:
        with self.connect() as conn:
            result = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))
            return result.rowcount
