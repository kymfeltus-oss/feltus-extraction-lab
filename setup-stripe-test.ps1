$ErrorActionPreference = "Stop"

# ============================================================
# FELTUS TEST PRICING
# Amounts are in cents.
# Change these values BEFORE running if you want different prices.
# ============================================================

$STARTER_MONTHLY = 2900
$STARTER_ANNUAL = 29000

$PRO_MONTHLY = 7900
$PRO_ANNUAL = 79000

$BUSINESS_MONTHLY = 19900
$BUSINESS_ANNUAL = 199000


function Invoke-StripeJson {
    param(
        [Parameter(Mandatory=$true)]
        [string[]]$StripeArgs
    )

    Write-Host ""
    Write-Host "stripe $($StripeArgs -join ' ')" -ForegroundColor Cyan

    $raw = & stripe @StripeArgs

    if ($LASTEXITCODE -ne 0) {
        throw "Stripe CLI command failed."
    }

    return (($raw -join "`n") | ConvertFrom-Json)
}


Write-Host ""
Write-Host "Creating FELTUS Stripe TEST products..." -ForegroundColor Green


# ============================================================
# STARTER
# ============================================================

$starterProduct = Invoke-StripeJson @(
    "products",
    "create",
    "-d", "name=FELTUS Starter"
)

$starterMonthly = Invoke-StripeJson @(
    "prices",
    "create",
    "-d", "product=$($starterProduct.id)",
    "-d", "unit_amount=$STARTER_MONTHLY",
    "-d", "currency=usd",
    "-d", "recurring[interval]=month",
    "-d", "nickname=Starter Monthly"
)

$starterAnnual = Invoke-StripeJson @(
    "prices",
    "create",
    "-d", "product=$($starterProduct.id)",
    "-d", "unit_amount=$STARTER_ANNUAL",
    "-d", "currency=usd",
    "-d", "recurring[interval]=year",
    "-d", "nickname=Starter Annual"
)


# ============================================================
# PROFESSIONAL
# ============================================================

$professionalProduct = Invoke-StripeJson @(
    "products",
    "create",
    "-d", "name=FELTUS Professional"
)

$professionalMonthly = Invoke-StripeJson @(
    "prices",
    "create",
    "-d", "product=$($professionalProduct.id)",
    "-d", "unit_amount=$PRO_MONTHLY",
    "-d", "currency=usd",
    "-d", "recurring[interval]=month",
    "-d", "nickname=Professional Monthly"
)

$professionalAnnual = Invoke-StripeJson @(
    "prices",
    "create",
    "-d", "product=$($professionalProduct.id)",
    "-d", "unit_amount=$PRO_ANNUAL",
    "-d", "currency=usd",
    "-d", "recurring[interval]=year",
    "-d", "nickname=Professional Annual"
)


# ============================================================
# BUSINESS
# ============================================================

$businessProduct = Invoke-StripeJson @(
    "products",
    "create",
    "-d", "name=FELTUS Business"
)

$businessMonthly = Invoke-StripeJson @(
    "prices",
    "create",
    "-d", "product=$($businessProduct.id)",
    "-d", "unit_amount=$BUSINESS_MONTHLY",
    "-d", "currency=usd",
    "-d", "recurring[interval]=month",
    "-d", "nickname=Business Monthly"
)

$businessAnnual = Invoke-StripeJson @(
    "prices",
    "create",
    "-d", "product=$($businessProduct.id)",
    "-d", "unit_amount=$BUSINESS_ANNUAL",
    "-d", "currency=usd",
    "-d", "recurring[interval]=year",
    "-d", "nickname=Business Annual"
)


# ============================================================
# WRITE ENV VALUES
# ============================================================

$envLines = @(
    "STRIPE_PRICE_STARTER_MONTHLY=$($starterMonthly.id)",
    "STRIPE_PRICE_STARTER_ANNUAL=$($starterAnnual.id)",
    "STRIPE_PRICE_PROFESSIONAL_MONTHLY=$($professionalMonthly.id)",
    "STRIPE_PRICE_PROFESSIONAL_ANNUAL=$($professionalAnnual.id)",
    "STRIPE_PRICE_BUSINESS_MONTHLY=$($businessMonthly.id)",
    "STRIPE_PRICE_BUSINESS_ANNUAL=$($businessAnnual.id)"
)

New-Item -ItemType Directory -Force -Path ".\data" | Out-Null

$envLines |
    Set-Content ".\data\stripe-test-env.txt"


Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host "STRIPE TEST PRODUCTS CREATED" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""

$envLines

Write-Host ""
Write-Host "Saved to:" -ForegroundColor Cyan
Write-Host ".\data\stripe-test-env.txt"