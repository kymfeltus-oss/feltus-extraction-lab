from __future__ import annotations

import re
import uuid
from typing import Any

from .db import Database, now_iso


CLASSIFIER_VERSION = "feltus-deterministic-classifier-1.1.0"


DOCUMENT_TYPE_REGISTRY: list[dict[str, Any]] = [
    {
        "document_type": "CREDIT_CARD_STATEMENT",
        "category": "Credit Cards",
        "parser_route": None,
        "required_groups": [
            [r"minimum payment(?: due)?"],
            [r"credit limit", r"credit line", r"available credit"],
            [r"payment due date", r"minimum payment due by"],
        ],
    },
    {
        "document_type": "PARTNERSHIP_TAX_RETURN",
        "category": "Taxes",
        "parser_route": None,
        "required_groups": [
            [r"\bform\s*1065\b", r"\b1065\b"],
            [r"partnership return", r"u\.s\. return of partnership income"],
            [r"internal revenue service", r"department of the treasury"],
        ],
    },
    {
        "document_type": "INDIVIDUAL_TAX_RETURN",
        "category": "Taxes",
        "parser_route": None,
        "required_groups": [
            [r"\bform\s*1040\b", r"\b1040\b"],
            [r"u\.s\. individual income tax return"],
        ],
    },
    {
        "document_type": "PAY_STUB",
        "category": "Payroll & Employment",
        "parser_route": None,
        "required_groups": [
            [r"gross pay", r"gross earnings"],
            [r"net pay", r"net earnings", r"take home pay"],
            [r"pay period", r"period beginning", r"period ending", r"pay date"],
        ],
    },
    {
        "document_type": "OPERATING_AGREEMENT",
        "category": "Owned Businesses",
        "parser_route": None,
        "required_groups": [
            [r"operating agreement"],
            [r"\bmember\b", r"\bmembers\b", r"shareholder", r"\bpartner\b"],
            [r"ownership", r"percentage", r"membership interest"],
        ],
    },
    {
        "document_type": "LEASE_AGREEMENT",
        "category": "Property & Rental",
        "parser_route": None,
        "required_groups": [
            [r"lease agreement", r"rental agreement"],
            [r"landlord", r"lessor"],
            [r"tenant", r"lessee"],
            [r"premises", r"leased premises"],
        ],
    },
    {
        "document_type": "PROMISSORY_NOTE",
        "category": "Debt / Liabilities",
        "parser_route": None,
        "required_groups": [
            [r"promissory note"],
            [r"principal amount", r"original principal"],
            [r"interest rate", r"interest at the rate"],
            [r"maturity date", r"due date of (?:this )?note"],
        ],
    },
    {
        "document_type": "LOAN_STATEMENT",
        "category": "Debt / Liabilities",
        "parser_route": None,
        "required_groups": [
            [r"principal balance", r"remaining principal", r"outstanding principal"],
            [r"amount due", r"payment due"],
            [r"loan number", r"loan account"],
        ],
    },
    {
        "document_type": "BANK_STATEMENT",
        "category": "Bank Statements",
        "parser_route": "BANK_STATEMENT_PARSER",
        "required_groups": [
            [r"account summary", r"summary of accounts"],
            [r"beginning balance", r"opening balance"],
            [r"ending balance", r"closing balance"],
            [r"deposits and other additions", r"total deposits", r"deposits and credits"],
            [r"withdrawals and other subtractions", r"total withdrawals", r"withdrawals and debits"],
        ],
    },
]


MANUAL_ONLY_TYPES = [
    {"document_type": "MISC_DOCUMENT", "category": "Misc", "parser_route": None},
]

CATEGORY_REGISTRY = [
    "Bank Statements",
    "Credit Cards",
    "Taxes",
    "Payroll & Employment",
    "Owned Businesses",
    "Property & Rental",
    "Debt / Liabilities",
    "Court & Legal Records",
    "Communications",
    "Insurance",
    "Misc",
]


def available_document_types() -> list[dict[str, Any]]:
    return [
        {
            "document_type": rule["document_type"],
            "category": rule["category"],
            "parser_route": rule["parser_route"],
            "parser_status": "AVAILABLE" if rule["parser_route"] else "NOT_IMPLEMENTED",
        }
        for rule in [*DOCUMENT_TYPE_REGISTRY, *MANUAL_ONLY_TYPES]
    ]


def available_categories() -> list[str]:
    return CATEGORY_REGISTRY.copy()


def _normalize(value: str) -> str:
    return " ".join((value or "").lower().split())


