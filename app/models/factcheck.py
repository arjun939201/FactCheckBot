from datetime import datetime,timezone
from enum import Enum
from pydantic import BaseModel,Field,HttpUrl

class Verdict(str,Enum):
    TRUE="TRUE"; MOSTLY_TRUE="MOSTLY TRUE"; PARTLY_TRUE="PARTLY TRUE"; MISLEADING="MISLEADING"
    MOSTLY_FALSE="MOSTLY FALSE"; FALSE="FALSE"; UNVERIFIED="UNVERIFIED"; OPINION="OPINION"; PREDICTION="PREDICTION"
class ContentType(str,Enum):
    AUTO="AUTO"; FACT="FACTUAL CLAIM"; ARGUMENT="ARGUMENT"; OPINION="OPINION"; PROPAGANDA="PROPAGANDA"
    PREDICTION="PREDICTION"; QUESTION="QUESTION"; SATIRE="SATIRE/UNCLEAR"; MIXED="MIXED"
class Source(BaseModel):
    title:str; publisher:str=""; url:HttpUrl; date:str=""; source_type:str="Other"; relevance:str=""; excerpt:str=""; retrieved_at:str=""
    source_quality:int=Field(default=0,ge=0,le=100); source_tier:str="Other"; relevance_score:int=Field(default=0,ge=0,le=100); relevance_reason:str=""; corroboration_count:int=0
class Evidence(BaseModel):
    evidence_id:str=""; claim:str; excerpt:str; url:HttpUrl; title:str=""; publisher:str=""; source_type:str="Other"
    source_quality:int=Field(default=0,ge=0,le=100); source_tier:str="Other"; relevance_score:int=Field(default=0,ge=0,le=100); relevance_reason:str=""
class ClaimAssessment(BaseModel):
    claim:str; content_type:ContentType=ContentType.FACT; verdict:Verdict; confidence:int=Field(ge=0,le=100); summary:str
    supporting_evidence_ids:list[str]=Field(default_factory=list); contradicting_evidence_ids:list[str]=Field(default_factory=list)
    source_quality:int=Field(default=0,ge=0,le=100); corroboration_count:int=0
    reasoning:str=""; what_would_change_conclusion:str=""
class FactCheckRequest(BaseModel):
    text:str=Field(min_length=1,max_length=12000)
    content_mode:str=Field(default="auto",pattern="^(auto|factual|argument|opinion|propaganda|prediction|question|satire|mixed)$")
    detail:str=Field(default="standard",pattern="^(quick|standard|detailed)$")
    audience:str=Field(default="general",pattern="^(general|student|professional|research)$")
    source_preference:str=Field(default="any",pattern="^(any|official|academic|news|factcheck)$")
    region:str=Field(default="global",max_length=80); language:str=Field(default="English",max_length=30)
class URLFactCheckRequest(FactCheckRequest): url:HttpUrl
class MediaAttachment(BaseModel):
    filename:str; media_type:str; kind:str; size_bytes:int; extracted_text:str=""; visual_summary:str=""
class ResearchData(BaseModel):
    question:str; answer:str; evidence_ids:list[str]=Field(default_factory=list)
class ResourcePlan(BaseModel):
    context:str="general"
    resource_types:list[str]=Field(default_factory=list)
    preferred_domains:list[str]=Field(default_factory=list)
    search_strategy:list[str]=Field(default_factory=list)
    rationale:str=""

class FactCheckResult(BaseModel):
    claim:str; verdict:Verdict; confidence:int=Field(ge=0,le=100); summary:str; reasoning:str
    key_points:list[str]=Field(default_factory=list); supporting_evidence:list[Evidence]=Field(default_factory=list); contradicting_evidence:list[Evidence]=Field(default_factory=list)
    context:str=""; sources:list[Source]=Field(default_factory=list); uncertainties:list[str]=Field(default_factory=list); content_type:ContentType=ContentType.FACT
    report_title:str="Fact Check Analysis Report"; report_sections:list[str]=Field(default_factory=list); attachments:list[MediaAttachment]=Field(default_factory=list)
    last_checked:str=Field(default_factory=lambda:datetime.now(timezone.utc).isoformat()); live_evidence_available:bool=False
    claims_checked:list[ClaimAssessment]=Field(default_factory=list)
    research_questions:list[str]=Field(default_factory=list)
    research_data:list[ResearchData]=Field(default_factory=list)
    resource_plan:ResourcePlan=Field(default_factory=ResourcePlan)
class ArticleClaim(BaseModel):
    claim:str; verdict:Verdict; confidence:int=Field(ge=0,le=100); summary:str
class ArticleFactCheck(BaseModel):
    article_title:str=""; article_url:HttpUrl; overall_verdict:Verdict; overall_confidence:int=Field(ge=0,le=100)
    summary:str; claims_checked:list[ArticleClaim]=Field(default_factory=list); sources:list[Source]=Field(default_factory=list); uncertainties:list[str]=Field(default_factory=list)
    last_checked:str=Field(default_factory=lambda:datetime.now(timezone.utc).isoformat()); live_evidence_available:bool=False
