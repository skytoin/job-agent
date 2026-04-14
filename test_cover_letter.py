"""Quick test: generate a cover letter for a fake Google ML Engineer posting."""

import asyncio
import json
from pathlib import Path

from src.cover_letter import generate_cover_letter
from src.profile import JobDescription, Profile

FAKE_JOB = JobDescription(
    url="https://careers.google.com/jobs/fake-123",
    company="Google",
    position="Senior ML Engineer",
    description="""\
About the role:
We're looking for a Senior ML Engineer to join Google's Search team working on
large-scale retrieval and ranking systems. You'll design, train, and deploy
models that serve billions of queries daily.

Responsibilities:
- Design and implement ML models for search ranking and retrieval
- Build and maintain training pipelines processing petabytes of data
- Collaborate with research teams to bring state-of-the-art NLP techniques
  into production
- Optimize model inference for low-latency serving at scale
- Mentor junior engineers and contribute to technical design reviews

Minimum qualifications:
- BS in Computer Science or related field
- 5+ years of experience in ML/AI engineering
- Strong proficiency in Python, PyTorch or TensorFlow
- Experience with large-scale distributed training
- Track record shipping ML models to production

Preferred qualifications:
- Experience with LLMs, RAG systems, or information retrieval
- Familiarity with reinforcement learning from human feedback (RLHF)
- Publications in top ML/NLP venues
- Experience with cloud infrastructure (GCP preferred)
""",
    requirements=[
        "5+ years ML engineering",
        "Python, PyTorch/TensorFlow",
        "Large-scale distributed training",
        "Production ML systems",
    ],
    location="Mountain View, CA (Hybrid)",
    salary_range="$200,000 - $300,000",
)


async def main() -> None:
    profile = Profile(**json.loads(Path("config/profile.json").read_text()))

    print(f"Profile: {profile.first_name} {profile.last_name}")
    print(f"Job: {FAKE_JOB.position} at {FAKE_JOB.company}")
    print("-" * 60)

    letter = await generate_cover_letter(profile, FAKE_JOB)

    print(letter)
    print("-" * 60)
    print(f"Word count: {len(letter.split())}")


if __name__ == "__main__":
    asyncio.run(main())
