-- FELTUS Extraction Lab
-- Migration 00002: Billing, plans, subscriptions, and Stripe integration
--
-- IMPORTANT:
-- Existing extraction tables and usage_records are intentionally untouched.
-- Free trial is capped at 10 lifetime pages per organization.

-- ============================================================================
-- BILLING PLANS
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.billing_plans (
    code                     TEXT PRIMARY KEY,
    display_name             TEXT NOT NULL,

    -- Extraction allowance
    page_limit               INTEGER,
    usage_period             TEXT NOT NULL
        CHECK (usage_period IN ('lifetime', 'monthly', 'custom')),

    member_limit             INTEGER,

    -- Stripe identifiers will be populated later
    stripe_product_id        TEXT,
    stripe_monthly_price_id  TEXT,
    stripe_annual_price_id   TEXT,

    is_paid                  BOOLEAN NOT NULL DEFAULT FALSE,
    is_active                BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order               INTEGER NOT NULL DEFAULT 0,

    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK (page_limit IS NULL OR page_limit >= 0),
    CHECK (member_limit IS NULL OR member_limit > 0),
    CHECK (
        usage_period = 'custom'
        OR page_limit IS NOT NULL
    )
);

-- ============================================================================
-- DEFAULT FELTUS PLAN DEFINITIONS
--
-- Stripe prices are intentionally NOT hard-coded here.
-- We will create the Stripe products later and then store their IDs.
-- ============================================================================

INSERT INTO public.billing_plans (
    code,
    display_name,
    page_limit,
    usage_period,
    member_limit,
    is_paid,
    sort_order
)
VALUES
    (
        'free_trial',
        'Free Trial',
        10,
        'lifetime',
        1,
        FALSE,
        10
    ),
    (
        'starter',
        'Starter',
        500,
        'monthly',
        1,
        TRUE,
        20
    ),
    (
        'professional',
        'Professional',
        2500,
        'monthly',
        3,
        TRUE,
        30
    ),
    (
        'business',
        'Business',
        10000,
        'monthly',
        10,
        TRUE,
        40
    ),
    (
        'enterprise',
        'Enterprise',
        NULL,
        'custom',
        NULL,
        TRUE,
        50
    )
ON CONFLICT (code) DO NOTHING;

-- ============================================================================
-- ORGANIZATION BILLING STATE
--
-- One billing record per organization.
-- Billing belongs to the organization, not the individual user.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.organization_billing (
    organization_id          UUID PRIMARY KEY
        REFERENCES public.organizations(id)
        ON DELETE CASCADE,

    plan_code                TEXT NOT NULL DEFAULT 'free_trial'
        REFERENCES public.billing_plans(code),

    status                   TEXT NOT NULL DEFAULT 'trialing'
        CHECK (
            status IN (
                'trialing',
                'active',
                'past_due',
                'canceled',
                'unpaid',
                'incomplete',
                'incomplete_expired',
                'paused'
            )
        ),

    -- Stripe identifiers
    stripe_customer_id       TEXT UNIQUE,
    stripe_subscription_id   TEXT UNIQUE,
    stripe_price_id          TEXT,

    billing_interval         TEXT
        CHECK (
            billing_interval IS NULL
            OR billing_interval IN ('month', 'year')
        ),

    current_period_start     TIMESTAMPTZ,
    current_period_end       TIMESTAMPTZ,

    cancel_at_period_end     BOOLEAN NOT NULL DEFAULT FALSE,

    -- ------------------------------------------------------------------------
    -- FREE TRIAL CONTROL
    --
    -- This is intentionally organization-specific.
    -- A free trial may NEVER exceed 10 pages.
    -- It does not reset monthly.
    -- ------------------------------------------------------------------------

    free_trial_page_limit    INTEGER NOT NULL DEFAULT 10
        CHECK (
            free_trial_page_limit >= 0
            AND free_trial_page_limit <= 10
        ),

    free_trial_consumed      BOOLEAN NOT NULL DEFAULT FALSE,

    trial_started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    trial_completed_at       TIMESTAMPTZ,

    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_organization_billing_plan
    ON public.organization_billing(plan_code);

CREATE INDEX IF NOT EXISTS ix_organization_billing_status
    ON public.organization_billing(status);

-- ============================================================================
-- STRIPE WEBHOOK EVENT LOG
--
-- Prevents the same Stripe event from being processed twice.
-- We deliberately do not store the entire Stripe payload here.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.stripe_webhook_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    stripe_event_id     TEXT NOT NULL UNIQUE,
    event_type          TEXT NOT NULL,

    processed           BOOLEAN NOT NULL DEFAULT FALSE,

    received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at        TIMESTAMPTZ,

    error_text          TEXT
);

