"""Data models for the job application agent."""

from pydantic import BaseModel, Field


class Education(BaseModel):
    school: str
    degree: str
    year: int


class Experience(BaseModel):
    company: str
    title: str
    dates: str
    bullets: list[str]


class Profile(BaseModel):
    """Applicant's structured resume data."""

    # Personal
    first_name: str
    last_name: str
    email: str
    phone: str
    location: str
    linkedin_url: str
    github_url: str | None = None
    portfolio_url: str | None = None

    # Professional
    current_title: str
    years_experience: int
    summary: str

    # Background
    education: list[Education]
    experience: list[Experience]
    skills: list[str]

    # Common form fields
    work_authorization: str = "Yes - US Citizen"
    requires_sponsorship: str = "No"
    salary_expectation: str = ""
    start_date: str = "2 weeks notice"

    # EEO (optional)
    gender: str = "Decline to self-identify"
    ethnicity: str = "Decline to self-identify"
    veteran_status: str = "Decline to self-identify"
    disability_status: str = "Decline to self-identify"

    # File
    resume_path: str

    def to_compact_str(self) -> str:
        """Minimal string representation for LLM prompts. Saves tokens."""
        skills_str = ", ".join(self.skills[:15])
        exp_str = "; ".join(
            f"{e.company} ({e.title}, {e.dates})" for e in self.experience[:3]
        )
        return (
            f"{self.first_name} {self.last_name} | {self.current_title} | "
            f"{self.years_experience}yr exp | {self.location}\n"
            f"Skills: {skills_str}\n"
            f"Experience: {exp_str}\n"
            f"Education: {self.education[0].degree}, {self.education[0].school}"
        )


class JobTarget(BaseModel):
    """A single job to apply to."""

    url: str
    company: str | None = None
    position: str | None = None
    notes: str | None = None
    credentials_key: str | None = None


class JobDescription(BaseModel):
    """Scraped job posting data."""

    url: str
    company: str = ""
    position: str = ""
    description: str = ""
    requirements: list[str] = Field(default_factory=list)
    location: str = ""
    salary_range: str = ""


class ApplicationResult(BaseModel):
    """Result of a single application attempt."""

    job_url: str
    company: str = ""
    position: str = ""
    status: str  # "filled" | "error" | "skipped"
    error: str | None = None
    screenshot_path: str | None = None
    cover_letter_path: str | None = None
