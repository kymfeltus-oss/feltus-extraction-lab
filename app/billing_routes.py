from __future__ import annotations

import os

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import AuthContext, get_current_user
from .supabase_billing import SupabaseBilling


router = APIRouter(
    prefix="/api/billing",
    tags=["billing"],
)

billing = SupabaseBilling()


class CheckoutRequest(BaseModel):
    plan_code: str
    interval: str = "month"


def _stripe_secret() -> str:
    value = os.getenv("STRIPE_SECRET_KEY", "")

    if not value:
        raise HTTPException(
            status_code=503,
            detail="Stripe is not configured.",
        )

    stripe.api_key = value
    return value


def _app_base_url() -> str:
    return os.getenv(
        "APP_BASE_URL",
        "http://localhost:8000",
    ).rstrip("/")


def _price_for(
    plan_code: str,
    interval: str,
) -> str:

    prices = {
        ("starter", "month"): os.getenv(
            "STRIPE_PRICE_STARTER_MONTHLY"
        ),
        ("starter", "year"): os.getenv(
            "STRIPE_PRICE_STARTER_ANNUAL"
        ),
        ("professional", "month"): os.getenv(
            "STRIPE_PRICE_PROFESSIONAL_MONTHLY"
        ),
        ("professional", "year"): os.getenv(
            "STRIPE_PRICE_PROFESSIONAL_ANNUAL"
        ),
        ("business", "month"): os.getenv(
            "STRIPE_PRICE_BUSINESS_MONTHLY"
        ),
        ("business", "year"): os.getenv(
            "STRIPE_PRICE_BUSINESS_ANNUAL"
        ),
    }

    price_id = prices.get(
        (plan_code, interval)
    )

    if not price_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "The selected plan has not yet "
                "been configured in Stripe."
            ),
        )

    return price_id


@router.get("/status")
def get_billing_status(
    auth_context: AuthContext = Depends(
        get_current_user
    ),
) -> dict:

    try:
        return billing.get_usage_status(
            auth_context.organization_id
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc


@router.post("/checkout")
def create_checkout(
    payload: CheckoutRequest,
    auth_context: AuthContext = Depends(
        get_current_user
    ),
) -> dict:

    _stripe_secret()

    plan_code = payload.plan_code.strip().lower()
    interval = payload.interval.strip().lower()

    if plan_code not in {
        "starter",
        "professional",
        "business",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid paid plan.",
        )

    if interval not in {
        "month",
        "year",
    }:
        raise HTTPException(
            status_code=400,
            detail="Interval must be month or year.",
        )

    price_id = _price_for(
        plan_code,
        interval,
    )

    current = billing.get_billing(
        auth_context.organization_id
    )

    if not current:
        raise HTTPException(
            status_code=404,
            detail="Organization billing record not found.",
        )

    customer_id = current.get(
        "stripe_customer_id"
    )

    if not customer_id:
        customer = stripe.Customer.create(
            email=auth_context.user_email,
            metadata={
                "organization_id":
                    auth_context.organization_id,
            },
        )

        customer_id = customer.id

        billing.update_billing(
            auth_context.organization_id,
            {
                "stripe_customer_id":
                    customer_id,
            },
        )

    base_url = _app_base_url()

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,

        line_items=[
            {
                "price": price_id,
                "quantity": 1,
            }
        ],

        success_url=(
            base_url
            + "/?billing=success"
            + "&session_id={CHECKOUT_SESSION_ID}"
        ),

        cancel_url=(
            base_url
            + "/?billing=canceled"
        ),

        metadata={
            "organization_id":
                auth_context.organization_id,
            "plan_code":
                plan_code,
            "billing_interval":
                interval,
        },

        subscription_data={
            "metadata": {
                "organization_id":
                    auth_context.organization_id,
                "plan_code":
                    plan_code,
                "billing_interval":
                    interval,
            }
        },
    )

    return {
        "checkout_url": session.url
    }


@router.post("/portal")
def create_portal(
    auth_context: AuthContext = Depends(
        get_current_user
    ),
) -> dict:

    _stripe_secret()

    current = billing.get_billing(
        auth_context.organization_id
    )

    if not current:
        raise HTTPException(
            status_code=404,
            detail="Organization billing record not found.",
        )

    customer_id = current.get(
        "stripe_customer_id"
    )

    if not customer_id:
        raise HTTPException(
            status_code=400,
            detail="No Stripe customer exists yet.",
        )

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=_app_base_url() + "/",
    )

    return {
        "portal_url": session.url
    }


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
) -> dict:

    _stripe_secret()

    webhook_secret = os.getenv(
        "STRIPE_WEBHOOK_SECRET",
        "",
    )

    if not webhook_secret:
        raise HTTPException(
            status_code=503,
            detail="Stripe webhook secret is not configured.",
        )

    payload = await request.body()

    signature = request.headers.get(
        "stripe-signature"
    )

    if not signature:
        raise HTTPException(
            status_code=400,
            detail="Missing Stripe-Signature header.",
        )

    try:
        event = stripe.Webhook.construct_event(
            payload,
            signature,
            webhook_secret,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid Stripe webhook.",
        ) from exc

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":

        metadata = obj.get("metadata") or {}

        organization_id = metadata.get(
            "organization_id"
        )

        if organization_id:
            values = {}

            if obj.get("customer"):
                values["stripe_customer_id"] = (
                    obj.get("customer")
                )

            if obj.get("subscription"):
                values["stripe_subscription_id"] = (
                    obj.get("subscription")
                )

            if metadata.get("plan_code"):
                values["plan_code"] = (
                    metadata["plan_code"]
                )

            if metadata.get("billing_interval"):
                values["billing_interval"] = (
                    metadata["billing_interval"]
                )

            values["status"] = "active"

            billing.update_billing(
                organization_id,
                values,
            )

    elif event_type in {
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }:

        metadata = obj.get("metadata") or {}

        organization_id = metadata.get(
            "organization_id"
        )

        if organization_id:
            values = {
                "stripe_customer_id":
                    obj.get("customer"),
                "stripe_subscription_id":
                    obj.get("id"),
                "status":
                    obj.get("status", "active"),
                "cancel_at_period_end":
                    bool(
                        obj.get(
                            "cancel_at_period_end",
                            False,
                        )
                    ),
            }

            if metadata.get("plan_code"):
                values["plan_code"] = (
                    metadata["plan_code"]
                )

            if metadata.get("billing_interval"):
                values["billing_interval"] = (
                    metadata[
                        "billing_interval"
                    ]
                )

            billing.update_billing(
                organization_id,
                values,
            )

    return {
        "received": True,
        "event_type": event_type,
    }