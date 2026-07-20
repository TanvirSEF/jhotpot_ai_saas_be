import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class PersonalInfo(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    summary: str | None = None


class WorkExperience(BaseModel):
    company: str = Field(..., min_length=1)
    role: str = Field(..., min_length=1)
    location: str | None = None
    start_date: str = Field(..., description="e.g. 'Jan 2022' or '2022-01'")
    end_date: str | None = Field(None, description="e.g. 'Present' or 'Dec 2023'")
    is_current: bool = False
    achievements: list[str] = Field(default_factory=list)


class Education(BaseModel):
    institution: str = Field(..., min_length=1)
    degree: str = Field(..., min_length=1)
    field_of_study: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    gpa: str | None = None


class SkillCategory(BaseModel):
    category_name: str = Field(..., min_length=1)
    skills: list[str] = Field(default_factory=list)


class Certification(BaseModel):
    name: str = Field(..., min_length=1)
    issuing_organization: str = Field(..., min_length=1)
    issue_date: str | None = None
    credential_id: str | None = None


class Project(BaseModel):
    title: str = Field(..., min_length=1)
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    link: str | None = None


class ResumeContent(BaseModel):
    personal_info: PersonalInfo
    work_experiences: list[WorkExperience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skill_categories: list[SkillCategory] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)


class ResumeCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    raw_data: ResumeContent


class ResumeUpdate(BaseModel):
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


class OptimizeRequest(BaseModel):
    target_job_description: str = Field(..., min_length=20)


class KeywordAnalysis(BaseModel):
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    optimization_summary: str


class OptimizeResponse(BaseModel):
    resume_id: uuid.UUID
    ats_score: int
    keyword_analysis: KeywordAnalysis
    optimized_json_data: dict

