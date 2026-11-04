"""
llm_client.py
Azure AI Foundry client for all LLM operations in the job search tool.
"""

import logging
import json
import re
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


class AzureAIFoundryClient:
    """
    Thin wrapper around azure-ai-inference for Azure AI Foundry.
    Handles chat completions used throughout the job search pipeline.
    """

    def __init__(self, config: dict):
        self.endpoint = config["azure"]["endpoint"]
        self.api_key = config["azure"]["api_key"]
        self.model_name = config["azure"]["model_name"]
        self.api_version = config["azure"].get("api_version")

        self._client = None
        self._omit_max_tokens = False  # set True if model rejects max_tokens
        self._omit_temperature = False  # set True if model rejects temperature
        self._init_client()

    def _init_client(self):
        """Initialize the Azure AI Inference client."""
        try:
            from azure.ai.inference import ChatCompletionsClient
            from azure.core.credentials import AzureKeyCredential

            endpoint = self.endpoint.rstrip("/")
            hostname = (urlsplit(endpoint).hostname or "").lower()
            if (
                hostname.endswith((".openai.azure.com", ".cognitiveservices.azure.com"))
                and not urlsplit(endpoint).path.strip("/")
            ):
                endpoint = f"{endpoint}/openai/deployments/{self.model_name}"

            kwargs = {
                "endpoint": endpoint,
                "credential": AzureKeyCredential(self.api_key),
            }
            if self.api_version:
                kwargs["api_version"] = self.api_version

            self._client = ChatCompletionsClient(**kwargs)
            logger.info(f"Azure AI Foundry client initialized. Model: {self.model_name}")
        except ImportError:
            logger.error(
                "azure-ai-inference not installed. Run: pip install azure-ai-inference"
            )
            raise
        except Exception as e:
            logger.error(f"Failed to initialize Azure AI Foundry client: {e}")
            raise

    def complete(
        self,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> object:
        """
        Send a chat completion request to Azure AI Foundry.

        Args:
            messages: List of {"role": "user"|"system"|"assistant", "content": str}
            temperature: Sampling temperature
            max_tokens: Max tokens in response
            json_mode: If True, request JSON response format

        Returns:
            ChatCompletions response object
        """
        from azure.ai.inference.models import (
            SystemMessage,
            UserMessage,
            AssistantMessage,
        )

        ai_messages = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                ai_messages.append(SystemMessage(content=content))
            elif role == "user":
                ai_messages.append(UserMessage(content=content))
            elif role == "assistant":
                ai_messages.append(AssistantMessage(content=content))

        kwargs = {
            "messages": ai_messages,
            "model": self.model_name,
        }

        # Some models restrict which parameters are accepted.
        # The API reports one unsupported param at a time, so we retry in a loop.
        if not self._omit_max_tokens:
            kwargs["max_tokens"] = max_tokens
        if not self._omit_temperature:
            kwargs["temperature"] = temperature

        for attempt in range(3):
            try:
                response = self._client.complete(**kwargs)
                return response
            except Exception as e:
                err_msg = str(e).lower()
                adjusted = False
                if "max_tokens" in err_msg or "max_completion_tokens" in err_msg:
                    self._omit_max_tokens = True
                    kwargs.pop("max_tokens", None)
                    logger.info("Model does not support max_tokens; retrying without it")
                    adjusted = True
                if "temperature" in err_msg:
                    self._omit_temperature = True
                    kwargs.pop("temperature", None)
                    logger.info("Model does not support custom temperature; retrying without it")
                    adjusted = True
                if not adjusted:
                    raise
        # Should not reach here, but just in case
        return self._client.complete(**kwargs)

    def complete_json(
        self, prompt: str, system: Optional[str] = None, max_tokens: int = 2000
    ) -> dict:
        """
        Request a JSON response from the LLM.
        Automatically cleans and parses the JSON output.
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self.complete(messages=messages, temperature=0.1, max_tokens=max_tokens)
        content = response.choices[0].message.content.strip()

        # Strip markdown code fences
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        return json.loads(content)

    def score_job_match(self, resume_data: dict, job: dict) -> dict:
        """
        Use LLM to score how well a job matches the resume.
        Returns a dict with score (0-100) and explanation.
        """
        system_prompt = """You are an expert recruiter and career coach.
Analyze job-resume fit objectively. Return ONLY valid JSON."""

        prompt = f"""Score this job-resume match from 0-100 and explain why.

RESUME SUMMARY:
- Title: {resume_data.get('current_title', 'N/A')}
- Seniority: {resume_data.get('seniority_level', 'N/A')}
- Years Experience: {resume_data.get('years_experience', 'N/A')}
- Technical Skills: {', '.join(resume_data.get('skills', {}).get('technical', [])[:20])}
- Industries: {', '.join(resume_data.get('industries', []))}
- Key Achievements: {' | '.join(resume_data.get('key_achievements', [])[:3])}

JOB POSTING:
- Title: {job.get('title', 'N/A')}
- Company: {job.get('company', 'N/A')}
- Description: {job.get('description', 'N/A')[:1500]}

Return JSON:
{{
  "score": <integer 0-100>,
  "match_level": "excellent|good|fair|poor",
  "matching_skills": ["list of matching skills"],
  "missing_skills": ["list of required skills candidate lacks"],
  "strengths": ["2-3 why this is a good fit"],
  "concerns": ["1-2 potential concerns"],
  "recommendation": "one sentence recommendation",
  "apply_priority": "high|medium|low|skip"
}}"""

        try:
            result = self.complete_json(prompt, system=system_prompt, max_tokens=800)
            return result
        except Exception as e:
            logger.error(f"LLM job scoring failed for {job.get('title')}: {e}")
            raise

    def generate_cover_letter_outline(self, resume_data: dict, job: dict) -> str:
        """Generate a cover letter outline for a specific job."""
        prompt = f"""Create a concise cover letter outline for this application.

Candidate: {resume_data.get('name', 'Candidate')}
Role: {job.get('title')} at {job.get('company')}
Key match skills: {', '.join(job.get('match_data', {}).get('matching_skills', [])[:5])}
Top achievement: {resume_data.get('key_achievements', [''])[0] if resume_data.get('key_achievements') else 'N/A'}

Write a 3-paragraph cover letter outline (intro, value proposition, close).
Be specific, confident, and avoid clichés."""

        try:
            response = self.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=500,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Cover letter generation failed: {e}")
            return "Cover letter generation failed. Please write manually."

    def extract_job_keywords(self, job_description: str) -> list[str]:
        """Extract key requirements and keywords from a job description."""
        prompt = f"""Extract the 10 most important keywords/requirements from this job description.
Return ONLY a JSON array of strings.

Job Description:
{job_description[:2000]}"""

        try:
            result = self.complete_json(prompt)
            if isinstance(result, list):
                return result
            return result.get("keywords", [])
        except Exception as e:
            logger.debug(f"Keyword extraction failed: {e}")
            return []
