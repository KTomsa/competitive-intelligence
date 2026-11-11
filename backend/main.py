
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from google.genai.errors import APIError

from evidence_agent import review_evidence


# --------------------------------------------------
# Configuration
# --------------------------------------------------

load_dotenv()

app = FastAPI(
    title="Fact or Watt",
    version="0.2.0",
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
    category: str
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
            1. Identify the claim category:
               price, environment,
               customers, or other.
            2. Normalize the claim.
            3. Identify required evidence.
            4. Describe limitations.

            Rules:
            - Do not invent facts.
            - Do not invent sources.
            - Do not judge truthfulness.
            - Do not accuse companies.
            - The provided claim is
              unverified user input.
            - Respond in Czech.
            """,
            response_mime_type="application/json",
            response_schema=ClaimAnalysis
        )
    )

    if not response.text:
        raise ValueError(
            "Claim Extractor returned an empty response."
        )

    analysis = ClaimAnalysis.model_validate_json(
        response.text
    )

    return analysis


# --------------------------------------------------
# API endpoints
# --------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "0.2.0",
        "agents": [
            "Claim Extractor",
            "Evidence Agent"
        ]
    }


@app.post("/analyze")
def analyze(request: ClaimRequest):
    try:
        # Agent 1: extract and classify the claim
        claim_analysis = extract_claim(
            request.claim
        )

        # Agent 2: evaluate supplied evidence
        evidence_analysis = None

        if request.evidence:
            evidence_analysis = review_evidence(
                claim=request.claim,
                category=claim_analysis.category,
                evidence=[
                    item.model_dump()
                    for item in request.evidence
                ]
            ).model_dump()

        # Return combined results
        return {
            "claim": request.claim,
            "claim_analysis": claim_analysis.model_dump(),
            "evidence_analysis": evidence_analysis,
            "evidence": [
                item.model_dump()
                for item in request.evidence
            ],
            "verification_status": "NOT_VERIFIED",
            "agents_executed": [
                "Claim Extractor",
                *(
                    ["Evidence Agent"]
                    if request.evidence
                    else []
                )
            ]
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
        raise HTTPException(
            status_code=500,
            detail="Claim analysis failed."
        ) from exc
