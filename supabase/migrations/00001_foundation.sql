-- FELTUS Extraction Lab Supabase foundation
-- Tables, indexes, RLS policies, and default FELTUS seed data.

-- Organizations ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.organizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    domain      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_organizations_slug ON public.organizations(slug);
CREATE INDEX IF NOT EXISTS ix_organizations_domain ON public.organizations(domain);

-- Organization membership (Supabase auth.users is the identity source) --------
CREATE TABLE IF NOT EXISTS public.organization_members (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','admin','member')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(organization_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_organization_members_org ON public.organization_members(organization_id);
CREATE INDEX IF NOT EXISTS ix_organization_members_user ON public.organization_members(user_id);

-- Tenant branding -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.tenant_branding (
    organization_id  UUID PRIMARY KEY REFERENCES public.organizations(id) ON DELETE CASCADE,
    app_name         TEXT NOT NULL DEFAULT '',
    brand_name       TEXT NOT NULL DEFAULT '',
    logo_url         TEXT,
    favicon_url      TEXT,
    primary_color    TEXT,
    secondary_color  TEXT,
    accent_color     TEXT,
    company_name     TEXT,
    support_email    TEXT,
    footer_text      TEXT,
    privacy_url      TEXT,
    terms_url        TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_tenant_branding_org ON public.tenant_branding(organization_id);

-- Extracted documents ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.lab_documents (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    original_filename   TEXT NOT NULL,
    source_relative_path TEXT NOT NULL DEFAULT '',
    stored_path         TEXT NOT NULL,
    sha256              TEXT NOT NULL,
    media_type          TEXT NOT NULL,
    size_bytes          INTEGER NOT NULL,
    page_count          INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_lab_documents_org_sha256 ON public.lab_documents(organization_id, sha256);

-- Extraction runs -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.lab_runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    document_id             UUID NOT NULL REFERENCES public.lab_documents(id) ON DELETE CASCADE,
    status                  TEXT NOT NULL CHECK (status IN ('PROCESSING','VALIDATED','NEEDS_REVIEW','FAILED')),
    parser_version          TEXT NOT NULL,
    document_type           TEXT NOT NULL DEFAULT 'UNKNOWN',
    classification_confidence REAL NOT NULL DEFAULT 0,
    extraction_confidence   REAL NOT NULL DEFAULT 0,
    ocr_used                INTEGER NOT NULL DEFAULT 0,
    raw_text                TEXT NOT NULL DEFAULT '',
    pages_json              JSONB NOT NULL DEFAULT '[]'::jsonb,
    tables_json             JSONB NOT NULL DEFAULT '[]'::jsonb,
    normalized_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    warnings_json           JSONB NOT NULL DEFAULT '[]'::jsonb,
    errors_json             JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_lab_runs_document ON public.lab_runs(document_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_lab_runs_org_status ON public.lab_runs(organization_id, status);

-- Reviews ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.lab_reviews (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    run_id          UUID NOT NULL REFERENCES public.lab_runs(id) ON DELETE CASCADE,
    decision        TEXT NOT NULL CHECK (decision IN ('APPROVED','REJECTED','NEEDS_CORRECTION')),
    note            TEXT NOT NULL DEFAULT '',
    reviewed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_lab_reviews_org_run ON public.lab_reviews(organization_id, run_id);

-- Usage tracking ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.usage_records (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL CHECK (event_type IN ('PDF_EXTRACTION')),
    document_id     UUID NOT NULL REFERENCES public.lab_documents(id) ON DELETE CASCADE,
    page_count      INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_usage_records_org ON public.usage_records(organization_id);
CREATE INDEX IF NOT EXISTS ix_usage_records_user ON public.usage_records(user_id);
CREATE INDEX IF NOT EXISTS ix_usage_records_created ON public.usage_records(created_at);

-- Row Level Security ------------------------------------------------------------
ALTER TABLE public.organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.organization_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenant_branding ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lab_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lab_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lab_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usage_records ENABLE ROW LEVEL SECURITY;

-- Helper: organizations the current auth user is a member of
CREATE OR REPLACE FUNCTION public.user_organization_ids()
RETURNS SETOF UUID
LANGUAGE sql
SECURITY DEFINER
AS $$
    SELECT organization_id FROM public.organization_members WHERE user_id = auth.uid();
$$;

-- Organizations: only those the user is a member of
DROP POLICY IF EXISTS org_select_policy ON public.organizations;
CREATE POLICY org_select_policy ON public.organizations
    FOR SELECT USING (
        id IN (SELECT public.user_organization_ids())
    );

-- Organization members: only those in the same organizations the user is in
DROP POLICY IF EXISTS member_select_policy ON public.organization_members;
CREATE POLICY member_select_policy ON public.organization_members
    FOR SELECT USING (
        organization_id IN (SELECT public.user_organization_ids())
    );

-- Tenant branding: only branding for organizations the user is in
DROP POLICY IF EXISTS branding_select_policy ON public.tenant_branding;
CREATE POLICY branding_select_policy ON public.tenant_branding
    FOR SELECT USING (
        organization_id IN (SELECT public.user_organization_ids())
    );

-- Lab documents: tenant-scoped CRUD
DROP POLICY IF EXISTS documents_tenant_policy ON public.lab_documents;
CREATE POLICY documents_tenant_policy ON public.lab_documents
    FOR ALL USING (
        organization_id IN (SELECT public.user_organization_ids())
    ) WITH CHECK (
        organization_id IN (SELECT public.user_organization_ids())
    );

-- Lab runs: tenant-scoped CRUD
DROP POLICY IF EXISTS runs_tenant_policy ON public.lab_runs;
CREATE POLICY runs_tenant_policy ON public.lab_runs
    FOR ALL USING (
        organization_id IN (SELECT public.user_organization_ids())
    ) WITH CHECK (
        organization_id IN (SELECT public.user_organization_ids())
    );

-- Lab reviews: tenant-scoped CRUD
DROP POLICY IF EXISTS reviews_tenant_policy ON public.lab_reviews;
CREATE POLICY reviews_tenant_policy ON public.lab_reviews
    FOR ALL USING (
        organization_id IN (SELECT public.user_organization_ids())
    ) WITH CHECK (
        organization_id IN (SELECT public.user_organization_ids())
    );

-- Usage records: tenant-scoped CRUD
DROP POLICY IF EXISTS usage_tenant_policy ON public.usage_records;
CREATE POLICY usage_tenant_policy ON public.usage_records
    FOR ALL USING (
        organization_id IN (SELECT public.user_organization_ids())
    ) WITH CHECK (
        organization_id IN (SELECT public.user_organization_ids())
    );

-- Default FELTUS seed ----------------------------------------------------------
INSERT INTO public.organizations (name, slug, created_at)
VALUES ('FELTUS Extraction Lab', 'feltus', now())
ON CONFLICT (slug) DO NOTHING;

INSERT INTO public.tenant_branding (
    organization_id, app_name, brand_name, logo_url, favicon_url,
    primary_color, secondary_color, accent_color, company_name,
    support_email, footer_text, privacy_url, terms_url, created_at, updated_at
)
SELECT
    id,
    'FELTUS Extraction Lab',
    'FELTUS',
    NULL,
    NULL,
    '#07172a',
    '#40bb90',
    '#50d6a6',
    'FELTUS',
    NULL,
    'FELTUS Extraction Lab',
    NULL,
    NULL,
    now(),
    now()
FROM public.organizations
WHERE slug = 'feltus'
ON CONFLICT (organization_id) DO NOTHING;