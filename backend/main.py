
import os
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

load_dotenv()

app = FastAPI(title="Fact or Watt")


class ClaimRequest(BaseModel):
    claim: str = Field(min_length=3)


class ClaimAnalysis(BaseModel):
    category: Literal[
        "price", "environment", "customers", "other"
    ]
    normalized_claim: str
    required_evidence: list[str]
    limitations: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/analyze")
def analyze(request: ClaimRequest):
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Gemini API key is missing"
        )

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=os.getenv(
            "GEMINI_MODEL",
            "gemini-3.5-flash-lite"
        ),
        contents=request.claim,
        config=types.GenerateContentConfig(
            system_instruction="""
            You are the Claim Extractor agent
            of Fact or Watt.

            Analyze energy supplier marketing claims.

            Identify the category and describe
            what evidence would be necessary
            to verify the claim.

            Never determine whether a claim
            is true or false.

            Never accuse a company of lying
            or violating the law.

            The supplied claim is unverified
            user input, not a confirmed
            statement made by a company.

            Respond in Czech.
            """,
            response_mime_type="application/json",
            response_schema=ClaimAnalysis,
        ),
    )

    if not response.text:
        raise HTTPException(
            status_code=502,
            detail="Gemini returned an empty response"
        )

    analysis = ClaimAnalysis.model_validate_json(
        response.text
    )

    return {
        "claim": request.claim,
        "analysis": analysis.model_dump(),
        "verification_status": "NOT_VERIFIED",
        "evidence": [],
    }
