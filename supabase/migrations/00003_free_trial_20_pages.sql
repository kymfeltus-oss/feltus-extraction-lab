-- FELTUS Extraction Lab
-- Migration 00003: Free tier becomes one free month with 20 pages
-- (2x the previous 10-page lifetime allowance).
--
-- The 30-day window uses the existing organization_billing.trial_started_at
-- column; enforcement lives in the API (app/supabase_billing.py).

-- Widen the free-trial page cap from 10 to 20.
ALTER TABLE public.organization_billing
    DROP CONSTRAINT IF EXISTS organization_billing_free_trial_page_limit_check;

ALTER TABLE public.organization_billing
    ALTER COLUMN free_trial_page_limit SET DEFAULT 20;

ALTER TABLE public.organization_billing
    ADD CONSTRAINT organization_billing_free_trial_page_limit_check
    CHECK (
        free_trial_page_limit >= 0
        AND free_trial_page_limit <= 20
    );

-- Existing free-tier organizations get the doubled allowance.
UPDATE public.organization_billing
SET free_trial_page_limit = 20
WHERE plan_code = 'free_trial'
  AND free_trial_page_limit < 20;

-- New organizations get 20 pages via the creation trigger.
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
        20
    )
    ON CONFLICT (organization_id) DO NOTHING;

    RETURN NEW;

END;
$$;

-- Keep the catalog definition in sync.
UPDATE public.billing_plans
SET page_limit = 20
WHERE code = 'free_trial';
