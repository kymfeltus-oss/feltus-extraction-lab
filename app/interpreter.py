from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any


INTERPRETER_VERSION = "bank-statement-1.0.0"
DATE_START = re.compile(r"^(\d{1,2}/\d{1,2}/(?:\d{2}|\d{4}))\b")
MONEY = re.compile(r"(?P<value>-?\$?\(?\d[\d,]*\.\d{2}\)?)")
DEPOSIT_HEADER = re.compile(r"DEPOSITS?\s+AND\s+OTHER\s+ADDITIONS", re.I)
WITHDRAWAL_HEADER = re.compile(r"WITHDRAWALS?\s+AND\s+OTHER\s+SUBTRACTIONS", re.I)


def _decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    negative = value.strip().startswith("-") or "(" in value
    cleaned = re.sub(r"[^\d.]", "", value)
    try:
        number = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return -number if negative else number


def _money_string(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def _control(patterns: list[str], text: str) -> Decimal | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.M)
        if match:
            return abs(_decimal(match.group(1)) or Decimal("0"))
    return None


def _text_direction(description: str) -> str:
    upper = description.upper()
    credit_markers = ("PAYROLL", "ZELLE PAYMENT FROM", "TRANSFER FROM", "DIRECT DEP", "ACH CREDIT", "REFUND")
    debit_markers = ("ZELLE PAYMENT TO", "PAYMENT TO", "TRANSFER TO", "CARD PURCHASE", "ATM WITHDRAWAL", "CHECK #")
    if any(marker in upper for marker in credit_markers):
        return "credit"
    if any(marker in upper for marker in debit_markers):
        return "debit"
    return "unknown"


def _institution(page_one: str) -> dict[str, Any]:
    upper = page_one.upper()
    registry = [
        ("BANK OF AMERICA", "Bank of America, N.A."),
        ("JPMORGAN CHASE", "JPMorgan Chase Bank, N.A."),
        ("CHASE", "JPMorgan Chase Bank, N.A."),
        ("WELLS FARGO", "Wells Fargo Bank, N.A."),
        ("CITIBANK", "Citibank, N.A."),
    ]
    for marker, name in registry:
        if marker in upper:
            return {"name": name, "status": "IDENTIFIED", "source_page": 1, "source_marker": marker}
    return {"name": None, "status": "UNRESOLVED", "source_page": 1, "source_marker": None}


def _account(page_one: str) -> dict[str, Any]:
    patterns = [r"Account\s+(?:number|ending(?:\s+in)?)\s*:?\s*(?:[*xX-]+)?\s*(\d{4})\b", r"\b(?:[*xX]{4,})\s*(\d{4})\b"]
    last4 = None
    source = None
    for pattern in patterns:
        match = re.search(pattern, page_one, re.I)
        if match:
            last4, source = match.group(1), match.group(0)
            break
    upper = page_one.upper()
    if "ADV PLUS BANKING" in upper or "ADVANTAGE PLUS BANKING" in upper:
        account_type, product = "PERSONAL_CHECKING", "Bank of America Advantage Plus Banking"
    elif "CHECKING" in upper:
        account_type, product = "CHECKING", None
    elif "SAVINGS" in upper:
        account_type, product = "SAVINGS", None
    else:
        account_type, product = "UNRESOLVED", None
    return {"last4": last4, "account_type": account_type, "product": product, "source_page": 1, "source_text": source}


