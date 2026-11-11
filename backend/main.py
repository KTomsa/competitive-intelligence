
import os
import logging
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.genai.errors import APIError

from evidence_agent import review_evidence
from judge_agent import judge_claim


# --------------------------------------------------
# Configuration
# --------------------------------------------------

load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Fact or Watt",
    version="0.3.0",
    description="Multi-agent claim checking system for the energy sector."
)

MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite"
)


# --------------------------------------------------
# Input and output models
# --------------------------------------------------

class EvidenceInput(BaseModel):
    source_name: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class ClaimRequest(BaseModel):
    claim: str = Field(min_length=3)
    evidence: list[EvidenceInput] = Field(
        default_factory=list
    )


class ClaimAnalysis(BaseModel):
    category: Literal[
        "price",
        "environment",
        "customers",
        "other"
    ]
    normalized_claim: str
    required_evidence: list[str]
    limitations: str


# --------------------------------------------------
# Agent 1 - Claim Extractor
# --------------------------------------------------

def extract_claim(claim: str) -> ClaimAnalysis:

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY is missing."
        )

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=MODEL,
        contents=claim,
        config=types.GenerateContentConfig(
            system_instruction="""
            You are Claim Extractor,
            an AI agent in Fact or Watt.

            Analyze marketing claims made by
            energy suppliers.

            Tasks:
            1. Identify claim category:
               price, environment,
               customers, or other.
            2. Normalize the claim.
            3. Identify required evidence.
            4. Describe limitations.

            Rules:
            - Do not invent facts or sources.
            - Do not judge truthfulness.
            - Do not accuse companies.
            - The claim is unverified user input.
            - Respond in Czech.
            """,
            response_mime_type="application/json",
            response_schema=ClaimAnalysis
        )
    )

    if not response.text:
        raise ValueError(
            "Claim Extractor returned an empty response"
        )

    return ClaimAnalysis.model_validate_json(
        response.text
    )


# --------------------------------------------------
# API endpoints
# --------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "0.3.0",
        "agents": [
            "Claim Extractor",
            "Evidence Agent",
            "Judge Agent"
        ]
    }


@app.post("/analyze")
def analyze(request: ClaimRequest):

    try:
        executed_agents = []

        supplied_evidence = [
            item.model_dump()
            for item in request.evidence
        ]

        # Agent 1 - Claim Extractor
        claim_analysis = extract_claim(
            request.claim
        )

        executed_agents.append(
            "Claim Extractor"
        )

        evidence_analysis = None
        judge_analysis = None

        if supplied_evidence:

            # Agent 2 - Evidence Agent
            evidence_analysis = review_evidence(
                claim=request.claim,
                category=claim_analysis.category,
                evidence=supplied_evidence
            ).model_dump()

            executed_agents.append(
                "Evidence Agent"
            )

            # Agent 3 - Judge Agent
            judge_analysis = judge_claim(
                claim=request.claim,
                claim_analysis=claim_analysis.model_dump(),
                evidence_analysis=evidence_analysis,
                evidence=supplied_evidence
            ).model_dump()

            executed_agents.append(
                "Judge Agent"
            )

        # Combined output from all agents
        return {
            "claim": request.claim,
            "claim_analysis": claim_analysis.model_dump(),
            "evidence_analysis": evidence_analysis,
            "judge_analysis": judge_analysis,
            "evidence": supplied_evidence,
            "evidence_provenance": (
                "USER_SUPPLIED_UNVERIFIED"
            ),
            "verification_status": "NOT_VERIFIED",
            "agents_executed": executed_agents
        }

    except APIError as exc:
        status = getattr(exc, "code", None)

        if status == 429:
            raise HTTPException(
                status_code=429,
                detail="Gemini rate limit exceeded."
            ) from exc

        if status == 503:
            raise HTTPException(
                status_code=503,
                detail="Gemini is temporarily unavailable."
            ) from exc

        raise HTTPException(
            status_code=502,
            detail="Gemini API request failed."
        ) from exc

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception(
            "Unexpected error during claim analysis"
        )

        raise HTTPException(
            status_code=500,
            detail="Claim analysis failed."
        ) from exc