CREATE INDEX IF NOT EXISTS ix_stripe_webhook_events_processed
    ON public.stripe_webhook_events(processed, received_at);

-- ============================================================================
-- GIVE EXISTING ORGANIZATIONS A FREE TRIAL BILLING RECORD
-- ============================================================================

INSERT INTO public.organization_billing (
    organization_id,
    plan_code,
    status,
    free_trial_page_limit
)
SELECT
    id,
    'free_trial',
    'trialing',
    10
FROM public.organizations
ON CONFLICT (organization_id) DO NOTHING;

-- ============================================================================
-- AUTOMATICALLY CREATE BILLING STATE FOR FUTURE ORGANIZATIONS
-- ============================================================================

CREATE OR REPLACE FUNCTION public.create_default_organization_billing()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN

    INSERT INTO public.organization_billing (
        organization_id,
        plan_code,
        status,
        free_trial_page_limit
    )
    VALUES (
        NEW.id,
        'free_trial',
        'trialing',
        10
    )
    ON CONFLICT (organization_id) DO NOTHING;

    RETURN NEW;

END;
$$;

DROP TRIGGER IF EXISTS trg_create_default_organization_billing
ON public.organizations;

CREATE TRIGGER trg_create_default_organization_billing
AFTER INSERT
ON public.organizations
FOR EACH ROW
EXECUTE FUNCTION public.create_default_organization_billing();

-- ============================================================================
-- AUTOMATIC UPDATED_AT HANDLING
-- ============================================================================

CREATE OR REPLACE FUNCTION public.billing_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_billing_plans_updated_at
ON public.billing_plans;

CREATE TRIGGER trg_billing_plans_updated_at
BEFORE UPDATE
ON public.billing_plans
FOR EACH ROW
EXECUTE FUNCTION public.billing_set_updated_at();

DROP TRIGGER IF EXISTS trg_organization_billing_updated_at
ON public.organization_billing;

CREATE TRIGGER trg_organization_billing_updated_at
BEFORE UPDATE
ON public.organization_billing
FOR EACH ROW
EXECUTE FUNCTION public.billing_set_updated_at();

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

ALTER TABLE public.billing_plans
ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.organization_billing
ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.stripe_webhook_events
ENABLE ROW LEVEL SECURITY;

-- Authenticated users may read active plan definitions.

DROP POLICY IF EXISTS billing_plans_select_policy
ON public.billing_plans;

CREATE POLICY billing_plans_select_policy
ON public.billing_plans
FOR SELECT
TO authenticated
USING (is_active = TRUE);

-- Users may see billing information only for organizations
-- in which they are members.
--
-- There are deliberately NO browser/client INSERT or UPDATE policies.
-- Subscription changes will be performed by the trusted backend.

DROP POLICY IF EXISTS organization_billing_select_policy
ON public.organization_billing;

CREATE POLICY organization_billing_select_policy
ON public.organization_billing
FOR SELECT
TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM public.organization_members AS om
        WHERE om.organization_id =
              organization_billing.organization_id
          AND om.user_id = auth.uid()
    )
);

-- stripe_webhook_events intentionally receives no authenticated-user policy.
-- Only the trusted backend/service-role should access it.