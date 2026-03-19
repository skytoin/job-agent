"""Tests for profile data models."""

from src.profile import ApplicationResult, JobTarget, Profile


def _sample_profile(**overrides) -> Profile:
    defaults = {
        "first_name": "Test",
        "last_name": "User",
        "email": "test@example.com",
        "phone": "+1-555-000-0000",
        "location": "Brooklyn, NY",
        "linkedin_url": "https://linkedin.com/in/test",
        "current_title": "ML Engineer",
        "years_experience": 5,
        "summary": "Test summary",
        "education": [{"school": "MIT", "degree": "BS CS", "year": 2020}],
        "experience": [
            {
                "company": "TestCorp",
                "title": "Engineer",
                "dates": "2020-2024",
                "bullets": ["Did things"],
            }
        ],
        "skills": ["Python", "PyTorch"],
        "resume_path": "/tmp/resume.pdf",
    }
    defaults.update(overrides)
    return Profile(**defaults)


def test_profile_creation():
    p = _sample_profile()
    assert p.first_name == "Test"
    assert p.years_experience == 5


def test_profile_compact_str():
    p = _sample_profile()
    compact = p.to_compact_str()
    assert "Test User" in compact
    assert "ML Engineer" in compact
    assert "Python" in compact


def test_profile_optional_fields():
    p = _sample_profile(github_url=None, portfolio_url=None)
    assert p.github_url is None


def test_job_target_minimal():
    j = JobTarget(url="https://example.com/apply")
    assert j.company is None
    assert j.credentials_key is None


def test_application_result_filled():
    r = ApplicationResult(job_url="https://example.com", status="filled")
    assert r.error is None


def test_application_result_error():
    r = ApplicationResult(job_url="https://example.com", status="error", error="Timeout")
    assert r.error == "Timeout"
