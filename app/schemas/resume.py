import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class StrictSchema(BaseModel):
    """Reject undeclared fields so every resume representation stays canonical."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PersonalInfo(StrictSchema):
    full_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    summary: str | None = None


class WorkExperience(StrictSchema):
    company: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    location: str | None = None
    start_date: str = Field(..., description="e.g. 'Jan 2022' or '2022-01'")
    end_date: str | None = Field(None, description="e.g. 'Present' or 'Dec 2023'")
    is_current: bool = False
    achievements: list[str] = Field(default_factory=list, max_length=50)


class Education(StrictSchema):
    institution: str = Field(..., min_length=1)
    degree: str = Field(..., min_length=1)
    field_of_study: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    gpa: str | None = None


class SkillCategory(StrictSchema):
    category_name: str = Field(..., min_length=1)
    skills: list[str] = Field(default_factory=list, max_length=100)


class Certification(StrictSchema):
    name: str = Field(..., min_length=1)
    issuing_organization: str = Field(..., min_length=1)
    issue_date: str | None = None
    credential_id: str | None = None


class Project(StrictSchema):
    title: str = Field(..., min_length=1)
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    link: str | None = None


class ResumeContent(StrictSchema):
    personal_info: PersonalInfo
    work_experiences: list[WorkExperience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skill_categories: list[SkillCategory] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)


class ResumeCreate(StrictSchema):
    title: str = Field(..., min_length=1, max_length=255)
    raw_data: ResumeContent


class ResumeUpdate(StrictSchema):
    title: str | None = Field(None, min_length=1, max_length=255)
    raw_data: ResumeContent | None = None


class ResumeOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    raw_json_data: dict
    optimized_json_data: dict | None
    ats_score: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OptimizeRequest(StrictSchema):
    target_job_description: str = Field(..., min_length=20)


class KeywordAnalysis(StrictSchema):
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    optimization_summary: str


class ResumeOptimizationResult(StrictSchema):
    ats_score: int = Field(..., ge=0, le=100)
    keyword_analysis: KeywordAnalysis
    optimized_resume_content: ResumeContent


class OptimizeResponse(StrictSchema):
    resume_id: uuid.UUID
    ats_score: int = Field(..., ge=0, le=100)
    keyword_analysis: KeywordAnalysis
    optimized_json_data: ResumeContent


class ResumeExportOut(StrictSchema):
    id: uuid.UUID
    resume_id: uuid.UUID
    state: Literal["pending", "processing", "ready", "failed"]
    filename: str
    size_bytes: int | None
    page_count: int | None
    selectable_text: bool
    last_error_code: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    model_config = ConfigDict(from_attributes=True, extra="forbid")
