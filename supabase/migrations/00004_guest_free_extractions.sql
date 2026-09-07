-- One anonymous raw-text extraction per public IP address.
-- IP addresses are HMAC-hashed by the backend before storage.

CREATE TABLE IF NOT EXISTS public.guest_free_extractions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ip_hash         TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'completed')),
    page_count      INTEGER CHECK (page_count IS NULL OR page_count >= 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_guest_free_extractions_created_at
    ON public.guest_free_extractions(created_at DESC);

ALTER TABLE public.guest_free_extractions ENABLE ROW LEVEL SECURITY;

-- No browser policies are defined. The backend service role is the only
-- caller allowed to check, reserve, complete, or release a guest attempt.