def classify_step_1_run(run: dict[str, Any]) -> dict[str, Any]:
    pages = run.get("pages", [])
    for rule in DOCUMENT_TYPE_REGISTRY:
        evidence: list[dict[str, Any]] = []
        matched_all_groups = True
        for group_number, patterns in enumerate(rule["required_groups"], start=1):
            group_evidence = _find_group_match(pages, patterns, group_number)
            if group_evidence is None:
                matched_all_groups = False
                break
            evidence.append(group_evidence)
        if matched_all_groups:
            return {
                "step_1_run_ref": run["id"],
                "source_sha256": run["sha256"],
                "classifier_version": CLASSIFIER_VERSION,
                "document_type": rule["document_type"],
                "category": rule["category"],
                "parser_route": rule["parser_route"],
                "parser_status": "AVAILABLE" if rule["parser_route"] else "NOT_IMPLEMENTED",
                "matched_indicators": evidence,
                "classification_status": "AUTO_CLASSIFIED",
                "needs_review": False,
            }
    filename_match = _find_wells_fargo_filename_match(run)
    if filename_match:
        return {
            "step_1_run_ref": run["id"],
            "source_sha256": run["sha256"],
            "classifier_version": CLASSIFIER_VERSION,
            "document_type": "BANK_STATEMENT",
            "category": "Bank Statements",
            "parser_route": "BANK_STATEMENT_PARSER",
            "parser_status": "AVAILABLE",
            "matched_indicators": [filename_match],
            "classification_status": "AUTO_CLASSIFIED",
            "needs_review": False,
        }
    return {
        "step_1_run_ref": run["id"],
        "source_sha256": run["sha256"],
        "classifier_version": CLASSIFIER_VERSION,
        "document_type": "UNKNOWN",
        "category": None,
        "parser_route": None,
        "parser_status": "NOT_SELECTED",
        "matched_indicators": [],
        "classification_status": "REQUIRES_MANUAL_SELECTION",
        "needs_review": True,
    }


def _find_wells_fargo_filename_match(run: dict[str, Any]) -> dict[str, Any] | None:
    """Classify a Wells Fargo statement when page text is unavailable or incomplete.

    The upload name is provenance retained with the document, so this is real source
    evidence rather than a default category.  It deliberately matches the bank name
    only, including compact names such as ``WellsFargo.pdf`` used by the existing
    document set.
    """
    filename = str(run.get("original_filename") or "")
    match = re.search(r"wells[\s_-]*fargo", filename, re.IGNORECASE)
    if not match:
        return None
    return {
        "group": "filename",
        "matched_phrase": match.group(0),
        "text_found": filename,
        "page_number": None,
        "line_number": None,
        "bbox": None,
        "evidence_source": "original_filename",
    }


def _find_group_match(pages: list[dict[str, Any]], patterns: list[str], group_number: int) -> dict[str, Any] | None:
    for page in pages:
        for line in page.get("lines", []):
            normalized = _normalize(line.get("text", ""))
            for pattern in patterns:
                match = re.search(pattern, normalized, re.IGNORECASE)
                if match:
                    return {
                        "group": group_number,
                        "matched_phrase": match.group(0),
                        "text_found": line.get("text", "").strip(),
                        "page_number": page.get("page_number"),
                        "line_number": line.get("line_number"),
                        "bbox": line.get("bbox"),
                    }
    return None


class ClassificationService:
    def __init__(self, database: Database):
        self.database = database

    def classify_run(self, run_id: str) -> dict[str, Any]:
        run = self.database.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if run["status"] != "VALIDATED":
            raise ValueError("Only a validated Step 1 extraction can be classified")
        existing = self.database.get_system_classification(run_id, CLASSIFIER_VERSION)
        if existing:
            return existing
        result = classify_step_1_run(run)
        return self._persist(run, result, "SYSTEM", None)

    def manually_assign(self, run_id: str, document_type: str) -> dict[str, Any]:
        run = self.database.get_run(run_id)
        if not run:
            raise KeyError(run_id)
        if run["status"] != "VALIDATED":
            raise ValueError("Only a validated Step 1 extraction can be assigned a document type")
        target = next((item for item in available_document_types() if item["document_type"] == document_type), None)
        if not target:
            raise ValueError(f"Unsupported document type: {document_type}")
        previous = self.database.get_latest_classification(run_id)
        result = {
            "step_1_run_ref": run["id"],
            "source_sha256": run["sha256"],
            "classifier_version": CLASSIFIER_VERSION,
            "document_type": target["document_type"],
            "category": target["category"],
            "parser_route": target["parser_route"],
            "parser_status": target["parser_status"],
            "matched_indicators": [],
            "classification_status": "MANUALLY_ASSIGNED",
            "needs_review": False,
        }
        return self._persist(run, result, "USER", previous["id"] if previous else None)

    def backfill_existing(self) -> list[dict[str, Any]]:
        results = []
        for run_id in self.database.list_validated_run_ids_without_classification(CLASSIFIER_VERSION):
            results.append(self.classify_run(run_id))
        return results

    def _persist(self, run: dict[str, Any], result: dict[str, Any], assignment_source: str, supersedes_id: str | None) -> dict[str, Any]:
        classification_id = str(uuid.uuid4())
        self.database.insert_classification({
            "id": classification_id,
            "source_run_id": run["id"],
            "source_sha256": run["sha256"],
            "classifier_version": CLASSIFIER_VERSION,
            "assignment_source": assignment_source,
            "status": result["classification_status"],
            "document_type": result["document_type"],
            "category": result["category"],
            "parser_route": result["parser_route"],
            "parser_status": result["parser_status"],
            "needs_review": result["needs_review"],
            "result": result,
            "supersedes_classification_id": supersedes_id,
            "created_at": now_iso(),
        })
        return self.database.get_classification(classification_id)
