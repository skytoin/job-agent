"""Tests for agent module: task prompt, complexity detection, failure classification."""

from src.agent import (
    SYSTEM_INSTRUCTIONS,
    _classify_failure,
    build_task_prompt,
)
from src.direct_fill import is_complex_url
from src.llm import CleanJsonOpenAI, _extract_json, create_browser_llm, is_openai_model
from src.profile import Profile


def _sample_profile(**overrides) -> Profile:
    defaults = {
        "first_name": "Test",
        "last_name": "User",
        "email": "test@example.com",
        "phone": "+1-555-000-0000",
        "location": "New York, NY",
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
        "skills": ["Python", "PyTorch", "SQL"],
        "resume_path": "/tmp/resume.pdf",
    }
    defaults.update(overrides)
    return Profile(**defaults)


# --- Task prompt tests ---


def _mock_job(url="https://example.com"):
    return type("Job", (), {"url": url, "credentials_key": None})()


def test_task_prompt_contains_applicant_info():
    p = _sample_profile()
    prompt = build_task_prompt(_mock_job(), p, "My cover letter")
    assert "Test User" in prompt
    assert "New York, NY" in prompt
    assert "ML Engineer" in prompt
    assert "Python" in prompt
    assert "My cover letter" in prompt


def test_task_prompt_sensitive_data_placeholders():
    p = _sample_profile()
    job = _mock_job()
    prompt = build_task_prompt(job, p, "", use_sensitive_data=True)
    assert "x_email" in prompt
    assert "x_phone" in prompt
    assert "test@example.com" not in prompt


def test_task_prompt_no_sensitive_data():
    p = _sample_profile()
    job = _mock_job()
    prompt = build_task_prompt(job, p, "", use_sensitive_data=False)
    assert "test@example.com" in prompt
    assert "+1-555-000-0000" in prompt


def test_task_prompt_with_credentials():
    p = _sample_profile()
    job = _mock_job()
    creds = {"email": "login@test.com", "password": "pass123"}
    prompt = build_task_prompt(job, p, "", credentials=creds, use_sensitive_data=True)
    assert "LOGIN" in prompt
    assert "x_login_email" in prompt


# --- Failure classification tests ---


def test_classify_auth_failure():
    assert _classify_failure("Stuck on login page, need verification code") == "auth"
    assert _classify_failure("Need to create account first") == "auth"
    assert _classify_failure("Email verification code required") == "auth"


def test_classify_page_failure():
    assert _classify_failure("Job has been filled") == "page"
    assert _classify_failure("404 not found") == "page"
    assert _classify_failure("Position is no longer available") == "page"
    assert _classify_failure("Hit a captcha") == "page"


def test_classify_form_failure():
    assert _classify_failure("Dropdown stuck on same value") == "form"
    assert _classify_failure("Required field validation error") == "form"
    assert _classify_failure("Could not fill the combobox") == "form"


def test_classify_unknown_failure():
    assert _classify_failure("Something went wrong") == "unknown"
    assert _classify_failure("") == "unknown"


# --- Complexity detection tests ---


def test_complex_url_detection():
    assert is_complex_url("https://company.myworkdayjobs.com/job/123") is True
    assert is_complex_url("https://jpmc.fa.oraclecloud.com/hcmUI/job") is True
    assert is_complex_url("https://taleo.net/career/apply") is True
    assert is_complex_url("https://jobs.ashbyhq.com/company/123") is False
    assert is_complex_url("https://boards.greenhouse.io/company/jobs") is False
    assert is_complex_url("https://jobs.lever.co/company/abc") is False
    assert is_complex_url("https://www.randomsite.com/careers") is False


# --- LLM factory tests ---


def test_is_openai_model():
    assert is_openai_model("gpt-4o") is True
    assert is_openai_model("gpt-5.2") is True
    assert is_openai_model("o1-mini") is True
    assert is_openai_model("o3") is True
    assert is_openai_model("o4-mini") is True
    assert is_openai_model("claude-sonnet-4-20250514") is False
    assert is_openai_model("claude-opus-4-6") is False
    assert is_openai_model("claude-haiku-4-5-20251001") is False


def test_extract_json_clean():
    assert _extract_json('{"key": "value"}') == '{"key": "value"}'


def test_extract_json_trailing_chars():
    dirty = '{"key": "value"}\nSome extra text'
    assert _extract_json(dirty) == '{"key": "value"}'


def test_extract_json_nested():
    nested = '{"a": {"b": [1, 2]}, "c": true}\nextra'
    assert _extract_json(nested) == '{"a": {"b": [1, 2]}, "c": true}'


def test_extract_json_with_strings():
    text = '{"msg": "hello } world"}\ntrailing'
    assert _extract_json(text) == '{"msg": "hello } world"}'


def test_create_browser_llm_anthropic():
    llm = create_browser_llm("claude-sonnet-4-20250514")
    assert llm.provider == "anthropic"


def test_create_browser_llm_openai():
    llm = create_browser_llm("gpt-5.2")
    assert isinstance(llm, CleanJsonOpenAI)
    assert llm.provider == "openai"


# --- System instructions tests ---


def test_system_instructions_has_radio_guidance():
    assert "RADIO BUTTONS" in SYSTEM_INSTRUCTIONS
    assert "NEVER click the same radio option twice" in SYSTEM_INSTRUCTIONS
    assert "DESELECTS" in SYSTEM_INSTRUCTIONS


def test_system_instructions_has_resume_guidance():
    assert "RESUME UPLOAD" in SYSTEM_INSTRUCTIONS
    assert "Resume upload is mandatory" in SYSTEM_INSTRUCTIONS


def test_system_instructions_has_dropdown_guidance():
    assert "DROPDOWN" in SYSTEM_INSTRUCTIONS
    assert "Toggle flyout" in SYSTEM_INSTRUCTIONS


def test_system_instructions_has_location_guidance():
    assert "location/autocomplete" in SYSTEM_INSTRUCTIONS


def test_system_instructions_has_workday_date_picker_guidance():
    assert "WORKDAY DATE PICKERS" in SYSTEM_INSTRUCTIONS
    assert "NEVER send Tab between month and year" in SYSTEM_INSTRUCTIONS
    assert "auto-advances" in SYSTEM_INSTRUCTIONS


def test_system_instructions_has_workday_button_guidance():
    assert "clicking by COORDINATES" in SYSTEM_INSTRUCTIONS


def test_system_instructions_has_sign_in_first():
    assert "Sign In FIRST" in SYSTEM_INSTRUCTIONS


def test_system_instructions_has_use_last_application():
    assert "Use My Last Application" in SYSTEM_INSTRUCTIONS
    assert "send_keys" in SYSTEM_INSTRUCTIONS
