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


def test_system_instructions_has_anti_paranoia_loop_prevention():
    assert "LOOP PREVENTION" in SYSTEM_INSTRUCTIONS
    assert "DO NOT scroll up and down repeatedly" in SYSTEM_INSTRUCTIONS


# ---------------------------------------------------------------------------
# PRE-VERIFIED FIELDS block in task prompt (Layer 0 handoff)
# ---------------------------------------------------------------------------


def _sample_job():
    from src.profile import JobTarget

    return JobTarget(url="https://example.com/apply", company="Acme", position="ML Engineer")


def test_task_prompt_without_hints_has_no_preverified_block():
    prompt = build_task_prompt(
        _sample_job(), _sample_profile(), cover_letter="", prefill_hints=None
    )
    assert "PRE-VERIFIED FIELDS" not in prompt


def test_task_prompt_with_hints_inserts_preverified_block():
    hints = [
        ("text", "Full Legal Name", "Gennadiy Khlopov"),
        ("button_group", "Authorized to work?", "Yes"),
        ("button_group", "Require sponsorship?", "No"),
        ("checkbox_group", "How did you hear?", "LinkedIn"),
    ]
    prompt = build_task_prompt(
        _sample_job(), _sample_profile(), cover_letter="", prefill_hints=hints
    )
    assert "PRE-VERIFIED FIELDS" in prompt
    assert "Full Legal Name" in prompt
    assert "type 'Gennadiy Khlopov'" in prompt
    assert "Authorized to work?" in prompt
    assert "click option 'Yes'" in prompt
    assert "LinkedIn" in prompt


def test_task_prompt_hints_include_action_verbs_per_field_type():
    hints_text_only = [("text", "First Name", "Gennadiy")]
    prompt = build_task_prompt(_sample_job(), _sample_profile(), "", prefill_hints=hints_text_only)
    assert "type 'Gennadiy'" in prompt

    hints_checkbox_only = [("checkbox", "I agree to terms", "Yes")]
    prompt = build_task_prompt(
        _sample_job(), _sample_profile(), "", prefill_hints=hints_checkbox_only
    )
    assert "click 'Yes'" in prompt


def test_task_prompt_empty_hint_list_adds_no_block():
    prompt = build_task_prompt(_sample_job(), _sample_profile(), "", prefill_hints=[])
    assert "PRE-VERIFIED FIELDS" not in prompt


# ---------------------------------------------------------------------------
# New helpers: cookie dismissal selectors, scroll loop abort, submit detection
# ---------------------------------------------------------------------------


def test_module_has_scroll_cap_constant():
    from src.agent import MAX_CONSECUTIVE_SCROLL_STEPS

    assert isinstance(MAX_CONSECUTIVE_SCROLL_STEPS, int)
    assert 2 <= MAX_CONSECUTIVE_SCROLL_STEPS <= 10


def test_cookie_dismiss_selectors_defined():
    from src.agent import COOKIE_DISMISS_SELECTORS

    assert len(COOKIE_DISMISS_SELECTORS) >= 5
    joined = " ".join(COOKIE_DISMISS_SELECTORS).lower()
    assert "accept" in joined
    assert "cookie" in joined or "[id*='cookie']" in joined


def test_form_ready_selector_covers_core_form_controls():
    from src.agent import FORM_READY_SELECTORS

    # Must match plain inputs, select, textarea, and common ARIA roles
    assert "input" in FORM_READY_SELECTORS
    assert "select" in FORM_READY_SELECTORS
    assert "textarea" in FORM_READY_SELECTORS
    assert "textbox" in FORM_READY_SELECTORS
    assert "combobox" in FORM_READY_SELECTORS
    assert "radio" in FORM_READY_SELECTORS
    assert "checkbox" in FORM_READY_SELECTORS
    # Must NOT match submit/button inputs (those aren't form fields)
    assert "type='hidden'" in FORM_READY_SELECTORS or 'type="hidden"' in FORM_READY_SELECTORS


def _fake_agent(steps: list) -> object:
    """Build a minimal fake Agent object mirroring browser-use's state shape."""
    from types import SimpleNamespace

    history_obj = SimpleNamespace(history=steps)
    state = SimpleNamespace(history=history_obj, consecutive_failures=0)
    return SimpleNamespace(state=state)


def test_last_step_action_types_returns_empty_on_missing_history():
    """Defensive test: helpers must not crash on minimal/malformed state."""
    from types import SimpleNamespace

    from src.agent import _last_step_action_types

    agent = SimpleNamespace(state=SimpleNamespace(history=None))
    assert _last_step_action_types(agent) == []


def test_last_step_action_types_parses_dict_actions():
    from types import SimpleNamespace

    from src.agent import _last_step_action_types

    step = SimpleNamespace(
        model_output=SimpleNamespace(
            action=[{"scroll": {"down": True, "pages": 1.0}}, {"click": None}]
        )
    )
    types = _last_step_action_types(_fake_agent([step]))
    # click has value None so it's filtered; scroll has a dict value so it's kept
    assert "scroll" in types
    assert "click" not in types


def test_last_step_action_types_handles_empty_step_list():
    from src.agent import _last_step_action_types

    assert _last_step_action_types(_fake_agent([])) == []


def test_looks_like_submit_click_returns_false_on_no_results():
    from types import SimpleNamespace

    from src.agent import _looks_like_submit_click

    step = SimpleNamespace(result=[])
    assert _looks_like_submit_click(_fake_agent([step])) is False


def test_looks_like_submit_click_detects_submit_content():
    from types import SimpleNamespace

    from src.agent import _looks_like_submit_click

    result_item = SimpleNamespace(
        extracted_content="Clicked button 'Submit Application'",
        error=None,
    )
    step = SimpleNamespace(result=[result_item])
    assert _looks_like_submit_click(_fake_agent([step])) is True


def test_looks_like_submit_click_ignores_unrelated_clicks():
    from types import SimpleNamespace

    from src.agent import _looks_like_submit_click

    result_item = SimpleNamespace(
        extracted_content="Clicked some random button",
        error=None,
    )
    step = SimpleNamespace(result=[result_item])
    assert _looks_like_submit_click(_fake_agent([step])) is False