def _stitch_transactions(pages: list[dict]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    section = "UNKNOWN"
    pending: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal pending
        if pending:
            candidates.append(pending)
            pending = None

    for page in pages:
        page_number = int(page["page_number"])
        for line in page.get("lines", []):
            text = str(line.get("text", "")).strip()
            if not text:
                continue
            if DEPOSIT_HEADER.search(text):
                flush(); section = "DEPOSITS_AND_ADDITIONS"; continue
            if WITHDRAWAL_HEADER.search(text):
                flush(); section = "WITHDRAWALS_AND_SUBTRACTIONS"; continue
            upper = text.upper()
            if upper.startswith("TOTAL DEPOSITS") or upper.startswith("TOTAL WITHDRAWALS"):
                flush(); section = "UNKNOWN"; continue
            date_match = DATE_START.match(text)
            line_id = f"p{page_number}-l{line.get('line_number')}"
            token_ids = [token.get("token_id") for token in line.get("tokens", []) if token.get("token_id")]
            if date_match:
                flush()
                pending = {"date": date_match.group(1), "section": section, "page_number": page_number,
                    "parts": [text], "source_line_ids": [line_id], "source_token_ids": token_ids}
            elif pending:
                pending["parts"].append(text)
                pending["source_line_ids"].append(line_id)
                pending["source_token_ids"].extend(token_ids)
    flush()
    return candidates


def _interpret_candidate(candidate: dict[str, Any]) -> dict[str, Any] | None:
    source_text = " ".join(candidate["parts"])
    amounts = list(MONEY.finditer(source_text))
    if not amounts:
        return None
    amount_match = amounts[-1]
    raw_amount = amount_match.group("value")
    signed_amount = _decimal(raw_amount)
    if signed_amount is None:
        return None
    description = source_text[DATE_START.match(source_text).end():amount_match.start()].strip(" ;-:")
    section_signal = "credit" if candidate["section"] == "DEPOSITS_AND_ADDITIONS" else "debit" if candidate["section"] == "WITHDRAWALS_AND_SUBTRACTIONS" else "unknown"
    sign_signal = "debit" if signed_amount < 0 else "credit" if raw_amount.strip().startswith("+") else "unknown"
    text_signal = _text_direction(description)
    if candidate["section"] == "UNKNOWN" and sign_signal == "unknown" and text_signal == "unknown":
        return None
    signals = {"section": section_signal, "printed_sign": sign_signal, "description": text_signal}
    conflicts: list[str] = []
    if section_signal != "unknown" and sign_signal != "unknown" and section_signal != sign_signal:
        direction, source, confidence = "UNRESOLVED_DIRECTION", "CONFLICTING_PRIMARY_SIGNALS", "0.00"
        conflicts.append(f"Section indicates {section_signal}; printed sign indicates {sign_signal}.")
    elif section_signal != "unknown":
        direction, source, confidence = section_signal, "SECTION_HEADER", "0.95"
    elif sign_signal != "unknown":
        direction, source, confidence = sign_signal, "PRINTED_SIGN", "0.85"
    elif text_signal != "unknown":
        direction, source, confidence = text_signal, "DESCRIPTION_FALLBACK", "0.70"
    else:
        direction, source, confidence = "UNRESOLVED_DIRECTION", "INSUFFICIENT_EVIDENCE", "0.00"
    if direction != "UNRESOLVED_DIRECTION" and text_signal != "unknown" and text_signal != direction:
        conflicts.append(f"Description indicates {text_signal}; controlling evidence indicates {direction}.")
        confidence = "0.65"
    return {
        "transaction_id": str(uuid.uuid4()), "date": candidate["date"], "description": description,
        "amount": _money_string(abs(signed_amount)), "direction": direction, "direction_source": source,
        "direction_confidence": confidence, "page_number": candidate["page_number"],
        "section_context": candidate["section"], "source_text": source_text,
        "source_line_ids": candidate["source_line_ids"], "source_token_ids": candidate["source_token_ids"],
        "evidence_signals": signals, "conflicts": conflicts,
    }


def interpret_bank_statement(run: dict[str, Any]) -> dict[str, Any]:
    pages = run.get("pages", [])
    if not pages:
        raise ValueError("Step 1 page evidence is unavailable; rerun extraction with the current parser")
    page_one = pages[0].get("raw_text", "")
    full_text = "\n".join(page.get("raw_text", "") for page in pages)
    institution = _institution(page_one)
    account = _account(page_one)
    candidates = _stitch_transactions(pages)
    transactions = [tx for candidate in candidates if (tx := _interpret_candidate(candidate)) is not None]
    beginning = _control([r"Beginning\s+balance[^\n]{0,100}?(-?\$?\(?\d[\d,]*\.\d{2}\)?)"], full_text)
    ending = _control([r"Ending\s+balance[^\n]{0,100}?(-?\$?\(?\d[\d,]*\.\d{2}\)?)"], full_text)
    printed_credits = _control([r"(?:Total\s+)?Deposits\s+and\s+other\s+additions[^\n]{0,60}?(-?\$?\(?\d[\d,]*\.\d{2}\)?)"], full_text)
    printed_debits = _control([r"(?:Total\s+)?Withdrawals\s+and\s+other\s+subtractions[^\n]{0,60}?(-?\$?\(?\d[\d,]*\.\d{2}\)?)"], full_text)
    credits = sum((Decimal(tx["amount"]) for tx in transactions if tx["direction"] == "credit"), Decimal("0"))
    debits = sum((Decimal(tx["amount"]) for tx in transactions if tx["direction"] == "debit"), Decimal("0"))
    unresolved = sum(tx["direction"] == "UNRESOLVED_DIRECTION" for tx in transactions)
    controls_complete = all(value is not None for value in (beginning, ending, printed_credits, printed_debits))
    calculated_ending = beginning + credits - debits if beginning is not None else None
    credit_variance = credits - printed_credits if printed_credits is not None else None
    debit_variance = debits - printed_debits if printed_debits is not None else None
    balance_variance = calculated_ending - ending if calculated_ending is not None and ending is not None else None
    if not controls_complete or unresolved:
        reconciliation_status = "INCOMPLETE"
    elif credit_variance == 0 and debit_variance == 0 and balance_variance == 0:
        reconciliation_status = "VERIFIED"
    else:
        reconciliation_status = "VARIANCE"
    return {
        "interpreter_version": INTERPRETER_VERSION, "source_run_id": run["id"],
        "institution": institution, "account": account,
        "transactions": transactions,
        "reconciliation": {
            "status": reconciliation_status, "controls_complete": controls_complete,
            "unresolved_transaction_count": unresolved,
            "printed_beginning_balance": _money_string(beginning), "printed_ending_balance": _money_string(ending),
            "printed_total_credits": _money_string(printed_credits), "printed_total_debits": _money_string(printed_debits),
            "interpreted_total_credits": _money_string(credits), "interpreted_total_debits": _money_string(debits),
            "calculated_ending_balance": _money_string(calculated_ending),
            "credit_variance": _money_string(credit_variance), "debit_variance": _money_string(debit_variance),
            "balance_variance": _money_string(balance_variance),
        },
        "warnings": (["One or more printed statement controls were not extracted."] if not controls_complete else []) +
            ([f"{unresolved} transaction(s) require direction review."] if unresolved else []),
    }
